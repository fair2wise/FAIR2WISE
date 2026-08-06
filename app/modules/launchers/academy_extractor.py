"""Launch TermExtractorAgent on HPC through Globus Compute and Academy.

Usage:
    python3 -m app.modules.launchers.academy_extractor \
        --data-dir /path/to/pdfs --output /path/to/terms.json

Run ``python3 -m app.modules.launchers.user_agent`` first so
``user_agent_handle.pkl`` exists in the repository working directory.
"""

import argparse
import asyncio
import logging
import os
import pickle

from academy.exchange.cloud.client import HttpExchangeFactory
from academy.handle import Handle
from academy.manager import Manager
from dotenv import load_dotenv
from globus_compute_sdk import Executor as GlobusComputeExecutor

from app.modules.term_extractor.academy_agent import TermExtractorAgent

load_dotenv()

logger = logging.getLogger(__name__)

ACADEMY_EXCHANGE_URL = "https://exchange.academy-agents.org"


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler("academy_extractor.log", mode="w")],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _load_user_agent_handle() -> Handle:
    """Load the UserAgent handle written by the user-agent launcher."""
    try:
        with open("user_agent_handle.pkl", "rb") as handle:
            user_agent_id = pickle.load(handle)
    except FileNotFoundError:
        raise SystemExit(
            "user_agent_handle.pkl not found. Start the dashboard first with "
            "`python3 -m app.modules.launchers.user_agent` and retry."
        )
    return Handle(user_agent_id)


async def run(
    data_dir: str,
    output_file: str,
    model: str,
    schema_path: str,
    backend: str,
    max_workers: int,
    log_file: str | None,
) -> None:
    user_agent_handle = _load_user_agent_handle()

    endpoint_id = os.environ["GLOBUS_COMPUTE_ENDPOINT_ID"]
    cborg_api_key = os.environ.get("CBORG_API_KEY")
    cborg_base = os.environ.get("CBORG_BASE_URL", "https://api.cborg.lbl.gov")

    logger.info("Connecting to Globus Compute endpoint: %s", endpoint_id)
    executor = GlobusComputeExecutor(endpoint_id)

    async with await Manager.from_exchange_factory(
        factory=HttpExchangeFactory(
            ACADEMY_EXCHANGE_URL,
            auth_method="globus",
        ),
        executors=executor,
    ) as manager:
        logger.info("Launching TermExtractorAgent on remote endpoint...")
        agent = TermExtractorAgent(
            user_agent_handle=user_agent_handle,
            model=model,
            schema_path=schema_path,
            output_file=output_file,
            backend=backend,
            max_workers=max_workers,
            cborg_base=cborg_base,
            cborg_api_key=cborg_api_key,
            log_file=log_file,
        )
        handle = await manager.launch(agent)
        logger.info("Agent launched. Waiting for startup...")

        logger.info("Starting extraction from: %s", data_dir)
        result = await handle.process_directory(data_dir)
        logger.info("Extraction complete: %s", result)

        term_count = await handle.get_term_count()
        logger.info("Total unique terms: %d", term_count)

        await manager.shutdown(handle, blocking=True)
        logger.info("Agent shut down cleanly.")

    print("\nResult:", result)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run term extraction on HPC via Academy"
    )
    parser.add_argument(
        "--data-dir", required=True, help="Path to PDF directory on the HPC endpoint"
    )
    parser.add_argument(
        "--output", required=True, help="Output JSON path on the HPC endpoint"
    )
    parser.add_argument("--model", default="qwen3.5:9b", help="LLM model name")
    parser.add_argument("--backend", default="ollama", choices=["cborg", "ollama"])
    parser.add_argument(
        "--schema-path",
        default="storage/schema/matkg_schema.yaml",
        help="Path to LinkML schema on the endpoint",
    )
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument(
        "--log-file", default=None, help="Remote log file path on the endpoint"
    )
    args = parser.parse_args()
    _configure_logging()

    asyncio.run(
        run(
            data_dir=args.data_dir,
            output_file=args.output,
            model=args.model,
            backend=args.backend,
            schema_path=args.schema_path,
            max_workers=args.max_workers,
            log_file=args.log_file,
        )
    )


if __name__ == "__main__":
    main()
