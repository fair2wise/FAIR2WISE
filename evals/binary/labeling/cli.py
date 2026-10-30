"""``python -m evals.binary.label``: provisional CBORG labels for the pilot papers, review and promotion.

Commands::

    python -m evals.binary.label --list-models
    python -m evals.binary.label --plan [--paper A | --all] [--papers-root papers]
    python -m evals.binary.label --model <id> --paper A [--paper B ...] | --all
        [--papers-root papers] [--out DIR] [--judge-model ID | --settings-workdir DIR]
        [--model-under-test ID] [--pages-per-call 5] [--force]
    python -m evals.binary.label promote --paper A --reviewer "Full Name" [--out DIR] [--sheet CSV]

The CBORG key comes only from the process environment (``CBORG_API_KEY``) or from a file
named explicitly with ``F2W_DOTENV=/path/to/file``. This tool never searches for a
``.env`` and never prints the key. Without a key, ``--list-models`` and labelling refuse
to run; ``--plan`` and ``promote`` need no key and make no model call.

Role separation (H3 B4): the labelling model must differ from the judge model (``--judge-model``
or the deployment setting under ``--settings-workdir``) and from the model under test.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from evals.binary.labeling import pilot as P
from evals.binary.labeling.labeler import (
    CborgLabelClient,
    DEFAULT_PAGES_PER_CALL,
    FIGURES_MODES,
    FIGURES_MULTIMODAL,
    FIGURES_TEXT,
    LabelClient,
    calls_for,
    label_version,
)
from evals.binary.labeling.parse import ParsedPaper, file_sha256, parse_pdf, render_figure_crops
from evals.binary.labeling.review import (
    PromotionError,
    promote,
    read_sheet,
    sheet_csv,
    sheet_has_decisions,
    sheet_markdown,
)
from evals.binary.labeling.roles import RoleSeparationError, check_roles, resolve_judge_model
from evals.binary.labeling.store import LabelStore, LabelStoreError, resolve_root
from evals.binary.sanitize import sanitize

ClientFactory = Callable[[str], LabelClient]


def _load_explicit_dotenv() -> None:
    """Load only an explicitly named ``F2W_DOTENV`` file; never search for a ``.env``."""
    if os.environ.get("F2W_DOTENV") is None:
        return
    from app.modules.dotenv_entry import load_entrypoint_dotenv

    load_entrypoint_dotenv(Path(__file__))


def _require_key() -> None:
    from app.modules.cborg_http import require_cborg_api_key

    require_cborg_api_key()


def _err(msg: str) -> int:
    print("error: " + sanitize(msg, 600), file=sys.stderr)
    return 2


def _papers(args: argparse.Namespace) -> List[P.PilotPaper]:
    if args.all:
        return [P.PILOT_PAPERS[k] for k in sorted(P.PILOT_PAPERS)]
    if not args.paper:
        raise ValueError("name a pilot paper with --paper A|B|C, or use --all")
    return [P.paper(p) for p in args.paper]


def _pdf(root: Path, version: P.PilotVersion) -> Path:
    path = root / version.path
    if not path.is_file():
        raise FileNotFoundError(f"pilot PDF not found under the papers root: {version.path}")
    sha = file_sha256(path)
    if sha != version.sha256:
        raise ValueError(f"{version.path} is not the reviewed pilot PDF (sha256 {sha[:12]}..., expected"
                         f" {version.sha256[:12]}...)")
    return path


def cmd_list_models(lister: Optional[Callable[[], object]] = None) -> int:
    """Print the models the CBORG backend lists right now."""
    from app.modules.model_catalog import list_cborg_models_live

    try:
        _require_key()
    except RuntimeError as exc:
        return _err(str(exc))
    listing = (lister or list_cborg_models_live)()
    if not listing.available:
        return _err(f"the CBORG model list is unavailable: {listing.error}")
    print(f"# {len(listing.models)} models listed by CBORG at {listing.listed_at}")
    for model in listing.models:
        print(model)
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """Parse the PDFs locally and print the planned model calls (no key, no model call)."""
    root = Path(args.papers_root)
    total = 0
    for paper in _papers(args) if (args.paper or args.all) else list(P.PILOT_PAPERS.values()):
        pages: Dict[str, int] = {}
        for version in paper.versions:
            pages[version.key] = parse_pdf(_pdf(root, version)).num_pages
        n = calls_for(paper, pages, args.pages_per_call)
        total += n
        print(f"pilot {paper.pilot_id}: {n} calls ({', '.join(f'{k} {v} pages' for k, v in pages.items())})")
    print(f"total: {total} calls at {args.pages_per_call} pages per value call, no retries")
    return 0


def cmd_label(args: argparse.Namespace, client_factory: Optional[ClientFactory] = None,
              lister: Optional[Callable[[], object]] = None) -> int:
    """Label the selected pilot papers with ``args.model`` into the private label directory."""
    from app.modules.model_catalog import list_cborg_models_live

    papers = _papers(args)
    store = LabelStore(resolve_root(args.out))
    judge, judge_source = resolve_judge_model(args.judge_model, args.settings_workdir)
    check_roles(labeler=args.model, judge=judge, model_under_test=args.model_under_test)
    if client_factory is None:
        try:
            _require_key()
        except RuntimeError as exc:
            return _err(str(exc))
        client_factory = CborgLabelClient
    listing = (lister or list_cborg_models_live)()
    if not listing.available:
        return _err(f"the CBORG model list is unavailable, so the labelling model cannot be checked: {listing.error}")
    if args.model not in listing.models:
        return _err(f"model {args.model!r} is not in the CBORG model list; run --list-models")
    for paper in papers:
        if sheet_has_decisions(store.sheet_csv(paper.pilot_id)) and not args.force:
            return _err(f"pilot {paper.pilot_id} already has review decisions; pass --force to relabel (the"
                        " old sheet is kept with a timestamp)")
    store.ensure()
    client = client_factory(args.model)
    failures = 0
    for paper in papers:
        docs = []
        for version in paper.versions:
            pdf = _pdf(Path(args.papers_root), version)
            parsed = parse_pdf(pdf)
            store.write_json(store.parse_cache(parsed.source_sha256), parsed.to_cache())
            figures_mode = getattr(args, "figures", FIGURES_TEXT)
            crops = render_figure_crops(pdf, parsed.figures) if figures_mode == FIGURES_MULTIMODAL else None
            doc = label_version(paper, version, parsed, client, pages_per_call=args.pages_per_call,
                                source_file=version.path, figures_mode=figures_mode, figure_images=crops)
            doc["provenance"]["judge_model_at_labeling"] = judge
            doc["provenance"]["judge_model_source"] = judge_source
            doc["provenance"]["model_under_test"] = args.model_under_test
            store.write_json(store.label_path(paper.pilot_id, version.key), doc)
            docs.append(doc)
            failures += len(doc["missing"])
            print(f"pilot {paper.pilot_id} {version.key}: {len(doc['items'])} provisional labels from"
                  f" {len(doc['calls'])} calls; missing {len(doc['missing'])};"
                  f" quote not located {doc['counts']['quote_not_located']}")
        old = store.sheet_csv(paper.pilot_id)
        if old.is_file() and sheet_has_decisions(old):
            stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            old.rename(old.with_name(f"review_sheet.{stamp}.csv"))
        store.sheet_csv(paper.pilot_id).write_text(sheet_csv(docs), encoding="utf-8")
        store.sheet_md(paper.pilot_id).write_text(sheet_markdown(docs), encoding="utf-8")
        print(f"pilot {paper.pilot_id}: review sheet {store.sheet_csv(paper.pilot_id)}")
    print(f"labels are PROVISIONAL (status provisional_model_label) until reviewed; root {store.root}")
    return 1 if failures else 0


def cmd_promote(args: argparse.Namespace) -> int:
    """Promote reviewed labels of one paper to gold."""
    store = LabelStore(resolve_root(args.out))
    paper = P.paper(args.paper)
    docs = store.labels_for(paper.pilot_id)
    if not docs:
        return _err(f"no provisional labels for pilot {paper.pilot_id} under {store.root}")
    sheet_path = Path(args.sheet) if args.sheet else store.sheet_csv(paper.pilot_id)
    if not sheet_path.is_file():
        return _err(f"review sheet not found: {sheet_path.name}")
    parsed: Dict[str, ParsedPaper] = {}
    for doc in docs:
        sha = doc["provenance"]["source_sha256"]
        cache = store.parse_cache(sha)
        if cache.is_file():
            parsed[sha] = ParsedPaper.from_cache(store.read_json(cache))
    try:
        gold = promote(docs, read_sheet(sheet_path), reviewer=args.reviewer, parsed=parsed)
    except PromotionError as exc:
        return _err(str(exc))
    store.write_json(store.gold_path(paper.pilot_id), gold)
    c = gold["counts"]
    print(f"pilot {paper.pilot_id}: {gold['status']}; promoted {c['promoted']}, rejected {c['rejected']},"
          f" refused {c['refused']}")
    for row in gold["refused"][:50]:
        print(f"  refused {row['label_id']}: {row['reason']}")
    return 0 if gold["complete"] else 1


def build_parser() -> argparse.ArgumentParser:
    """Parser for the labelling commands (``promote`` is handled separately)."""
    p = argparse.ArgumentParser(prog="python -m evals.binary.label", description=__doc__.split("\n\n")[0])
    p.add_argument("--list-models", action="store_true", help="list the models CBORG offers now")
    p.add_argument("--plan", action="store_true", help="parse locally and print planned calls (no key)")
    p.add_argument("--model", help="labelling model id (from --list-models)")
    p.add_argument("--paper", action="append", help="pilot paper A, B or C (repeatable)")
    p.add_argument("--all", action="store_true", help="all three pilot papers")
    p.add_argument("--papers-root", default=os.environ.get("F2W_PAPERS_ROOT", "papers"),
                   help="directory holding the private pilot PDFs (default ./papers or F2W_PAPERS_ROOT)")
    p.add_argument("--out", type=Path, default=None, help="private label directory (default ~/.f2w/pilot_labels)")
    p.add_argument("--pages-per-call", type=int, default=DEFAULT_PAGES_PER_CALL)
    p.add_argument("--judge-model", default=None, help="judge model the labeller must differ from")
    p.add_argument("--settings-workdir", type=Path, default=None,
                   help="F2W workdir whose validation/judge_model.json names the judge")
    p.add_argument("--model-under-test", default=None, help="model under test the labeller must differ from")
    p.add_argument("--force", action="store_true", help="relabel a paper that already has review decisions")
    p.add_argument("--figures", choices=list(FIGURES_MODES), default=FIGURES_TEXT,
                   help="figure call mode (owner decision D1.7): 'text' (default) sends captions and the text layer"
                        " only; 'multimodal' also attaches PNG crops of the figure areas, so --model must be a"
                        " vision-capable CBORG model")
    return p


def build_promote_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m evals.binary.label promote",
                                description="Promote reviewed labels of one pilot paper to gold.")
    p.add_argument("--paper", required=True)
    p.add_argument("--reviewer", required=True, help="the person who reviewed the sheet")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--sheet", default=None, help="filled review sheet (default <out>/<paper>/review_sheet.csv)")
    return p


def main(argv: Optional[Sequence[str]] = None, *, client_factory: Optional[ClientFactory] = None,
         lister: Optional[Callable[[], object]] = None) -> int:
    """CLI entry point (``client_factory`` and ``lister`` are for tests and dry runs)."""
    args_list = list(sys.argv[1:] if argv is None else argv)
    _load_explicit_dotenv()
    try:
        if args_list[:1] == ["promote"]:
            return cmd_promote(build_promote_parser().parse_args(args_list[1:]))
        args = build_parser().parse_args(args_list)
        if args.list_models:
            return cmd_list_models(lister)
        if args.plan:
            return cmd_plan(args)
        if not args.model:
            return _err("--model is required to label (see --list-models), or use --plan / --list-models")
        return cmd_label(args, client_factory=client_factory, lister=lister)
    except (LabelStoreError, RoleSeparationError, FileNotFoundError, KeyError, ValueError) as exc:
        return _err(str(exc))


__all__ = ("build_parser", "build_promote_parser", "main")
