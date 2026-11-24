"""Draft a LinkML schema with the model of the user's choice, validate it, and repair it a bounded number of times.

The call goes through :func:`app.modules.term_extractor.clients.make_chat_client` (the same clients as extraction) under
the shared provider admission and a hard deadline. A CBORG model must pass the free-model policy before any client is
built. The reply is untrusted text: capped at 64 KiB, markdown fences stripped, then checked with
:func:`validate_schema_text`. When the draft is invalid the problems are sent back, at most twice; every attempt (text and
problems) is returned and nothing is ever replaced silently. Failures reach the caller as typed errors that carry
plain words only (never a URL, key or provider body).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
import os
import re
from typing import Any

import requests

from . import schema_prompt as SP
from ..contracts.errors import ContractError, ErrorCode
from ..model_policy import default_free_model, require_free_cborg_model
from .extraction import require_backend
from .user_schema_validation import Report, validate_schema_text

logger = logging.getLogger(__name__)

#: Label of the model call (the closed set ``LLM_LABELS`` of ``app.modules.tracing.runtime``).
LABEL = "schema-generate"
#: Largest accepted model reply, in bytes.
MAX_RESPONSE_BYTES = 64 * 1024
#: Repair rounds after the first draft.
MAX_REPAIRS = 2
#: Seconds one model call may take (the hard deadline), the reply cap in tokens, and Ollama's context window.
CALL_TIMEOUT = 180
CALL_MAX_TOKENS = 6_000
OLLAMA_NUM_CTX = 8_192
SYSTEM = "You write LinkML schemas. Reply with the YAML document only."

_FENCE = re.compile(r"```[A-Za-z0-9_-]*[ \t]*\n(.*?)(?:\n```|\Z)", re.S)

#: ``factory(backend=, model=, ollama_url=, cborg_base=, cborg_api_key=) -> chat client`` (tests inject fakes).
ClientFactory = Callable[..., Any]


def strip_fences(text: str) -> str:
    """The first fenced block of ``text`` when there is one, else ``text``; surrounding blank lines removed."""
    match = _FENCE.search(text)
    return (match.group(1) if match else text).strip("\n").rstrip() + "\n"


def _default_factory(**kwargs: Any) -> Any:  # noqa: ANN401  # Any: the chat client
    from ..term_extractor import clients

    return clients.make_chat_client(**kwargs)


class SchemaGenerator:
    """Builds the prompt, makes the bounded model call and runs the validate-and-repair loop.

    Args:
        env: Startup environment (backend selection; never read from a dotenv file).
        client_factory: Chat-client builder (default :func:`make_chat_client`; tests inject a fake).
        schema_paths: Shipped schema files the condensed view is built from (default: the offered extraction schemas).
        deadline: ``(call, seconds) -> result`` raising ``TimeoutError`` (default ``cborg_http.call_with_deadline``).
    """

    def __init__(self, env: Mapping[str, str] | None = None, *, client_factory: ClientFactory | None = None,
                 schema_paths: tuple[str, ...] | None = None,
                 deadline: Callable[[Callable[[], str], float], str] | None = None) -> None:
        self.env: Mapping[str, str] = dict(os.environ if env is None else env)
        self.client_factory = client_factory or _default_factory
        self._schema_paths = schema_paths
        self._deadline = deadline
        self._view_cache: dict[int, str] = {}

    # ------------------------------------------------------------------ prompt
    def view(self, budget: int) -> str:
        """The condensed shipped-schema text for ``budget`` characters (cached; deterministic)."""
        if budget not in self._view_cache:
            paths = self._schema_paths
            if paths is None:
                from .schemas import list_schemas

                paths = tuple(c["path"] for c in list_schemas() if c.get("origin") == "shipped")
            classes, relations = SP.shipped_view_sources(paths)
            self._view_cache[budget] = SP.condensed_view(classes, relations, budget)
        return self._view_cache[budget]

    def first_prompt(self, description: str, backend: str) -> str:
        """The first-round prompt for ``backend`` (budget: 6,000 characters for Ollama, 12,000 for CBORG)."""
        budget = SP.BUDGET_OLLAMA if backend == "ollama" else SP.BUDGET_CBORG
        return SP.build_prompt(description, view=self.view(budget))

    # ------------------------------------------------------------------ the call
    def model_for(self, backend: dict[str, str], model: str | None) -> str:
        """The model to call: a CBORG model must be free (``DENIED`` otherwise; the policy default when omitted); Ollama needs one."""
        chosen = (model or "").strip()
        if backend["name"] == "cborg":
            return require_free_cborg_model(chosen or default_free_model(), context="schema generation model")
        if not chosen:
            raise ContractError(ErrorCode.INVALID_INPUT, "choose a model for the Ollama backend", retryable=False)
        return chosen

    def complete(self, backend: dict[str, str], model: str, prompt: str, *, timeout: int = CALL_TIMEOUT,
                 max_tokens: int = CALL_MAX_TOKENS, system: str = SYSTEM) -> str:
        """One model call under the provider admission and a hard deadline of ``timeout`` seconds (label ``schema-generate``).

        Failures are typed with plain words only (see the module docstring); the schema preview shares this path.
        """
        from ..cborg_http import call_with_deadline
        from ..llm_admission import ProviderAdmission

        run = self._deadline or call_with_deadline
        kwargs: dict[str, Any] = {"backend": backend["name"], "model": model}
        for key in ("cborg_base", "cborg_api_key", "ollama_url"):
            if backend.get(key):
                kwargs[key] = backend[key]
        extra: dict[str, Any] = {"num_ctx": OLLAMA_NUM_CTX} if backend["name"] == "ollama" else {}

        def call() -> str:
            client = self.client_factory(**kwargs)
            return str(client.chat(prompt, temperature=0.0, timeout=timeout, trace_label=LABEL, system=system,
                                   max_tokens=max_tokens, **extra))

        try:
            with ProviderAdmission().slot("schema_generate"):
                return run(call, float(timeout))
        except ContractError:  # incl. the free-model refusal and CBORG's address refusal: already plain words
            raise
        except TimeoutError:
            raise ContractError(ErrorCode.UNAVAILABLE, f"the model did not answer within {timeout} seconds",
                                retryable=True, reason="model_timeout") from None
        except (requests.ConnectionError, requests.Timeout):
            what = "Ollama" if backend["name"] == "ollama" else "the model server"
            raise ContractError(ErrorCode.UNAVAILABLE, f"{what} is not reachable", retryable=True,
                                reason="ollama_unreachable" if backend["name"] == "ollama" else "model_unreachable") from None
        except Exception as exc:  # noqa: BLE001 - provider and transport errors can carry URLs, keys or bodies: only the type is kept
            logger.warning("schema generation call failed: %s", type(exc).__name__)
            raise ContractError(ErrorCode.UNAVAILABLE, f"the model call failed ({type(exc).__name__})", retryable=True,
                                reason="model_call_failed") from None

    @staticmethod
    def _clean(reply: str) -> str:
        if len(reply.encode("utf-8", errors="replace")) > MAX_RESPONSE_BYTES:
            raise ContractError(ErrorCode.UNAVAILABLE, f"the model's answer was larger than {MAX_RESPONSE_BYTES // 1024} KiB "
                                "and was discarded", retryable=True, reason="response_too_large")
        return strip_fences(reply)

    # ------------------------------------------------------------------ the loop
    def generate(self, description: str, *, backend: str | None = None, model: str | None = None) -> dict[str, Any]:
        """Draft, validate and repair; every attempt is returned.

        Returns:
            ``{ok, text, report, attempts: [{round, text, report}], repairs, model, backend, stopped}`` where ``text`` and
            ``report`` are the last attempt's, ``ok`` is its validity and ``stopped`` is ``None`` or the reason a repair
            round could not run.

        Raises:
            ContractError: ``INVALID_INPUT`` for a bad description, ``DENIED`` (``model_not_free``) for a paid CBORG model,
                ``UNSUPPORTED_CAPABILITY`` when the backend is not configured, ``UNAVAILABLE`` when the model cannot be
                reached or answers too much (before any draft exists).
        """
        if not isinstance(description, str) or not description.strip():
            raise ContractError(ErrorCode.INVALID_INPUT, "describe the domain first", retryable=False)
        if len(description) > SP.MAX_DESCRIPTION:
            raise ContractError(ErrorCode.INVALID_INPUT, f"the description is longer than {SP.MAX_DESCRIPTION} characters",
                                retryable=False)
        chosen_backend = require_backend(self.env, backend, purpose="schema generation")
        chosen_model = self.model_for(chosen_backend, model)
        attempts: list[dict[str, Any]] = []
        prompt = self.first_prompt(description, chosen_backend["name"])
        stopped: str | None = None
        for round_no in range(MAX_REPAIRS + 1):
            try:
                text = self._clean(self.complete(chosen_backend, chosen_model, prompt))
            except ContractError as exc:
                if not attempts:
                    raise
                stopped = str(exc)  # a repair round failed: keep what was already shown
                break
            report: Report = validate_schema_text(text)
            attempts.append({"round": round_no, "text": text, "report": report.as_dict()})
            if report.ok or round_no == MAX_REPAIRS:
                break
            prompt = SP.repair_prompt(description, text, report.problems)
        last = attempts[-1]
        return {"ok": bool(last["report"]["ok"]), "text": last["text"], "report": last["report"], "attempts": attempts,
                "repairs": len(attempts) - 1, "model": chosen_model, "backend": chosen_backend["name"], "stopped": stopped}


__all__ = ("CALL_TIMEOUT", "LABEL", "MAX_REPAIRS", "MAX_RESPONSE_BYTES", "SchemaGenerator", "strip_fences")
