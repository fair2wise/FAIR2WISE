"""Free-model policy for CBORG (owner decision 2026-11-09, seventeenth set).

The owner: "we should only allow free models via CBORG at the moment. We get 50 free usage credits per
month (mileage varies per model) on CBORG, so I want to avoid using those." Read as a model policy: every
CBORG call path accepts only the free LBL-hosted model ids and refuses any other id with a typed error
*before* a request is made; model pickers show only free ids.

The allowlist is data, not code: ``free_cborg_models.json`` next to this module (shipped with the package)
or the file named by ``F2W_MODEL_POLICY_FILE``. The owner edits the JSON (``free_models``, ``aliases``,
``default_model``, with ``source`` and ``as_of`` recording where the list came from); nothing here needs
to change. A missing or unreadable file is an *empty* allowlist: every CBORG id is refused (fail closed).

Two spellings name one model: the CBORG listing endpoint returns bare ids (``cborg-chat``) while the chat
API also accepts the ``lbl/`` aliases (``lbl/cborg-chat``); :func:`canonical_model_id` maps both to the
``lbl/`` form. An id with another provider prefix (``openai/``, ``anthropic/``, ``google/``, ...) is never
LBL-hosted and never free.

The live listing refines the allowlist when it carries prices (:attr:`ModelListing.prices`): a model is
allowed only if it is in the allowlist **and**, when the listing has a price for it, that price is 0.
Whether the ``/models`` payload carries a price at all is owner-gated (it did not on 2026-11-06), so the
allowlist alone decides when no price is known.

Refusals are :class:`ModelPolicyError` (``ErrorCode.DENIED``): the message names the id and the policy
and never contains a key. Ollama and other local backends are not CBORG and are not checked here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Any

from .contracts.errors import ContractError, ErrorCode

if TYPE_CHECKING:
    from .model_catalog import ModelListing

logger = logging.getLogger(__name__)

#: Environment variable naming another policy file (a deployment's edited copy).
POLICY_FILE_VAR = "F2W_MODEL_POLICY_FILE"
#: The packaged allowlist (``app/modules/free_cborg_models.json``).
DEFAULT_POLICY_PATH = Path(__file__).with_name("free_cborg_models.json")
#: Prefix of the LBL-hosted ids; a bare id (no ``/``) is the same model as its ``lbl/`` form.
LBL_PREFIX = "lbl/"
#: Default model when the file names none (also the fallback of a saved setting that names a refused id).
FALLBACK_DEFAULT_MODEL = "lbl/cborg-chat"
#: Backends whose models are CBORG models (checked); every other backend is local and unaffected.
CBORG_BACKENDS = frozenset({"cborg", "cborg-openai"})


def canonical_model_id(model: str) -> str:
    """The comparison form of a CBORG id: stripped, lower-case, bare ids given the ``lbl/`` prefix.

    ``cborg-chat`` and ``lbl/cborg-chat`` are one model; ``openai/gpt-x`` keeps its provider prefix.
    """
    cleaned = str(model or "").strip().lower()
    if not cleaned:
        return ""
    return cleaned if "/" in cleaned else LBL_PREFIX + cleaned


class ModelPolicyError(ContractError):
    """A CBORG model id is refused by the free-model policy (``ErrorCode.DENIED``; no request was made).

    Args:
        model: The refused id (as given).
        context: The call path that refused it (``"chat client"``, ``"judge model"``, ...).
        policy: The policy that refused it (for ``source`` and ``as_of`` in the message).
        why: Short reason (``"not in the free-model allowlist"`` or ``"the live listing prices it above 0"``).
    """

    def __init__(self, model: str, *, context: str, policy: ModelPolicy, why: str) -> None:
        detail = f" ({policy.error})" if policy.error else ""
        super().__init__(
            ErrorCode.DENIED,
            f"CBORG model {model!r} is refused by the free-model policy ({context}): {why}. Only free LBL-hosted "
            f"models are allowed (owner decision 2026-11-09; {policy.source}, as of {policy.as_of}); the default "
            f"free model is {policy.default_model!r}{detail}.",
            retryable=False,
            reason="model_not_free",
        )
        self.model = model
        self.context = context


@dataclass(frozen=True)
class ModelPolicy:
    """The loaded allowlist.

    Attributes:
        allowed: Canonical ids (``lbl/...``) that are free.
        aliases: Canonical alias -> canonical target (``mistral-large-4`` -> ``lbl/cborg-coder-ultra``).
        default_model: The free model used when nothing (valid) is chosen.
        source: Where the list came from (from the file).
        as_of: Date the list was taken (from the file).
        path: The file the policy was read from (``None`` for an in-memory policy).
        free_models: The ids as written in the file, in file order (for pickers and messages).
        error: Why the file could not be used, when it could not (the allowlist is then empty).
    """

    allowed: frozenset[str] = frozenset()
    aliases: Mapping[str, str] = field(default_factory=dict)
    default_model: str = FALLBACK_DEFAULT_MODEL
    source: str = "no policy file"
    as_of: str = "unknown"
    path: str | None = None
    free_models: tuple[str, ...] = ()
    error: str | None = None

    def canonical(self, model: str) -> str:
        """Canonical id after alias resolution."""
        canon = canonical_model_id(model)
        return self.aliases.get(canon, canon)

    def is_allowed(self, model: str, listing: ModelListing | None = None) -> bool:
        """True when ``model`` is in the allowlist and the live listing (if any) does not price it above 0."""
        return self._why_refused(model, listing) is None

    def _why_refused(self, model: str, listing: ModelListing | None) -> str | None:
        canon = self.canonical(model)
        if not canon:
            return "the model id is empty"
        if canon not in self.allowed:
            return "it is not in the free-model allowlist"
        if listing is not None:
            price = listing.price_of(model)
            if price is None:
                price = listing.price_of(canon)
            if price is not None and price > 0:
                return f"the live CBORG listing prices it at {price:g} (not free)"
        return None

    def require(self, model: str, *, context: str, listing: ModelListing | None = None) -> str:
        """Return ``model`` stripped, or raise :class:`ModelPolicyError` before any request is made."""
        why = self._why_refused(model, listing)
        if why is not None:
            logger.warning("free-model policy refused model %r (%s): %s", model, context, why)
            raise ModelPolicyError(str(model or ""), context=context, policy=self, why=why)
        return str(model).strip()

    def filter(self, models: Iterable[str], listing: ModelListing | None = None) -> list[str]:
        """The ids of ``models`` (order kept, duplicates dropped) that the policy allows."""
        out: list[str] = []
        for model in models:
            if model not in out and self.is_allowed(model, listing):
                out.append(model)
        return out

    def fallback(self, model: str | None, listing: ModelListing | None = None) -> tuple[str, bool]:
        """``(model, False)`` when allowed, else ``(default_model, True)`` (a saved paid id falls back)."""
        if model and self.is_allowed(model, listing):
            return str(model).strip(), False
        return self.default_model, True

    def describe(self) -> dict[str, Any]:
        """JSON-ready summary for API payloads and the UI notice (never the whole file)."""
        return {"source": self.source, "as_of": self.as_of, "default_model": self.default_model,
                "free_models": list(self.free_models), "error": self.error}


def _parse_policy(data: Any, path: Path) -> ModelPolicy:  # noqa: ANN401  # Any: freshly parsed JSON
    if not isinstance(data, dict):
        raise ValueError("the policy file must hold a JSON object")
    raw_models = data.get("free_models")
    if not isinstance(raw_models, list) or not all(isinstance(m, str) and m.strip() for m in raw_models):
        raise ValueError("free_models must be a list of non-empty model ids")
    raw_aliases = data.get("aliases") or {}
    if not isinstance(raw_aliases, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in raw_aliases.items()):
        raise ValueError("aliases must map model ids to model ids")
    aliases = {canonical_model_id(k): canonical_model_id(v) for k, v in raw_aliases.items()}
    allowed = frozenset(canonical_model_id(m) for m in raw_models)
    default = str(data.get("default_model") or "").strip() or FALLBACK_DEFAULT_MODEL
    default_canon = aliases.get(canonical_model_id(default), canonical_model_id(default))
    if default_canon not in allowed:
        raise ValueError(f"default_model {default!r} is not in free_models")
    return ModelPolicy(
        allowed=allowed,
        aliases=aliases,
        default_model=default,
        source=str(data.get("source") or "unspecified source"),
        as_of=str(data.get("as_of") or "unknown"),
        path=str(path),
        free_models=tuple(dict.fromkeys(m.strip() for m in raw_models)),
    )


def policy_path() -> Path:
    """The policy file in use: ``F2W_MODEL_POLICY_FILE`` or the packaged default."""
    explicit = (os.environ.get(POLICY_FILE_VAR) or "").strip()
    return Path(explicit) if explicit else DEFAULT_POLICY_PATH


def read_policy(path: Path) -> ModelPolicy:
    """Read ``path`` without caching; an unreadable or invalid file is an empty (fail-closed) policy."""
    try:
        policy = _parse_policy(json.loads(Path(path).read_text(encoding="utf-8")), Path(path))
    except (OSError, ValueError) as exc:
        reason = f"policy file {path} unusable: {type(exc).__name__}: {exc}"
        logger.error("free-model policy: %s; every CBORG model is refused", reason)
        return ModelPolicy(path=str(path), error=reason)
    return policy


_lock = threading.Lock()
_cache: dict[str, tuple[int, ModelPolicy]] = {}  # path -> (mtime_ns, policy)


def load_policy(path: Path | None = None) -> ModelPolicy:
    """The policy at ``path`` (default :func:`policy_path`), re-read whenever the file changes."""
    target = Path(path) if path is not None else policy_path()
    key = str(target)
    try:
        stamp = target.stat().st_mtime_ns
    except OSError:
        stamp = -1
    with _lock:
        cached = _cache.get(key)
        if cached is not None and cached[0] == stamp:
            return cached[1]
    policy = read_policy(target)
    with _lock:
        _cache[key] = (stamp, policy)
    return policy


#: Opt-in switch for the chat helper agents' model calls (owner decision 2026-11-09: avoid spending even the
#: free CBORG credits). Unset or off, the orchestrator router and the evidence debater take their deterministic
#: path and make no model call; ``1``/``true``/``yes``/``on`` enables the admitted, budgeted calls.
HELPER_LLM_FLAG = "F2W_HELPER_AGENT_LLM"


def helper_model_calls_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """True when :data:`HELPER_LLM_FLAG` opts the routing/debate helper agents into model calls (default off)."""
    env = os.environ if environ is None else environ
    return (env.get(HELPER_LLM_FLAG) or "").strip().lower() in {"1", "true", "yes", "on"}


def is_cborg_backend(backend: str | None) -> bool:
    """True for the backends whose models the policy governs (``cborg``, ``cborg-openai``)."""
    return (backend or "").strip().lower() in CBORG_BACKENDS


def require_free_cborg_model(model: str, *, context: str, listing: ModelListing | None = None) -> str:
    """Refuse ``model`` with :class:`ModelPolicyError` unless the policy allows it; return it stripped."""
    return load_policy().require(model, context=context, listing=listing)


def is_free_cborg_model(model: str, listing: ModelListing | None = None) -> bool:
    """True when the policy allows ``model``."""
    return load_policy().is_allowed(model, listing)


def free_cborg_models(listing: ModelListing | None = None) -> list[str]:
    """The ids a picker may offer: the live listing filtered by the policy, or the allowlist as written.

    A live listing (``listing.available``) is the owner's "what is currently available (not hardcoded)";
    only its free ids are returned. Without one the allowlist itself is returned, so a picker is never empty
    for the wrong reason (an unreadable policy file does leave it empty: fail closed).
    """
    policy = load_policy()
    if listing is not None and listing.available:
        return policy.filter(listing.models, listing)
    return policy.filter(policy.free_models)


def fallback_free_model(model: str | None, listing: ModelListing | None = None) -> tuple[str, bool]:
    """``(model, False)`` when allowed, else ``(the policy's default free model, True)``."""
    return load_policy().fallback(model, listing)


def default_free_model() -> str:
    """The policy's default free model."""
    return load_policy().default_model


__all__ = (
    "CBORG_BACKENDS",
    "DEFAULT_POLICY_PATH",
    "FALLBACK_DEFAULT_MODEL",
    "HELPER_LLM_FLAG",
    "LBL_PREFIX",
    "POLICY_FILE_VAR",
    "ModelPolicy",
    "ModelPolicyError",
    "canonical_model_id",
    "default_free_model",
    "fallback_free_model",
    "free_cborg_models",
    "helper_model_calls_enabled",
    "is_cborg_backend",
    "is_free_cborg_model",
    "load_policy",
    "policy_path",
    "read_policy",
    "require_free_cborg_model",
)
