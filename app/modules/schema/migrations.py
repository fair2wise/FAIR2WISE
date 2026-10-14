"""Preview-only schema diff (T22B). No automatic data transformation exists in this release.

Compares two registered assets through ``SchemaView`` (classes, slots, ``is_a``) so a reviewer can see what a
new registered version would change. Nothing is rewritten, retyped or applied.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .loading import closure_fingerprint, load_schema_view
from .registry import SchemaEntry


@dataclass(frozen=True)
class SchemaDiff:
    """Difference between two registered schema closures."""

    old_fingerprint: str
    new_fingerprint: str
    classes_added: List[str] = field(default_factory=list)
    classes_removed: List[str] = field(default_factory=list)
    is_a_changed: Dict[str, List[Optional[str]]] = field(default_factory=dict)
    slots_added: List[str] = field(default_factory=list)
    slots_removed: List[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        """True when classes, inheritance and slots are the same."""
        return not (self.classes_added or self.classes_removed or self.is_a_changed
                    or self.slots_added or self.slots_removed)


def preview_diff(old: SchemaEntry, new: SchemaEntry, *, allow_known_broken: bool = True) -> SchemaDiff:
    """Diff the closures of ``old`` and ``new``; a preview, never applied."""
    a = load_schema_view(old, allow_known_broken=allow_known_broken)
    b = load_schema_view(new, allow_known_broken=allow_known_broken)
    ca, cb = a.all_classes(), b.all_classes()
    sa, sb = set(a.all_slots()), set(b.all_slots())
    changed = {
        name: [ca[name].is_a, cb[name].is_a]
        for name in sorted(set(ca) & set(cb)) if ca[name].is_a != cb[name].is_a
    }
    return SchemaDiff(
        old_fingerprint=closure_fingerprint(old), new_fingerprint=closure_fingerprint(new),
        classes_added=sorted(set(cb) - set(ca)), classes_removed=sorted(set(ca) - set(cb)),
        is_a_changed=changed, slots_added=sorted(sb - sa), slots_removed=sorted(sa - sb),
    )
