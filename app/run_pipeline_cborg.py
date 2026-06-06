"""
run_pipeline.py -- FAIR2WISE checkpoint evaluation with incremental JSON outputs

Organization:
  • Papers divided into 4 folders (25 papers each)
  • Checkpoints build incrementally per model:
    - Checkpoint 25: run model on paper_25
    - Checkpoint 50: copy cp25 terms JSON, then process paper_50
    - Checkpoint 75: copy cp50 terms JSON, then process paper_75
    - Checkpoint 100: copy cp75 terms JSON, then process paper_100
  • Each run produces new unique JSONs with timestamp suffixes
"""

import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
from pathlib import Path
from typing import List
import shutil
import tempfile
import os

from modules.extract_terms import run_extraction
from modules.json2kg import convert_terms_to_graph

# -----------------------------------------------------------------------------
# Configuration for evaluation
# -----------------------------------------------------------------------------
load_dotenv()

EVALUATION_MODELS = [
    "google/gemini-flash-lite",
]

# Checkpoint folders (incremental buckets of 25 papers)
PAPER_FOLDERS = {
    25: ["paper_25"],
    50: ["paper_50"],
    75: ["paper_75"],
    100: ["paper_100"],
}

DEFAULT_PDF_ROOT = Path("polymer_papers")
DEFAULT_OLLAMA_URL = "http://localhost:11434"  # unused for cborg but harmless
DEFAULT_CBORG_BASE = os.environ.get("CBORG_BASE_URL", "https://api.cborg.lbl.gov")
print("Using CBORG_BASE_URL =", DEFAULT_CBORG_BASE)
# -----------------------------------------------------------------------------
# Helper: Organize papers into 4 folders of 25 each
# -----------------------------------------------------------------------------
def organize_papers_into_folders(source_dir: Path, output_root: Path, papers_per_folder: int = 25) -> None:
    """Organize PDFs from source_dir into 4 folders of 25 papers each."""
    all_pdfs = sorted(list(source_dir.glob("*.pdf")))
    total_pdfs = len(all_pdfs)
    logging.info(f"Found {total_pdfs} PDFs to organize")

    for i in range(0, min(100, total_pdfs), papers_per_folder):
        folder_name = f"paper_{i + papers_per_folder}"
        folder_path = output_root / folder_name
        folder_path.mkdir(parents=True, exist_ok=True)

        start_idx = i
        end_idx = min(i + papers_per_folder, total_pdfs)

        for pdf in all_pdfs[start_idx:end_idx]:
            shutil.copy2(pdf, folder_path / pdf.name)

        logging.info(f"Created {folder_name} with {end_idx - start_idx} papers")


# -----------------------------------------------------------------------------
# Core pipeline runner (incremental, per model)
# -----------------------------------------------------------------------------
def run_checkpoint_pipeline(
    pdf_root: Path,
    models: List[str],
    dry_run: bool = False
) -> None:
    """Run FAIR2WISE pipeline incrementally per model, retaining all JSONs with unique filenames."""

    cborg_api_key = os.environ.get("CBORG_API_KEY")
    if not cborg_api_key:
        raise RuntimeError("CBORG_API_KEY is not set in the environment")

    for model in models:
        logging.info("=" * 80)
        logging.info(f"Starting incremental pipeline for model={model}")

        prev_terms_json = None  # pointer to previous checkpoint JSON

        for checkpoint, new_folders in PAPER_FOLDERS.items():
            # Collect new PDFs for this checkpoint
            new_pdfs = []
            for folder_name in new_folders:
                folder_path = pdf_root / folder_name
                if not folder_path.exists():
                    logging.error(f"Missing folder {folder_name} for checkpoint {checkpoint}")
                    continue
                new_pdfs.extend(list(folder_path.glob("*.pdf")))

            # Build unique filenames with timestamp
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            safe_model = model.replace(":", "_").replace("/", "_")  # make FS-safe
            terms_json = Path(f"./storage/terminology/extracted_terms_{safe_model}_{checkpoint}_{timestamp}.json")
            graph_json = Path(f"./storage/kg/matkg_{safe_model}_{checkpoint}_{timestamp}.json")

            if dry_run:
                print(f"[DRY-RUN] Checkpoint={checkpoint}, model={model}")
                print(f"  → New folders: {new_folders}")
                print(f"  → New papers: {len(new_pdfs)}")
                print(f"  → Prev terms: {prev_terms_json}")
                print(f"  → Terms file: {terms_json}")
                print(f"  → Graph file: {graph_json}")
                continue

            logging.info(f"Checkpoint {checkpoint}, model={model}")
            logging.info(f"→ Extracting {len(new_pdfs)} new papers from {new_folders}")

            # Step 1: If there’s a previous JSON, copy it forward
            if prev_terms_json and prev_terms_json.exists():
                shutil.copy2(prev_terms_json, terms_json)

            # Step 2: Extract new papers into the terms_json using CBORG
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = Path(temp_dir)
                for pdf in new_pdfs:
                    shutil.copy2(pdf, temp_path / pdf.name)

                run_extraction(
                    temp_path,
                    terms_json,
                    model=model,
                    backend="cborg",
                    cborg_base=DEFAULT_CBORG_BASE,
                    cborg_api_key=cborg_api_key,
                    # the next param is ignored for cborg but fine to pass:
                    ollama_url=DEFAULT_OLLAMA_URL,
                    schema_path="storage/schema/matkg_schema.yaml",
                    temperature=0.0,
                    context_length=50,
                    max_workers=1
                )

            # Step 3: Generate KG from cumulative JSON
            graph = convert_terms_to_graph(terms_json, graph_json)
            logging.info(
                "✓ Finished cp=%d, model=%s → %d nodes, %d edges",
                checkpoint, model, len(graph["things"]), len(graph["associations"])
            )

            # Update pointer for next checkpoint
            prev_terms_json = terms_json


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run FAIR2WISE checkpoint evaluation")
    parser.add_argument("--pdf-root", type=Path, default=DEFAULT_PDF_ROOT)
    parser.add_argument("--source-dir", type=Path, help="Source directory with all PDFs (for --organize)")
    parser.add_argument("--organize", action="store_true", help="Organize PDFs into 4 folders of 25 papers each")
    parser.add_argument("--models", nargs="+", default=EVALUATION_MODELS)
    parser.add_argument("--dry-run", action="store_true", help="Print planned runs without executing")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    if args.organize:
        if not args.source_dir:
            raise ValueError("--source-dir required when using --organize")
        if not args.source_dir.exists():
            raise FileNotFoundError(f"Source directory not found: {args.source_dir}")
        organize_papers_into_folders(args.source_dir, args.pdf_root)
        logging.info("Paper organization complete!")
        raise SystemExit(0)

    if not args.pdf_root.exists():
        raise FileNotFoundError(f"PDF root directory not found: {args.pdf_root}")

    run_checkpoint_pipeline(pdf_root=args.pdf_root, models=args.models, dry_run=args.dry_run)
