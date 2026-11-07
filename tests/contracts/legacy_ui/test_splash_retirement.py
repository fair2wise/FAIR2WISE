"""T16B Splash retirement state (plan D03/D16, section 5.2; packet docs/migration/reviews/H8/splash_retirement.md).

Pins the state the retirement packet describes, so that neither half can drift silently before the
H8 cutover:

* the legacy wire vocabulary stays: the browser stores ``graphSource=splash_links`` and sends
  ``graph_source=splash``; the API accepts ``^(splash|json)$``; ``kg_rag_api`` accepts the three
  Splash spellings. Removing a token is a frozen-UI break (FRONTEND_COMPATIBILITY.md UI04).
* no code path requires a live Splash service: no ``app`` module imports the vendored
  ``splash_links`` package or its own dependencies; the legacy Splash GraphQL client exists in
  exactly the two modules the deletion list names; the Tiled Save stand-in covers every client
  call the UI Save path makes; every supported launcher defaults to JSON / Tiled.
* packaging carries no Splash dependency, and the packet's inventory names every code/config
  file that still mentions Splash (so the deletion list cannot be shorter than reality).

No network, no .env, no model or Tiled service. The ``agent`` Dockerfile target and
``compose.splash.yaml`` are pinned separately by ``tests/images/test_images_dockerfile.py``.
"""
from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKET = REPO_ROOT / "docs/migration/reviews/H8/splash_retirement.md"

#: The vendored service and the libraries only it uses (splash_links/pyproject.toml).
FORBIDDEN_IMPORT_ROOTS = ("splash_links", "strawberry", "alembic", "pgvector", "psycopg2")

#: Class (a)/(c) of the packet: the only two modules that may hold the legacy Splash GraphQL client.
SPLASH_CLIENT_MODULES = {
    "app/modules/graph/kg_update.py",  # moved from f2w_agent/kg_update.py by the T17B port (alias shim stays there)
    "app/modules/kg_rag_api.py",
}

#: The six client calls the UI Save path makes (``AgentPipelineService._splash``); the Tiled
#: stand-in must answer every one so a Tiled-backed Save can never reach a Splash host.
SPLASH_SAVE_CALLS = (
    "splash_find_entity_by_matkg_id", "splash_update_entity", "splash_create_entity",
    "splash_create_link", "splash_find_links", "splash_delete_link",
)

#: Code and configuration the inventory covers (tests, docs and ``splash_links/`` itself are listed
#: by directory in the packet, not file by file).
INVENTORY_ROOTS = (
    "app", "ui/src", "ui/e2e", "scripts", "docker", "deploy", ".github", "mkdocs/mkdocs.yml",
    "Dockerfile", "compose.yaml", "compose.hostnet.yaml", "compose.splash.yaml", "docker-compose.yml",
    "config.yml", "pyproject.toml", "pixi.toml", ".dockerignore", ".gitignore",
)
SKIP_DIRS = {"node_modules", "__pycache__", "dist", "graphify-out", ".pixi"}
SKIP_SUFFIXES = {".lock", ".pyc", ".png", ".jpg", ".svg", ".ico", ".woff", ".woff2"}


def _app_py_files() -> list[Path]:
    out: list[Path] = []
    for path in (REPO_ROOT / "app").rglob("*.py"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith(("app/modules/legacy/", "app/graphify-out/")):
            continue
        out.append(path)
    assert len(out) > 100, "the app tree looks truncated"
    return out


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def _inventory_files() -> list[str]:
    files: list[str] = []
    for root in INVENTORY_ROOTS:
        base = REPO_ROOT / root
        if not base.exists():
            continue
        paths = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file())
        for path in paths:
            rel = path.relative_to(REPO_ROOT)
            if SKIP_DIRS & set(rel.parts) or path.suffix in SKIP_SUFFIXES:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if re.search(r"splash", text, re.IGNORECASE):
                files.append(rel.as_posix())
    return files


# --- (a) the wire vocabulary stays -------------------------------------------------------------


def test_ui_source_keeps_both_legacy_tokens():
    settings = (REPO_ROOT / "ui/src/app/components/agentSettings.ts").read_text(encoding="utf-8")
    assert "export type AgentGraphSource = 'splash_links' | 'json';" in settings
    assert "return source === 'json' ? 'json' : 'splash';" in settings          # graphSourceToApi
    assert "return source === 'json' ? 'json' : 'splash_links';" in settings    # graphSourceFromApi
    live = (REPO_ROOT / "ui/src/app/components/data/liveAgent.ts").read_text(encoding="utf-8")
    assert live.count("graph_source: 'splash' | 'json';") == 1
    assert live.count("graph_source?: 'splash' | 'json';") == 1
    keys = json.loads((REPO_ROOT / "tests/contracts/legacy_ui/fixtures/persistence_keys.json").read_text("utf-8"))
    assert "splash_links" in json.dumps(keys)


def test_api_models_keep_the_splash_or_json_pattern():
    from app.modules.f2w_agent import api

    for model in (api.ChatRequest, api.ChatActionRequest, api.AgentSettingsUpdate):
        field = model.model_fields["graph_source"]
        patterns = [getattr(m, "pattern", None) for m in field.metadata]
        assert "^(splash|json)$" in patterns, model.__name__


