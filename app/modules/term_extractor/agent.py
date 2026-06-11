from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from .tools import TOOLS


def agent_node(state: MessagesState) -> dict:
    # TODO: llm.invoke(state["messages"])
    pass


def should_continue(state: MessagesState) -> str:
    last = state["messages"][-1]
    if hasattr(last, "tool_calls") and last.tool_calls:
        return "tools"
    return END


def build_graph():
    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(TOOLS))
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()
