"""Deployment-wide judge-model setting for validation runs (owner O-U2, H3 B7).

The owner asked that "model judges should be able to [be] picked from a dropdown with
what's currently available (not hardcoded)", "in the settings panel in the UI". This module
holds that one setting and nothing else:

* :class:`JudgeModelStore` persists ``{"judge_model", "updated_by", "updated_at"}`` as JSON
  at ``<workdir>/validation/judge_model.json`` (:func:`judge_settings_path`).
* :class:`JudgeModelService` reads and changes it for a server-resolved
  :class:`~app.modules.contracts.context.RequestContext`. Both need ``settings.global``:
  on ALS only a verified admin can see or change it (validation runs are admin actions);
  on ``:local`` the local operator holds every capability (D34). A new value must be in the
  provider's *current* live listing (:mod:`app.modules.model_catalog`); when the listing
  cannot be fetched the change is refused rather than accepted unchecked. It must also be a
  free LBL-hosted model (:mod:`app.modules.model_policy`, owner decision 2026-11-09): the view
  offers only free ids and flags a saved paid id (``judge_model_allowed``), and a paid id is
  refused with ``denied``.

**Deployment-wide, not personal (T25D).** A judge decides validation verdicts that are gate
evidence for the whole deployment. A per-user judge would let two users produce reports
for the same revision under different judges, and would let a non-admin choose the judge of
an admin's validation run. So it is one value per deployment, admin-only on ALS, like
``extraction_mode`` (docs/migration/authentication/settings_fields.md).

**No application path reads a verdict (H3 B).** The application only stores the chosen id.
The validation runner (the binary evaluation suite, outside ``app/``) reads it; nothing in ``app/``
imports the evaluation suite or waits on a binary result.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import threading
from typing import Any, cast

from .contracts.context import RequestContext
from .contracts.errors import ContractError, ErrorCode
from .model_catalog import CachedModelCatalog
from .model_policy import load_policy
from .security.authorization import require

logger = logging.getLogger(__name__)

#: Capability needed to read or change the judge (admin on ALS, local operator on ``:local``).
JUDGE_CAPABILITY = "settings.global"
#: Settings scope recorded with the value (T25D classification).
JUDGE_SCOPE = "deployment"
MAX_MODEL_ID = 200


def judge_settings_path(workdir: Path) -> Path:
    """Where a deployment's judge setting lives under its workdir."""
    return Path(workdir) / "validation" / "judge_model.json"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class JudgeModelStore:
    """The persisted judge setting (one JSON file, atomic replace).

    Args:
        path: The JSON file (see :func:`judge_settings_path`).
        clock: UTC timestamp source.
    """

    def __init__(self, path: Path, *, clock: Callable[[], str] = _now) -> None:
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()

    def read(self) -> dict[str, Any]:
        """``{"judge_model", "updated_by", "updated_at", "scope"}``; ``judge_model`` is None when unset."""
        empty = {"judge_model": None, "updated_by": None, "updated_at": None, "scope": JUDGE_SCOPE}
        with self._lock:
            if not self.path.is_file():
                return empty
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("judge setting unreadable, treated as unset: %s", type(exc).__name__)
                return empty
        model = data.get("judge_model") if isinstance(data, dict) else None
        if not isinstance(model, str) or not model.strip():
            model = None
        return {"judge_model": model, "updated_by": data.get("updated_by") if model else None,
                "updated_at": data.get("updated_at") if model else None, "scope": JUDGE_SCOPE}

    def write(self, judge_model: str | None, *, updated_by: str) -> dict[str, Any]:
        """Persist ``judge_model`` (``None`` clears it) and return the stored record."""
        record = {"judge_model": judge_model, "updated_by": updated_by, "updated_at": self._clock(),
                  "scope": JUDGE_SCOPE}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            tmp.replace(self.path)
        return dict(record)


class JudgeModelService:
    """Read and change the judge for a request context.

    Args:
        store: The persisted setting.
        catalog: Live model listing (cached).
    """

    def __init__(self, store: JudgeModelStore, catalog: CachedModelCatalog | None = None) -> None:
        self.store = store
        self.catalog = catalog or CachedModelCatalog()

    def view(self, ctx: RequestContext, *, refresh: bool = False) -> dict[str, Any]:
        """The current judge plus the models the provider offers right now."""
        require(ctx, JUDGE_CAPABILITY)
        listing = self.catalog.get(refresh=refresh)
        policy = load_policy()
        current = self.store.read()
        model = current["judge_model"]
        return {
            **current,
            "judge_model_available": None if not listing.available or model is None else model in listing.models,
            # Free-model policy (owner decision 2026-11-09): only free ids are offered; a saved paid id is flagged.
            "judge_model_allowed": None if model is None else policy.is_allowed(model, listing),
            "available_models": policy.filter(listing.models, listing),
            "models_source": listing.source,
            "models_listed_at": listing.listed_at,
            "models_error": listing.error,
            "models_error_code": listing.error_code,
            "backend": listing.backend,
            "model_policy": policy.describe(),
        }

    def update(self, ctx: RequestContext, judge_model: str | None) -> dict[str, Any]:
        """Set (or clear, with ``None``/empty) the judge; the model must be listed right now."""
        require(ctx, JUDGE_CAPABILITY)
        cleaned = (judge_model or "").strip() or None
        if cleaned is not None:
            if len(cleaned) > MAX_MODEL_ID:
                raise ContractError(ErrorCode.INVALID_INPUT, "judge model id is too long")
            # Free-model policy (owner decision 2026-11-09): a paid id is refused (DENIED) before the live check.
            load_policy().require(cleaned, context="judge model")
            listing = self.catalog.get(refresh=True)
            if not listing.available:
                raise ContractError(ErrorCode.UNAVAILABLE,
                                    "the provider model list is unavailable, so the judge cannot be checked: "
                                    + (listing.error or "unknown reason"))
            if cleaned not in listing.models:
                raise ContractError(ErrorCode.INVALID_INPUT,
                                    "judge model is not in the provider's current model list")
            load_policy().require(cleaned, context="judge model", listing=listing)
        self.store.write(cleaned, updated_by=ctx.principal.subject)
        logger.info("validation judge model set to %s by %s", cleaned, ctx.principal.subject)
        return self.view(ctx)


def read_judge_model(workdir: Path) -> str | None:
    """The stored judge id under ``workdir`` (no context: for the trusted validation CLI only)."""
    return cast(str | None, JudgeModelStore(judge_settings_path(workdir)).read()["judge_model"])


__all__ = ("JUDGE_CAPABILITY", "JUDGE_SCOPE", "JudgeModelService", "JudgeModelStore", "judge_settings_path",
           "read_judge_model")
