"""Probe the installed f2w wheel: namespace isolation, double-loading, FORGE app collision, .env."""
import importlib, os, sys
sys.path.insert(0, sys.argv[1])          # simulate FORGE checkout exposing its own `app` package
import app as forge_app                  # FORGE's app must stay the FORGE one
before = set(sys.modules)
path_before = list(sys.path)
for m in ("f2w.modules.f2w_agent.api", "f2w.modules.extract_terms", "f2w.modules.kg_rag_api",
          "f2w.modules.term_extractor.orchestrator"):
    importlib.import_module(m)
new = set(sys.modules) - before
print("app.* loaded by f2w:", sorted(n for n in new if n == "app" or n.startswith("app.")))
print("modules.* loaded:", sorted(n for n in new if n == "modules" or n.startswith("modules.")))
print("top-level scripts loaded:", sorted(n for n in new if n in {"download_pdfs", "harvest_rsoxs", "paper_finder"}))
print("FORGE app untouched:", sys.modules["app"] is forge_app and getattr(forge_app, "FORGE_APP", False))
import f2w.modules.cborg_http as c1; from f2w.modules.f2w_agent import download_agent
print("single cborg_http:", sum(1 for n in sys.modules if n.endswith("cborg_http")), c1.__file__.split("site-packages/")[1])
print("sys.path entries added by import:", [p.split("site-packages")[-1] or "<site-packages>" for p in sys.path if p not in path_before])
print("dotenv sentinel from CWD .env:", os.environ.get("F2W_DOTENV_SENTINEL"))
