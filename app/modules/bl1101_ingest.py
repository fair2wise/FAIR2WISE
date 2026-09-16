#!/usr/bin/env python3
"""Replayable ingest for the ALS 11.0.1.2 (bl1101) operations knowledge graph.

Produces versioned snapshots ``storage/kg/matkg_bl1101_vN.json`` in the
``beamline:`` namespace. Does not merge into ``matkg_rsoxs_v1.json``.
Each successful full run appends the next ``vN``; existing snapshots are kept.

JSON-first motor linking (v4): copy an existing snapshot and apply
``storage/schema/bl1101_motor_stage_map.yaml`` without refetching GitHub/ALS.

Usage (from repo root):
  python3 scripts/ingest_bl1101.py
  python3 scripts/ingest_bl1101.py --from-scratch
  python3 scripts/ingest_bl1101.py --from-graph storage/kg/matkg_bl1101_v3.json --snapshot 4
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml
from lxml import html as lhtml

from app.modules import json2kg

LOGGER = logging.getLogger("ingest_bl1101")

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "storage/schema/bl1101_schema.yaml"
MOTOR_STAGE_MAP_PATH = REPO_ROOT / "storage/schema/bl1101_motor_stage_map.yaml"
BLUEPRINT_PATH = (
    REPO_ROOT / "papers/rsoxs/beamline_blueprint/blueprint_bl11012_version09092026.html"
)
KG_DIR = REPO_ROOT / "storage/kg"
TERMS_DIR = REPO_ROOT / "storage/terminology"
CACHE_DIR = REPO_ROOT / ".cache/bl1101"
REPOS_DIR = CACHE_DIR / "repos"
READMES_DIR = CACHE_DIR / "readmes"
PAGES_DIR = CACHE_DIR / "pages"
WORK_DIR = CACHE_DIR / "work"

SCHEMA_VERSION = "bl1101_v2"
INGEST_VERSION = "1.2.0"
NAMESPACE = "beamline"
BEAMLINE_ID = "beamline:BL-11-0-1-2"
ALS_PAGE_URL = "https://als.lbl.gov/beamlines/11-0-1-2/"
ALS_SOURCE = "als.lbl.gov/beamlines/11-0-1-2"
PERSON_NAME_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-z]+)+)\b")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
BLUEPRINT_PERSON_RE = re.compile(
    r"(?:reviewed with|review with|beamline scientist\s*[:\(])"
    r"\s*([A-Z][a-z]+(?:\s+[A-Z]\.)?\s+[A-Z][a-z]+)",
    re.IGNORECASE,
)
PERSON_NAME_STOPWORDS = {
    "on",
    "of",
    "the",
    "and",
    "does",
    "every",
    "time",
    "with",
    "from",
    "for",
    "order",
    "pair",
}
# Title-case pairs that appear in ALS/blueprint prose but are not people.
SKIP_PERSON_NAMES = {
    "beamline energy",
    "primary contact",
    "additional notes",
    "technique category",
    "current status",
    "minimum energy",
    "maximum energy",
    "magnetic scattering",
    "resonant scattering",
    "kirkpatrick baez",
    "higher order",
    "storage ring",
    "sample stage",
    "exit slits",
    "gold mesh",
    "scatter slits",
    "beam current",
    "beam dumped",
    "soft x",
    "device blueprint",
}
SNAPSHOT_RE = re.compile(r"^matkg_bl1101_v(\d+)\.json$")
SECRET_PREFIXES = (".env",)
SECRET_NAMES = {"credentials.json", "secrets.yaml", "secrets.yml", "id_rsa", "priv_key.pem"}
SKIP_SUFFIXES = {
    ".h5",
    ".hdf5",
    ".fits",
    ".npy",
    ".npz",
    ".pt",
    ".pkl",
    ".pickle",
    ".env",
}
SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
}
SKIP_DIR_PREFIXES = ("ExampleData",)
MAX_SNIPPET_LINES = 80
PUBLIC_FUNCTIONS = {
    "load_sim_devices",
    "aload_sim_devices",
    "load_beamline_config",
    "load_sim_config",
}
DEVICE_BASE_HINTS = (
    "Device",
    "Motor",
    "Detector",
    "Readable",
    "StandardDetector",
    "StandardReadable",
    "SimMotor",
    "SynAxis",
    "SynSignal",
    "PatternGenerator",
    "SignalRW",
)
KIND_CATEGORY = {
    "motor": "Motor",
    "analog input": "AnalogInput",
    "digital i/o": "DigitalIO",
    "digital io": "DigitalIO",
    "detector": "Detector",
}
BEAM_PATH_RELATIONS = ("beam_path_next", "upstream_of", "feeds", "connected_to")
# Sheet A spatial/documented names → A′ stage names when they refer to the same element.
STAGE_NAME_ALIASES = {
    "storage ring": "Storage ring",
    "epu": "EPU",
    "m101": "M101",
    "mono": "Mono 101",
    "mono 101": "Mono 101",
    "m103": "M103",
    "kb pair": "M103",
    "exit slits": "Exit slits",
    "exit slit": "Exit slits",
    "pzt shutter": "PZT shutter",
    "piezoshutter": "PZT shutter",
    "m121": "M121",
    "diag 106": "Diag 106",
    "hos": "Higher Order Suppressor",
    "higher order suppressor": "Higher Order Suppressor",
    "gold mesh": "Gold mesh",
    "shutters": "Shutters",
    "scatter slits (jj)": "Scatter slits (JJ)",
    "scatter slits": "Scatter slits (JJ)",
    "upstream slits (jj)": "Upstream slits (JJ)",
    "middle slits (jj)": "Middle slits (JJ)",
    "in-chamber slits (jj)": "Scatter slits (JJ)",
    "chamber": "Chamber",
    "sample stage": "Sample stage",
    "sample": "Sample stage",
    "detector": "Detector",
    "axis-sxr-40": "Detector",
}
# Front-end before the figcaption subsequence "mono → M103 → …".
BEAM_PATH_PREFIX = ("Storage ring", "EPU", "M101")
BEAM_PATH_CHAMBER_TAIL = ("Sample stage", "Detector")
BCS2SIM_PATH_GLOBS = (
    "**/bl11012_knowledge.md",
    "**/*knowledge*.md",
    "**/blueprint*.html",
    "**/*beamline*.html",
)

SOFTWARE_ALIAS = {
    "bcs2sim-ophyd": "ophyd-async",
    "ADAxisSXR40": "ADAxisSXR40",
    "bl11012-bluesky": "Bluesky",
    "bl11012-scan-replay": "Bluesky",
    "bl11012-finch": "Finch",
    "splash_tiled": "splash_tiled",
    "tiled": "Tiled",
    "als_computing_hub": "als_computing_hub",
}

USER_AGENT = "FAIR2WISE-bl1101-ingest/1.0 (+https://github.com/matesuu/FAIRtoWISE-FORUM-AI)"


@dataclass(frozen=True)
class RepoSpec:
    name: str
    url: str
    mode: str  # full | docs | stack | readme
    python_globs: Tuple[str, ...] = ()
    md_globs: Tuple[str, ...] = ("README.md",)
    yaml_globs: Tuple[str, ...] = ()
    compose_globs: Tuple[str, ...] = ()
    readme_only: bool = False


REPO_SPECS: Tuple[RepoSpec, ...] = (
    RepoSpec(
        name="bcs2sim-ophyd",
        url="https://github.com/als-computing/bcs2sim-ophyd",
        mode="full",
        python_globs=(
            "sim_ophyd/devices/**/*.py",
            "sim_ophyd/_loader.py",
            "sim_ophyd/_config.py",
            "queue-server/startup_bl11012_sim/15_plans.py",
        ),
        md_globs=("README.md", "info/*.md"),
        yaml_globs=("sim-config.yaml",),
    ),
    RepoSpec(
        name="ADAxisSXR40",
        url="https://github.com/als-computing/ADAxisSXR40",
        mode="docs",
        md_globs=("README.md", "RELEASE.md", "info/camera/*.md", "info/README.md"),
    ),
    RepoSpec(
        name="bl11012-bluesky",
        url="https://github.com/als-computing/bl11012-bluesky",
        mode="stack",
        python_globs=("queue-server/startup_sim/15_plans.py",),
        md_globs=("README.md", "queue-server/README.md", "tiled/README.md"),
        compose_globs=("docker-compose.yml", "docker-compose-bl531.yml", "docker-compose-bl531.sim.yml"),
    ),
    RepoSpec(
        name="bl11012-scan-replay",
        url="https://github.com/als-computing/bl11012-scan-replay",
        mode="docs",
        python_globs=(
            "labview_sim/replay_plan.py",
            "labview_sim/sim_plans.py",
            "labview_sim/als_ccd_sim_devices.py",
        ),
        md_globs=("README.md", "qs_startup/README.md"),
    ),
    RepoSpec(
        name="bl11012-finch",
        url="https://github.com/als-computing/bl11012-finch",
        mode="docs",
        md_globs=(
            "README.md",
            "src/stories/*.md",
            "src/components/Experiment/*.md",
            "src/lib/ophyd-sim/*.md",
        ),
    ),
    RepoSpec(
        name="splash_tiled",
        url="https://github.com/als-computing/splash_tiled",
        mode="readme",
        readme_only=True,
        md_globs=("README.md",),
    ),
    RepoSpec(
        name="tiled",
        url="https://github.com/als-computing/tiled",
        mode="readme",
        readme_only=True,
        md_globs=("README.md",),
    ),
    RepoSpec(
        name="als_computing_hub",
        url="https://github.com/als-computing/als_computing_hub",
        mode="readme",
        readme_only=True,
        md_globs=("README.md",),
    ),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def slug(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").strip()).strip("-")
    return cleaned or "unnamed"


def beam_id(*parts: str) -> str:
    local = "-".join(slug(p) for p in parts if p)
    return f"beamline:{local}"


def strip_tags(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value or ""))).strip()


def next_snapshot_version(kg_dir: Path) -> int:
    """Return the next vN that does not yet exist (1 if none)."""
    versions: List[int] = []
    if kg_dir.exists():
        for path in kg_dir.iterdir():
            match = SNAPSHOT_RE.match(path.name)
            if match:
                versions.append(int(match.group(1)))
    return (max(versions) + 1) if versions else 1


def snapshot_paths(
    version: int,
    *,
    kg_dir: Optional[Path] = None,
    terms_dir: Optional[Path] = None,
) -> Tuple[Path, Path]:
    kg_dir = kg_dir or KG_DIR
    terms_dir = terms_dir or TERMS_DIR
    return (
        kg_dir / f"matkg_bl1101_v{version}.json",
        terms_dir / f"extracted_terms_bl1101_v{version}.json",
    )


def _rel_to_repo(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def is_secret_path(path: Path) -> bool:
    name = path.name
    if name in SECRET_NAMES:
        return True
    if name.startswith(SECRET_PREFIXES) and name != ".env.example":
        return True
    return False


def should_skip_file(path: Path) -> bool:
    if is_secret_path(path):
        return True
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    for part in path.parts:
        if part in SKIP_DIR_NAMES:
            return True
        if any(part.startswith(prefix) for prefix in SKIP_DIR_PREFIXES):
            return True
    return False


def _rel(term_id: str, relation: str, related_id: str, evidence: str | None = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "relation": relation,
        "related_term": related_id,
        "related_id": related_id,
    }
    if evidence:
        payload["evidence"] = [evidence]
    return payload


def make_term(
    term_id: str,
    name: str,
    category: str,
    definition: str,
    source: str,
    relations: Optional[List[Dict[str, Any]]] = None,
    **extra: Any,
) -> Dict[str, Any]:
    term: Dict[str, Any] = {
        "id": term_id,
        "id_prefix": NAMESPACE,
        "term": name,
        "name": name,
        "category": category,
        "definition": definition,
        "source_papers": [source],
        "type": f"beamline:{category}",
        "relations": relations or [],
    }
    for key, value in extra.items():
        if value not in (None, "", [], {}):
            term[key] = value
    return term


# ---------------------------------------------------------------------------
# HTML / ALS / blueprint
# ---------------------------------------------------------------------------

def fetch_url(url: str, dest: Path, timeout: int = 45) -> Dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
            status = getattr(response, "status", 200)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        LOGGER.warning("Failed to fetch %s: %s", url, exc)
        return {"url": url, "status": "failed", "reason": str(exc)}
    dest.write_bytes(body)
    return {
        "url": url,
        "status": "ok",
        "fetched_at": utc_now(),
        "path": str(dest.relative_to(REPO_ROOT)),
        "sha256": sha256_bytes(body),
        "http_status": status,
        "bytes": len(body),
    }


def parse_als_page(html_text: str) -> Dict[str, Any]:
    """Extract fact table + technique list from the public ALS beamline page."""
    tree = lhtml.fromstring(html_text)
    facts: Dict[str, str] = {}
    for row in tree.xpath("//tr"):
        cells = [" ".join(cell.itertext()).strip() for cell in row.xpath("./th|./td")]
        cells = [re.sub(r"\s+", " ", c) for c in cells if c]
        if len(cells) >= 2:
            facts[cells[0]] = " | ".join(cells[1:])
    title = " ".join(tree.xpath("//h1//text()")).strip() or "Beamline 11.0.1.2"
    paragraphs = [
        re.sub(r"\s+", " ", " ".join(p.itertext()).strip())
        for p in tree.xpath("//p")
        if " ".join(p.itertext()).strip()
    ]
    techniques: List[str] = []
    tech_blob = facts.get("Technique category") or facts.get("Techniques") or ""
    for token in re.split(r"[|\n;/]+", tech_blob):
        token = token.strip(" -")
        if len(token) > 2:
            techniques.append(token)
    full_text = re.sub(r"\s+", " ", " ".join(tree.itertext()))
    people = parse_als_contact_people(facts, full_text, source=ALS_PAGE_URL)
    return {
        "title": title,
        "facts": facts,
        "techniques": techniques,
        "paragraphs": paragraphs[:8],
        "text": full_text[:8000],
        "people": people,
    }


def _clean_person_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", (name or "").strip(" ,;:()"))
    if not cleaned or cleaned.casefold() in SKIP_PERSON_NAMES:
        return ""
    parts = cleaned.split()
    if len(parts) < 2:
        return ""
    last = parts[-1].rstrip(".").casefold()
    if last in PERSON_NAME_STOPWORDS or len(last) < 3:
        return ""
    return cleaned


def _email_near(text: str, index: int, name: str) -> str:
    window = (text or "")[index : index + max(len(name) + 80, 120)]
    match = EMAIL_RE.search(window)
    return match.group(0) if match else ""


def parse_als_contact_people(
    facts: Dict[str, str],
    text: str = "",
    *,
    source: str = ALS_PAGE_URL,
) -> List[Dict[str, str]]:
    """Parse documented ALS 11.0.1.2 contacts. Does not invent names."""
    blob = facts.get("Primary contact(s)") or facts.get("Primary contacts") or facts.get("Contacts") or ""
    people: List[Dict[str, str]] = []
    seen = set()
    chunks = re.split(r"[|;]+", blob) if blob else [text]
    for chunk in chunks:
        for match in PERSON_NAME_RE.finditer(chunk or ""):
            name = _clean_person_name(match.group(1))
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            people.append(
                {
                    "name": name,
                    "email": _email_near(chunk, match.start(), name),
                    "role": "BeamlineScientist",
                    "source": source,
                    "evidence": f"ALS primary contact: {chunk.strip()[:240]}",
                }
            )
    return people


def parse_blueprint_people(blueprint: Dict[str, Any], html_text: str = "") -> List[Dict[str, str]]:
    """Names the Gabe blueprint actually writes (e.g. reviewed with Thomas Ferron)."""
    header = blueprint.get("header") or {}
    blobs = [
        header.get("sources") or "",
        " ".join(str(v) for v in header.values()),
        html_text,
    ]
    people: List[Dict[str, str]] = []
    seen = set()
    for blob in blobs:
        if not blob:
            continue
        for match in BLUEPRINT_PERSON_RE.finditer(blob):
            name = _clean_person_name(match.group(1))
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            people.append(
                {
                    "name": name,
                    "email": "",
                    "role": "BeamlineScientist",
                    "source": BLUEPRINT_PATH.name,
                    "evidence": match.group(0).strip()[:240],
                }
            )
    return people


@dataclass
class BlueprintDevice:
    dev_id: str
    name: str
    kind: str
    category: str
    pv: str
    handle: str
    ophyd_name: str
    ophyd_class: str
    ioc_record: str
    mapping_file: str
    drawn: str
    meaning: str
    collection: str = ""


@dataclass
class BlueprintStage:
    name: str
    section: str
    what: str
    does: str
    range_text: str
    device_ids: List[str] = field(default_factory=list)


def _ophyd_name_from_handle(handle: str, fallback: str) -> Tuple[str, str]:
    match = re.search(r'd\.(\w+)\[\s*["\']([^"\']+)["\']\s*\]', handle or "")
    if match:
        return match.group(2), match.group(1)
    return fallback, ""


def canonical_stage_name(name: str) -> str:
    """Map SVG / caption / prose aliases onto a stable BeamlineStage name."""
    raw = re.sub(r"\s+", " ", (name or "").strip())
    if not raw:
        return ""
    key = raw.casefold()
    key = key.replace("kirkpatrick–baez", "m103").replace("kirkpatrick-baez", "m103")
    return STAGE_NAME_ALIASES.get(key, raw)


def _svg_coord(el: Any, attr: str) -> float:
    try:
        return float(el.get(attr) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def parse_sheet_a_svg_order(tree: Any) -> List[str]:
    """Sheet A SVG left-to-right, top-to-bottom. This is beam order, not A′ DOM."""
    labels: List[Tuple[float, float, str]] = []
    for text_el in tree.xpath("//section[@id='sheet-a']//figure//svg//text[contains(@class,'big')]"):
        name = canonical_stage_name(re.sub(r"\s+", " ", " ".join(text_el.itertext())).strip())
        if not name:
            continue
        # "Sample · θ = 0" belongs to sheet B; ignore if it leaked.
        if name.casefold().startswith("sample ·"):
            continue
        labels.append((_svg_coord(text_el, "y"), _svg_coord(text_el, "x"), name))
    labels.sort(key=lambda item: (item[0], item[1]))
    ordered: List[str] = []
    seen = set()
    for _, _, name in labels:
        if name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def parse_arrow_sequence(text: str) -> List[str]:
    """Pull a documented A → B → C beam-path chain out of prose or a figcaption."""
    blob = re.sub(r"\s+", " ", unescape(text or ""))
    blob = blob.replace("->", "→").replace("⟶", "→")
    if "→" not in blob:
        return []
    match = re.search(r"((?:[^→]{1,60}→\s*){2,}[^→.]{1,60})", blob)
    if not match:
        return []
    names: List[str] = []
    seen = set()
    for token in match.group(1).split("→"):
        name = canonical_stage_name(token.strip(" :;,"))
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def parse_figcaption_beam_path(tree: Any) -> List[str]:
    captions = tree.xpath("//section[@id='sheet-a']//figcaption")
    text = " ".join(re.sub(r"\s+", " ", " ".join(el.itertext())) for el in captions)
    return parse_arrow_sequence(text)


def parse_bcs2sim_beam_path(root: Optional[Path]) -> List[str]:
    """Reuse Gabe's map if bcs2sim checked in the same knowledge/blueprint text."""
    if root is None or not root.exists():
        return []
    for pattern in BCS2SIM_PATH_GLOBS:
        for path in sorted(root.glob(pattern)):
            if not path.is_file() or should_skip_file(path):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "M103" not in text or not re.search(r"exit\s+slits?", text, re.I):
                continue
            names = parse_arrow_sequence(text)
            if names:
                LOGGER.info("bcs2sim beam path from %s (%d stages)", path, len(names))
                return names
    return []


