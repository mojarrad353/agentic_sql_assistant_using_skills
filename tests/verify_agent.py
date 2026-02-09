import sys
import os

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from src.sql_assistant.agent import create_agent_graph
from langchain_core.messages import HumanMessage
import uuid

def test_agent_execution():
    print("Initializing Agent Graph...")
    graph = create_agent_graph()
    
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    
    question = "Find 3 high-value orders from last month."
    print(f"\nUser Query: {question}")
    
    input_state = {"messages": [HumanMessage(content=question)]}
    
    # Run the graph until it pauses (human approval) or ends
    # Since we want to check if it generates a query, we'll iterate
    
    events = graph.stream(input_state, config, stream_mode="values")
    
    generated_query = None
    
    print("\n--- Agent Execution ---")
    for event in events:
        if "messages" in event:
            msg = event["messages"][-1]
            print(f"[{type(msg).__name__}]: {msg.content[:100]}...")
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                 for tc in msg.tool_calls:
                     print(f"  Tool Call: {tc['name']}")
    
    # Check state to see if we are at approval
    snapshot = graph.get_state(config)
    if snapshot.next and "human_approval" in snapshot.next:
        last_msg = snapshot.values["messages"][-1]
        print(f"\nAgent proposed response: {last_msg.content}")
        
        if "SELECT" in last_msg.content.upper():
            print("\nSUCCESS: Agent generated a SQL query.")
        else:
             print("\nWARNING: Agent did not generate a SQL query directly.")

if __name__ == "__main__":
    test_agent_execution()
