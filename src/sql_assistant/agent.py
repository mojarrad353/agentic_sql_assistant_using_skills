import operator
from typing import Annotated, Sequence, TypedDict, Union, List
import logging

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END, START
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI
import psycopg2 

from .config import get_settings
from .skills.repository import get_skill_repository

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Type definition for the agent state
class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], operator.add]

# --- Tools ---

# --- Tools ---
@tool
def load_skill(skill_name: str) -> str:
    """Load the full content (schema, rules) of a specific skill."""
    logger.info(f"Tool Call: load_skill({skill_name})")
    try:
        repo = get_skill_repository()
        skill = repo.get_skill(skill_name)
        
        if skill and skill.get("content"):
            return skill["content"]
            
        logger.warning(f"Skill not found or empty: {skill_name}")
        return f"Skill '{skill_name}' not found."
    except Exception as e:
        logger.error(f"Error loading skill {skill_name}: {e}")
        return f"Error loading skill: {e}"

@tool
def execute_postgres_query(query: str) -> str:
    """Execute a PostgreSQL query against the business database.
    
    Returns the result as a formatted string table or an error message.
    """
    logger.info(f"Tool Call: execute_postgres_query()")
    logger.debug(f"Query: {query}")
    # settings = get_settings() # Handled by database.py
    
    from .database import get_db_connection # Lazy import to avoid circular deps if any
    
    try:
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
            # conn closes automatically by context manager (returned to pool)
        
        if not results:
            logger.info("Query returned no results.")
            return "Query executed successfully per row count: 0"
            
        # Format as simple markdown table for LLM readability
        header = "| " + " | ".join(columns) + " |"
        separator = "| " + " | ".join(["---"] * len(columns)) + " |"
        rows = []
        for row in results[:10]: # Limit context usage
            rows.append("| " + " | ".join(str(cell) for cell in row) + " |")
            
        if len(results) > 10:
            rows.append(f"... ({len(results) - 10} more rows)")
            
        logger.info(f"Query executed successfully. Rows: {len(results)}")
        return "\n".join([header, separator] + rows)

    except Exception as e:
        logger.error(f"Database Error: {e}")
        return f"Error executing query: {e}"

# --- Agent Logic ---

def create_agent_graph(checkpointer=None):
    """Builds the LangGraph state graph for the agent."""
    settings = get_settings()
    # repo = get_skill_repository() # This line is not used in the provided snippet, but was in the original.
    from .skills.repository import get_skill_repository # Lazy import
    repo = get_skill_repository()

    # Initialize model
    llm = ChatOpenAI(
        model=settings.OPENAI_MODEL_NAME, 
        api_key=settings.OPENAI_API_KEY,
        temperature=0
    )
    
    # Bind tools
    tools = [load_skill, execute_postgres_query]
    llm_with_tools = llm.bind_tools(tools)

    # Initial System Prompt Construction
    skills_descriptions = []
    for skill in repo.list_skills():
        skills_descriptions.append(f"- **{skill['name']}**: {skill['description']}")
    
    skills_prompt = "\n".join(skills_descriptions)
    
    system_prompt = (
        "You are a SQL query assistant that helps users write queries against business databases.\n"
        "You MUST output valid PostgreSQL queries.\n"
        "You MUST wrap the proposed SQL query in a markdown block, e.g., ```sql ... ```.\n\n"
        "## Available Skills\n\n"
        f"{skills_prompt}\n\n"
        "Use the load_skill tool when you need detailed information "
        "about handling a specific type of request. "
        "Do not guess the schema; always load the relevant skill first."
    )

    # Node: Agent (LLM Call)
    def agent_node(state: AgentState):
        messages = state["messages"]
        
        # Construct the call explicitly
        response = llm_with_tools.invoke([SystemMessage(content=system_prompt)] + messages)
        return {"messages": [response]}

# Node: Human Approval (Pass-through)
    def human_approval_node(state: AgentState):
        pass

    # Build Graph
    graph_builder = StateGraph(AgentState)
    
    graph_builder.add_node("agent", agent_node)
    tool_node = ToolNode(tools=tools)
    graph_builder.add_node("tools", tool_node)
    graph_builder.add_node("human_approval", human_approval_node)
    
    graph_builder.add_edge(START, "agent")
    
    # Conditional edge logic
    def should_continue(state: AgentState):
        messages = state["messages"]
        last_message = messages[-1]
        
        # If the agent made tool calls, go to tools
        # Safely check for tool_calls attribute
        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            return "tools"
        
        # Check if we just finished executing a query
        # If the second to last message was a ToolMessage from 'execute_postgres_query', 
        # then this AIMessage is the final summary, so we should END.
        if len(messages) > 1:
            second_last = messages[-2]
            if isinstance(second_last, ToolMessage) and second_last.name == "execute_postgres_query":
                return END

        # Otherwise, go to human approval
        return "human_approval"

    graph_builder.add_conditional_edges(
        "agent",
        should_continue,
    )
    
    # New logic: Check if we should return to agent or END (for optimizations)
    def route_tool_output(state: AgentState):
        messages = state["messages"]
        last_message = messages[-1]
        
        # If the last message is a ToolMessage from 'execute_postgres_query', we stop.
        if isinstance(last_message, ToolMessage) and last_message.name == "execute_postgres_query":
            return END
            
        return "agent"

    graph_builder.add_conditional_edges(
        "tools",
        route_tool_output,
    )
    
    # Human Approval logic:
    # If we resume with a HumanMessage (feedback), loop back to agent.
    # If we resume without feedback (approval), go to END.
    def check_approval_outcome(state: AgentState):
        messages = state["messages"]
        last_message = messages[-1]
        
        if isinstance(last_message, HumanMessage):
            # Check for special system flag to end conversation silently
            if last_message.content.startswith("<SYSTEM:"):
                return END
            return "agent"
        return END

    graph_builder.add_conditional_edges(
        "human_approval",
        check_approval_outcome,
    )

    return graph_builder.compile(checkpointer=checkpointer, interrupt_before=["human_approval"])