def _index_ci(names: Sequence[str], needle: str) -> int:
    target = needle.casefold()
    for i, name in enumerate(names):
        if target in name.casefold():
            return i
    return -1


def ensure_m103_before_exit_slits(names: Sequence[str]) -> List[str]:
    """Prose puts M103 between mono and the exit slits; A.5 before A.6 is not optical order."""
    ordered = list(names)
    i_m = _index_ci(ordered, "m103")
    i_e = _index_ci(ordered, "exit slit")
    if i_m >= 0 and i_e >= 0 and i_m > i_e:
        item = ordered.pop(i_m)
        ordered.insert(i_e, item)
    return ordered


def finalize_beam_path(names: Sequence[str]) -> List[str]:
    """Prefix source/EPU/M101, replace a trailing Chamber with sample → detector."""
    ordered = ensure_m103_before_exit_slits(names)
    if not ordered:
        return []
    lowered = [n.casefold() for n in ordered]
    prefix: List[str] = []
    for name in BEAM_PATH_PREFIX:
        if name.casefold() not in lowered:
            prefix.append(name)
        else:
            break
    ordered = prefix + ordered
    if ordered and ordered[-1].casefold() == "chamber":
        ordered = ordered[:-1]
    lowered = [n.casefold() for n in ordered]
    for name in BEAM_PATH_CHAMBER_TAIL:
        if name.casefold() not in lowered:
            ordered.append(name)
    seen = set()
    unique: List[str] = []
    for name in ordered:
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(name)
    return ensure_m103_before_exit_slits(unique)


