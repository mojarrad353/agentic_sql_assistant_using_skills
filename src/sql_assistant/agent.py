import operator
import time
from typing import Annotated, Sequence, TypedDict, Union, List

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END, START
from langgraph.prebuilt import ToolNode
from langchain_openai import ChatOpenAI
import psycopg2
import sqlglot

from .config import get_settings
from .skills.repository import get_skill_repository
from .logging_config import get_logger
from .metrics import (
    TOOL_CALLS_TOTAL,
    TOOL_DURATION_SECONDS,
    VALIDATION_RESULTS_TOTAL,
    QUERY_EXECUTION_DURATION_SECONDS,
    QUERY_ROWS_RETURNED,
    LLM_CALLS_TOTAL,
)

logger = get_logger(__name__)

# Type definition for the agent state
class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], operator.add]


# --- Tools ---
@tool
def load_skill(skill_name: str) -> str:
    """Load the full content (schema, rules) of a specific skill."""
    logger.info("tool_called", tool="load_skill", skill_name=skill_name)
    start = time.time()
    try:
        repo = get_skill_repository()
        skill = repo.get_skill(skill_name)
        
        if skill and skill.get("content"):
            duration = time.time() - start
            TOOL_CALLS_TOTAL.labels(tool_name="load_skill", status="success").inc()
            TOOL_DURATION_SECONDS.labels(tool_name="load_skill").observe(duration)
            logger.info("tool_completed", tool="load_skill", skill_name=skill_name, duration_s=round(duration, 3))
            return skill["content"]
            
        logger.warning("skill_not_found", skill_name=skill_name)
        TOOL_CALLS_TOTAL.labels(tool_name="load_skill", status="error").inc()
        TOOL_DURATION_SECONDS.labels(tool_name="load_skill").observe(time.time() - start)
        return f"Skill '{skill_name}' not found."
    except Exception as e:
        TOOL_CALLS_TOTAL.labels(tool_name="load_skill", status="error").inc()
        TOOL_DURATION_SECONDS.labels(tool_name="load_skill").observe(time.time() - start)
        logger.error("tool_error", tool="load_skill", error=str(e))
        return f"Error loading skill: {e}"

@tool
def execute_postgres_query(query: str) -> str:
    """Execute a PostgreSQL query against the business database.
    
    Returns the result as a formatted string table or an error message.
    """
    logger.info("tool_called", tool="execute_postgres_query", query_length=len(query))
    start = time.time()
    # settings = get_settings() # Handled by database.py
    
    from .database import get_db_connection # Lazy import to avoid circular deps if any
    
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            
            query_start = time.time()
            cursor.execute(query)
            query_duration = time.time() - query_start
            QUERY_EXECUTION_DURATION_SECONDS.labels(source="agent_tool").observe(query_duration)
            
            if cursor.description:
                columns = [col.name for col in cursor.description]
                results = cursor.fetchall()
            else:
                columns = []
                results = []
            
            conn.commit()
            # conn closes automatically by context manager (returned to pool)
        
        row_count = len(results)
        QUERY_ROWS_RETURNED.observe(row_count)
        
        if not results:
            duration = time.time() - start
            TOOL_CALLS_TOTAL.labels(tool_name="execute_postgres_query", status="success").inc()
            TOOL_DURATION_SECONDS.labels(tool_name="execute_postgres_query").observe(duration)
            logger.info("query_no_results", duration_s=round(duration, 3), query_duration_s=round(query_duration, 3))
            return "Query executed successfully per row count: 0"
            
        # Format as simple markdown table for LLM readability
        header = "| " + " | ".join(columns) + " |"
        separator = "| " + " | ".join(["---"] * len(columns)) + " |"
        rows = []
        for row in results[:10]: # Limit context usage
            rows.append("| " + " | ".join(str(cell) for cell in row) + " |")
            
        if len(results) > 10:
            rows.append(f"... ({len(results) - 10} more rows)")
            
        duration = time.time() - start
        TOOL_CALLS_TOTAL.labels(tool_name="execute_postgres_query", status="success").inc()
        TOOL_DURATION_SECONDS.labels(tool_name="execute_postgres_query").observe(duration)
        logger.info("query_executed", rows=row_count, duration_s=round(duration, 3), query_duration_s=round(query_duration, 3))
        return "\n".join([header, separator] + rows)

    except Exception as e:
        TOOL_CALLS_TOTAL.labels(tool_name="execute_postgres_query", status="error").inc()
        TOOL_DURATION_SECONDS.labels(tool_name="execute_postgres_query").observe(time.time() - start)
        logger.error("tool_error", tool="execute_postgres_query", error=str(e))
        return f"Error executing query: {e}"

