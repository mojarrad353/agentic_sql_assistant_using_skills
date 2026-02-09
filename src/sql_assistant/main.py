import sys
import uuid
from typing import Dict, Any

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from .agent import create_agent_graph
from .config import get_settings

def print_message_verbose(message):
    """Prints a message in a verbose format matching the user request."""
    if isinstance(message, HumanMessage):
        print("\n" + "=" * 32 + " Human Message " + "=" * 33 + "\n")
        print(message.content)
    elif isinstance(message, AIMessage):
        if message.tool_calls:
            print("\n" + "=" * 34 + " AI Action " + "=" * 35)
            for tool_call in message.tool_calls:
                # Just show which tool is being used
                print(f"Using Tool: {tool_call['name']}")
                # Optional: Show args if useful, but maybe kept minimal?
                # User asked for "which tool it uses". 
                # Let's keep args concise or hidden if verbose.
                # query args are important though.
                if 'query' in tool_call['args']:
                    print(f"  Query: {tool_call['args']['query']}") 
                elif 'skill_name' in tool_call['args']:
                    print(f"  Skill: {tool_call['args']['skill_name']}")
        
        if message.content:
            print("\n" + "=" * 34 + " Agent Response " + "=" * 34)
            print(message.content)

    elif isinstance(message, ToolMessage):
        # Hide massive tool outputs (like schemas)
        # print("\n" + "=" * 33 + " Tool Message " + "=" * 33)
        # print(f"Name: {message.name}\n")
        # print(message.content)
        pass  # Skip printing raw tool outputs to keep UI clean

# ... previous imports ...
import re
import psycopg2

