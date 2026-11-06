"""KG update helpers shared by the legacy coordinator loop and the library extraction path.

Real port of ``app/modules/f2w_agent/kg_update.py`` (T17B, H8; owner decision 2026-11-04). Wire
behaviour is unchanged; the old path stays importable as an alias of this module.

- ``rebuild_kg``: extracted-terms JSON -> MatKG graph JSON via the local ``json2kg`` converter,
  preserving ``source_metadata`` and ``code_snippets``.
- ``publish_extraction``: post-extraction publication to Tiled (T18E); replaced the Splash wipe +
  re-import, which no longer exists.
- ``splash_*`` / ``load_splash_graph`` / ``export_splash_graph_to_json``: the non-destructive legacy
  Splash entity/link operations the frozen UI contract (class (r)) still uses when no Tiled Save
  target is configured. They talk to the configured ``splash://`` endpoint only (``KG_RAG_SPLASH_URI``
  / ``SPLASH_LINKS_URI``, loopback by default) and send no credential.

Importing this module has no side effects: the Splash URI is resolved at call time.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast
from urllib import request
from urllib.parse import urlparse

from ..contracts.json_types import JsonObject, JsonValue

if TYPE_CHECKING:
    from ...api.post_extraction import PostExtractionPublisher
    from ..contracts.context import RequestContext

logger = logging.getLogger(__name__)


def _splash_base_url(uri: str) -> str:
    """Convert a splash:// or http(s) URI into an HTTP base URL."""
    parsed = urlparse(uri)
    if parsed.scheme == "splash":
        host = parsed.netloc or parsed.path
        return f"http://{host}".rstrip("/")
    if parsed.scheme in {"http", "https"}:
        return uri.rstrip("/")
    raise ValueError(f"unsupported splash URI: {uri}")


