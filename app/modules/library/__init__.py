"""Reference-paper library (addendum A8): job kinds, capabilities and registration.

T34 owns the wiring only: the ``library_*`` job kinds, their local-operator capabilities and
:func:`register_library_jobs`. Cataloguing and upload are T35, indexing T36 and extraction T39;
until those land the matching kinds fail closed with ``unsupported_capability``.
"""

from __future__ import annotations

from .jobs import (
    LIBRARY_EXTRACT_KIND,
    LIBRARY_HARVEST_KIND,
    LIBRARY_INDEX_KIND,
    LIBRARY_KIND_CAPABILITIES,
    LIBRARY_PROBE_KIND,
    LIBRARY_VERIFY_KIND,
    NONCE_PARAM,
    StageHandler,
    library_inputs,
    not_implemented,
    register_library_jobs,
)

__all__ = (
    "LIBRARY_EXTRACT_KIND",
    "LIBRARY_HARVEST_KIND",
    "LIBRARY_INDEX_KIND",
    "LIBRARY_KIND_CAPABILITIES",
    "LIBRARY_PROBE_KIND",
    "LIBRARY_VERIFY_KIND",
    "NONCE_PARAM",
    "StageHandler",
    "library_inputs",
    "not_implemented",
    "register_library_jobs",
)
