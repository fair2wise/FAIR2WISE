import logging

from langgraph.graph import END, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

logger = logging.getLogger(__name__)


def build_graph(llm=None, tools=None):
    tool_node = ToolNode(tools or [])

    def agent_node(state: MessagesState) -> dict:
        response = llm.invoke(state["messages"])
        tool_calls = getattr(response, "tool_calls", [])
        logger.debug("agent_node: %d tool call(s) requested", len(tool_calls))
        return {"messages": [response]}

    def should_continue(state: MessagesState) -> str:
        last = state["messages"][-1]
        if hasattr(last, "tool_calls") and last.tool_calls:
            return "tools"
        return END

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    compiled = graph.compile()
    logger.debug("Graph compiled with %d tool(s)", len(tools or []))
    return compiled
