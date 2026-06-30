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
from typing import Any, Dict, Optional
from urllib import request
from urllib.parse import urlparse

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
    raw = os.environ.get("SPLASH_LINKS_DB")
    if not raw:
        return repo / "links.sqlite"
    if raw in {":memory:", "sqlite:///:memory:"}:
        return None
    if raw.startswith("sqlite:///"):
        raw = raw.removeprefix("sqlite:///")
    path = Path(raw)
    if not path.is_absolute():
        path = repo / path
    return path


def _splash_graphql(splash_uri: str, query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = request.Request(
        f"{_splash_base_url(splash_uri)}/splash_links/graphql",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=30) as resp:
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
        data = _splash_graphql(splash_uri, list_query, {"limit": page_size, "offset": 0})
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
    repo = Path(splash_repo or os.environ.get("SPLASH_LINKS_REPO", "../splash_links")).resolve()
    uri = splash_uri or os.environ.get("KG_RAG_SPLASH_URI", "splash://localhost:8081")
    if not repo.is_dir():
        return {"status": "error", "message": f"splash_links repo not found: {repo}"}
    import_script = repo / "scripts" / "import_kg.py"
    if not import_script.exists():
        return {"status": "error", "message": f"splash import script not found: {import_script}"}

    kg_abspath = str(Path(kg_json).resolve())
    db_path = _splash_db_path(repo)
    db_path_display = str(db_path) if db_path else os.environ.get("SPLASH_LINKS_DB", ":memory:")
    if shutil.which("pixi") is None:
        return {"status": "error", "message": "pixi not found on PATH", "db_path": db_path_display}
    if wipe:
        if not allow_wipe:
            return {
                "status": "error",
                "message": f"refusing to wipe splash DB without --allow-splash-wipe: {db_path_display}",
                "db_path": db_path_display,
            }
        if db_path and not db_path.exists():
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
        proc = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True, timeout=900)
    except Exception as e:
        return {"status": "error", "message": f"splash import failed: {e}", "db_path": db_path_display}
    if proc.returncode != 0:
        return {
            "status": "error",
            "message": proc.stderr.strip()[-2000:] or "import_kg.py failed",
            "db_path": db_path_display,
        }
    result = {"status": "success", "stdout": proc.stdout.strip()[-2000:], "db_path": db_path_display}
    if wipe:
        result["deleted_entities"] = deleted
    return result
