"""Ingest orchestration and command line for the BL11.0.1.2 knowledge graph (``scripts/ingest_bl1101.py``)."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from app.modules import json2kg
from app.modules.cli_output import output

from .common import LOGGER, sha256_bytes, sha256_file, utc_now
from .fetch import fetch_url, git_sha, sync_repo
from .parse_als import parse_als_contact_people, parse_als_page
from .parse_blueprint import parse_bcs2sim_beam_path, parse_blueprint_html, resolve_beam_path_order
from .paths import (
    BLUEPRINT_PATH,
    KG_DIR,
    PAGES_DIR,
    READMES_DIR,
    REPO_ROOT,
    REPOS_DIR,
    SCHEMA_PATH,
    TERMS_DIR,
    WORK_DIR,
)
from .snapshot import next_snapshot_version, run_motor_link_promote, snapshot_paths
from .specs import ALS_PAGE_URL, BEAMLINE_ID, INGEST_VERSION, NAMESPACE, REPO_SPECS, SCHEMA_VERSION, RepoSpec
from .symbols import (
    expand_globs,
    extract_markdown_software,
    extract_python_symbols,
    extract_ts_fences,
    parse_compose_services,
)
from .terms import (
    _rel,
    apply_motor_stage_map_to_terms,
    attach_repo_terms,
    beamline_seed,
    load_motor_stage_map,
    merge_terms,
    software_catalog,
    sort_graph,
    terms_from_als,
    terms_from_blueprint,
    terms_from_people,
)


def ingest_records(
    *,
    als: dict[str, Any],
    blueprint: dict[str, Any],
    als_source: str = "als.lbl.gov/beamlines/11-0-1-2",
    blueprint_source: str = "blueprint_bl11012_version09092026.html",
    repo_payloads: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build terms + snippets from already-parsed sources (network-free)."""
    blueprint = dict(blueprint)
    if not blueprint.get("beam_path"):
        for payload in repo_payloads or []:
            spec = payload.get("spec")
            if spec is None or getattr(spec, "name", "") != "bcs2sim-ophyd":
                continue
            extra = parse_bcs2sim_beam_path(payload.get("root"))
            if extra:
                blueprint["beam_path"] = resolve_beam_path_order(
                    svg_names=blueprint.get("beam_path_svg") or [],
                    caption_names=blueprint.get("beam_path_caption") or extra,
                    bcs2sim_names=extra,
                )
                break
    terms = [
        beamline_seed(als, blueprint, als_source, blueprint_source),
        *software_catalog(),
        *terms_from_als(als, als_source),
        *terms_from_blueprint(blueprint, blueprint_source),
        *terms_from_people(als, blueprint, als_source, blueprint_source),
    ]
    snippets: list[dict[str, Any]] = []
    for payload in repo_payloads or []:
        extra_terms, extra_snips = attach_repo_terms(
            payload["spec"],
            payload.get("root") or Path(),
            payload.get("sha") or "",
            payload.get("symbols") or [],
            payload.get("snippets") or [],
            payload.get("compose") or [],
            payload.get("markdown") or [],
        )
        terms.extend(extra_terms)
        snippets.extend(extra_snips or payload.get("snippets") or [])

    # Wire motors/detectors onto the beamline from blueprint categories.
    motor_ids = [t["id"] for t in terms if t.get("category") == "Motor"]
    detector_ids = [t["id"] for t in terms if t.get("category") == "Detector"]
    plan_ids = [t["id"] for t in terms if t.get("category") == "BlueskyPlan"]
    beam = next(t for t in terms if t["id"] == BEAMLINE_ID)
    for mid in motor_ids:
        beam["relations"].append(_rel(BEAMLINE_ID, "hasMotor", mid, blueprint_source))
    for did in detector_ids:
        beam["relations"].append(_rel(BEAMLINE_ID, "hasDetector", did, blueprint_source))
    for pid in plan_ids:
        beam["relations"].append(_rel(BEAMLINE_ID, "implementsPlan", pid, "bluesky plans"))

    merged = merge_terms([terms])
    apply_motor_stage_map_to_terms(merged, load_motor_stage_map())
    return {
        "terms": merged,
        "code_snippets": snippets,
    }


