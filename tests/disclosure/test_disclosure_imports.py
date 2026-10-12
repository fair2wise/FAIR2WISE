"""T21B import hygiene: the security modules have no import-time side effects."""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MODULES = ("app/modules/security/content_access.py", "app/modules/security/disclosure.py")


def test_import_has_no_side_effects():
    code = (
        "import json, os, sys, threading\n"
        f"sys.path.insert(0, {str(REPO)!r})\n"
        "env = dict(os.environ); before = set(sys.modules); threads = threading.active_count()\n"
        "import app.modules.security.content_access, app.modules.security.disclosure\n"
        "new = sorted(set(sys.modules) - before)\n"
        "print(json.dumps({'new': new, 'env_changed': dict(os.environ) != env,\n"
        "                  'threads': threading.active_count() - threads}))\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True, cwd="/")
    result = json.loads(out.stdout.strip().splitlines()[-1])
    app_mods = [m for m in result["new"] if m.startswith("app")]
    allowed = ("app.modules.contracts", "app.modules.security")
    assert all(m in {"app", "app.modules"} or m.startswith(allowed) for m in app_mods), app_mods
    heavy = {"torch", "faiss", "sentence_transformers", "fitz", "openai", "langgraph", "tiled", "dotenv",
             "academy", "fastapi", "httpx", "requests"}
    assert not heavy & {m.split(".")[0] for m in result["new"]}
    assert result["env_changed"] is False and result["threads"] == 0


def test_no_module_level_authority_or_io():
    """No authority/projector instances, environment reads or file/network calls at module level."""
    banned_calls = {"open", "getenv", "load_dotenv", "SourceAccessAuthority", "DisclosureProjector",
                    "DisclosureLedger", "urlopen", "connect", "Popen", "run"}
    for rel in MODULES:
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    name = getattr(sub.func, "id", None) or getattr(sub.func, "attr", None)
                    assert name not in banned_calls, f"{rel}:{sub.lineno} calls {name} at import"
                if isinstance(sub, ast.Attribute) and sub.attr == "environ":
                    raise AssertionError(f"{rel}:{sub.lineno} reads os.environ at import")


def test_security_modules_import_only_contracts_from_app():
    for rel in MODULES:
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                top = (node.module or "").split(".")[0]
                assert top in {"contracts", "content_access"}, (rel, node.module)
            if isinstance(node, ast.ImportFrom) and not node.level:
                assert not (node.module or "").startswith(("app", "modules")), (rel, node.module)
