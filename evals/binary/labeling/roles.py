"""Role separation between the labelling model, the judge and the model under test (H3 B4).

The owner's exception allows a CBORG model to label the pilot papers for now, on the
condition that "the labelling model must differ from the judge model and from the model
under test". :func:`check_roles` refuses any run where two of the three roles are the same
model id. The judge comes from ``--judge-model`` or, failing that, the deployment-wide
setting the settings panel saves (``<workdir>/validation/judge_model.json``, owner O-U2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Tuple


class RoleSeparationError(ValueError):
    """Two roles (labeller, judge, model under test) name the same model."""


def _norm(model: Optional[str]) -> Optional[str]:
    if model is None:
        return None
    cleaned = str(model).strip()
    return cleaned.casefold() or None


def resolve_judge_model(explicit: Optional[str], settings_workdir: Optional[Path]) -> Tuple[Optional[str], str]:
    """``(judge model, source)``: ``cli`` for ``--judge-model``, ``settings`` for the stored value, else ``none``."""
    if explicit and explicit.strip():
        return explicit.strip(), "cli"
    if settings_workdir is not None:
        from app.modules.validation_settings import read_judge_model

        stored = read_judge_model(Path(settings_workdir))
        if stored:
            return stored, "settings"
    return None, "none"


def check_roles(*, labeler: Optional[str] = None, judge: Optional[str] = None,
                model_under_test: Optional[str] = None, labelers: Iterable[Optional[str]] = ()) -> None:
    """Refuse when the labeller(s), the judge and the model under test are not pairwise different."""
    labs = {m for m in (_norm(x) for x in [labeler, *labelers]) if m}
    j, mut = _norm(judge), _norm(model_under_test)
    if j and j in labs:
        raise RoleSeparationError(f"the judge model {j!r} also labelled the pilot papers; choose another judge")
    if mut and mut in labs:
        raise RoleSeparationError(f"the model under test {mut!r} also labelled the pilot papers")
    if j and mut and j == mut:
        raise RoleSeparationError(f"the judge model {j!r} is the model under test; choose another judge")


__all__ = ("RoleSeparationError", "check_roles", "resolve_judge_model")