def resolve_beam_path_order(
    *,
    svg_names: Sequence[str],
    caption_names: Sequence[str],
    bcs2sim_names: Sequence[str] = (),
    stage_names: Sequence[str] = (),
) -> List[str]:
    """Prefer Sheet A SVG, then figcaption / bcs2sim. Never A′ DOM order as the path."""
    if svg_names:
        return finalize_beam_path(svg_names)
    if caption_names:
        return finalize_beam_path(caption_names)
    if bcs2sim_names:
        return finalize_beam_path(bcs2sim_names)
    # stage_names is A′ DOM (A.5 exit slits before A.6 M103). Do not use it.
    _ = stage_names
    return []


def parse_blueprint_html(html_text: str) -> Dict[str, Any]:
    """Parse Gabe's BL 11.0.1.2 device blueprint (titleblock, stages, sheet E, beam path)."""
    tree = lhtml.fromstring(html_text)
    header: Dict[str, str] = {}
    for block in tree.xpath("//header[contains(@class,'titleblock')]/div"):
        key = " ".join(block.xpath(".//*[contains(@class,'k')]/text()")).strip()
        val = " ".join(block.xpath(".//*[contains(@class,'v')]/text()")).strip()
        if key:
            header[key] = val
    sources = " ".join(tree.xpath("//*[contains(@class,'sources')]//text()"))
    header["sources"] = re.sub(r"\s+", " ", sources).strip()

    devices: Dict[str, BlueprintDevice] = {}
    for row in tree.xpath("//table[@id='corr']//tr[@id]"):
        dev_id = (row.get("id") or "").replace("dev-", "")
        tds = row.xpath("./td")
        if len(tds) < 6:
            continue
        drawn = " ".join(tds[0].itertext()).strip()
        name = " ".join(tds[1].itertext()).strip()
        ioc = " ".join(tds[2].itertext()).strip()
        pv = " ".join(tds[3].xpath(".//span[contains(@class,'pv')]/text()")).strip()
        handle = " ".join(tds[4].xpath(".//span[contains(@class,'hd')]/text()")).strip()
        ophyd_class = " ".join(tds[5].itertext()).strip()
        mapping = " ".join(tds[6].itertext()).strip() if len(tds) > 6 else ""
        tip = unescape(" ".join(tds[1].xpath(".//*[@data-tip]/@data-tip")))
        kind = strip_tags(
            " ".join(re.findall(r'tip-k">([^<]+)', tip)) or ""
        ).lower()
        meaning = strip_tags(" ".join(re.findall(r'tip-m">([^<]+)', tip)))
        if not kind:
            if "detector" in (ophyd_class + name).lower():
                kind = "detector"
            elif "dio" in ophyd_class.lower() or "digital" in ophyd_class.lower():
                kind = "digital i/o"
            elif "ai" in ophyd_class.lower() or "analog" in ophyd_class.lower():
                kind = "analog input"
            else:
                kind = "motor"
        ophyd_name, collection = _ophyd_name_from_handle(handle, dev_id)
        devices[dev_id] = BlueprintDevice(
            dev_id=dev_id,
            name=name or ophyd_name,
            kind=kind,
            category=KIND_CATEGORY.get(kind, "Device"),
            pv=pv,
            handle=handle,
            ophyd_name=ophyd_name,
            ophyd_class=ophyd_class,
            ioc_record=ioc,
            mapping_file=mapping,
            drawn=drawn,
            meaning=meaning,
            collection=collection,
        )

    stages: List[BlueprintStage] = []
    for stage_el in tree.xpath("//*[contains(@class,'stage')]"):
        name = " ".join(stage_el.xpath(".//*[contains(@class,'stage-name')]/text()")).strip()
        if not name:
            continue
        section = " ".join(stage_el.xpath(".//*[contains(@class,'stage-id')]/text()")).strip()
        what = strip_tags(" ".join(stage_el.xpath(".//*[contains(@class,'stage-what')]//text()")))
        does = strip_tags(" ".join(stage_el.xpath(".//*[contains(@class,'stage-does')]//text()")))
        range_text = strip_tags(" ".join(stage_el.xpath(".//*[contains(@class,'stage-range')]//text()")))
        hrefs = [h.replace("#dev-", "") for h in stage_el.xpath(".//a/@href") if h.startswith("#dev-")]
        stages.append(
            BlueprintStage(
                name=name,
                section=section,
                what=what,
                does=does,
                range_text=range_text,
                device_ids=sorted(set(hrefs)),
            )
        )

    rules = []
    for rule in tree.xpath("//*[contains(@class,'rule')]"):
        key = " ".join(rule.xpath(".//*[contains(@class,'rule-k')]/text()")).strip()
        body = strip_tags(" ".join(rule.xpath(".//text()")))
        if key or body:
            rules.append({"name": key, "text": body})

    svg_names = parse_sheet_a_svg_order(tree)
    caption_names = parse_figcaption_beam_path(tree)
    beam_path = resolve_beam_path_order(
        svg_names=svg_names,
        caption_names=caption_names,
        stage_names=[stage.name for stage in stages],
    )
    parsed_bp = {
        "header": header,
        "devices": devices,
        "stages": stages,
        "rules": rules,
        "beam_note": header.get("sources", ""),
        "beam_path": beam_path,
        "beam_path_svg": svg_names,
        "beam_path_caption": caption_names,
    }
    parsed_bp["people"] = parse_blueprint_people(parsed_bp, html_text)
    return parsed_bp