def records_to_graph(records: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    """Convert term records to a graph with ``json2kg``.

    Args:
        records: Dict with ``terms`` and optionally ``code_snippets``.
        metadata: Metadata dict that replaces the graph's ``metadata``.

    Returns:
        The sorted graph dict.
    """
    graph = json2kg.build_graph(
        records["terms"],
        code_snippets=records.get("code_snippets") or [],
        default_id_prefix=NAMESPACE,
        strict_snippets=False,
    )
    graph = sort_graph(graph)
    graph["metadata"] = metadata
    return graph


def ingest_fixture(html: str, python_files: Sequence[Path]) -> dict[str, Any]:
    """Tiny deterministic ingest used by unit tests."""
    als = parse_als_page(html) if "<table" in html and "Minimum energy" in html else {
        "title": "Beamline 11.0.1.2",
        "facts": {
            "Minimum energy (eV)": "165",
            "Maximum energy (eV)": "1500",
            "Source": "EPU5",
            "Primary contact(s)": "Cheng Wang; Thomas Ferron",
        },
        "techniques": ["RSoXS"],
        "paragraphs": [],
        "text": "",
        "people": parse_als_contact_people(
            {"Primary contact(s)": "Cheng Wang; Thomas Ferron"},
            source=ALS_PAGE_URL,
        ),
    }
    blueprint = parse_blueprint_html(html)
    payloads = []
    for path in python_files:
        symbols, snippets = extract_python_symbols(path, repo_name="fixture", commit="test")
        payloads.append(
            {
                "spec": RepoSpec(name="fixture", url="https://example.invalid/fixture", mode="full"),
                "root": path.parent,
                "sha": "test",
                "symbols": symbols,
                "snippets": snippets,
                "compose": [],
                "markdown": [],
            }
        )
    return ingest_records(als=als, blueprint=blueprint, repo_payloads=payloads)


def _clear_work(from_scratch: bool) -> None:
    if from_scratch and WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    if from_scratch and PAGES_DIR.exists():
        shutil.rmtree(PAGES_DIR)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    READMES_DIR.mkdir(parents=True, exist_ok=True)


def _ingest_repos(offline: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    landed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    payloads: list[dict[str, Any]] = []
    for spec in REPO_SPECS:
        dest = (READMES_DIR if spec.readme_only else REPOS_DIR) / spec.name
        if offline and not dest.exists():
            skipped.append({"name": spec.name, "url": spec.url, "status": "skipped", "reason": "offline, no cache"})
            LOGGER.warning("Offline: no cache for %s", spec.name)
            continue
        info = {"name": spec.name, "url": spec.url, "status": "ok", "sha": "", "mode": spec.mode, "path": str(dest)}
        if not offline:
            info = sync_repo(spec, dest)
        elif (dest / ".git").exists():
            try:
                info["sha"] = git_sha(dest)
                info["path"] = str(dest.relative_to(REPO_ROOT))
            except (subprocess.CalledProcessError, OSError) as exc:
                info = {"name": spec.name, "url": spec.url, "status": "skipped", "reason": str(exc), "mode": spec.mode}
        if info.get("status") != "ok":
            skipped.append(info)
            continue
        landed.append(info)
        symbols: list[dict[str, Any]] = []
        snippets: list[dict[str, Any]] = []
        for py in expand_globs(dest, spec.python_globs):
            py_symbols, py_snips = extract_python_symbols(py, repo_name=spec.name, commit=info.get("sha") or "")
            for item in py_symbols:
                if "file" in item:
                    try:
                        item["file"] = str(py.relative_to(dest))
                    except ValueError:
                        LOGGER.debug("symbol file %s is outside %s; path left as extracted", py, dest)
            for snip in py_snips:
                try:
                    snip["source_file_path"] = str(py.relative_to(dest))
                except ValueError:
                    LOGGER.debug("snippet file %s is outside %s; path left as extracted", py, dest)
            symbols.extend(py_symbols)
            snippets.extend(py_snips)
        compose = []
        for yml in expand_globs(dest, spec.compose_globs):
            compose.extend(parse_compose_services(yml))
        markdown: list[tuple[str, str]] = []
        for md in expand_globs(dest, spec.md_globs):
            markdown.append((str(md.relative_to(dest)), extract_markdown_software(md, spec.name)))
            if spec.name == "bl11012-finch" and md.name.lower().startswith("readme"):
                snippets.extend(extract_ts_fences(md, spec.name, info.get("sha") or ""))
        payloads.append(
            {
                "spec": spec,
                "root": dest,
                "sha": info.get("sha") or "",
                "symbols": symbols,
                "snippets": snippets,
                "compose": compose,
                "markdown": markdown,
            }
        )
    return landed, skipped, payloads


def run_ingest(*, from_scratch: bool = False, offline: bool = False) -> dict[str, Any]:
    """Run the full BL 11.0.1.2 ingest and write the next snapshot.

    Fetches (or reads from cache) the ALS page, parses the blueprint, syncs the
    configured repos, builds terms, and writes the extracted-terms file and the
    graph as the next unused version.

    Args:
        from_scratch: Delete the work and page-snapshot directories first.
        offline: Do not fetch or clone. Cached pages and repos are used when
            present, otherwise the source is skipped. A missing ALS page falls back
            to built-in ALS facts.

    Returns:
        Dict with ``kg_path``, ``terms_path``, ``version``, ``nodes``, ``edges``,
        ``sources_landed``, ``sources_skipped`` and the ``graph``.

    Raises:
        FileNotFoundError: If the blueprint file is missing.
    """
    _clear_work(from_scratch)
    sources_landed: list[dict[str, Any]] = []
    sources_skipped: list[dict[str, Any]] = []

    als_snapshot: dict[str, Any]
    als_html = ""
    als_dest = PAGES_DIR / "als_11-0-1-2.html"
    if offline and als_dest.exists():
        als_html = als_dest.read_text(encoding="utf-8", errors="replace")
        als_snapshot = {
            "url": ALS_PAGE_URL,
            "status": "ok",
            "fetched_at": datetime.fromtimestamp(als_dest.stat().st_mtime, UTC).date().isoformat(),
            "path": str(als_dest.relative_to(REPO_ROOT)),
            "sha256": sha256_file(als_dest),
            "offline": True,
        }
    elif offline:
        als_snapshot = {"url": ALS_PAGE_URL, "status": "skipped", "reason": "offline, no cached page"}
        LOGGER.warning("Offline: ALS page missing; continuing with blueprint + hardcoded ALS facts")
    else:
        als_snapshot = fetch_url(ALS_PAGE_URL, als_dest)
        if als_snapshot.get("status") == "ok":
            als_html = als_dest.read_text(encoding="utf-8", errors="replace")
    if als_snapshot.get("status") == "ok":
        sources_landed.append({"name": "als_public_page", **als_snapshot})
        als = parse_als_page(als_html)
    else:
        sources_skipped.append({"name": "als_public_page", **als_snapshot})
        als = {
            "title": "Beamline 11.0.1.2",
            "facts": {
                "Current status": "Operational; open to general users",
                "Source": "EPU5",
                "Minimum energy (eV)": "165",
                "Maximum energy (eV)": "1,500",
                "Technique category": "XAS; SAXS; WAXS; Grazing-incidence SAXS/WAXS; Magnetic scattering; Resonant scattering",
                "Additional notes": "Polarization is user selectable; linear polarization continuously variable from horizontal to vertical; left and right elliptical (or circular) polarization.",
                "Primary contact(s)": "Cheng Wang; Thomas Ferron",
            },
            "techniques": [],
            "paragraphs": [],
            "text": "",
            "people": parse_als_contact_people(
                {"Primary contact(s)": "Cheng Wang; Thomas Ferron"},
                source=ALS_PAGE_URL,
            ),
        }

    if not BLUEPRINT_PATH.exists():
        raise FileNotFoundError(f"Blueprint not found: {BLUEPRINT_PATH}")
    bp_html = BLUEPRINT_PATH.read_text(encoding="utf-8")
    blueprint = parse_blueprint_html(bp_html)
    bp_meta = {
        "name": "gabe_blueprint",
        "path": str(BLUEPRINT_PATH.relative_to(REPO_ROOT)),
        "status": "ok",
        "sha256": sha256_bytes(bp_html.encode("utf-8")),
        "date": (blueprint.get("header") or {}).get("Date") or "2026-09-09",
    }
    sources_landed.append(bp_meta)

    repo_landed, repo_skipped, payloads = _ingest_repos(offline=offline)
    sources_landed.extend(repo_landed)
    sources_skipped.extend(repo_skipped)

    records = ingest_records(
        als=als,
        blueprint=blueprint,
        als_source="als.lbl.gov/beamlines/11-0-1-2",
        blueprint_source=BLUEPRINT_PATH.name,
        repo_payloads=payloads,
    )

    version = next_snapshot_version(KG_DIR)
    kg_path, terms_path = snapshot_paths(version)
    git_shas = {item["name"]: item.get("sha") for item in repo_landed if item.get("sha")}
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "extraction_version": INGEST_VERSION,
        "ingest_version": INGEST_VERSION,
        "id_prefix": NAMESPACE,
        "namespace": NAMESPACE,
        "kg": "bl1101",
        "schema": str(SCHEMA_PATH.relative_to(REPO_ROOT)),
        "corpus_revision": {
            "git_shas": git_shas,
            "page_snapshots": {
                "als_public": {k: als_snapshot.get(k) for k in ("url", "fetched_at", "sha256", "status", "reason", "path")},
                "blueprint": {
                    "path": bp_meta["path"],
                    "sha256": bp_meta["sha256"],
                    "date": bp_meta["date"],
                },
            },
        },
        "sources_landed": sources_landed,
        "sources_skipped": sources_skipped,
        "graph_snapshot": {
            "name": f"matkg_bl1101_v{version}",
            "version": version,
            "written_at": utc_now(),
        },
    }
    records_out = {"metadata": metadata, "terms": records["terms"], "code_snippets": records["code_snippets"]}
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    work_terms = WORK_DIR / "extracted_terms.json"
    work_terms.write_text(json.dumps(records_out, indent=2, ensure_ascii=False), encoding="utf-8")
    TERMS_DIR.mkdir(parents=True, exist_ok=True)
    KG_DIR.mkdir(parents=True, exist_ok=True)
    terms_path.write_text(json.dumps(records_out, indent=2, ensure_ascii=False), encoding="utf-8")

    graph = records_to_graph(records, metadata)
    tmp = kg_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    digest = sha256_file(tmp)
    graph["metadata"]["graph_snapshot"].update(
        {
            "sha256": digest,
            "nodes": len(graph["things"]),
            "edges": len(graph["associations"]),
            "path": str(kg_path.relative_to(REPO_ROOT)),
        }
    )
    tmp.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(kg_path)

    LOGGER.info(
        "Wrote %s (%d nodes, %d edges); terms %s; skipped %d sources",
        kg_path,
        len(graph["things"]),
        len(graph["associations"]),
        terms_path,
        len(sources_skipped),
    )
    return {
        "kg_path": kg_path,
        "terms_path": terms_path,
        "version": version,
        "nodes": len(graph["things"]),
        "edges": len(graph["associations"]),
        "sources_landed": sources_landed,
        "sources_skipped": sources_skipped,
        "graph": graph,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the ingest CLI.

    Args:
        argv: Argument list. Defaults to ``sys.argv[1:]``.

    Returns:
        The parsed namespace.
    """
    parser = argparse.ArgumentParser(description="Ingest BL 11.0.1.2 ops knowledge graph")
    parser.add_argument(
        "--from-scratch",
        action="store_true",
        help="Rebuild working files (page snapshots + work dir) then write the next vN snapshot",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Do not fetch the ALS page or git-clone; use .cache/bl1101 if present",
    )
    parser.add_argument(
        "--from-graph",
        type=Path,
        help="JSON-first: copy an existing ops KG and apply the motor-stage map (no network)",
    )
    parser.add_argument(
        "--snapshot",
        type=int,
        help="Write this vN (default: next unused). --from-graph never overwrites the source file",
    )
    parser.add_argument(
        "--motor-stage-map",
        type=Path,
        default=None,
        help="Override storage/schema/bl1101_motor_stage_map.yaml",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ingest CLI: a full ingest, or a motor-stage promote with ``--from-graph``.

    Args:
        argv: Argument list. Defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code, always ``0``; failures surface as exceptions.
    """
    args = parse_args(argv)
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    if args.from_graph:
        result = run_motor_link_promote(
            source_kg=args.from_graph,
            version=args.snapshot,
            map_path=args.motor_stage_map,
        )
        output.info(
            f"bl1101 v{result['version']}: {result['nodes']} nodes, {result['edges']} edges → {result['kg_path']}"
        )
        output.info(
            "motor links: "
            f"+{result['stats']['linked']} part_of, "
            f"+{result['stats']['aliased']} alias related_to"
        )
        return 0
    result = run_ingest(from_scratch=args.from_scratch, offline=args.offline)
    output.info(
        f"bl1101 v{result['version']}: {result['nodes']} nodes, {result['edges']} edges → {result['kg_path']}"
    )
    landed = ", ".join(item.get("name", "?") for item in result["sources_landed"]) or "(none)"
    output.info(f"landed: {landed}")
    if result["sources_skipped"]:
        skipped = ", ".join(
            f"{item.get('name')} ({item.get('reason', item.get('status'))})"
            for item in result["sources_skipped"]
        )
        output.info(f"skipped: {skipped}")
    return 0