def test_kg_rag_accepts_the_three_splash_spellings_and_json():
    from app.modules import kg_rag_api

    source = (REPO_ROOT / "app/modules/kg_rag_api.py").read_text(encoding="utf-8")
    assert 'elif source in {"splash", "splash_links", "splash-links"}:' in source
    assert 'choices=["splash", "splash_links", "splash-links", "json"]' in source
    # The three spellings stay a *fallback* path: an unreachable Splash falls back to the JSON file (no hard
    # dependency on a Splash process), exactly as tests/test_splash_fallback.py pins in detail.
    assert "json_fallback" in source
    assert kg_rag_api._splash_base_url("splash://h:1") == "http://h:1"


# --- (b)/(c) no code path requires a live Splash service ---------------------------------------


def test_no_app_module_imports_the_splash_service_or_its_libraries():
    offenders = {}
    for path in _app_py_files():
        hit = _imported_roots(path) & set(FORBIDDEN_IMPORT_ROOTS)
        if hit:
            offenders[path.relative_to(REPO_ROOT).as_posix()] = sorted(hit)
    assert offenders == {}


def test_splash_graphql_client_exists_only_in_the_two_modules_on_the_deletion_list():
    holders = set()
    for path in _app_py_files():
        text = path.read_text(encoding="utf-8")
        if "/splash_links/graphql" in text or "def _splash_graphql(" in text:
            holders.add(path.relative_to(REPO_ROOT).as_posix())
    assert holders == SPLASH_CLIENT_MODULES


def test_tiled_save_stand_in_answers_every_splash_call_the_ui_save_makes():
    from app.modules.f2w_agent import api, kg_update

    for name in SPLASH_SAVE_CALLS:
        assert callable(getattr(kg_update, name)), name                 # the legacy client (class c)
        assert callable(getattr(api._TILED_SAVE_NO_SPLASH, name)), name  # the no-op stand-in (T18D)
    assert api._TILED_SAVE_NO_SPLASH.splash_find_links() == []
    assert api._TILED_SAVE_NO_SPLASH.splash_delete_link("x") is False
    # Only the two legacy Save paths are allowed to touch the client, and both go through ``_splash``.
    source = (REPO_ROOT / "app/modules/f2w_agent/api.py").read_text(encoding="utf-8")
    assert "kg_update.splash_" not in source.replace("kg_update.load_splash_graph", "")
    assert "kg_update.load_splash_graph(timeout=5)" in source                # the splash->session sync (class c)
    assert not hasattr(kg_update, "splash_reimport")                          # the wipe is gone (T18E, Q11)


def test_every_supported_launcher_defaults_to_json_or_tiled_not_splash():
    for name in ("local", "als-dev", "als-prod"):
        profile = json.loads((REPO_ROOT / f"docker/profiles/{name}.json").read_text(encoding="utf-8"))
        assert profile["env"]["KG_RAG_GRAPH_SOURCE"] == "json", name
        assert "SPLASH" not in json.dumps(profile), name
    assert 'KG_MODE="${F2W_KG_MODE:-json}"' in (REPO_ROOT / "scripts/start_agent_backend.sh").read_text("utf-8")
    compose = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "F2W_KG_MODE: json" in compose and "KG_RAG_GRAPH_SOURCE: json" in compose
    assert "splash" not in compose.lower()
    # The Dockerfile mentions Splash only in the legacy ``agent`` target (pinned in detail by tests/images).
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    runtime_part = dockerfile.split("FROM runtime-base AS agent", 1)[0].split("# Splash/pixi for the legacy", 1)[0]
    instructions = "\n".join(line for line in runtime_part.splitlines() if not line.lstrip().startswith("#"))
    assert "splash" not in instructions.lower()


def test_packaging_carries_no_splash_dependency():
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pixi = tomllib.loads((REPO_ROOT / "pixi.toml").read_text(encoding="utf-8"))
    for blob in (json.dumps(pyproject), json.dumps(pixi)):
        assert "splash" not in blob.lower()
        for lib in ("strawberry", "pgvector", "psycopg2"):
            assert lib not in blob, lib
    # The unread ``paths.splash_links_*`` keys left config.yml with T16B; the env names stay script-only.
    config = (REPO_ROOT / "config.yml").read_text(encoding="utf-8")
    assert "splash_links_repo" not in config and "splash_links_db" not in config
    assert "../splash_links/src" not in (REPO_ROOT / "mkdocs/mkdocs.yml").read_text(encoding="utf-8")
    assert "splash_links/" not in (REPO_ROOT / ".github/workflows/build-app.yml").read_text(encoding="utf-8")


# --- the packet's inventory is complete --------------------------------------------------------


@pytest.mark.skipif(not PACKET.exists(), reason="retirement packet not present in this export")
def test_retirement_packet_names_every_code_or_config_file_that_mentions_splash():
    packet = PACKET.read_text(encoding="utf-8")
    missing = sorted(f for f in _inventory_files() if f"`{f}`" not in packet)
    assert missing == [], f"add these to docs/migration/reviews/H8/splash_retirement.md: {missing}"
    for section in ("## 2. Class (a)", "## 3. Class (b)", "## 4. Class (c)", "## 5. Class (d)",
                    "## 6. Proposed compatibility window", "## 7. Proposed deletion list"):
        assert section in packet, section
    assert "Nothing in this document approves" in packet