def run_interactive_session():
    """Runs an interactive session with the agent."""
    print("Initializing SQL Assistant...")
    settings = get_settings()
    
    # Ensure environment variables are set for LangChain/LangSmith to pick them up
    if settings.LANGSMITH_TRACING:
        import os
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_PROJECT"] = settings.LANGSMITH_PROJECT
        if settings.LANGSMITH_API_KEY:
            os.environ["LANGSMITH_API_KEY"] = settings.LANGSMITH_API_KEY
        
        print(f"LangSmith Tracing enabled. Project: {settings.LANGSMITH_PROJECT}")

    memory = InMemorySaver()
    graph = create_agent_graph(checkpointer=memory)
    
    print("\nSQL Assistant Ready. Type 'exit' or 'quit' to stop.")
    
    # We use a thread ID to persist state across graph runs
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    
    while True:
        try:
            # Check if we are waiting for approval (interrupted state)
            snapshot = graph.get_state(config)
            if snapshot.next and "human_approval" in snapshot.next:
                print("\n" + "*" * 30 + " Approval Required " + "*" * 30)
                print("The agent has generated a response/query.")
                
                # Get the last message content
                last_msg = snapshot.values["messages"][-1]
                content = last_msg.content
                
                decision = input("Do you approve this response? (y/n): ").strip().lower()
                
                if decision == "y":
                    print("Approved. Executing locally...")
                    
                    # 1. Extract SQL from content
                    # Look for ```sql ... ``` or just ``` ... ```
                    match = re.search(r"```(?:sql)?(.*?)```", content, re.DOTALL)
                    if match:
                        query = match.group(1).strip()
                    else:
                        # Fallback: assume the whole message might be a query if no blocks
                        # Check if it looks like a query before warning
                        candidate = content.strip()
                        upper_candidate = candidate.upper()
                        if upper_candidate.startswith("SELECT") or upper_candidate.startswith("WITH"):
                             # Likely a raw query, proceed silently
                             query = candidate
                        else:
                             print("Warning: No SQL code block found. Trying to execute entire message...")
                             query = candidate

                    # 2. Execute locally (No Agent/LLM call)
                    try:
                        settings = get_settings()
                        conn = psycopg2.connect(
                            host=settings.POSTGRES_HOST,
                            database=settings.POSTGRES_DB,
                            user=settings.POSTGRES_USER,
                            password=settings.POSTGRES_PASSWORD,
                            port=settings.POSTGRES_PORT
                        )
                        cursor = conn.cursor()
                        cursor.execute(query)
                        
                        if cursor.description:
                            columns = [col.name for col in cursor.description]
                            results = cursor.fetchall()
                        else:
                            columns = []
                            results = []
                            
                        conn.commit()
                        conn.close()
                        
                        # 3. Format Output
                        if not results:
                            print("\n[Execution Result]: No results found.")
                        else:
                            col_widths = [len(col) for col in columns]
                            formatted_rows = []
                            for row in results:
                                str_row = [str(cell) for cell in row]
                                formatted_rows.append(str_row)
                                for i, cell_str in enumerate(str_row):
                                    col_widths[i] = max(col_widths[i], len(cell_str))

                            header = " | ".join(col.ljust(width) for col, width in zip(columns, col_widths))
                            separator = "-+-".join("-" * width for width in col_widths)
                            lines = ["", "=" * 33 + " Execution Result " + "=" * 33, header, separator]
                            for row in formatted_rows:
                                lines.append(" | ".join(cell.ljust(width) for cell, width in zip(row, col_widths)))
                            
                            print("\n".join(lines))
                            print("\n" + "=" * 84)

                        # IMPORTANT: We do NOT update the graph state with this result to save tokens.
                        # We just 'continue' loop, effectively treating this turn as done.
                        # However, we DO need to move the graph past the interrupt.
                        # We can inject a placeholder ToolMessage or just a dummy HumanMessage 
                        # saying "Execution done" so the graph is ready for next input?
                        # ACTUALLY: The graph is paused at 'human_approval'. 
                        # If we don't update state, it stays paused. 
                        # We must resume it.
                        # We can send a special signal or just a dummy "Done" message to get it to END.
                        
                        # Simplest way: Inject a "HumanMessage" saying "Done", and ensuring our graph logic handles it.
                        # Or, since we want to be pure local, we can just leave the graph paused?
                        # No, if we leave it paused, next time we come in `snapshot.next` will still be human_approval.
                        # So we MUST resume the graph to clear the interrupt.
                        
                        # We will send a customized message that the agent (or graph logic) ignores/ends on.
                        # Update graph state with a dummy message to clear the interrupt and move to END.
                        graph.update_state(config, {"messages": [HumanMessage(content="<SYSTEM: Execution Completed Locally>")]})
                        # IMPORTANT: Resume graph execution to process this message and reach END
                        list(graph.stream(None, config, stream_mode="values"))
                        
                        # Process the event to reach END (our custom routing logic needs to handle this)
                        # Actually, our `check_approval_outcome` routes HumanMessage back to Agent...
                        # We need to update `check_approval_outcome` in agent.py to handle this special flag if strictly NO LLM is desired.
                        
                        # Alternative: Just let the user prompt "next query".
                        # But wait, if I type "next query", it will be treated as feedback to the paused state.
                        # So we DO need to close the loop.
                        
                        # Let's fix `agent.py` shortly to handle "<SYSTEM: ...>" to go to END.
                        
                    except Exception as e:
                        print(f"\nError executing query: {e}")

                    continue
                else:
                    # ... rejection logic ...
                    feedback = input("Please provide feedback/correction: ").strip()
                    print("Sending feedback to agent...")
                    graph.update_state(config, {"messages": [HumanMessage(content=f"Rejected. Feedback: {feedback}")]})
                    # ... streaming ...
                    events = graph.stream(None, config, stream_mode="values")
                    for event in events:
                         if "messages" in event:
                             msg = event["messages"][-1]
                             if isinstance(msg, AIMessage):
                                 print_message_verbose(msg)
                    continue

            print("\n" + "=" * 80) # Separator for new turn
            user_input = input("User: ")
            if user_input.lower() in ["exit", "quit"]:
                print("Goodbye!")
                break
            
            # Send the user message to the graph
            events = graph.stream({"messages": [HumanMessage(content=user_input)]}, config, stream_mode="values")
            
            for event in events:
                if "messages" in event:
                    msg = event["messages"][-1]
                    if isinstance(msg, HumanMessage) and msg.content == user_input:
                        continue
                    print_message_verbose(msg)
            
        except KeyboardInterrupt:
            print("\nGoodbye!")
            break
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    run_interactive_session()
