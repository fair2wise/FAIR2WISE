"""KG update helpers shared by the coordinator loop.

- ``rebuild_kg``: extracted-terms JSON -> MatKG graph JSON via the local
  [json2kg.py], preserving ``source_metadata`` and ``code_snippets``.
- ``splash_reimport``: wipe + re-import the cumulative KG into splash-links per
  the HANDOFF runbook (only used in splash mode).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import request
from urllib.parse import urlparse

from ..project_config import PROJECT_ROOT, config_value

logger = logging.getLogger(__name__)


def _splash_base_url(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme == "splash":
        host = parsed.netloc or parsed.path
        return f"http://{host}".rstrip("/")
    if parsed.scheme in {"http", "https"}:
        return uri.rstrip("/")
    raise ValueError(f"unsupported splash URI: {uri}")


def _splash_db_path(repo: Path) -> Optional[Path]:
    raw = str(config_value("paths.splash_links_db", fallback="links.sqlite"))
    if raw in {":memory:", "sqlite:///:memory:"}:
        return None
    if raw.startswith("sqlite:///"):
        raw = raw.removeprefix("sqlite:///")
    path = Path(raw)
    if not path.is_absolute():
        path = repo / path
    return path


def _splash_is_local(uri: str) -> bool:
    """Whether the Splash URI points at the same host as this process."""
    parsed = urlparse(uri)
    host = (parsed.hostname or "").lower()
    return host in {"", "localhost", "127.0.0.1", "::1"}


def _splash_graphql(
    splash_uri: str,
    query: str,
    variables: Optional[Dict[str, Any]] = None,
    *,
    timeout: int = 30,
) -> Dict[str, Any]:
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


def _wipe_splash_via_graphql(splash_uri: str, *, page_size: int = 500) -> int:
    """Delete entities through the live splash API so SQLite handles stay valid."""
    list_query = """
    query ListEntities($limit: Int!, $offset: Int!) {
      entities(limit: $limit, offset: $offset) { id }
    }
    """
    delete_mutation = """
    mutation DeleteEntity($id: ID!) {
      deleteEntity(id: $id)
    }
    """
    deleted = 0
    while True:
        data = _splash_graphql(
            splash_uri, list_query, {"limit": page_size, "offset": 0}
        )
        entities = data.get("entities") or []
        if not entities:
            return deleted
        for entity in entities:
            _splash_graphql(splash_uri, delete_mutation, {"id": entity["id"]})
            deleted += 1


def rebuild_kg(terms_json: str, kg_json: str) -> Dict[str, Any]:
    """Convert ``terms_json`` to MatKG ``kg_json`` (keeps provenance + snippets)."""
    from app.modules.json2kg import convert_terms_to_graph

    out = Path(kg_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    graph = convert_terms_to_graph(Path(terms_json), out)
    return {
        "status": "success",
        "kg_json": str(out),
        "nodes": len(graph.get("things", [])),
        "edges": len(graph.get("associations", [])),
    }


def splash_reimport(
    kg_json: str,
    *,
    splash_repo: Optional[str] = None,
    splash_uri: Optional[str] = None,
    wipe: bool = True,
    allow_wipe: bool = False,
) -> Dict[str, Any]:
    """Re-import ``kg_json`` into splash-links (wipe DB first, per HANDOFF).

    Requires a `splash_links` checkout and a running/`pixi`-managed environment.
    Wiping is done through GraphQL. Do not unlink ``links.sqlite`` while the
    splash server is running; SQLite can keep a stale readonly file handle.
    """
    configured_repo = config_value("paths.splash_links_repo", fallback="splash_links")
    repo = Path(splash_repo or configured_repo)
    if not repo.is_absolute():
        repo = PROJECT_ROOT / repo
    repo = repo.resolve()
    uri = splash_uri or os.environ.get("KG_RAG_SPLASH_URI", "splash://localhost:8081")
    if not repo.is_dir():
        return {"status": "error", "message": f"splash_links repo not found: {repo}"}
    import_script = repo / "scripts" / "import_kg.py"
    if not import_script.exists():
        return {
            "status": "error",
            "message": f"splash import script not found: {import_script}",
        }

    kg_abspath = str(Path(kg_json).resolve())
    db_path = _splash_db_path(repo)
    db_path_display = (
        str(db_path) if db_path else os.environ.get("SPLASH_LINKS_DB", ":memory:")
    )
    if shutil.which("pixi") is None:
        return {
            "status": "error",
            "message": "pixi not found on PATH",
            "db_path": db_path_display,
        }
    if wipe:
        if not allow_wipe:
            return {
                "status": "error",
                "message": f"refusing to wipe splash DB without --allow-splash-wipe: {db_path_display}",
                "db_path": db_path_display,
            }
        if _splash_is_local(uri) and db_path and not db_path.exists():
            return {
                "status": "error",
                "message": (
                    "splash DB file is missing while the splash server may still be running. "
                    "Restart splash-links to recreate a writable SQLite handle, then retry."
                ),
                "db_path": db_path_display,
                "splash_uri": uri,
            }
        logger.warning("Wiping splash-links graph through API before import: %s", uri)
        try:
            deleted = _wipe_splash_via_graphql(uri)
        except Exception as e:
            return {
                "status": "error",
                "message": (
                    "splash API wipe failed. If links.sqlite was deleted while the "
                    f"server was running, restart splash-links, then retry. Details: {e}"
                ),
                "db_path": db_path_display,
                "splash_uri": uri,
            }
    cmd = ["pixi", "run", "python", "scripts/import_kg.py", "--url", uri, kg_abspath]
    logger.info("splash import: %s (cwd=%s)", " ".join(cmd), repo)
    try:
        proc = subprocess.run(
            cmd, cwd=str(repo), capture_output=True, text=True, timeout=900
        )
    except Exception as e:
        return {
            "status": "error",
            "message": f"splash import failed: {e}",
            "db_path": db_path_display,
        }
    if proc.returncode != 0:
        return {
            "status": "error",
            "message": proc.stderr.strip()[-2000:] or "import_kg.py failed",
            "db_path": db_path_display,
        }
    result = {
        "status": "success",
        "stdout": proc.stdout.strip()[-2000:],
        "db_path": db_path_display,
    }
    if wipe:
        result["deleted_entities"] = deleted
    return result


_ENTITY_FIELDS = "id entityType name uri properties"


def splash_uri_default() -> str:
    return (
        os.environ.get("KG_RAG_SPLASH_URI")
        or os.environ.get("SPLASH_LINKS_URI")
        or "splash://localhost:8081"
    )


def splash_find_entity_by_matkg_id(
    matkg_id: str,
    *,
    splash_uri: Optional[str] = None,
    page_size: int = 500,
) -> Optional[Dict[str, Any]]:
    """Resolve a splash entity by MatKG id (uri / properties.matkg_id) or splash UUID."""
    uri = splash_uri or splash_uri_default()
    if not matkg_id:
        return None

    by_id = _splash_graphql(
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
        batch = (
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
    name: Optional[str] = None,
    entity_type: Optional[str] = None,
    properties: Optional[Dict[str, Any]] = None,
    splash_uri: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    uri = splash_uri or splash_uri_default()
    input_payload: Dict[str, Any] = {}
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
    uri: Optional[str] = None,
    properties: Optional[Dict[str, Any]] = None,
    splash_uri: Optional[str] = None,
) -> Dict[str, Any]:
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
    entity = data.get("createEntity")
    if not entity:
        raise RuntimeError("splash createEntity returned null")
    return entity


def splash_create_link(
    *,
    subject_id: str,
    predicate: str,
    object_id: str,
    properties: Optional[Dict[str, Any]] = None,
    splash_uri: Optional[str] = None,
) -> Dict[str, Any]:
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
    link = data.get("createLink")
    if not link:
        raise RuntimeError("splash createLink returned null")
    return link


def splash_find_links(
    *,
    subject_id: Optional[str] = None,
    predicate: Optional[str] = None,
    object_id: Optional[str] = None,
    splash_uri: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
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


def splash_delete_link(link_id: str, *, splash_uri: Optional[str] = None) -> bool:
    splash = splash_uri or splash_uri_default()
    data = _splash_graphql(
        splash,
        "mutation Delete($id: ID!) { deleteLink(id: $id) }",
        {"id": link_id},
    )
    return bool(data.get("deleteLink"))


def _splash_node_id(entity: Dict[str, Any]) -> str:
    props = entity.get("properties") or {}
    return entity.get("uri") or props.get("matkg_id") or entity["id"]


def _splash_entity_to_node(entity: Dict[str, Any]) -> Dict[str, Any]:
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
    splash_uri: Optional[str] = None,
    page_size: int = 500,
    timeout: int = 30,
) -> Dict[str, Any]:
    """Load splash-links entities/links into MatKG-shaped JSON."""
    uri = splash_uri or splash_uri_default()
    entities: List[Dict[str, Any]] = []
    links: List[Dict[str, Any]] = []

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
    associations: List[Dict[str, Any]] = []
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
    splash_uri: Optional[str] = None,
    page_size: int = 500,
    timeout: int = 5,
) -> Dict[str, Any]:
    """Write the live splash graph into a MatKG JSON file for UI session reads."""
    data = load_splash_graph(
        splash_uri=splash_uri, page_size=page_size, timeout=timeout
    )
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data
