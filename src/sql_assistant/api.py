import os
import time
import uuid
import re
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from .agent import create_agent_graph
from .config import get_settings
from .database import get_db_connection, DatabasePool
from .logging_config import configure_logging, get_logger
from .metrics import (
    REQUESTS_TOTAL,
    REQUEST_DURATION_SECONDS,
    APPROVAL_DECISIONS_TOTAL,
    QUERY_EXECUTION_DURATION_SECONDS,
    QUERY_ROWS_RETURNED,
)

logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle: DB Pool, Checkpointer, Env Vars."""
    # Configure structured logging once at startup
    configure_logging()
    
    logger.info("api_starting")
    
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
        logger.info("graph_initialized", persistence="in_memory")
        yield
    except Exception as e:
        logger.critical("graph_init_failed", error=str(e))
        raise
    finally:
        # Cleanup
        DatabasePool.close_all()
        logger.info("api_shutdown_complete")

app = FastAPI(title="SQL Assistant API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # React Dev Server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Prometheus Metrics Middleware ---

@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    """Record HTTP request count and latency for every endpoint."""
    # Skip the /metrics endpoint itself to avoid self-referential noise
    if request.url.path == "/metrics":
        return await call_next(request)
    
    start = time.time()
    response = await call_next(request)
    duration = time.time() - start
    
    endpoint = request.url.path
    method = request.method
    status_code = str(response.status_code)
    
    REQUESTS_TOTAL.labels(endpoint=endpoint, method=method, status_code=status_code).inc()
    REQUEST_DURATION_SECONDS.labels(endpoint=endpoint, method=method).observe(duration)
    
    logger.info("http_request",
                endpoint=endpoint,
                method=method,
                status_code=status_code,
                duration_s=round(duration, 3))
    
    return response

# --- Prometheus Metrics Endpoint ---

@app.get("/metrics")
async def metrics():
    """Expose Prometheus metrics for scraping."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

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
        logger.info("local_query_executing", query_length=len(query))
        
        # Use the context manager from database.py which handles getting/returning connection
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            query_start = time.time()
            cursor.execute(query)
            query_duration = time.time() - query_start
            QUERY_EXECUTION_DURATION_SECONDS.labels(source="local_api").observe(query_duration)
            
            if cursor.description:
                columns = [col.name for col in cursor.description]
                results = cursor.fetchall()
            else:
                columns = []
                results = []
            
            conn.commit()
        
        row_count = len(results)
        QUERY_ROWS_RETURNED.observe(row_count)
        
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
        
        logger.info("local_query_executed", rows=row_count,
                     query_duration_s=round(query_duration, 3))
        return result_text, structured_data, None

    except Exception as e:
        logger.error("local_query_error", error=str(e))
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
        logger.error("process_run_error", error=str(e), exc_info=True)
        raise

# --- Endpoints ---

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, api_request: Request):
    logger.info("chat_request_received", message_length=len(request.message),
                thread_id=request.thread_id, auto_execute=request.auto_execute)
    
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
            
            logger.info("auto_execute_start", thread_id=thread_id, query_length=len(query))
            
            # Execute
            result_text, structured_data, error = execute_query_locally(query)
            
            if error:
                 logger.warning("auto_execute_failed", thread_id=thread_id, error=error)
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
            logger.critical("auto_execute_system_error", error=str(e), exc_info=True)
            return ChatResponse(
                thread_id=thread_id,
                response=f"System Error during auto-execution: {str(e)}",
                status="done"
            )

    return chat_response

@app.post("/approval", response_model=ChatResponse)
async def approval(request: ApprovalRequest, api_request: Request):
    logger.info("approval_received", decision=request.decision, thread_id=request.thread_id)
    
    graph = api_request.app.state.graph
    config = {"configurable": {"thread_id": request.thread_id}}
    snapshot = graph.get_state(config)
    
    if not (snapshot.next and "human_approval" in snapshot.next):
        raise HTTPException(status_code=400, detail="Conversation is not waiting for approval.")

    if request.decision == "approve":
        APPROVAL_DECISIONS_TOTAL.labels(decision="approve").inc()
        
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
        APPROVAL_DECISIONS_TOTAL.labels(decision="reject").inc()
        
        # Rejection
        feedback = request.feedback or "Rejected."
        logger.info("approval_rejected", thread_id=request.thread_id, feedback=feedback)
        graph.update_state(config, {"messages": [HumanMessage(content=f"Rejected. Feedback: {feedback}")]})
        return process_run(graph, request.thread_id, None, config)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
