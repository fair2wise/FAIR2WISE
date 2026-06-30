#!/usr/bin/env python3
"""Entry point for the FAIR2WISE 3-agent KG-RAG pipeline.

Three Academy agents (retrieval, download, extractor) cooperate so that a user
question is answered from the KG when evidence suffices, and otherwise triggers
download -> extract -> KG update -> re-query until it does.

Examples:
    python f2w_agent.py status
    python f2w_agent.py --graph storage/kg/matkg_xray_papers_cborg_chat.json \
        --seed-terms storage/terminology/extracted_terms_xray_papers_cborg_chat.json \
        ask "What is find_scattering_peaks used for?"
    python f2w_agent.py --kg-mode json --max-papers 2 chat
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.modules.f2w_agent.cli import main

if __name__ == "__main__":
    sys.exit(main())
