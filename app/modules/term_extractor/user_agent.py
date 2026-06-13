"""UserAgent that receives messages from monitored agents and drives the dashboard."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from academy.agent import action
from academy.agent import Agent
from academy.identifier import AgentId

from als_knowledge_agent.dashboard import Dashboard
from als_knowledge_agent.message import Log, Message, Registration, Stats, UserPrompt

logger = logging.getLogger(__name__)


class UserAgent(Agent):
    """Receives messages from MonitoredAgents and serves a live web dashboard."""

    def __init__(self, host: str = '0.0.0.0', port: int = 8000) -> None:

        super().__init__()
        print(f'Starting user agent on Port: {port}')
        self._dashboard = Dashboard(host=host, port=port)

    async def agent_on_startup(self) -> None:
        """Start the Flask dashboard on startup."""
        loop = asyncio.get_event_loop()

        def _shutdown_callback(agent_id: str) -> None:
            asyncio.run_coroutine_threadsafe(
                self._agent_manager.get_handle(
                    AgentId(uid=uuid.UUID(agent_id)),
                ).shutdown(),
                loop,
            )

        self._dashboard.set_shutdown_callback(_shutdown_callback)
        self._dashboard.start()
        print('Starting dashboard')

    @action
    async def message(self, sender: str, message: Message) -> None:
        """Route an incoming message to the appropriate dashboard handler.

        sender: Agent ID UUID string
        message: Message object
        """
        logger.info(
            f'Received message from {sender}: {message} ======================',
        )
        self._dashboard.agent_heartbeat(sender)
        if isinstance(message, Log):
            self._dashboard.push_log(sender, message)
        elif isinstance(message, Stats):
            self._dashboard.push_stats(sender, message)
        elif isinstance(message, Registration):
            self._dashboard.register_agent(sender, message)

    @action
    async def prompt_user(
        self,
        sender: str,
        user_prompt: UserPrompt,
    ) -> str:
        """Prompt the user for a response and block until the user selects one."""
        loop = asyncio.get_event_loop()
        prompt_id = self._dashboard.push_prompt(sender, user_prompt)
        return await loop.run_in_executor(
            None,
            self._dashboard.wait_for_response,
            prompt_id,
        )

    @action
    async def get_messages(self) -> dict[str, Any]:
        """Return a snapshot of the current dashboard state."""
        return self._dashboard._snapshot()