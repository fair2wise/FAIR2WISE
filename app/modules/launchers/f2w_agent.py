#!/usr/bin/env python3
"""Launch the FAIR2WISE orchestrated KG-RAG CLI or API.

Examples:
    python3 -m app.modules.launchers.f2w_agent status
    python3 -m app.modules.launchers.f2w_agent --kg-mode json chat
"""

import sys

from app.modules.f2w_agent.cli import main

if __name__ == "__main__":
    sys.exit(main())