@tool
def validate_sql(sql_query: str) -> str:
    """Validate a SQL query before execution.

    Checks for:
    - Syntax correctness (PostgreSQL dialect)
    - Dangerous/destructive statements (DROP, DELETE, TRUNCATE, ALTER, UPDATE)
    - Missing safety clauses (SELECT without LIMIT, missing WHERE on large scans)

    Returns 'VALID' if the query passes all checks, or an error description.
    """
    logger.info("tool_called", tool="validate_sql", query_length=len(sql_query))
    start = time.time()

    errors = []

    # 1. Syntax check using sqlglot
    try:
        parsed = sqlglot.parse(sql_query, dialect="postgres")
        if not parsed or all(expr is None for expr in parsed):
            errors.append("Syntax Error: Could not parse the query. It may be empty or malformed.")
    except sqlglot.errors.ParseError as e:
        errors.append(f"Syntax Error: {e}")

    # 2. Safety check — block destructive statements
    dangerous_keywords = ["DROP", "DELETE", "TRUNCATE", "ALTER", "UPDATE", "INSERT"]
    upper_query = sql_query.upper().strip()
    for keyword in dangerous_keywords:
        # Check if the statement starts with or contains a top-level dangerous keyword
        if upper_query.startswith(keyword):
            errors.append(
                f"Safety Error: '{keyword}' statements are not allowed. "
                f"This assistant is read-only."
            )
            break

    # 3. Best-practice warnings
    if upper_query.startswith("SELECT"):
        if "LIMIT" not in upper_query:
            errors.append(
                "Warning: SELECT query has no LIMIT clause. "
                "Consider adding LIMIT to avoid returning excessive rows."
            )

    duration = time.time() - start
    TOOL_CALLS_TOTAL.labels(tool_name="validate_sql", status="success").inc()
    TOOL_DURATION_SECONDS.labels(tool_name="validate_sql").observe(duration)

    if errors:
        result = "INVALID — Please fix the following issues:\n" + "\n".join(
            f"  - {e}" for e in errors
        )
        VALIDATION_RESULTS_TOTAL.labels(result="invalid").inc()
        logger.warning("validation_failed", issues=errors, duration_s=round(duration, 3))
        return result

    VALIDATION_RESULTS_TOTAL.labels(result="valid").inc()
    logger.info("validation_passed", duration_s=round(duration, 3))
    return "VALID"


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
    tools = [load_skill, validate_sql, execute_postgres_query]
    llm_with_tools = llm.bind_tools(tools)

    # Initial System Prompt Construction
    skills_descriptions = []
    for skill in repo.list_skills():
        skills_descriptions.append(f"- **{skill['name']}**: {skill['description']}")
    
    skills_prompt = "\n".join(skills_descriptions)
    
    system_prompt = (
        "You are a SQL query assistant that helps users write queries against business databases.\n"
        "You MUST output valid PostgreSQL queries.\n"
        "You MUST ALWAYS include a `LIMIT` clause in your SELECT queries (default to 10 if not specified) to prevent excessive data retrieval.\n"
        "You MUST wrap the proposed SQL query in a markdown block, e.g., ```sql ... ```.\n\n"
        "If the user's request is ambiguous or you need more information to write the query (like which schema to use), ask the user a clarifying question. Do NOT output any SQL blocks when asking a question.\n\n"
        "## Workflow\n\n"
        "1. Load the relevant skill to understand the schema.\n"
        "2. Generate the SQL query.\n"
        "3. ALWAYS call `validate_sql` with the generated query before presenting it to the user.\n"
        "4. If validation fails, fix the issues and re-validate until it passes.\n"
        "5. Only present the query to the user after validation returns VALID.\n\n"
        "## Available Skills\n\n"
        f"{skills_prompt}\n\n"
        "Use the load_skill tool when you need detailed information "
        "about handling a specific type of request. "
        "Do not guess the schema; always load the relevant skill first."
    )

    # Node: Agent (LLM Call)
    def agent_node(state: AgentState):
        messages = state["messages"]
        
        LLM_CALLS_TOTAL.labels(node="agent").inc()
        logger.info("llm_call_start", node="agent", message_count=len(messages))
        start = time.time()
        
        # Construct the call explicitly
        response = llm_with_tools.invoke([SystemMessage(content=system_prompt)] + messages)
        
        duration = time.time() - start
        logger.info("llm_call_complete", node="agent", duration_s=round(duration, 3),
                     has_tool_calls=bool(response.tool_calls) if hasattr(response, 'tool_calls') else False)
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

        # CONVERSATIONAL CHECK: Does the message contain a proposed SQL block?
        content = getattr(last_message, "content", "")
        if isinstance(content, str):
            if "```sql" in content or "```" in content:
                return "human_approval"

        # Otherwise, go to human approval
        return END

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
            if isinstance(last_message.content, str) and last_message.content.startswith("<SYSTEM:"):
                return END
            return "agent"
        return END

    graph_builder.add_conditional_edges(
        "human_approval",
        check_approval_outcome,
    )

    # LangGraph API passes a config dict as the first argument, so we need to handle that.
    # If checkpointer is None or not a valid saver, default to InMemorySaver for local dev.
    if checkpointer is None or isinstance(checkpointer, dict):
        from langgraph.checkpoint.memory import InMemorySaver
        checkpointer = InMemorySaver()

    return graph_builder.compile(checkpointer=checkpointer, interrupt_before=["human_approval"])
