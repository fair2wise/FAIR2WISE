"""LangGraph agent/tool loop used by the term-extractor orchestrator."""
from collections.abc import Sequence
import logging
from typing import Any

from langgraph.graph import END, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

logger = logging.getLogger(__name__)


def build_graph(
    llm: Any = None,  # noqa: ANN401  # Any: LangChain chat model
    tools: Sequence[Any] | None = None,  # Any: LangChain tools
) -> CompiledStateGraph[MessagesState, None, MessagesState, MessagesState]:
    """Compile a LangGraph that alternates an LLM agent node with a tool node.

    The agent runs first; while its reply requests tool calls the tools run and
    control returns to the agent, otherwise the graph ends.

    Args:
        llm: Chat model whose ``invoke`` is called on the message state; required
            in practice, since the agent node calls it unconditionally.
        tools: Tools available to the tool node; none when ``None``.

    Returns:
        The compiled graph.
    """
    tool_node = ToolNode(tools or [])

    def agent_node(state: MessagesState) -> dict[str, list[Any]]:
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