# ---------------------------------------------------------------------------
# GitHub / Python / compose
# ---------------------------------------------------------------------------

def git_sha(path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _run_git(args: Sequence[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=str(cwd) if cwd else None,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )


def sync_repo(spec: RepoSpec, dest: Path) -> Dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if (dest / ".git").exists():
            _run_git(["git", "fetch", "--depth", "1", "origin"], cwd=dest)
            _run_git(["git", "reset", "--hard", "FETCH_HEAD"], cwd=dest)
        elif spec.readme_only:
            _run_git(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--filter=blob:none",
                    "--sparse",
                    spec.url,
                    str(dest),
                ]
            )
            try:
                _run_git(["git", "sparse-checkout", "set", "--skip-checks", "README.md"], cwd=dest)
            except subprocess.CalledProcessError:
                LOGGER.info("%s sparse-checkout README skipped; using clone as-is", spec.name)
        else:
            _run_git(["git", "clone", "--depth", "1", spec.url, str(dest)])
        sha = git_sha(dest)
        LOGGER.info("Repo %s @ %s", spec.name, sha[:12])
        return {
            "name": spec.name,
            "url": spec.url,
            "status": "ok",
            "sha": sha,
            "path": str(dest.relative_to(REPO_ROOT)),
            "mode": spec.mode,
        }
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) and exc.stderr else str(exc)
        LOGGER.warning("Skipping private/unavailable repo %s: %s", spec.name, detail)
        return {
            "name": spec.name,
            "url": spec.url,
            "status": "skipped",
            "reason": detail,
            "mode": spec.mode,
        }


def expand_globs(root: Path, patterns: Sequence[str]) -> List[Path]:
    found: List[Path] = []
    for pattern in patterns:
        found.extend(sorted(root.glob(pattern)))
    unique: List[Path] = []
    seen = set()
    for path in found:
        if not path.is_file() or should_skip_file(path):
            continue
        if path.resolve() in seen:
            continue
        seen.add(path.resolve())
        unique.append(path)
    return unique


def _base_names(node: ast.ClassDef) -> str:
    chunks = []
    for base in node.bases:
        try:
            chunks.append(ast.unparse(base))
        except Exception:
            chunks.append(getattr(base, "id", "") or "")
    return " ".join(chunks)


def _is_device_class(node: ast.ClassDef) -> bool:
    if node.name.endswith(("Error", "Exception")):
        return False
    bases = _base_names(node)
    if any(hint in bases for hint in DEVICE_BASE_HINTS):
        return True
    return bool(
        re.match(r"^(Sim|Epics|AD|Replay)", node.name)
        or node.name.endswith(("Device", "Motor", "Detector", "Endstation", "CCD", "Plan"))
    )


def _is_plan_function(node: ast.FunctionDef) -> bool:
    if node.name.startswith("_"):
        return False
    if node.name in PUBLIC_FUNCTIONS:
        return True
    deco = " ".join(
        ast.unparse(d) if not isinstance(d, ast.Name) else d.id for d in node.decorator_list
    )
    if "parameter_annotation" in deco or "plan" in deco.lower():
        return True
    try:
        src = ast.unparse(node)
    except Exception:
        src = node.name
    if "yield" in src and any(tok in node.name.lower() for tok in ("scan", "count", "replay")):
        return True
    return node.name in {"count", "scan", "rel_scan", "grid_scan"} or "scan" in node.name.lower()


def extract_python_symbols(path: Path, repo_name: str, commit: str = "") -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (terms-like symbol records, curated code snippets) from one Python file."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError) as exc:
        LOGGER.warning("Skipping Python %s: %s", path, exc)
        return [], []
    lines = source.splitlines()
    rel = path.name
    snippets: List[Dict[str, Any]] = []
    symbols: List[Dict[str, Any]] = []

    def snippet_for(node: ast.AST, kind: str, function_name: str) -> Optional[Dict[str, Any]]:
        start = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", start)
        chunk = lines[start - 1 : end]
        body = "\n".join(chunk[:MAX_SNIPPET_LINES])
        if not body.strip():
            return None
        paper = f"{repo_name}:{path.name}:{function_name}"
        return {
            "id": beam_id("snippet", repo_name, function_name, str(start)),
            "id_prefix": NAMESPACE,
            "curated": True,
            "function_name": function_name,
            "code_snippet": body,
            "code_language": "python",
            "code_description": f"{kind} `{function_name}` from {repo_name} ({path.name}:{start}-{end})",
            "source_paper": paper,
            "page": start,
            "source_type": "github",
            "repo_name": repo_name,
            "repo_url": f"https://github.com/als-computing/{repo_name}",
            "repo_commit_sha": commit,
            "source_file_path": rel,
            "source_start_line": start,
            "source_end_line": end,
            "source_file_url": (
                f"https://github.com/als-computing/{repo_name}/blob/{commit}/{rel}#L{start}-L{end}"
                if commit
                else ""
            ),
        }

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and _is_device_class(node):
            snip = snippet_for(node, "class", node.name)
            if not snip:
                continue
            snippets.append(snip)
            category = "Detector" if "detect" in node.name.lower() or "ccd" in node.name.lower() else "OphydDevice"
            if node.name.lower().endswith("plan") or "scan" in node.name.lower():
                category = "BlueskyPlan"
            if node.name in {"Endstation"}:
                category = "Endstation"
            symbols.append(
                {
                    "id": beam_id("ophyd", node.name),
                    "name": node.name,
                    "category": category,
                    "file": rel,
                    "snippet_paper": snip["source_paper"],
                    "start": snip["source_start_line"],
                    "end": snip["source_end_line"],
                }
            )
        elif isinstance(node, ast.FunctionDef) and _is_plan_function(node):
            snip = snippet_for(node, "function", node.name)
            if not snip:
                continue
            snippets.append(snip)
            is_loader = node.name in PUBLIC_FUNCTIONS
            symbols.append(
                {
                    "id": beam_id("plan" if not is_loader else "fn", node.name),
                    "name": node.name,
                    "category": "Method" if is_loader else "BlueskyPlan",
                    "file": rel,
                    "snippet_paper": snip["source_paper"],
                    "start": snip["source_start_line"],
                    "end": snip["source_end_line"],
                }
            )
    return symbols, snippets


def parse_compose_services(path: Path) -> List[Dict[str, str]]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        LOGGER.warning("YAML parse failed for %s: %s", path, exc)
        data = {}
    services = data.get("services") if isinstance(data, dict) else None
    names: List[str] = []
    if isinstance(services, dict):
        names = [str(k) for k in services.keys()]
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        names = re.findall(r"(?m)^  ([A-Za-z0-9_-]+):\s*$", text)
        skip = {"networks", "volumes", "configs", "secrets", "x-anchors"}
        names = [n for n in names if n not in skip]
    return [{"name": n, "file": path.name} for n in names]


def extract_markdown_software(path: Path, repo_name: str, limit: int = 400) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    # First heading + following paragraph; never dump whole READMEs into nodes.
    lines = text.splitlines()
    kept: List[str] = []
    for line in lines[:80]:
        if line.startswith("#") or kept:
            kept.append(line)
        if len(" ".join(kept)) > limit and line.strip() == "":
            break
    blob = " ".join(x.strip() for x in kept if x.strip() and not x.startswith("```"))
    return re.sub(r"\s+", " ", blob)[:limit]


