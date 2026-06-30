#!/usr/bin/env python
"""
run.py — launch the FAIR2WISE term-extraction agent.

Usage:
  python run.py --pdf-dir polymer_papers --output storage/terminology/terms.json
  python run.py --pdf-dir polymer_papers --backend ollama --model llama3
  python run.py --pdf-dir polymer_papers --dry-run
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="FAIR2WISE term-extraction agent",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--pdf-dir", type=Path, default=Path("papers_test"),
                   help="Directory containing PDF files to process")
    p.add_argument("--output", type=Path, default=Path("storage/terminology/terms_test.json"),
                   help="Output JSON file for extracted terms")
    p.add_argument("--model", default="amazon/gpt-oss-20b",
                   help="LLM model name")
    p.add_argument("--backend", choices=["cborg", "ollama"], default="cborg",
                   help="LLM backend")
    p.add_argument("--workers", type=int, default=4,
                   help="Parallel page-processing workers")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--schema", default="storage/schema/matkg_schema.yaml",
                   help="Path to LinkML schema file")
    p.add_argument("--chebi", default="storage/ontologies/chebi.obo",
                   help="Path to ChEBI .obo file (optional)")
    p.add_argument("--ollama-url", default="http://localhost:11434")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate config and print settings without running")
    p.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING"], default="INFO")
    p.add_argument("--log-file", type=Path, default=Path("logs/run.log"),
                   help="File to write logs to")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    args.log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(args.log_file, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logging.root.setLevel(getattr(logging, args.log_level))
    logging.root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    log = logging.getLogger(__name__)
    log.info("Logging to %s", args.log_file)

    # --- Validate inputs ---
    if not args.pdf_dir.exists():
        log.error("PDF directory not found: %s", args.pdf_dir)
        sys.exit(1)

    pdfs = list(args.pdf_dir.glob("*.pdf"))
    if not pdfs:
        log.error("No PDFs found in %s", args.pdf_dir)
        sys.exit(1)

    cborg_api_key = os.environ.get("CBORG_API_KEY")
    if args.backend == "cborg" and not cborg_api_key:
        log.error("CBORG backend requires CBORG_API_KEY to be set")
        sys.exit(1)

    chebi_path = args.chebi if Path(args.chebi).exists() else None
    if not chebi_path:
        log.warning("ChEBI file not found at %s — chemical lookups disabled", args.chebi)

    # --- Print config ---
    log.info("Backend  : %s", args.backend)
    log.info("Model    : %s", args.model)
    log.info("PDFs     : %d files in %s", len(pdfs), args.pdf_dir)
    log.info("Output   : %s", args.output)
    log.info("Workers  : %d", args.workers)
    log.info("Schema   : %s", args.schema)
    log.info("ChEBI    : %s", chebi_path or "disabled")

    if args.dry_run:
        log.info("Dry-run complete — exiting without processing.")
        return

    # --- Run ---
    sys.path.insert(0, str(Path(__file__).parent / "app"))
    from modules.term_extractor import run_extraction

    result = run_extraction(
        pdf_dir=args.pdf_dir,
        output_json=args.output,
        model=args.model,
        backend=args.backend,
        cborg_api_key=cborg_api_key,
        cborg_base=os.environ.get("CBORG_BASE_URL", "https://api.cborg.lbl.gov"),
        ollama_url=args.ollama_url,
        schema_path=args.schema,
        temperature=args.temperature,
        max_workers=args.workers,
        chebi_obo_path=chebi_path,
    )

    if result["status"] == "success":
        log.info("Done — %d terms extracted from %d/%d pages across %d files",
                 result["unique_terms"],
                 result["processed_pages_with_terms"],
                 result["processed_pages_total"],
                 result["processed_files"])
        log.info("Output: %s", result["output_file"])
    else:
        log.error("Pipeline failed: %s", result.get("message"))
        sys.exit(1)


if __name__ == "__main__":
    main()
