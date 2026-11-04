"""F2W graph identity <-> Tiled Graph 0.2.18 identity (T08A; catalog K-07, rewritten).

FORGE's ``paths.py`` derives identities from label slugs. F2W must not do that: the JSON
node id produced by ``json2kg.make_id`` (or carried by a ``nodes``/``edges`` file) **is**
the identity, and it is only meaningful inside its graph (B27, ``multi_kg.merge_hits``).
Nothing here reads, normalizes or compares display names.

Mapping (what T08B must write; what the read adapter accepts)
---------------------------------------------------------------

F2W key                         Tiled 0.2.18 representation
------------------------------  ------------------------------------------------------------
entity ``(graph_id, node_id)``  ``Entity.uri = "f2w:kg/<q(graph_id)>/node/<q(node_id)>"``
                                ``Entity.properties`` contains the reserved markers
                                ``f2w_owner = "f2w-literature"``, ``f2w_graph_id = graph_id``,
                                ``f2w_id = node_id`` (verbatim strings). ``Entity.id`` is the
                                server UUID, recorded in an :class:`IdentityLedger`.
edge ``(graph_id, edge_id)``    ``Link.properties`` contains ``f2w_owner``, ``f2w_graph_id``,
                                ``f2w_edge_id``; ``predicate`` is the JSON predicate verbatim
                                (a registered namespace may compact a full IRI on read-back;
                                F2W predicates are already CURIE-like, e.g. ``rel:USES``).
                                ``subjectId``/``objectId`` are the endpoint entity UUIDs.

``q`` is ``urllib.parse.quote(value, safe="")``, so ``/`` and ``:`` in ids cannot forge
another graph's URI. An entity is in graph ``G`` only if **all three** agree: the URI parses
to ``(G, id)``, ``properties.f2w_graph_id == G`` and ``properties.f2w_id == id``, and
``f2w_owner`` is the literature owner. Anything else (a FORGE entity, an unmarked record, a
record whose URI and properties disagree) is foreign and is never returned in a literature
scope. Tiled 0.2.18 has no uniqueness or upsert (capabilities_0.2.18.md item 13), so two
entities can carry the same URI; readers treat that as a ``CONFLICT``, never pick one.

Edge ids: json2kg keeps the first ``(subject, predicate, object)`` triple, so the triple is
the edge identity. :func:`edge_id` hashes it (``e:<sha256[:32]>``). Files outside json2kg
(``matkg_rsoxs_v3.json``) may repeat a triple; the n-th repeat (n >= 1, file order) gets the
suffix ``#n`` so every stored edge keeps a stable, distinct id.

The ledger maps both ways and refuses to rebind a key to a different UUID (or a UUID to a
different key). T08B should persist it in the import receipt (``id_mapping_sha256``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any
from urllib.parse import quote, unquote

from .ports import ContractError, ErrorCode

#: Value of ``properties.f2w_owner`` on every F2W literature record.
LITERATURE_OWNER = "f2w-literature"
URI_PREFIX = "f2w:kg/"
OWNER_KEY = "f2w_owner"
GRAPH_KEY = "f2w_graph_id"
ENTITY_KEY = "f2w_id"
EDGE_KEY = "f2w_edge_id"
#: Reserved property keys added by the mapping; stripped from ``data`` on read.
RESERVED_KEYS = frozenset({OWNER_KEY, GRAPH_KEY, ENTITY_KEY, EDGE_KEY})

EntityKey = tuple[str, str]


def _check(value: str, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(ErrorCode.INVALID_INPUT, f"{what} must be a non-empty string")
    return value


def entity_uri(graph_id: str, node_id: str) -> str:
    """Return the Tiled ``uri`` for F2W node ``node_id`` in ``graph_id``."""
    return (f"{URI_PREFIX}{quote(_check(graph_id, 'graph_id'), safe='')}"
            f"/node/{quote(_check(node_id, 'node_id'), safe='')}")


def parse_entity_uri(uri: str | None) -> EntityKey | None:
    """Inverse of :func:`entity_uri`; ``None`` for any other URI."""
    if not isinstance(uri, str) or not uri.startswith(URI_PREFIX):
        return None
    parts = uri[len(URI_PREFIX):].split("/")
    if len(parts) != 3 or parts[1] != "node" or not parts[0] or not parts[2]:
        return None
    return unquote(parts[0]), unquote(parts[2])


def entity_properties(graph_id: str, node_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
    """Properties T08B writes for a node: the node payload plus reserved markers."""
    clash = RESERVED_KEYS & set(data)
    if clash:
        raise ContractError(ErrorCode.INVALID_INPUT, f"node uses reserved keys {sorted(clash)}")
    return {**dict(data), OWNER_KEY: LITERATURE_OWNER, GRAPH_KEY: graph_id, ENTITY_KEY: node_id}


def link_properties(graph_id: str, edge_key: str, data: Mapping[str, Any]) -> dict[str, Any]:
    """Properties T08B writes for an edge (payload minus endpoints, plus reserved markers)."""
    body = {k: v for k, v in data.items() if k not in ("subject", "predicate", "object")}
    clash = RESERVED_KEYS & set(body)
    if clash:
        raise ContractError(ErrorCode.INVALID_INPUT, f"edge uses reserved keys {sorted(clash)}")
    return {**body, OWNER_KEY: LITERATURE_OWNER, GRAPH_KEY: graph_id, EDGE_KEY: edge_key}


def edge_id(subject: object, predicate: object, obj: object, occurrence: int = 0) -> str:
    """Stable id of the ``occurrence``-th edge with this triple in its graph."""
    digest = hashlib.sha256(json.dumps([subject, predicate, obj], ensure_ascii=False).encode()).hexdigest()[:32]
    return f"e:{digest}" if occurrence == 0 else f"e:{digest}#{occurrence}"


def assign_edge_ids[E: Mapping[str, Any]](edges: Iterable[E]) -> Iterable[tuple[str, E]]:
    """Yield ``(edge_id, edge)`` in file order, numbering repeated triples."""
    seen: dict[tuple[str, ...], int] = {}
    for edge in edges:
        triple = tuple(json.dumps(edge.get(k)) for k in ("subject", "predicate", "object"))
        n = seen.get(triple, 0)
        seen[triple] = n + 1
        yield edge_id(edge.get("subject"), edge.get("predicate"), edge.get("object"), n), edge


def literature_entity_key(entity: Mapping[str, Any]) -> EntityKey | None:
    """Return ``(graph_id, node_id)`` for an owned F2W literature entity, else ``None``."""
    props = entity.get("properties")
    if not isinstance(props, Mapping) or props.get(OWNER_KEY) != LITERATURE_OWNER:
        return None
    key = parse_entity_uri(entity.get("uri"))
    if key is None or props.get(GRAPH_KEY) != key[0] or props.get(ENTITY_KEY) != key[1]:
        return None
    return key


def literature_link_key(link: Mapping[str, Any]) -> tuple[str, str] | None:
    """Return ``(graph_id, edge_id)`` for an owned F2W literature link, else ``None``."""
    props = link.get("properties")
    if not isinstance(props, Mapping) or props.get(OWNER_KEY) != LITERATURE_OWNER:
        return None
    graph_id, key = props.get(GRAPH_KEY), props.get(EDGE_KEY)
    if not isinstance(graph_id, str) or not isinstance(key, str) or not graph_id or not key:
        return None
    return graph_id, key


def strip_reserved(props: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return ``props`` without the reserved mapping markers."""
    return {k: v for k, v in (props or {}).items() if k not in RESERVED_KEYS}


