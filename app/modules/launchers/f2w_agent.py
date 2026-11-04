#!/usr/bin/env python3
"""Launch the FAIR2WISE orchestrated KG-RAG CLI or API.

Examples:
    python3 -m app.modules.launchers.f2w_agent status
    python3 -m app.modules.launchers.f2w_agent --kg-mode json chat
"""

import sys

if __name__ == "__main__":
    # Load .env before the CLI imports coordinator/kg_rag_api, which read configuration at import.
    from ..dotenv_entry import load_entrypoint_dotenv

    load_entrypoint_dotenv(__file__)

from ..f2w_agent.cli import (  # noqa: E402 - module-level import placed after the optional-dependency / path setup above
    main,
)

if __name__ == "__main__":
    sys.exit(main())