def _splash_graphql(
    splash_uri: str,
    query: str,
    variables: JsonObject | None = None,
    *,
    timeout: int = 30,
) -> JsonObject:
    """POST a GraphQL query to splash-links and return ``data``, raising on errors."""
    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = request.Request(
        f"{_splash_base_url(splash_uri)}/splash_links/graphql",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if body.get("errors"):
        raise RuntimeError(body["errors"])
    return body.get("data") or {}


def rebuild_kg(terms_json: str, kg_json: str) -> JsonObject:
    """Convert ``terms_json`` to MatKG ``kg_json`` (keeps provenance + snippets)."""
    from ..json2kg import convert_terms_to_graph

    out = Path(kg_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    graph = convert_terms_to_graph(Path(terms_json), out)
    return {
        "status": "success",
        "kg_json": str(out),
        "nodes": len(graph.get("things", [])),
        "edges": len(graph.get("associations", [])),
    }


#: ``publish_state`` values: a distinct, explicit state beside the legacy ``status`` (which stays as it was).
PUBLISH_STATE_PUBLISHED = "tiled_published"
PUBLISH_STATE_REVIEW = "awaiting_review"
PUBLISH_STATE_ERROR = "error"
PUBLISH_STATE_JSON_ONLY = "json_available_tiled_unavailable"
#: Owner wording (2026-11-03) for the state where no Tiled publisher is configured.
JSON_ONLY_INDICATOR = "JSON available, Tiled unavailable"
#: ``status`` -> ``publish_state`` for a result a publisher returned.
PUBLISH_STATE_BY_STATUS = {
    "success": PUBLISH_STATE_PUBLISHED,
    "awaiting_review": PUBLISH_STATE_REVIEW,
    "error": PUBLISH_STATE_ERROR,
    "skipped": PUBLISH_STATE_JSON_ONLY,
}


def publish_extraction(
    terms_json: str,
    *,
    publisher: PostExtractionPublisher | None = None,
    ctx: RequestContext | None = None,
) -> JsonObject:
    """Publish the corpus an extraction run just rebuilt (T18E; replaces the Splash wipe-and-reimport, Q11).

    ``publisher`` is a :class:`~app.api.post_extraction.PostExtractionPublisher` (set on the launcher's
    config as ``extraction_publisher``): it freezes ``v<N+1>`` from ``terms_json``, plans the corpus-scoped
    replacement and applies it under the profile's approval policy (``:local`` auto-approves a safe plan, an
    unsafe plan waits for review, ALS always waits for an admin). Nothing here deletes anything: there is no
    wipe, no ``--allow-splash-wipe`` and no ``links.sqlite`` handling.

    Without a publisher (no Tiled publication target configured) nothing is published. The legacy ``status``
    stays ``skipped`` (wire compatibility); the explicit state is ``publish_state =
    "json_available_tiled_unavailable"`` with ``indicator = "JSON available, Tiled unavailable"``: the session
    graph JSON is available and stays the source for retrieval, Tiled has no copy, and Splash is neither wiped
    nor re-imported.

    Returns:
        ``{"status": "success" | "awaiting_review" | "skipped" | "error", "publish_state": ..., "message": ...}``
        plus the job and plan ids when a publication ran. ``publish_state`` is ``tiled_published``,
        ``awaiting_review``, ``error`` or ``json_available_tiled_unavailable``.
    """
    if publisher is None:
        return {
            "status": "skipped",
            "publish_state": PUBLISH_STATE_JSON_ONLY,
            "indicator": JSON_ONLY_INDICATOR,
            "message": f"{JSON_ONLY_INDICATOR}: no Tiled publication target is configured; "
                       "the Splash graph is not re-imported",
        }
    result = dict(publisher.publish(ctx, terms_json))
    state = PUBLISH_STATE_BY_STATUS.get(str(result.get("status")))
    if state is not None:
        result.setdefault("publish_state", state)
    return result


_ENTITY_FIELDS = "id entityType name uri properties"


def splash_uri_default() -> str:
    """Return the splash URI from env, defaulting to localhost:8081."""
    return (
        os.environ.get("KG_RAG_SPLASH_URI")
        or os.environ.get("SPLASH_LINKS_URI")
        or "splash://localhost:8081"
    )


def splash_find_entity_by_matkg_id(
    matkg_id: str,
    *,
    splash_uri: str | None = None,
    page_size: int = 500,
) -> JsonObject | None:
    """Resolve a splash entity by MatKG id (uri / properties.matkg_id) or splash UUID."""
    uri = splash_uri or splash_uri_default()
    if not matkg_id:
        return None

    by_id: JsonObject | None = _splash_graphql(
        uri,
        f"query Entity($id: ID!) {{ entity(id: $id) {{ {_ENTITY_FIELDS} }} }}",
        {"id": matkg_id},
    ).get("entity")
    if by_id:
        return by_id

    offset = 0
    list_query = f"""
    query Entities($limit: Int!, $offset: Int!) {{
      entities(limit: $limit, offset: $offset) {{ {_ENTITY_FIELDS} }}
    }}
    """
    while True:
        batch: list[JsonObject] = (
            _splash_graphql(
                uri, list_query, {"limit": page_size, "offset": offset}
            ).get("entities")
            or []
        )
        for entity in batch:
            props = entity.get("properties") or {}
            if entity.get("uri") == matkg_id or props.get("matkg_id") == matkg_id:
                return entity
        if len(batch) < page_size:
            return None
        offset += page_size


def splash_update_entity(
    entity_id: str,
    *,
    name: str | None = None,
    entity_type: str | None = None,
    properties: JsonObject | None = None,
    splash_uri: str | None = None,
) -> JsonObject | None:
    """Patch splash entity fields; no-op fetch if the update payload is empty."""
    uri = splash_uri or splash_uri_default()
    input_payload: JsonObject = {}
    if name is not None:
        input_payload["name"] = name
    if entity_type is not None:
        input_payload["entityType"] = entity_type
    if properties is not None:
        input_payload["properties"] = properties
    if not input_payload:
        return splash_find_entity_by_matkg_id(entity_id, splash_uri=uri)
    data = _splash_graphql(
        uri,
        f"""
        mutation Update($id: ID!, $input: UpdateEntityInput!) {{
          updateEntity(id: $id, input: $input) {{ {_ENTITY_FIELDS} }}
        }}
        """,
        {"id": entity_id, "input": input_payload},
    )
    return data.get("updateEntity")


def splash_create_entity(
    *,
    entity_type: str,
    name: str,
    uri: str | None = None,
    properties: JsonObject | None = None,
    splash_uri: str | None = None,
) -> JsonObject:
    """Create a splash entity and return it, raising if the mutation is null."""
    splash = splash_uri or splash_uri_default()
    data = _splash_graphql(
        splash,
        f"""
        mutation Create($input: CreateEntityInput!) {{
          createEntity(input: $input) {{ {_ENTITY_FIELDS} }}
        }}
        """,
        {
            "input": {
                "entityType": entity_type,
                "name": name,
                "uri": uri,
                "properties": properties,
            }
        },
    )
    entity: JsonObject | None = data.get("createEntity")
    if not entity:
        raise RuntimeError("splash createEntity returned null")
    return entity


def splash_create_link(
    *,
    subject_id: str,
    predicate: str,
    object_id: str,
    properties: JsonObject | None = None,
    splash_uri: str | None = None,
) -> JsonObject:
    """Create a splash link and return it, raising if the mutation is null."""
    splash = splash_uri or splash_uri_default()
    data = _splash_graphql(
        splash,
        """
        mutation Create($input: CreateLinkInput!) {
          createLink(input: $input) { id subjectId predicate objectId }
        }
        """,
        {
            "input": {
                "subjectId": subject_id,
                "predicate": predicate,
                "objectId": object_id,
                "properties": properties,
            }
        },
    )
    link: JsonObject | None = data.get("createLink")
    if not link:
        raise RuntimeError("splash createLink returned null")
    return link


def splash_find_links(
    *,
    subject_id: str | None = None,
    predicate: str | None = None,
    object_id: str | None = None,
    splash_uri: str | None = None,
    limit: int = 100,
) -> list[JsonObject]:
    """Query splash links filtered by subject, predicate, and/or object."""
    splash = splash_uri or splash_uri_default()
    data = _splash_graphql(
        splash,
        """
        query Links($subjectId: ID, $predicate: String, $objectId: ID, $limit: Int!) {
          links(subjectId: $subjectId, predicate: $predicate, objectId: $objectId, limit: $limit) {
            id subjectId predicate objectId
          }
        }
        """,
        {
            "subjectId": subject_id,
            "predicate": predicate,
            "objectId": object_id,
            "limit": limit,
        },
    )
    return list(data.get("links") or [])


def splash_delete_link(link_id: str, *, splash_uri: str | None = None) -> bool:
    """Delete a splash link by id and return whether the mutation succeeded."""
    splash = splash_uri or splash_uri_default()
    data = _splash_graphql(
        splash,
        "mutation Delete($id: ID!) { deleteLink(id: $id) }",
        {"id": link_id},
    )
    return bool(data.get("deleteLink"))


def _splash_node_id(entity: JsonObject) -> str:
    """Prefer uri/matkg_id as the MatKG node id, else the splash UUID."""
    props = entity.get("properties") or {}
    return cast(str, entity.get("uri") or props.get("matkg_id") or entity["id"])  # JsonValue: GraphQL payload


def _splash_entity_to_node(entity: JsonObject) -> JsonObject:
    """Map a splash entity into a MatKG-shaped thing dict."""
    props = dict(entity.get("properties") or {})
    node_id = _splash_node_id(entity)
    node = {
        **props,
        "id": node_id,
        "name": entity.get("name") or node_id,
        "category": entity.get("entityType") or props.get("category", "Thing"),
    }
    if "description" not in node:
        node["description"] = ""
    return node


def load_splash_graph(
    *,
    splash_uri: str | None = None,
    page_size: int = 500,
    timeout: int = 30,
) -> JsonObject:
    """Load splash-links entities/links into MatKG-shaped JSON."""
    uri = splash_uri or splash_uri_default()
    entities: list[JsonObject] = []
    links: list[JsonObject] = []

    offset = 0
    list_entities = f"""
    query Entities($limit: Int!, $offset: Int!) {{
      entities(limit: $limit, offset: $offset) {{ {_ENTITY_FIELDS} }}
    }}
    """
    while True:
        batch = (
            _splash_graphql(
                uri,
                list_entities,
                {"limit": page_size, "offset": offset},
                timeout=timeout,
            ).get("entities")
            or []
        )
        entities.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    offset = 0
    list_links = """
    query Links($limit: Int!, $offset: Int!) {
      links(limit: $limit, offset: $offset) {
        id subjectId predicate objectId properties
      }
    }
    """
    while True:
        batch = (
            _splash_graphql(
                uri, list_links, {"limit": page_size, "offset": offset}, timeout=timeout
            ).get("links")
            or []
        )
        links.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    uuid_to_node_id = {entity["id"]: _splash_node_id(entity) for entity in entities}
    nodes = [_splash_entity_to_node(entity) for entity in entities]
    associations: list[JsonObject] = []
    for link in links:
        subject = uuid_to_node_id.get(link.get("subjectId"))
        obj = uuid_to_node_id.get(link.get("objectId"))
        if not subject or not obj:
            continue
        props = link.get("properties") or {}
        associations.append(
            {
                "subject": subject,
                "predicate": link.get("predicate", "rel:related_to"),
                "object": obj,
                "has_evidence": props.get("has_evidence"),
            }
        )
    return {"things": nodes, "associations": associations}


def export_splash_graph_to_json(
    dest: Path,
    *,
    splash_uri: str | None = None,
    page_size: int = 500,
    timeout: int = 5,
) -> JsonObject:
    """Write the live splash graph into a MatKG JSON file for UI session reads."""
    data = load_splash_graph(
        splash_uri=splash_uri, page_size=page_size, timeout=timeout
    )
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data
