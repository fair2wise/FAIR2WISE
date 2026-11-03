#!/usr/bin/env python
"""Launch the FAIR2WISE term-extraction agent.

Console script: ``f2w-run`` (``scripts/run.py`` stays as a launch facade).

Usage:
  python3 scripts/run.py --pdf-dir papers --output storage/terminology/terms.json
  python3 scripts/run.py --pdf-dir papers --backend ollama --model llama3
  python3 scripts/run.py --pdf-dir papers --dry-run
"""

import argparse
import logging
import os
from pathlib import Path
import sys


def parse_args() -> argparse.Namespace:
    """Parse the command line (no secrets in argv: ``CBORG_API_KEY`` comes from the environment)."""
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
    p.add_argument("--merge-top-k", type=int, default=int(os.environ.get("F2W_MERGE_TOP_K", "12")),
                   help="Nearest existing terms sent to the LLM for merge verification")
    p.add_argument("--merge-embed-model", default=os.environ.get("F2W_MERGE_EMBED_MODEL", "paraphrase-MiniLM-L6-v2"),
                   help="SentenceTransformer model for merge candidate retrieval")
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
    """Run the extraction agent (or validate the configuration with ``--dry-run``)."""
    args = parse_args()

    args.log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(args.log_file, mode="w", encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logging.root.setLevel(getattr(logging, args.log_level))
    logging.root.addHandler(handler)
    console = logging.StreamHandler()
    console.setFormatter(handler.formatter)
    logging.root.addHandler(console)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    log = logging.getLogger(__name__)
    log.info("Logging to %s", args.log_file)

    # langchain-openai may inject a keepalive transport that drops the IPv6
    # local_address bind used for CBorg. Keep our httpx client intact.
    os.environ.setdefault("LANGCHAIN_OPENAI_TCP_KEEPALIVE", "0")
    # Cursor agent shells inject HTTP(S)_PROXY=127.0.0.1:xxxxx. That proxy dies
    # after double-fork and cannot reach api.cborg.lbl.gov over IPv6.
    for key in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        os.environ.pop(key, None)

    # --- Validate inputs ---
    if not args.pdf_dir.exists():
        log.error("PDF directory not found: %s", args.pdf_dir)
        sys.exit(1)

    pdfs = sorted(p for p in args.pdf_dir.rglob("*.pdf") if p.is_file())
    if not pdfs:
        log.error("No PDFs found in %s (searched recursively)", args.pdf_dir)
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
    if args.backend == "cborg":
        log.info(
            "CBORG IP : family=%s force_ipv6=%s (allowlist host IPv6 2601:647:c103:b76d:b8b5:513f:11ea:18cc)",
            os.environ.get("CBORG_IP_FAMILY", "auto"),
            os.environ.get("CBORG_FORCE_IPV6", ""),
        )
    log.info("PDFs     : %d files in %s", len(pdfs), args.pdf_dir)
    log.info("Output   : %s", args.output)
    log.info("Workers  : %d", args.workers)
    log.info("Merge k  : %d", args.merge_top_k)
    log.info("Merge emb: %s", args.merge_embed_model)
    log.info("Schema   : %s", args.schema)
    log.info("ChEBI    : %s", chebi_path or "disabled")

    if args.dry_run:
        log.info("Dry-run complete — exiting without processing.")
        return

    # --- Run ---
    from ..term_extractor import run_extraction

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
        merge_top_k=args.merge_top_k,
        merge_embed_model=args.merge_embed_model,
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


def cli() -> None:
    """Console-script entry point (``f2w-run``).

    Entry point only (AUD-02): the checkout .env, or the file named by F2W_DOTENV, is loaded here before
    main() reads CBORG_API_KEY; importing this module loads nothing.
    """
    from ..dotenv_entry import load_entrypoint_dotenv

    load_entrypoint_dotenv(__file__)
    main()


if __name__ == "__main__":
    cli()
