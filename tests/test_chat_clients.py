"""
Unit tests for OllamaClient and CBorgClient chat methods.

Covers: success paths, HTTP errors, connection errors, timeout errors,
        authentication errors, empty/missing content.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules import kg_rag_api


# ─────────────────────────────────────────────────────────────────────────────
# OllamaClient
# ─────────────────────────────────────────────────────────────────────────────


class TestOllamaClient:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_chat_returns_content(self):
        async def fake_post(self_sess, url, json=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = AsyncMock(return_value={
                "message": {"role": "assistant", "content": "hello"}
            })
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)
            return mock_resp

        with patch("aiohttp.ClientSession.post", fake_post):
            client = kg_rag_api.OllamaClient(model="test")
            result = self._run(client.chat([{"role": "user", "content": "hi"}]))
        assert result == "hello"

    def test_chat_returns_empty_on_missing_content(self):
        async def fake_post(self_sess, url, json=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = AsyncMock(return_value={"message": {}})
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)
            return mock_resp

        with patch("aiohttp.ClientSession.post", fake_post):
            client = kg_rag_api.OllamaClient(model="test")
            result = self._run(client.chat([{"role": "user", "content": "hi"}]))
        assert result == ""

    def test_chat_returns_empty_on_no_message_key(self):
        async def fake_post(self_sess, url, json=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json = AsyncMock(return_value={})
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)
            return mock_resp

        with patch("aiohttp.ClientSession.post", fake_post):
            client = kg_rag_api.OllamaClient(model="test")
            result = self._run(client.chat([{"role": "user", "content": "hi"}]))
        assert result == ""

    def test_chat_raises_on_http_error(self):
        import aiohttp

        async def fake_post(self_sess, url, json=None, **kwargs):
            mock_resp = AsyncMock()
            mock_resp.raise_for_status = MagicMock(
                side_effect=aiohttp.ClientResponseError(
                    request_info=MagicMock(), history=(), status=500
                )
            )
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=False)
            return mock_resp

        with patch("aiohttp.ClientSession.post", fake_post):
            client = kg_rag_api.OllamaClient(model="test")
            with pytest.raises(Exception):
                self._run(client.chat([{"role": "user", "content": "hi"}]))


# ─────────────────────────────────────────────────────────────────────────────
# CBorgClient
# ─────────────────────────────────────────────────────────────────────────────


class TestCBorgClient:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_chat_returns_content(self):
        mock_choice = MagicMock()
        mock_choice.message.content = "response"
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        client = kg_rag_api.CBorgClient(model="test", api_key="fake-key")
        client.client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = self._run(client.chat([{"role": "user", "content": "hi"}]))
        assert result == "response"

    def test_chat_returns_last_choice(self):
        choices = []
        for text in ["first", "second", "last"]:
            c = MagicMock()
            c.message.content = text
            choices.append(c)
        mock_resp = MagicMock()
        mock_resp.choices = choices

        client = kg_rag_api.CBorgClient(model="test", api_key="fake-key")
        client.client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = self._run(client.chat([{"role": "user", "content": "hi"}]))
        assert result == "last"

    def test_chat_returns_empty_on_none_content(self):
        mock_choice = MagicMock()
        mock_choice.message.content = None
        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]

        client = kg_rag_api.CBorgClient(model="test", api_key="fake-key")
        client.client.chat.completions.create = AsyncMock(return_value=mock_resp)

        result = self._run(client.chat([{"role": "user", "content": "hi"}]))
        assert result == ""

    def test_chat_raises_on_connection_error(self):
        import openai

        client = kg_rag_api.CBorgClient(model="test", api_key="fake-key")
        client.client.chat.completions.create = AsyncMock(
            side_effect=openai.APIConnectionError(request=MagicMock())
        )

        with pytest.raises(RuntimeError, match="CBORG connection failed"):
            self._run(client.chat([{"role": "user", "content": "hi"}]))

    def test_chat_raises_on_timeout(self):
        import openai

        client = kg_rag_api.CBorgClient(model="test", api_key="fake-key")
        client.client.chat.completions.create = AsyncMock(
            side_effect=openai.APITimeoutError(request=MagicMock())
        )

        with pytest.raises(RuntimeError, match="timed out"):
            self._run(client.chat([{"role": "user", "content": "hi"}]))

    def test_chat_raises_on_auth_error(self):
        import openai

        client = kg_rag_api.CBorgClient(model="test", api_key="fake-key")
        client.client.chat.completions.create = AsyncMock(
            side_effect=openai.AuthenticationError(
                message="bad key", response=MagicMock(), body={}
            )
        )

        with pytest.raises(RuntimeError, match="authentication failed"):
            self._run(client.chat([{"role": "user", "content": "hi"}]))
