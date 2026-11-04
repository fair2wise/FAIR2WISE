"""Shared aliases for JSON-like data (T27 sniff row F1.4).

Runtime annotations only: importing this module has no side effects. The two
aliases are the single, documented home of ``Any`` for data whose shape is
decided by whoever produced the JSON, so call sites annotate ``JsonValue`` or
``JsonObject`` instead of repeating a bare ``Any`` (ruff ANN401).
"""

from __future__ import annotations

from typing import Any

# Any: arbitrary decoded JSON (dict, list, str, number, bool, None) or a JSON-like record; callers narrow it with isinstance.
JsonValue = Any
# Any: a JSON object whose values are decided by the producer (model output, stored record, wire payload).
JsonObject = dict[str, Any]
# Any: a deliberately dynamic value: an untyped third-party object, a lazily imported attribute or a wrapped callable's result.
Untyped = Any
