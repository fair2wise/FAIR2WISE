"""MonitoredAgent that sends messages to a UserAgent via its handle."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import queue as _queue
import socket
from typing import Any

import academy.exception
import psutil
import requests
from academy.agent import action
from academy.agent import Agent
from academy.handle import Handle

from .message import Log, Message, Registration, Stats, UserPrompt

_FORWARDED_LOGGER_SKIP_PREFIXES = (
    # 'academy',
    'asyncio',
    'globus_compute_sdk',
    'globus_sdk',
    'pika',
)


class _UserAgentLogHandler(logging.Handler):
    """Puts formatted log records onto a SimpleQueue; no asyncio touched here."""

    def __init__(self, buf: _queue.SimpleQueue[tuple[str, str]]) -> None:
        super().__init__()
        self._buf = buf

    def emit(self, record: logging.LogRecord) -> None:
        """Push log records in a queue for async processing."""
        # Skip agent transport internals to prevent feedback loops in the monitor.
        if record.name.startswith(_FORWARDED_LOGGER_SKIP_PREFIXES):
            return
        self._buf.put(
            (record.levelname, self.format(record)),
        )  # SimpleQueue.put() never blocks


class MonitoredAgent(Agent):
    """Agent that reports messages to a UserAgent via a handle."""

    def __init__(
        self,
        user_agent_handle: Handle[UserAgent],
        agent_name: str | None = None,
    ) -> None:
        """Initialize with a handle to the UserAgent."""
        super().__init__()
        self.agent_name = agent_name or type(self).__name__
        self.user_agent = user_agent_handle

    async def agent_on_startup(self) -> None:
        """Initiate log handlers for communication with UserAgent."""
        self._log_buf: _queue.SimpleQueue[tuple[str, str]] = _queue.SimpleQueue()
        self._agent_uid_str = str(self.agent_id.uid)
        self._log_handler = _UserAgentLogHandler(self._log_buf)
        logging.getLogger().addHandler(self._log_handler)
        logging.getLogger().setLevel(logging.INFO)
        self._drain_task: asyncio.Task[None] = asyncio.create_task(
            self._drain_logs(),
        )
        self._stats_task: asyncio.Task[None] = asyncio.create_task(
            self._report_stats(),
        )
        await self.agent_registration()

    async def _drain_logs(self) -> None:
        """Single long-lived task: drains the log queue and forwards messages."""
        try:
            while True:
                try:
                    loglevel, msg = self._log_buf.get(block=False)
                except _queue.Empty:
                    await asyncio.sleep(0.05)
                    continue
                try:
                    await self.log(msg, level=loglevel)
                except academy.exception.AgentTerminatedError:
                    break
                except Exception:
                    # If the send to the user agent fails,
                    # this loop should exit early
                    raise

        except asyncio.CancelledError:
            pass  # intentional cancellation on shutdown

    async def agent_on_shutdown(self) -> None:
        """Cancel the drainer and remove the log handler."""
        print('Agent on shutdown... closing log handler hooks')
        self._drain_task.cancel()
        self._stats_task.cancel()
        logging.getLogger().removeHandler(self._log_handler)

    async def _send_message(self, message: Message) -> None:
        """Send a message to the UserAgent."""
        print(f'Sending message to UserAgent {message}')
        await self.user_agent.message(self._agent_uid_str, message)

    @action
    async def log(self, message: str, level: str = 'INFO') -> None:
        """Action to report a log message to the UserAgent."""
        print(f'Monitored Agent : message={message} level={level}')
        print(f'Agent id : {self._agent_uid_str}')
        await self._send_message(
            Log(
                agent_id=self._agent_uid_str,
                agent_name=self.agent_name,
                message=message,
                level=level,
            ),
        )

    @action
    async def prompt_user_agent(
        self,
        prompt: str,
        responses: list[str],
    ) -> str:
        """Send user prompt to UserAgent.

        Note: This call will block until the user has supplied a response.
        """
        return await self.user_agent.prompt_user(
            self._agent_uid_str,
            UserPrompt(
                agent_id=str(self.agent_id.uid),
                prompt=prompt,
                responses=responses,
            ),
        )

    async def _report_stats(self, report_period_s: int = 30) -> None:
        """Gather current process stats and push them to the UserAgent dashboard."""
        try:
            while True:
                try:
                    await self._send_message(await self.gather_stats())
                except academy.exception.AgentTerminatedError:
                    break
                except Exception:
                    # If the send to the user agent fails,
                    # this loop should exit early
                    raise
                await asyncio.sleep(report_period_s)
        except asyncio.CancelledError:
            pass  # intentional cancellation on shutdown

    async def get_geolocation(self) -> dict[str, Any]:
        """Fetch geolocation for this machine from ipinfo.io."""
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: requests.get('https://ipinfo.io/json', timeout=6),
            )
            resp.raise_for_status()
            data = resp.json()
            return data
        except Exception:
            return {}

    async def agent_registration(self) -> None:
        """Send registration message with hardware info and geolocation."""
        intro = Registration(
            agent_name=self.agent_name,
            agent_id=self._agent_uid_str,
            fqdn=socket.getfqdn(),
            cpu=platform.processor(),
            gpu='?',
            arch=platform.machine(),
            python_version=platform.python_version(),
            os=platform.system(),
            geolocation=await self.get_geolocation(),
        )
        await self._send_message(intro)

    async def gather_stats(self) -> Stats:
        """Gather CPU, memory, and GPU utilization for the current process."""
        proc = psutil.Process(os.getpid())
        mem = proc.memory_info()
        gpu: list[dict[str, Any]] = []

        try:
            import pynvml  # noqa: PLC0415

            pynvml.nvmlInit()
            for i in range(pynvml.nvmlDeviceGetCount()):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                gpu.append(
                    {
                        'index': i,
                        'utilization_percent': util.gpu,
                        'memory_used_mb': mem_info.used / 1024**2,
                        'memory_total_mb': mem_info.total / 1024**2,
                    },
                )
        except Exception:
            pass

        return Stats(
            agent_id=self._agent_uid_str,
            cpu_percent=proc.cpu_percent(interval=0.1),
            memory_rss_mb=mem.rss / 1024**2,
            memory_vms_mb=mem.vms / 1024**2,
            gpu=gpu,
        )