def extract_ts_fences(path: Path, repo_name: str, commit: str = "") -> List[Dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    snippets = []
    for match in re.finditer(r"```(?:tsx|ts|typescript)\n(.*?)```", text, re.S):
        body = match.group(1).strip()
        if "FinchConfig" not in body and "ophyd" not in body.lower():
            continue
        start = text[: match.start()].count("\n") + 1
        snippets.append(
            {
                "id": beam_id("snippet", repo_name, "FinchConfigProvider", str(start)),
                "id_prefix": NAMESPACE,
                "curated": True,
                "function_name": "FinchConfigProvider",
                "code_snippet": body[:4000],
                "code_language": "typescript",
                "code_description": f"Finch config snippet from {repo_name} README",
                "source_paper": f"{repo_name}:README.md:FinchConfigProvider",
                "page": start,
                "source_type": "github",
                "repo_name": repo_name,
                "repo_commit_sha": commit,
                "source_file_path": path.name,
                "source_start_line": start,
            }
        )
        break
    return snippets


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def _energy_from_sources(als: Dict[str, Any], blueprint: Dict[str, Any]) -> Tuple[str, str]:
    facts = als.get("facts") or {}
    lo = facts.get("Minimum energy (eV)") or "165"
    hi = facts.get("Maximum energy (eV)") or "1,500"
    note = blueprint.get("beam_note") or ""
    bp_match = re.search(r"~?\s*200\s*[–-]\s*1500\s*eV", note)
    definition = (
        f"ALS public range {lo}–{hi} eV. "
        + (f"Blueprint beam note: {bp_match.group(0)}." if bp_match else "Blueprint reports ~200–1500 eV, E/ΔE 4000 at 800 eV.")
    )
    return f"{lo}–{hi} eV", definition


def terms_from_als(als: Dict[str, Any], source: str) -> List[Dict[str, Any]]:
    facts = als.get("facts") or {}
    terms: List[Dict[str, Any]] = []
    techniques = [
        ("RSoXS", "Resonant soft X-ray scattering; primary technique at BL 11.0.1.2."),
        ("SAXS", "Small-angle X-ray scattering listed on the ALS 11.0.1.2 page."),
        ("WAXS", "Wide-angle X-ray scattering listed on the ALS 11.0.1.2 page."),
        ("GISAXS", "Grazing-incidence SAXS/WAXS listed on the ALS 11.0.1.2 page."),
        ("XAS", "X-ray absorption spectroscopy listed on the ALS 11.0.1.2 page."),
        ("Resonant scattering", "Resonant scattering listed under technique category."),
        ("Magnetic scattering", "Magnetic scattering listed on the ALS 11.0.1.2 page."),
    ]
    for name, definition in techniques:
        terms.append(
            make_term(
                beam_id("technique", name),
                name,
                "ExperimentalTechnique",
                definition,
                source,
                relations=[_rel(BEAMLINE_ID, "part_of", BEAMLINE_ID, source)],
            )
        )
    for name, definition, cat in (
        ("linear polarization", "Linear polarization continuously variable from horizontal to vertical.", "Polarization"),
        ("left circular polarization", "Left elliptical / circular polarization, user selectable.", "Polarization"),
        ("right circular polarization", "Right elliptical / circular polarization, user selectable.", "Polarization"),
        ("transmission", "Transmission scattering geometry used for polymer thin films.", "ScatteringGeometry"),
        ("grazing incidence", "Grazing-incidence SAXS/WAXS geometry listed by ALS.", "ScatteringGeometry"),
        ("reflectivity", "Reflectometer endstation supports reflectivity / reflectometry.", "ScatteringGeometry"),
        ("C K-edge", "Carbon K-edge falls in the beamline soft X-ray range.", "AbsorptionEdge"),
        ("N K-edge", "Nitrogen K-edge falls in the beamline soft X-ray range.", "AbsorptionEdge"),
        ("O K-edge", "Oxygen K-edge falls in the beamline soft X-ray range.", "AbsorptionEdge"),
        ("polymer thin films", "Sample class listed by ALS: polymer thin films.", "Sample"),
        ("solid thin films", "Sample class listed by ALS: solid thin films.", "Sample"),
        ("vacuum compatible liquid cell", "Sample class listed by ALS: vacuum compatible liquid cell.", "Sample"),
    ):
        terms.append(make_term(beam_id(cat, name), name, cat, definition, source))

    contacts = facts.get("Primary contact(s)") or ""
    notes = facts.get("Additional notes") or ""
    energy_label, energy_def = _energy_from_sources(als, {"beam_note": ""})
    terms.append(
        make_term(
            beam_id("PhotonEnergy", "soft-xray"),
            f"Photon energy {energy_label}",
            "PhotonEnergy",
            energy_def + ((" " + notes) if notes else ""),
            source,
            photon_energy_eV={"min": 165, "max": 1500},
        )
    )
    terms.append(
        make_term(
            beam_id("source", "EPU5"),
            "EPU5",
            "BeamlineStage",
            f"Insertion device / source listed by ALS as {facts.get('Source', 'EPU5')}."
            + (f" Contacts: {contacts}." if contacts else ""),
            source,
        )
    )
    return terms


def _beam_path_stage_terms(
    blueprint: Dict[str, Any],
    source: str,
    stage_ids: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Directed topology along the optical beam path (not Sheet E inventory order)."""
    path = [name for name in (blueprint.get("beam_path") or []) if canonical_stage_name(name)]
    if len(path) < 2:
        return []
    terms: List[Dict[str, Any]] = []
    ids: List[str] = []
    for index, name in enumerate(path, start=1):
        sid = stage_ids.get(name.casefold()) or beam_id("stage", name)
        ids.append(sid)
        if name.casefold() not in stage_ids:
            stage_ids[name.casefold()] = sid
            terms.append(
                make_term(
                    sid,
                    name,
                    "BeamlineStage",
                    f"Optical beam-path stage {index}: {name} (Gabe blueprint Sheet A / documented sequence).",
                    source,
                    relations=[_rel(sid, "part_of", BEAMLINE_ID, source)],
                    beam_path_index=index,
                )
            )
        else:
            terms.append(
                make_term(
                    sid,
                    name,
                    "BeamlineStage",
                    f"Optical beam-path stage {index}: {name}.",
                    source,
                    beam_path_index=index,
                )
            )
    evidence_base = "Gabe blueprint Sheet A SVG / figcaption (mono → M103 → exit slits → …); not A′ DOM order"
    for index, (src_id, dst_id) in enumerate(zip(ids, ids[1:]), start=1):
        step = f"beam path step {index}: {path[index - 1]} → {path[index]} ({evidence_base})"
        for relation in BEAM_PATH_RELATIONS:
            terms.append(
                make_term(
                    src_id,
                    path[index - 1],
                    "BeamlineStage",
                    "",
                    source,
                    relations=[_rel(src_id, relation, dst_id, step)],
                )
            )
    return terms


def terms_from_blueprint(blueprint: Dict[str, Any], source: str) -> List[Dict[str, Any]]:
    terms: List[Dict[str, Any]] = []
    header = blueprint.get("header") or {}
    drawing = header.get("Drawing") or "BL 11.0.1.2 RSOXS · Reflectometer endstation"
    terms.append(
        make_term(
            beam_id("Endstation", "Reflectometer"),
            "Reflectometer endstation",
            "Endstation",
            f"{drawing}. Blueprint revision {header.get('Revision', '')} dated {header.get('Date', '')}. {header.get('sources', '')}",
            source,
            relations=[_rel(BEAMLINE_ID, "part_of", BEAMLINE_ID, source)],
        )
    )
    stage_id_by_dev: Dict[str, str] = {}
    stage_ids: Dict[str, str] = {}
    for stage in blueprint.get("stages") or []:
        sid = beam_id("stage", stage.name)
        stage_ids[stage.name.casefold()] = sid
        for dev_id in stage.device_ids:
            stage_id_by_dev[dev_id] = sid
        rels = [_rel(sid, "part_of", BEAMLINE_ID, source)]
        terms.append(
            make_term(
                sid,
                stage.name,
                "BeamlineStage",
                " ".join(x for x in (stage.section, stage.what, stage.does, stage.range_text) if x),
                source,
                relations=rels,
            )
        )
    terms.extend(_beam_path_stage_terms(blueprint, source, stage_ids))

    for device in (blueprint.get("devices") or {}).values():
        did = beam_id(device.category, device.ophyd_name or device.dev_id)
        pv_id = beam_id("PV", device.pv or device.ophyd_name) if device.pv else ""
        ophyd_id = beam_id("ophyd", device.ophyd_class) if device.ophyd_class else ""
        rels = [_rel(did, "part_of", BEAMLINE_ID, source)]
        stage_id = stage_id_by_dev.get(device.dev_id)
        if stage_id:
            rels.append(_rel(did, "part_of", stage_id, f"blueprint stage {stage_id}"))
        if pv_id:
            rels.append(_rel(did, "hasPV", pv_id, device.pv))
        if ophyd_id:
            rels.append(_rel(did, "related_to", ophyd_id, device.ophyd_class))
        definition = device.meaning or f"{device.kind} '{device.name}' ({device.ophyd_class})"
        if device.handle:
            definition += f" Handle {device.handle}."
        terms.append(
            make_term(
                did,
                device.name,
                device.category,
                definition,
                source,
                relations=rels,
                pv=device.pv,
                ophyd_name=device.ophyd_name,
                ophyd_class=device.ophyd_class,
                ioc_record=device.ioc_record,
                mapping_file=device.mapping_file,
                device_kind=device.kind,
            )
        )
        if pv_id:
            terms.append(
                make_term(
                    pv_id,
                    device.pv,
                    "ProcessVariable",
                    f"EPICS PV {device.pv} ({device.ioc_record or 'IOC record unknown'}) for {device.name}.",
                    source,
                    relations=[_rel(pv_id, "part_of", did, device.handle)],
                    pv=device.pv,
                )
            )
        if device.ophyd_class:
            terms.append(
                make_term(
                    beam_id("ophyd", device.ophyd_class),
                    device.ophyd_class,
                    "OphydDevice",
                    f"Ophyd class {device.ophyd_class} used as {device.handle or device.ophyd_name}.",
                    source,
                )
            )

    for rule in blueprint.get("rules") or []:
        name = rule.get("name") or "operating rule"
        terms.append(
            make_term(
                beam_id("rule", name),
                name,
                "Condition",
                rule.get("text") or name,
                source,
                relations=[_rel(BEAMLINE_ID, "part_of", BEAMLINE_ID, source)],
            )
        )
    terms.append(
        make_term(
            beam_id("Detector", "AXIS-SXR-40"),
            "AXIS-SXR-40",
            "Detector",
            "Area detector at BL 11.0.1.2 (Tucsen Dhyana XFXV4040BSI in AXIS-SXR-40; blueprint sim_det).",
            source,
            relations=[
                _rel(BEAMLINE_ID, "part_of", BEAMLINE_ID, source),
                _rel(BEAMLINE_ID, "part_of", beam_id("Endstation", "Reflectometer"), source),
            ],
        )
    )
    return terms


def beamline_seed(als: Dict[str, Any], blueprint: Dict[str, Any], als_source: str, bp_source: str) -> Dict[str, Any]:
    facts = als.get("facts") or {}
    energy_label, energy_def = _energy_from_sources(als, blueprint)
    notes = facts.get("Additional notes") or ""
    header = blueprint.get("header") or {}
    definition = (
        f"ALS Beamline 11.0.1.2, resonant soft X-ray scattering (RSoXS) with a "
        f"reflectometer endstation. Source {facts.get('Source', 'EPU5')}; "
        f"photon energy {energy_label}. {energy_def} {notes} "
        f"Flux {facts.get('Flux/Brightness', '10^13 ph/s/0.1%BW at 800 eV')}; "
        f"resolving power {facts.get('Resolving power', '4000 at 800 eV')}. "
        f"Blueprint: {header.get('Drawing', '')} ({header.get('Date', '')})."
    )
    software_ids = [
        beam_id("Software", name)
        for name in ("Bluesky", "ophyd-async", "EPICS", "LabVIEW", "Finch", "Tiled", "Queue-Server")
    ]
    technique_ids = [
        beam_id("technique", n)
        for n in ("RSoXS", "SAXS", "WAXS", "GISAXS", "XAS", "Resonant scattering", "Magnetic scattering")
    ]
    geometry_ids = [
        beam_id("ScatteringGeometry", n)
        for n in ("transmission", "grazing incidence", "reflectivity")
    ]
    rels = (
        [_rel(BEAMLINE_ID, "hasEndstation", beam_id("Endstation", "Reflectometer"), bp_source)]
        + [_rel(BEAMLINE_ID, "hasDetector", beam_id("Detector", "AXIS-SXR-40"), bp_source)]
        + [_rel(BEAMLINE_ID, "runsSoftware", sid, "ops stack") for sid in software_ids]
        + [_rel(BEAMLINE_ID, "usesTechnique", tid, als_source) for tid in technique_ids]
        + [_rel(BEAMLINE_ID, "supportsGeometry", gid, als_source) for gid in geometry_ids]
        + [_rel(BEAMLINE_ID, "related_to", beam_id("PhotonEnergy", "soft-xray"), als_source)]
        + [
            _rel(BEAMLINE_ID, "related_to", beam_id("Polarization", n), als_source)
            for n in ("linear polarization", "left circular polarization", "right circular polarization")
        ]
    )
    return make_term(
        BEAMLINE_ID,
        "ALS Beamline 11.0.1.2",
        "Beamline",
        definition,
        als_source,
        relations=rels,
        source_papers=[als_source, bp_source],
    )


def software_catalog() -> List[Dict[str, Any]]:
    items = [
        ("Bluesky", "Bluesky RunEngine / queueserver plans for BL 11.0.1.2."),
        ("ophyd-async", "ophyd-async device layer (sim-ophyd and real IOC devices)."),
        ("EPICS", "EPICS IOCs and process variables (SIM11012: prefix in the virtual beamline)."),
        ("LabVIEW", "Legacy LabVIEW controls (lv-config Motor/AI/DIO Setup.json) that the sims reproduce."),
        ("Finch", "React component library for Bluesky beamline UIs."),
        ("Tiled", "Tiled data service for scan documents (README-only ingest of the ALS fork)."),
        ("Queue-Server", "Bluesky queueserver HTTP API used by Finch."),
        ("ADAxisSXR40", "EPICS areaDetector driver for the AXIS-SXR-40 (Tucsen Dhyana XFXV4040BSI)."),
        ("splash_tiled", "Splash-on-Tiled graph service used with FAIR2WISE graphs."),
        ("als_computing_hub", "ALS Computing hub documentation for supporting services."),
    ]
    return [
        make_term(beam_id("Software", name), name, "Software", definition, f"catalog:{name}")
        for name, definition in items
    ]


def attach_repo_terms(
    spec: RepoSpec,
    root: Path,
    sha: str,
    symbols: List[Dict[str, Any]],
    snippets: List[Dict[str, Any]],
    compose_services: List[Dict[str, str]],
    md_summaries: List[Tuple[str, str]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    terms: List[Dict[str, Any]] = []
    software_id = beam_id("Software", SOFTWARE_ALIAS.get(spec.name, spec.name))
    terms.append(
        make_term(
            beam_id("repo", spec.name),
            spec.name,
            "Software",
            f"GitHub {spec.url} @ {sha[:12] or 'unknown'} ({spec.mode} ingest).",
            spec.name,
            relations=[
                _rel(BEAMLINE_ID, "part_of", BEAMLINE_ID, spec.name),
                _rel(software_id, "related_to", software_id, spec.name),
            ],
            repo_url=spec.url,
            repo_commit_sha=sha,
        )
    )
    for symbol in symbols:
        rels = [_rel(symbol["id"], "runsSoftware", software_id, spec.name)]
        if symbol.get("snippet_paper"):
            rels.append(
                _rel(symbol["id"], "has_code_snippet", beam_id("snippet", spec.name, symbol["name"], str(symbol.get("start") or 1)), spec.name)
            )
        if symbol["category"] == "BlueskyPlan":
            rels.append(_rel(BEAMLINE_ID, "implementsPlan", symbol["id"], spec.name))
        terms.append(
            make_term(
                symbol["id"],
                symbol["name"],
                symbol["category"],
                f"{symbol['category']} `{symbol['name']}` in {symbol.get('file', spec.name)}.",
                symbol.get("snippet_paper") or spec.name,
                relations=rels,
                source_file_path=symbol.get("file"),
                source_start_line=symbol.get("start"),
                source_end_line=symbol.get("end"),
                repo_commit_sha=sha,
            )
        )
    for service in compose_services:
        sid = beam_id("service", spec.name, service["name"])
        terms.append(
            make_term(
                sid,
                service["name"],
                "ComposeService",
                f"Compose service `{service['name']}` from {service['file']} in {spec.name}.",
                spec.name,
                relations=[
                    _rel(sid, "part_of", software_id, service["file"]),
                    _rel(sid, "configured_by", beam_id("repo", spec.name), service["file"]),
                ],
            )
        )
    for md_path, summary in md_summaries:
        if not summary:
            continue
        terms.append(
            make_term(
                beam_id("doc", spec.name, Path(md_path).stem),
                f"{spec.name} {Path(md_path).name}",
                "Software",
                summary,
                spec.name,
                relations=[_rel(software_id, "related_to", software_id, md_path)],
            )
        )
    # Documented data paths (never h5 payloads).
    if spec.name == "bl11012-scan-replay":
        terms.append(
            make_term(
                beam_id("DataPath", "labview-replay-bundle"),
                "labview_sim/data/replay_bundle.json",
                "DataPath",
                "Per-frame replay metadata (~1 MB). Ingest records the path only; HDF5/FITS arrays are not loaded.",
                spec.name,
                relations=[_rel(software_id, "configured_by", beam_id("repo", spec.name), spec.name)],
            )
        )
    if spec.name == "bl11012-bluesky":
        terms.append(
            make_term(
                beam_id("DataPath", "tiled-compose"),
                "http://tiled:8000",
                "DataPath",
                "In-compose Tiled URI (TILED_URI default http://tiled:8000). API keys from .env are not ingested.",
                spec.name,
            )
        )
    if spec.name == "bcs2sim-ophyd":
        terms.append(
            make_term(
                beam_id("config", "sim-config"),
                "sim-config.yaml",
                "Software",
                "Static sim configuration (exclude_devices, shutter wiring, initial_state). Configures load_sim_devices.",
                spec.name,
                relations=[_rel(beam_id("fn", "load_sim_devices"), "configured_by", beam_id("config", "sim-config"), "sim-config.yaml")],
            )
        )
    return terms, snippets


def merge_terms(batches: Iterable[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for batch in batches:
        for term in batch:
            tid = term["id"]
            if tid not in merged:
                merged[tid] = term
                continue
            existing = merged[tid]
            if len(term.get("definition") or "") > len(existing.get("definition") or ""):
                existing["definition"] = term["definition"]
            src = existing.setdefault("source_papers", [])
            for paper in term.get("source_papers") or []:
                if paper not in src:
                    src.append(paper)
            rels = existing.setdefault("relations", [])
            seen = {(r.get("relation"), r.get("related_id") or r.get("related_term")) for r in rels}
            for rel in term.get("relations") or []:
                key = (rel.get("relation"), rel.get("related_id") or rel.get("related_term"))
                if key not in seen:
                    rels.append(rel)
                    seen.add(key)
            for key, value in term.items():
                if key in existing or value in (None, "", [], {}):
                    continue
                existing[key] = value
    return [merged[k] for k in sorted(merged)]


def sort_graph(graph: Dict[str, Any]) -> Dict[str, Any]:
    graph["things"] = sorted(graph.get("things") or [], key=lambda n: n.get("id") or "")
    graph["associations"] = sorted(
        graph.get("associations") or [],
        key=lambda e: (e.get("subject") or "", e.get("predicate") or "", e.get("object") or ""),
    )
    return graph


# ---------------------------------------------------------------------------
# v4 motor → stage linking (JSON-first; mapping file is the source of truth)
# ---------------------------------------------------------------------------

def load_motor_stage_map(path: Optional[Path] = None) -> Dict[str, Any]:
    """Load ophyd_name → stage assignments. Missing file → empty map."""
    dest = path or MOTOR_STAGE_MAP_PATH
    if not dest.exists():
        LOGGER.warning("Motor-stage map missing: %s", dest)
        return {"version": 0, "motors": {}, "aliases": {}, "leave_on_beamline": {}}
    data = yaml.safe_load(dest.read_text(encoding="utf-8")) or {}
    motors: Dict[str, str] = {}
    for key, value in (data.get("motors") or {}).items():
        if isinstance(value, dict):
            stage = value.get("stage") or ""
        else:
            stage = str(value or "")
        name = canonical_stage_name(stage)
        if key and name:
            motors[str(key)] = name
    aliases = {str(k): str(v) for k, v in (data.get("aliases") or {}).items() if k and v}
    leave = data.get("leave_on_beamline") or {}
    if isinstance(leave, list):
        leave = {str(item): "" for item in leave}
    else:
        leave = {str(k): str(v or "") for k, v in leave.items()}
    return {
        "version": data.get("version") or 0,
        "source": data.get("source") or dest.name,
        "path": _rel_to_repo(dest),
        "motors": motors,
        "aliases": aliases,
        "leave_on_beamline": leave,
    }


def _stage_id_by_name(nodes: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    ids: Dict[str, str] = {}
    for node in nodes:
        if node.get("category") != "BeamlineStage":
            continue
        name = canonical_stage_name(node.get("name") or "")
        if not name:
            continue
        # Prefer the short synoptic names; skip the concatenated .stages DOM blob.
        if " " in name and name.count(" ") > 4:
            continue
        ids[name.casefold()] = node["id"]
    return ids


def _motor_by_ophyd(nodes: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    for node in nodes:
        if node.get("category") != "Motor":
            continue
        ophyd = node.get("ophyd_name") or ""
        if ophyd:
            found[str(ophyd)] = node
    return found


def _stage_of_motor(
    motor_id: str,
    *,
    part_of: Dict[str, List[str]],
    stage_ids: set[str],
) -> Optional[str]:
    for obj in part_of.get(motor_id, []):
        if obj in stage_ids:
            return obj
    return None


def _append_term_rel(term: Dict[str, Any], relation: str, related_id: str, evidence: str) -> bool:
    rels = term.setdefault("relations", [])
    seen = {(r.get("relation"), r.get("related_id") or r.get("related_term")) for r in rels}
    key = (relation, related_id)
    if key in seen:
        return False
    rels.append(_rel(term["id"], relation, related_id, evidence))
    return True


def apply_motor_stage_map_to_terms(
    terms: List[Dict[str, Any]],
    mapping: Optional[Dict[str, Any]] = None,
    *,
    evidence: str = "bl1101_motor_stage_map.yaml",
) -> int:
    """Add Motor `part_of` stage (and alias `related_to`) onto term records."""
    mapping = mapping or load_motor_stage_map()
    stage_ids = _stage_id_by_name(terms)
    motors = _motor_by_ophyd(terms)
    added = 0
    for ophyd, stage_name in (mapping.get("motors") or {}).items():
        motor = motors.get(ophyd)
        sid = stage_ids.get(stage_name.casefold())
        if not motor or not sid:
            continue
        if _append_term_rel(motor, "part_of", sid, f"{evidence}: {ophyd} → {stage_name}"):
            added += 1
    for alias, canonical in (mapping.get("aliases") or {}).items():
        motor = motors.get(alias)
        if not motor:
            continue
        canon = motors.get(canonical)
        if canon:
            if _append_term_rel(
                motor,
                "related_to",
                canon["id"],
                f"{evidence}: {alias} aliases {canonical}",
            ):
                added += 1
            sid = None
            for rel in canon.get("relations") or []:
                if rel.get("relation") != "part_of":
                    continue
                obj = rel.get("related_id") or rel.get("related_term") or ""
                if obj in stage_ids.values():
                    sid = obj
                    break
            if not sid:
                mapped = (mapping.get("motors") or {}).get(canonical)
                sid = stage_ids.get((mapped or "").casefold()) if mapped else None
            if sid and _append_term_rel(
                motor,
                "part_of",
                sid,
                f"{evidence}: {alias} aliases {canonical}",
            ):
                added += 1
    return added


def apply_motor_stage_map_to_graph(
    graph: Dict[str, Any],
    mapping: Optional[Dict[str, Any]] = None,
    *,
    evidence: str = "bl1101_motor_stage_map.yaml",
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """JSON-first: add `rel:part_of` (and alias `rel:related_to`) on an ops KG."""
    mapping = mapping or load_motor_stage_map()
    things = list(graph.get("things") or [])
    assocs = list(graph.get("associations") or [])
    seen = {(e.get("subject"), e.get("predicate"), e.get("object")) for e in assocs}
    stage_ids_map = _stage_id_by_name(things)
    stage_id_set = set(stage_ids_map.values())
    motors = _motor_by_ophyd(things)
    part_of: Dict[str, List[str]] = {}
    for edge in assocs:
        if edge.get("predicate") == "rel:part_of":
            part_of.setdefault(edge["subject"], []).append(edge["object"])

    def add_edge(subject: str, predicate: str, obj: str, note: str) -> bool:
        sig = (subject, predicate, obj)
        if sig in seen:
            return False
        seen.add(sig)
        assocs.append(
            {
                "subject": subject,
                "predicate": predicate,
                "object": obj,
                "has_evidence": note,
            }
        )
        if predicate == "rel:part_of":
            part_of.setdefault(subject, []).append(obj)
        return True

    linked = 0
    aliased = 0
    skipped_unknown = 0
    for ophyd, stage_name in (mapping.get("motors") or {}).items():
        motor = motors.get(ophyd)
        sid = stage_ids_map.get(stage_name.casefold())
        if not motor or not sid:
            skipped_unknown += 1
            LOGGER.warning("Motor-stage map skip %s → %s (motor or stage missing)", ophyd, stage_name)
            continue
        if add_edge(motor["id"], "rel:part_of", sid, f"{evidence}: {ophyd} → {stage_name}"):
            linked += 1
    for alias, canonical in (mapping.get("aliases") or {}).items():
        motor = motors.get(alias)
        canon = motors.get(canonical)
        if not motor or not canon:
            skipped_unknown += 1
            LOGGER.warning("Motor alias skip %s → %s (missing node)", alias, canonical)
            continue
        if add_edge(
            motor["id"],
            "rel:related_to",
            canon["id"],
            f"{evidence}: {alias} aliases {canonical}",
        ):
            aliased += 1
        sid = _stage_of_motor(canon["id"], part_of=part_of, stage_ids=stage_id_set)
        if not sid:
            mapped = (mapping.get("motors") or {}).get(canonical)
            sid = stage_ids_map.get((mapped or "").casefold()) if mapped else None
        if sid and add_edge(
            motor["id"],
            "rel:part_of",
            sid,
            f"{evidence}: {alias} aliases {canonical}",
        ):
            linked += 1
    graph = dict(graph)
    graph["things"] = things
    graph["associations"] = assocs
    sort_graph(graph)
    stats = {
        "linked": linked,
        "aliased": aliased,
        "skipped_unknown": skipped_unknown,
        "motors": len(motors),
    }
    return graph, stats


def _terms_snapshot_for_kg(kg_path: Path) -> Path:
    match = SNAPSHOT_RE.match(kg_path.name)
    if not match:
        return TERMS_DIR / f"{kg_path.stem.replace('matkg_', 'extracted_terms_')}.json"
    return TERMS_DIR / f"extracted_terms_bl1101_v{match.group(1)}.json"


def write_kg_snapshot(
    graph: Dict[str, Any],
    version: int,
    *,
    kg_dir: Optional[Path] = None,
) -> Path:
    kg_path, _ = snapshot_paths(version, kg_dir=kg_dir)
    kg_path.parent.mkdir(parents=True, exist_ok=True)
    meta = graph.setdefault("metadata", {})
    snap = meta.setdefault("graph_snapshot", {})
    snap.update(
        {
            "name": f"matkg_bl1101_v{version}",
            "version": version,
            "written_at": utc_now(),
            "path": _rel_to_repo(kg_path),
        }
    )
    meta["ingest_version"] = INGEST_VERSION
    meta["extraction_version"] = INGEST_VERSION
    tmp = kg_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    digest = sha256_file(tmp)
    snap["sha256"] = digest
    snap["nodes"] = len(graph.get("things") or [])
    snap["edges"] = len(graph.get("associations") or [])
    tmp.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(kg_path)
    return kg_path


def run_motor_link_promote(
    *,
    source_kg: Path,
    version: Optional[int] = None,
    map_path: Optional[Path] = None,
    kg_dir: Optional[Path] = None,
    terms_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Copy an existing ops KG, apply the motor-stage map, write vN. Does not touch literature."""
    source_kg = source_kg.resolve()
    if not source_kg.exists():
        raise FileNotFoundError(source_kg)
    out_kg_dir = kg_dir or KG_DIR
    out_terms_dir = terms_dir or TERMS_DIR
    version = version if version is not None else next_snapshot_version(out_kg_dir)
    dest_kg, dest_terms = snapshot_paths(version, kg_dir=out_kg_dir, terms_dir=out_terms_dir)
    if dest_kg.resolve() == source_kg:
        raise ValueError(f"Refusing to overwrite source snapshot {source_kg}")
    mapping = load_motor_stage_map(map_path)
    graph = json.loads(source_kg.read_text(encoding="utf-8"))
    graph, stats = apply_motor_stage_map_to_graph(graph, mapping)
    meta = graph.setdefault("metadata", {})
    meta["schema_version"] = meta.get("schema_version") or SCHEMA_VERSION
    promote = {
        "kind": "motor_stage_links",
        "source_graph": _rel_to_repo(source_kg),
        "motor_stage_map": mapping.get("path") or _rel_to_repo(MOTOR_STAGE_MAP_PATH),
        "map_version": mapping.get("version"),
        "linked": stats["linked"],
        "aliased": stats["aliased"],
    }
    meta["promote"] = promote
    kg_path = write_kg_snapshot(graph, version, kg_dir=out_kg_dir)

    src_terms = _terms_snapshot_for_kg(source_kg)
    if src_terms.exists():
        records = json.loads(src_terms.read_text(encoding="utf-8"))
        terms = list(records.get("terms") or [])
        apply_motor_stage_map_to_terms(terms, mapping)
        records["terms"] = terms
        rec_meta = records.setdefault("metadata", {})
        rec_meta["ingest_version"] = INGEST_VERSION
        rec_meta["promote"] = promote
        rec_meta["graph_snapshot"] = dict(graph.get("metadata", {}).get("graph_snapshot") or {})
        dest_terms.parent.mkdir(parents=True, exist_ok=True)
        dest_terms.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    LOGGER.info(
        "Promoted %s → %s (+%d part_of, +%d alias related_to)",
        source_kg,
        kg_path,
        stats["linked"],
        stats["aliased"],
    )
    return {
        "kg_path": kg_path,
        "terms_path": dest_terms if dest_terms.exists() else None,
        "version": version,
        "nodes": len(graph.get("things") or []),
        "edges": len(graph.get("associations") or []),
        "stats": stats,
        "graph": graph,
        "sources_landed": [promote],
        "sources_skipped": [],
    }


def terms_from_people(
    als: Dict[str, Any],
    blueprint: Dict[str, Any],
    als_source: str,
    blueprint_source: str,
) -> List[Dict[str, Any]]:
    """Typed Person / BeamlineScientist nodes in beamline: only. No science-KG hubs."""
    people: List[Dict[str, str]] = []
    seen = set()
    for raw in list(als.get("people") or []) + list(blueprint.get("people") or []):
        name = _clean_person_name(raw.get("name") or "")
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        people.append({**raw, "name": name})
    if not people:
        people.extend(
            parse_als_contact_people(als.get("facts") or {}, als.get("text") or "", source=ALS_PAGE_URL)
        )
        for extra in parse_blueprint_people(blueprint):
            if extra["name"].casefold() not in {p["name"].casefold() for p in people}:
                people.append(extra)
    if not people:
        return []
    role_id = beam_id("Role", "BeamlineScientist")
    terms: List[Dict[str, Any]] = [
        make_term(
            role_id,
            "Beamline scientist",
            "BeamlineScientist",
            "Documented scientific contact / beamline scientist role for ALS 11.0.1.2.",
            als_source,
            source_url=ALS_PAGE_URL,
        )
    ]
    for person in people:
        pid = beam_id("Person", person["name"])
        source = person.get("source") or als_source
        evidence = person.get("evidence") or source
        definition = (
            f"{person['name']} is a documented 11.0.1.2 contact ({person.get('role') or 'BeamlineScientist'}). "
            f"Source: {source}."
        )
        if person.get("email"):
            definition += f" Email listed on the ALS page: {person['email']}."
        terms.append(
            make_term(
                pid,
                person["name"],
                "Person",
                definition,
                source,
                relations=[
                    _rel(pid, "hasRole", role_id, evidence),
                    _rel(pid, "supports", BEAMLINE_ID, evidence),
                ],
                source_url=source if str(source).startswith("http") else ALS_PAGE_URL,
                source_papers=[source, blueprint_source] if source != blueprint_source else [source],
                email=person.get("email") or None,
                role=person.get("role") or "BeamlineScientist",
            )
        )
    return terms


def ingest_records(
    *,
    als: Dict[str, Any],
    blueprint: Dict[str, Any],
    als_source: str = "als.lbl.gov/beamlines/11-0-1-2",
    blueprint_source: str = "blueprint_bl11012_version09092026.html",
    repo_payloads: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
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
    snippets: List[Dict[str, Any]] = []
    for payload in repo_payloads or []:
        extra_terms, extra_snips = attach_repo_terms(
            payload["spec"],
            payload.get("root") or Path("."),
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


def records_to_graph(records: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
    graph = json2kg.build_graph(
        records["terms"],
        code_snippets=records.get("code_snippets") or [],
        default_id_prefix=NAMESPACE,
        strict_snippets=False,
    )
    graph = sort_graph(graph)
    graph["metadata"] = metadata
    return graph


def ingest_fixture(html: str, python_files: Sequence[Path]) -> Dict[str, Any]:
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _clear_work(from_scratch: bool) -> None:
    if from_scratch and WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    if from_scratch and PAGES_DIR.exists():
        shutil.rmtree(PAGES_DIR)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    READMES_DIR.mkdir(parents=True, exist_ok=True)


def _ingest_repos(offline: bool) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    landed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    payloads: List[Dict[str, Any]] = []
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
        symbols: List[Dict[str, Any]] = []
        snippets: List[Dict[str, Any]] = []
        for py in expand_globs(dest, spec.python_globs):
            py_symbols, py_snips = extract_python_symbols(py, repo_name=spec.name, commit=info.get("sha") or "")
            for item in py_symbols:
                if "file" in item:
                    try:
                        item["file"] = str(py.relative_to(dest))
                    except ValueError:
                        pass
            for snip in py_snips:
                try:
                    snip["source_file_path"] = str(py.relative_to(dest))
                except ValueError:
                    pass
            symbols.extend(py_symbols)
            snippets.extend(py_snips)
        compose = []
        for yml in expand_globs(dest, spec.compose_globs):
            compose.extend(parse_compose_services(yml))
        markdown: List[Tuple[str, str]] = []
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


def run_ingest(*, from_scratch: bool = False, offline: bool = False) -> Dict[str, Any]:
    _clear_work(from_scratch)
    sources_landed: List[Dict[str, Any]] = []
    sources_skipped: List[Dict[str, Any]] = []

    als_snapshot: Dict[str, Any]
    als_html = ""
    als_dest = PAGES_DIR / "als_11-0-1-2.html"
    if offline and als_dest.exists():
        als_html = als_dest.read_text(encoding="utf-8", errors="replace")
        als_snapshot = {
            "url": ALS_PAGE_URL,
            "status": "ok",
            "fetched_at": datetime.fromtimestamp(als_dest.stat().st_mtime, timezone.utc).date().isoformat(),
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


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
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


def main(argv: Optional[Sequence[str]] = None) -> int:
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
        print(
            f"bl1101 v{result['version']}: {result['nodes']} nodes, {result['edges']} edges → {result['kg_path']}"
        )
        print(
            "motor links:",
            f"+{result['stats']['linked']} part_of,",
            f"+{result['stats']['aliased']} alias related_to",
        )
        return 0
    result = run_ingest(from_scratch=args.from_scratch, offline=args.offline)
    print(
        f"bl1101 v{result['version']}: {result['nodes']} nodes, {result['edges']} edges → {result['kg_path']}"
    )
    print("landed:", ", ".join(item.get("name", "?") for item in result["sources_landed"]) or "(none)")
    if result["sources_skipped"]:
        skipped = ", ".join(
            f"{item.get('name')} ({item.get('reason', item.get('status'))})"
            for item in result["sources_skipped"]
        )
        print("skipped:", skipped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
