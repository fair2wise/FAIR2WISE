"""
Launch the TermExtractorAgent on NERSC Perlmutter via Globus Compute + Academy.

Usage:
    python scripts/run_academy_extractor.py --data-dir /path/to/pdfs --output /path/to/terms.json

The agent code runs remotely on the Globus Compute endpoint; this script is
the local client that submits, monitors, and retrieves results.
"""

import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from globus_compute_sdk import Executor as GlobusComputeExecutor

from academy.exchange.cloud.client import HttpExchangeFactory
from academy.manager import Manager

# The agent class must be importable on the remote endpoint too.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.modules.term_extractor.academy_agent import TermExtractorAgent

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler("academy_extractor.log", mode="w")],
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

ACADEMY_EXCHANGE_URL = "https://exchange.academy-agents.org"


async def run(
    data_dir: str,
    output_file: str,
    model: str,
    schema_path: str,
    backend: str,
    max_workers: int,
    log_file: str | None,
) -> None:
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
    parser = argparse.ArgumentParser(description="Run term extraction on NERSC via Academy")
    parser.add_argument("--data-dir", required=True, help="Path to PDF directory on NERSC")
    parser.add_argument("--output", required=True, help="Output JSON path on NERSC")
    parser.add_argument("--model", default="qwen3.5:9b", help="LLM model name")
    parser.add_argument("--backend", default="ollama", choices=["cborg", "ollama"])
    parser.add_argument("--schema-path", default="/pscratch/sd/b/bzheng2/FAIR2WISE/storage/schema/matkg_schema.yaml", help="Path to LinkML schema")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--log-file", default="/pscratch/sd/b/bzheng2/FAIR2WISE/f2w_academy.log", help="Remote log file path (written on the NERSC endpoint)")
    args = parser.parse_args()

    asyncio.run(run(
        data_dir=args.data_dir,
        output_file=args.output,
        model=args.model,
        backend=args.backend,
        schema_path=args.schema_path,
        max_workers=args.max_workers,
        log_file=args.log_file,
    ))


if __name__ == "__main__":
    main()
