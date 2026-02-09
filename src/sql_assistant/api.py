import logging
import os
import uuid
import re
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from .agent import create_agent_graph
from .config import get_settings
from .database import get_db_connection, DatabasePool

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle: DB Pool, Checkpointer, Env Vars."""
    logger.info("Starting SQL Assistant API (In-Memory Persistence)...")
    
    # 1. Environment Setup (LangSmith)
    settings = get_settings()
    if settings.LANGSMITH_TRACING:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_PROJECT"] = settings.LANGSMITH_PROJECT
        if settings.LANGSMITH_API_KEY:
            os.environ["LANGSMITH_API_KEY"] = settings.LANGSMITH_API_KEY
            
    # 2. Initialize Database Pool (for tools)
    DatabasePool.initialize()
    
    # 3. Initialize Persistence (In-Memory)
    try:
        checkpointer = MemorySaver()
        app.state.graph = create_agent_graph(checkpointer=checkpointer)
        logger.info("Graph initialized with In-Memory Persistence.")
        yield
    except Exception as e:
        logger.critical(f"Failed to initialize Graph: {e}")
        raise
    finally:
        # Cleanup
        DatabasePool.close_all()
        logger.info("Shutdown complete.")

app = FastAPI(title="SQL Assistant API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # React Dev Server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Models ---

class ChatRequest(BaseModel):
    message: str
    thread_id: Optional[str] = None
    auto_execute: bool = False

class ApprovalRequest(BaseModel):
    decision: str  # "approve" or "reject"
    feedback: Optional[str] = None
    thread_id: str

class ChatResponse(BaseModel):
    thread_id: str
    response: str
    status: str  # "done", "approval_required"
    tool_calls: Optional[List[Dict[str, Any]]] = None
    structured_data: Optional[Dict[str, Any]] = None
    query: Optional[str] = None

# --- Helpers ---

def execute_query_locally(query: str):
    """Executes a SQL query against the configured database using the pool."""
    try:
        logger.info(f"Executing SQL Query (Local): {query}")
        
        # Use the context manager from database.py which handles getting/returning connection
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            
            if cursor.description:
                columns = [col.name for col in cursor.description]
                results = cursor.fetchall()
            else:
                columns = []
                results = []
            
            conn.commit()
        
        # Format Output
        if not results:
            result_text = "[Execution Result]: No results found."
            structured_data = None
        else:
            # Simple Markdown Table
            header = "| " + " | ".join(columns) + " |"
            separator = "| " + " | ".join(["---"] * len(columns)) + " |"
            rows_md = []
            for row in results:
                rows_md.append("| " + " | ".join(str(cell) for cell in row) + " |")
            
            result_text = "\n".join([header, separator] + rows_md)
            
            # Structured Data
            structured_data = {
                "headers": columns,
                "rows": results
            }
        
        logger.info(f"Query executed successfully. Rows returned: {len(results)}")
        return result_text, structured_data, None

    except Exception as e:
        logger.error(f"Database Execution Error: {e}")
        return f"Error executing query: {str(e)}", None, str(e)

def process_run(graph, thread_id, inputs, config):
    """Helper to run the graph and format response."""
    # Note: graph is now passed in
    try:
        # Stream strictly values to handle updates
        events = list(graph.stream(inputs, config, stream_mode="values"))
        
        # Check final state
        snapshot = graph.get_state(config)
        
        # Determine Status
        if snapshot.next and "human_approval" in snapshot.next:
            status = "approval_required"
        else:
            status = "done"
            
        # Get last message
        if not snapshot.values or "messages" not in snapshot.values:
             # Basic fallback if state is empty
             return ChatResponse(thread_id=thread_id, response="Error: No state found.", status="done")

        last_message = snapshot.values["messages"][-1]
        response_text = last_message.content
        
        return ChatResponse(
            thread_id=thread_id,
            response=response_text,
            status=status
        )
    except Exception as e:
        logger.error(f"Error in process_run: {e}", exc_info=True)
        raise

# --- Endpoints ---

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, api_request: Request):
    logger.info(f"Received chat request: {request.message} (Thread: {request.thread_id}, Auto: {request.auto_execute})")
    
    thread_id = request.thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    
    # Retrieve graph from app state
    graph = api_request.app.state.graph
    
    # Check interrupt state
    snapshot = graph.get_state(config)
    if snapshot.next and "human_approval" in snapshot.next:
         graph.update_state(config, {"messages": [HumanMessage(content=f"Rejected. Feedback: {request.message}")]})

    inputs = {"messages": [HumanMessage(content=request.message)]} if not (snapshot.next and "human_approval" in snapshot.next) else None
    
    chat_response = process_run(graph, thread_id, inputs, config)

    # AUTO EXECUTE LOGIC
    if request.auto_execute and chat_response.status == "approval_required":
        try:
            # Extract SQL
            content = chat_response.response
            match = re.search(r"```(?:sql)?(.*?)```", content, re.DOTALL)
            if match:
                query = match.group(1).strip()
            else:
                query = content.strip() # Fallback
            
            logger.info(f"Auto-executing query for thread {thread_id}: {query}")
            
            # Execute
            result_text, structured_data, error = execute_query_locally(query)
            
            if error:
                 logger.warning(f"Auto-execution failed for thread {thread_id}: {error}")
                 return ChatResponse(
                    thread_id=thread_id,
                    response=f"Error executing query (Auto-Mode): {error}",
                    status="done",
                    query=query
                )

            # Resume Graph with Result
            graph.update_state(config, {"messages": [HumanMessage(content="<SYSTEM: Execution Completed Locally>")]})
            # Resume strictly
            list(graph.stream(None, config, stream_mode="values"))
            
            return ChatResponse(
                thread_id=thread_id,
                response=f"**Auto Execution Result**:\n\n{result_text}",
                status="done",
                structured_data=structured_data,
                query=query
            )
        except Exception as e:
            logger.critical(f"Auto-execute system error: {e}", exc_info=True)
            return ChatResponse(
                thread_id=thread_id,
                response=f"System Error during auto-execution: {str(e)}",
                status="done"
            )

    return chat_response

@app.post("/approval", response_model=ChatResponse)
async def approval(request: ApprovalRequest, api_request: Request):
    logger.info(f"Received approval decision: {request.decision} (Thread: {request.thread_id})")
    
    graph = api_request.app.state.graph
    config = {"configurable": {"thread_id": request.thread_id}}
    snapshot = graph.get_state(config)
    
    if not (snapshot.next and "human_approval" in snapshot.next):
        raise HTTPException(status_code=400, detail="Conversation is not waiting for approval.")

    if request.decision == "approve":
        # 1. Get last message content
        last_msg = snapshot.values["messages"][-1]
        content = last_msg.content
        
        # 2. Extract SQL
        match = re.search(r"```(?:sql)?(.*?)```", content, re.DOTALL)
        if match:
            query = match.group(1).strip()
        else:
            # Use whole content if no code block found (flexible fallback)
            query = content.strip()
            
        # 3. Execute using Helper
        result_text, structured_data, error = execute_query_locally(query)
        
        if error:
             return ChatResponse(
                thread_id=request.thread_id,
                response=f"Error executing query: {error}",
                status="done",
                query=query
            )
            
        # 5. "Finish" the turn without Agent LLM
        graph.update_state(config, {"messages": [HumanMessage(content="<SYSTEM: Execution Completed Locally>")]})
        list(graph.stream(None, config, stream_mode="values"))
        
        return ChatResponse(
            thread_id=request.thread_id,
            response=f"**Execution Result**:\n\n{result_text}",
            status="done",
            structured_data=structured_data,
            query=query
        )

    else:
        # Rejection
        feedback = request.feedback or "Rejected."
        graph.update_state(config, {"messages": [HumanMessage(content=f"Rejected. Feedback: {feedback}")]})
        return process_run(graph, request.thread_id, None, config)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
