"""Write client for Tiled Graph 0.2.18 (T08B; catalog K-06 selective, rewritten).

Only the importer uses this. Differences from the donor ``mutations.py`` (K-06 notes: "no global
resets, enforce namespace/ownership and explicit capabilities"):

* No reset, no bulk delete, no namespace changes, no ``upsert_entity`` (0.2.18 has no upsert and
  URIs are not unique: reconciliation is by complete scan, never by "find then write").
* Observed 0.2.18 shapes (``docs/adr/evidence/tiled/capabilities_0.2.18.md``): properties are
  create-only; ``updateEntity``/``updateLink`` fail unless ``accessBlob`` is sent, so every
  update method here requires it and every create sends it.
* ``update_entity`` and ``delete_link`` refuse a record that does not carry the F2W literature
  owner marker for the caller's graph (client-side owner check; the server has none).
* Transport failures are ``UNAVAILABLE`` and mean "outcome unknown": callers reconcile by scan.

Adapted from als-computing/forge_rsoxs_analysis src/forge/kg/mutations.py (BSD-3-Clause).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import requests

from ..identity_adapter import GRAPH_KEY, LITERATURE_OWNER, OWNER_KEY
from ..ports import ContractError, ErrorCode, TruncatedGraphRead
from .tiled_client import TiledCredentialRejected, TiledGraphClient

_ENTITY_FIELDS = "id name entityType uri properties"
_LINK_FIELDS = "id predicate subjectId objectId properties accessBlob"
_ENTITY_PAGE = "query($l: Int!, $o: Int!) { entities(limit: $l, offset: $o) { " + _ENTITY_FIELDS + " } }"
_TYPED_ENTITY_PAGE = ("query($t: String!, $l: Int!, $o: Int!) { entities(entityType: $t, limit: $l, offset: $o) { "
                      + _ENTITY_FIELDS + " } }")
_LINK_PAGE = "query($l: Int!, $o: Int!) { links(limit: $l, offset: $o) { " + _LINK_FIELDS + " } }"
_LINKS_BY_SUBJECT = ("query($s: ID!, $l: Int!, $o: Int!) { links(subjectId: $s, limit: $l, offset: $o) { "
                     + _LINK_FIELDS + " } }")
_LINKS_BY_OBJECT = ("query($s: ID!, $l: Int!, $o: Int!) { links(objectId: $s, limit: $l, offset: $o) { "
                    + _LINK_FIELDS + " } }")
_ENTITY_BY_ID = "query($i: ID!) { entity(id: $i) { " + _ENTITY_FIELDS + " } }"
_CREATE_ENTITY = "mutation($i: CreateEntityInput!) { createEntity(input: $i) { id } }"
_CREATE_LINK = "mutation($i: CreateLinkInput!) { createLink(input: $i) { id } }"
_UPDATE_ENTITY = "mutation($id: ID!, $i: UpdateEntityInput!) { updateEntity(id: $id, input: $i) { id uri } }"
_DELETE_LINK = "mutation($id: ID!) { deleteLink(id: $id) }"
_DELETE_ENTITY = "mutation($id: ID!) { deleteEntity(id: $id) }"


def _owned_by(row: Mapping[str, Any], graph_id: str) -> bool:
    props = row.get("properties")
    return isinstance(props, Mapping) and props.get(OWNER_KEY) == LITERATURE_OWNER \
        and props.get(GRAPH_KEY) == graph_id


class TiledMutationClient(TiledGraphClient):
    """:class:`TiledGraphClient` that may also send the importer's mutations.

    The credential is the configured runtime writer (``from_external``/``from_managed``);
    nothing here accepts a user token.
    """

    def mutate(self, document: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        """POST one mutation; never retried (a retry could duplicate a create)."""
        try:
            resp = self._session.post(self.endpoint, json={"query": document, "variables": dict(variables)},
                                      headers=self._headers(), timeout=self._timeout, verify=self._verify)
        except requests.RequestException as exc:
            raise ContractError(ErrorCode.UNAVAILABLE,
                                f"Tiled graph at {self.base_url} is unavailable ({type(exc).__name__}); "
                                "the write outcome is unknown, reconcile by scan") from None
        if resp.status_code in (401, 403):
            raise TiledCredentialRejected(f"Tiled graph rejected the writer credential "
                                          f"(HTTP {resp.status_code})", resp.status_code)
        if resp.status_code >= 500 or resp.status_code == 404:
            raise ContractError(ErrorCode.UNAVAILABLE, f"Tiled graph answered HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError:
            raise ContractError(ErrorCode.UNAVAILABLE, "Tiled graph sent a non-JSON body") from None
        errors = body.get("errors") if isinstance(body, dict) else None
        if errors:
            message = str((errors[0] or {}).get("message", ""))[:500]
            code = ErrorCode.DENIED if "Not permitted" in message else ErrorCode.INTERNAL
            raise ContractError(code, f"Tiled graph mutation failed: {message}")
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            raise ContractError(ErrorCode.UNAVAILABLE, "Tiled graph returned no data object")
        return data

    # -- complete reads -------------------------------------------------------------------
    def _scan(self, document: str, key: str, page_size: int, max_rows: int,
              variables: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        while True:
            limit = min(page_size, max_rows - len(rows))
            if limit <= 0:
                raise TruncatedGraphRead(f"tiled.{key} scan", max_rows)
            page = self.query(document, {**dict(variables or {}), "l": limit, "o": len(rows)}).get(key) or []
            if any(r["id"] in seen for r in page):
                raise ContractError(ErrorCode.CONFLICT, f"the {key} listing changed while it was read",
                                    retryable=True)
            seen.update(r["id"] for r in page)
            rows.extend(page)
            if len(page) < limit:
                break
        return rows

    def scan_entities(self, *, page_size: int = 500, max_rows: int = 200_000,
                      entity_type: str | None = None) -> list[dict[str, Any]]:
        """Every entity (all owners), or every entity of ``entity_type`` as other readers list them.

        Raises ``TruncatedGraphRead`` rather than returning a partial list.
        """
        if entity_type is not None:
            return self._scan(_TYPED_ENTITY_PAGE, "entities", page_size, max_rows, {"t": entity_type})
        return self._scan(_ENTITY_PAGE, "entities", page_size, max_rows)

    def entity(self, entity_id: str) -> dict[str, Any] | None:
        """One entity by UUID (any owner, any state), or ``None`` when it does not exist."""
        row = self.query(_ENTITY_BY_ID, {"i": entity_id}).get("entity")
        return cast(dict[str, Any] | None, row)

    def links_of(self, entity_id: str, *, page_size: int = 500, max_rows: int = 100_000) -> list[dict[str, Any]]:
        """Every link attached to ``entity_id`` in either direction (all owners), de-duplicated by id."""
        rows = self._scan(_LINKS_BY_SUBJECT, "links", page_size, max_rows, {"s": entity_id})
        seen = {r["id"] for r in rows}
        for row in self._scan(_LINKS_BY_OBJECT, "links", page_size, max_rows, {"s": entity_id}):
            if row["id"] not in seen:
                rows.append(row)
        return rows

    def scan_links(self, *, page_size: int = 500, max_rows: int = 500_000) -> list[dict[str, Any]]:
        """Every link (all owners), with ``accessBlob``."""
        return self._scan(_LINK_PAGE, "links", page_size, max_rows)

    # -- writes ---------------------------------------------------------------------------
    def create_entity(self, *, entity_type: str, name: str, uri: str, properties: Mapping[str, Any],
                      access_blob: Mapping[str, Any]) -> str:
        """``createEntity``; returns the server UUID."""
        data = self.mutate(_CREATE_ENTITY, {"i": {"entityType": entity_type, "name": name, "uri": uri,
                                                  "properties": dict(properties),
                                                  "accessBlob": dict(access_blob)}})
        return cast(str, data["createEntity"]["id"])

    def create_link(self, *, subject_id: str, predicate: str, object_id: str, properties: Mapping[str, Any],
                    access_blob: Mapping[str, Any]) -> str:
        """``createLink``; returns the server UUID."""
        data = self.mutate(_CREATE_LINK, {"i": {"subjectId": subject_id, "predicate": predicate,
                                                "objectId": object_id, "properties": dict(properties),
                                                "accessBlob": dict(access_blob)}})
        return cast(str, data["createLink"]["id"])

    def set_entity_uri(self, row: Mapping[str, Any], graph_id: str, uri: str,
                       access_blob: Mapping[str, Any]) -> None:
        """``updateEntity`` of ``uri`` only, always with ``accessBlob`` (0.2.18 defect); owned rows only."""
        if not _owned_by(row, graph_id):
            raise ContractError(ErrorCode.DENIED, "refusing to modify a record F2W literature does not own")
        self.mutate(_UPDATE_ENTITY, {"id": row["id"], "i": {"uri": uri, "accessBlob": dict(access_blob)}})

    def set_entity_state(self, row: Mapping[str, Any], graph_id: str, *, uri: str, entity_type: str,
                         access_blob: Mapping[str, Any]) -> None:
        """``updateEntity`` of ``uri`` and ``entityType`` together (T08C stage/retire/promote); owned rows only.

        The type change is what keeps staged and retired records out of other readers' type-filtered
        listings (``f2w:Staged`` / ``f2w:Retired``, T20C P3); properties are create-only and stay as they are.
        """
        if not _owned_by(row, graph_id):
            raise ContractError(ErrorCode.DENIED, "refusing to modify a record F2W literature does not own")
        self.mutate(_UPDATE_ENTITY, {"id": row["id"], "i": {"uri": uri, "entityType": entity_type,
                                                         "accessBlob": dict(access_blob)}})

    def delete_link(self, row: Mapping[str, Any], graph_id: str) -> None:
        """``deleteLink`` for an owned link only."""
        if not _owned_by(row, graph_id):
            raise ContractError(ErrorCode.DENIED, "refusing to delete a record F2W literature does not own")
        self.mutate(_DELETE_LINK, {"id": row["id"]})

    def delete_entity(self, row: Mapping[str, Any], graph_id: str) -> None:
        """``deleteEntity`` for an owned entity only (T08D purge and abandon-plan).

        0.2.18 cascades the delete into every attached link of every owner (T20C P1/P2), so callers must
        read the entity's links immediately before and refuse when any is foreign; this method only
        enforces ownership of the entity itself.
        """
        if not _owned_by(row, graph_id):
            raise ContractError(ErrorCode.DENIED, "refusing to delete a record F2W literature does not own")
        self.mutate(_DELETE_ENTITY, {"id": row["id"]})