@dataclass
class IdentityLedger:
    """Bidirectional ``(graph_id, node_id) <-> Tiled entity UUID`` map; never rebinds."""

    _forward: dict[EntityKey, str] = field(default_factory=dict)
    _reverse: dict[str, EntityKey] = field(default_factory=dict)

    def bind(self, graph_id: str, node_id: str, tiled_id: str) -> None:
        """Record a mapping; raise ``CONFLICT`` if either side is already bound elsewhere."""
        key = (_check(graph_id, "graph_id"), _check(node_id, "node_id"))
        _check(tiled_id, "tiled_id")
        if self._forward.get(key, tiled_id) != tiled_id or self._reverse.get(tiled_id, key) != key:
            raise ContractError(ErrorCode.CONFLICT,
                                f"identity {key!r} / {tiled_id!r} is already bound differently")
        self._forward[key] = tiled_id
        self._reverse[tiled_id] = key

    def tiled_id(self, graph_id: str, node_id: str) -> str | None:
        """Tiled UUID for an F2W node, if known."""
        return self._forward.get((graph_id, node_id))

    def f2w_key(self, tiled_id: str) -> EntityKey | None:
        """F2W key for a Tiled UUID, if known."""
        return self._reverse.get(tiled_id)

    def to_json(self) -> dict[str, Any]:
        """Deterministic serializable form (sorted) for receipts."""
        rows = sorted([g, n, t] for (g, n), t in self._forward.items())
        return {"version": 1, "entities": rows}

    def sha256(self) -> str:
        """Hash of :meth:`to_json` (``id_mapping_sha256`` in receipts)."""
        text = json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(text.encode()).hexdigest()

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> IdentityLedger:
        """Load a ledger written by :meth:`to_json`."""
        if payload.get("version") != 1:
            raise ContractError(ErrorCode.INVALID_INPUT, "unsupported identity ledger version")
        ledger = cls()
        for graph_id, node_id, tiled_id in payload.get("entities", []):
            ledger.bind(graph_id, node_id, tiled_id)
        return ledger

    def __len__(self) -> int:
        return len(self._forward)
