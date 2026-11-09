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
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
import hashlib as hashlib  # re-exported: tests reach bl1101_ingest.hashlib
import json
import logging
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any
from urllib.request import urlopen as urlopen  # re-exported: tests patch bl1101_ingest.urlopen

import yaml

from . import json2kg
from .cli_output import output
from .graph.imports.bl1101 import common as _common
from .graph.imports.bl1101 import compat as _compat
from .graph.imports.bl1101 import fetch as _fetch
from .graph.imports.bl1101 import parse_als as _parse_als
from .graph.imports.bl1101 import parse_blueprint as _parse_blueprint
from .graph.imports.bl1101 import paths as _paths
from .graph.imports.bl1101 import symbols as _symbols

# Split modules (T17B/H8 P3): the legacy path re-exports every moved name, same objects.
from .graph.imports.bl1101.common import LOGGER as LOGGER
from .graph.imports.bl1101.common import _rel_to_repo as _rel_to_repo
from .graph.imports.bl1101.common import _texts as _texts
from .graph.imports.bl1101.common import _xp_elems as _xp_elems
from .graph.imports.bl1101.common import _xp_strs as _xp_strs
from .graph.imports.bl1101.common import beam_id as beam_id
from .graph.imports.bl1101.common import is_secret_path as is_secret_path
from .graph.imports.bl1101.common import sha256_bytes as sha256_bytes
from .graph.imports.bl1101.common import sha256_file as sha256_file
from .graph.imports.bl1101.common import should_skip_file as should_skip_file
from .graph.imports.bl1101.common import slug as slug
from .graph.imports.bl1101.common import strip_tags as strip_tags
from .graph.imports.bl1101.common import utc_now as utc_now
from .graph.imports.bl1101.fetch import _run_git as _run_git
from .graph.imports.bl1101.fetch import fetch_url as fetch_url
from .graph.imports.bl1101.fetch import git_sha as git_sha
from .graph.imports.bl1101.fetch import sync_repo as sync_repo
from .graph.imports.bl1101.parse_als import _clean_person_name as _clean_person_name
from .graph.imports.bl1101.parse_als import _email_near as _email_near
from .graph.imports.bl1101.parse_als import parse_als_contact_people as parse_als_contact_people
from .graph.imports.bl1101.parse_als import parse_als_page as parse_als_page
from .graph.imports.bl1101.parse_blueprint import _index_ci as _index_ci
from .graph.imports.bl1101.parse_blueprint import _ophyd_name_from_handle as _ophyd_name_from_handle
from .graph.imports.bl1101.parse_blueprint import _svg_coord as _svg_coord
from .graph.imports.bl1101.parse_blueprint import canonical_stage_name as canonical_stage_name
from .graph.imports.bl1101.parse_blueprint import ensure_m103_before_exit_slits as ensure_m103_before_exit_slits
from .graph.imports.bl1101.parse_blueprint import finalize_beam_path as finalize_beam_path
from .graph.imports.bl1101.parse_blueprint import parse_arrow_sequence as parse_arrow_sequence
from .graph.imports.bl1101.parse_blueprint import parse_bcs2sim_beam_path as parse_bcs2sim_beam_path
from .graph.imports.bl1101.parse_blueprint import parse_blueprint_html as parse_blueprint_html
from .graph.imports.bl1101.parse_blueprint import parse_blueprint_people as parse_blueprint_people
from .graph.imports.bl1101.parse_blueprint import parse_figcaption_beam_path as parse_figcaption_beam_path
from .graph.imports.bl1101.parse_blueprint import parse_sheet_a_svg_order as parse_sheet_a_svg_order
from .graph.imports.bl1101.parse_blueprint import resolve_beam_path_order as resolve_beam_path_order
from .graph.imports.bl1101.paths import BLUEPRINT_PATH as BLUEPRINT_PATH
from .graph.imports.bl1101.paths import CACHE_DIR as CACHE_DIR
from .graph.imports.bl1101.paths import KG_DIR as KG_DIR
from .graph.imports.bl1101.paths import MOTOR_STAGE_MAP_PATH as MOTOR_STAGE_MAP_PATH
from .graph.imports.bl1101.paths import PAGES_DIR as PAGES_DIR
from .graph.imports.bl1101.paths import READMES_DIR as READMES_DIR
from .graph.imports.bl1101.paths import REPOS_DIR as REPOS_DIR
from .graph.imports.bl1101.paths import REPO_ROOT as REPO_ROOT
from .graph.imports.bl1101.paths import SCHEMA_PATH as SCHEMA_PATH
from .graph.imports.bl1101.paths import TERMS_DIR as TERMS_DIR
from .graph.imports.bl1101.paths import WORK_DIR as WORK_DIR
from .graph.imports.bl1101.specs import ALS_PAGE_URL as ALS_PAGE_URL  # re-exported: the legacy path keeps these names
from .graph.imports.bl1101.specs import ALS_SOURCE as ALS_SOURCE
from .graph.imports.bl1101.specs import BCS2SIM_PATH_GLOBS as BCS2SIM_PATH_GLOBS
from .graph.imports.bl1101.specs import BEAMLINE_ID as BEAMLINE_ID
from .graph.imports.bl1101.specs import BEAM_PATH_CHAMBER_TAIL as BEAM_PATH_CHAMBER_TAIL
from .graph.imports.bl1101.specs import BEAM_PATH_PREFIX as BEAM_PATH_PREFIX
from .graph.imports.bl1101.specs import BEAM_PATH_RELATIONS as BEAM_PATH_RELATIONS
from .graph.imports.bl1101.specs import BLUEPRINT_PERSON_RE as BLUEPRINT_PERSON_RE
from .graph.imports.bl1101.specs import BlueprintDevice as BlueprintDevice
from .graph.imports.bl1101.specs import BlueprintStage as BlueprintStage
from .graph.imports.bl1101.specs import DEVICE_BASE_HINTS as DEVICE_BASE_HINTS
from .graph.imports.bl1101.specs import EMAIL_RE as EMAIL_RE
from .graph.imports.bl1101.specs import INGEST_VERSION as INGEST_VERSION
from .graph.imports.bl1101.specs import KIND_CATEGORY as KIND_CATEGORY
from .graph.imports.bl1101.specs import MAX_SNIPPET_LINES as MAX_SNIPPET_LINES
from .graph.imports.bl1101.specs import NAMESPACE as NAMESPACE
from .graph.imports.bl1101.specs import PERSON_NAME_RE as PERSON_NAME_RE
from .graph.imports.bl1101.specs import PERSON_NAME_STOPWORDS as PERSON_NAME_STOPWORDS
from .graph.imports.bl1101.specs import PUBLIC_FUNCTIONS as PUBLIC_FUNCTIONS
from .graph.imports.bl1101.specs import REPO_SPECS as REPO_SPECS
from .graph.imports.bl1101.specs import RepoSpec as RepoSpec
from .graph.imports.bl1101.specs import SCHEMA_VERSION as SCHEMA_VERSION
from .graph.imports.bl1101.specs import SECRET_NAMES as SECRET_NAMES
from .graph.imports.bl1101.specs import SECRET_PREFIXES as SECRET_PREFIXES
from .graph.imports.bl1101.specs import SKIP_DIR_NAMES as SKIP_DIR_NAMES
from .graph.imports.bl1101.specs import SKIP_DIR_PREFIXES as SKIP_DIR_PREFIXES
from .graph.imports.bl1101.specs import SKIP_PERSON_NAMES as SKIP_PERSON_NAMES
from .graph.imports.bl1101.specs import SKIP_SUFFIXES as SKIP_SUFFIXES
from .graph.imports.bl1101.specs import SNAPSHOT_RE as SNAPSHOT_RE
from .graph.imports.bl1101.specs import SOFTWARE_ALIAS as SOFTWARE_ALIAS
from .graph.imports.bl1101.specs import STAGE_NAME_ALIASES as STAGE_NAME_ALIASES
from .graph.imports.bl1101.specs import USER_AGENT as USER_AGENT
from .graph.imports.bl1101.symbols import _base_names as _base_names
from .graph.imports.bl1101.symbols import _is_device_class as _is_device_class
from .graph.imports.bl1101.symbols import _is_plan_function as _is_plan_function
from .graph.imports.bl1101.symbols import expand_globs as expand_globs
from .graph.imports.bl1101.symbols import extract_markdown_software as extract_markdown_software
from .graph.imports.bl1101.symbols import extract_python_symbols as extract_python_symbols
from .graph.imports.bl1101.symbols import extract_ts_fences as extract_ts_fences
from .graph.imports.bl1101.symbols import parse_compose_services as parse_compose_services


def next_snapshot_version(kg_dir: Path) -> int:
    """Return the next vN that does not yet exist (1 if none)."""
    versions: list[int] = []
    if kg_dir.exists():
        for path in kg_dir.iterdir():
            match = SNAPSHOT_RE.match(path.name)
            if match:
                versions.append(int(match.group(1)))
    return (max(versions) + 1) if versions else 1


def snapshot_paths(
    version: int,
    *,
    kg_dir: Path | None = None,
    terms_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Return the on-disk paths of one graph snapshot version.

    Args:
        version: Snapshot number N.
        kg_dir: Directory for the graph file. Defaults to ``KG_DIR``.
        terms_dir: Directory for the extracted-terms file. Defaults to ``TERMS_DIR``.

    Returns:
        ``(kg_path, terms_path)`` for ``matkg_bl1101_vN.json`` and
        ``extracted_terms_bl1101_vN.json``. Nothing is created or checked.
    """
    kg_dir = kg_dir or KG_DIR
    terms_dir = terms_dir or TERMS_DIR
    return (
        kg_dir / f"matkg_bl1101_v{version}.json",
        terms_dir / f"extracted_terms_bl1101_v{version}.json",
    )








def _rel(term_id: str, relation: str, related_id: str, evidence: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
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
    relations: list[dict[str, Any]] | None = None,
    **extra: object,
) -> dict[str, Any]:
    """Build one term record in the shape the ``json2kg`` graph builder expects.

    Args:
        term_id: Node ID, normally from :func:`beam_id`.
        name: Display name; stored as both ``term`` and ``name``.
        category: Ontology category. The record ``type`` becomes
            ``beamline:<category>``.
        definition: Free-text definition.
        source: Provenance string stored as the single entry of ``source_papers``.
        relations: Relation dicts from :func:`_rel`. Defaults to none.
        **extra: Additional fields copied onto the record, except values that are
            ``None``, ``""``, ``[]`` or ``{}``. A key that collides with a base
            field overrides it.

    Returns:
        The term dict.
    """
    term: dict[str, Any] = {
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





































# ---------------------------------------------------------------------------
# GitHub / Python / compose
# ---------------------------------------------------------------------------























# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def _energy_from_sources(als: dict[str, Any], blueprint: dict[str, Any]) -> tuple[str, str]:
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


def terms_from_als(als: dict[str, Any], source: str) -> list[dict[str, Any]]:
    """Build the term records that come from the public ALS beamline page.

    Covers the fixed technique, polarization, scattering-geometry, absorption-edge
    and sample-class terms, the photon-energy term, and the EPU5 source. Names and
    definitions of the fixed terms are hard-coded; only energy, notes, source and
    contacts are read from the parsed page.

    Args:
        als: Output of :func:`parse_als_page`.
        source: Provenance string stamped on every term.

    Returns:
        The list of term dicts.
    """
    facts = als.get("facts") or {}
    terms: list[dict[str, Any]] = []
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
    blueprint: dict[str, Any],
    source: str,
    stage_ids: dict[str, str],
) -> list[dict[str, Any]]:
    """Directed topology along the optical beam path (not Sheet E inventory order)."""
    path = [name for name in (blueprint.get("beam_path") or []) if canonical_stage_name(name)]
    if len(path) < 2:
        return []
    terms: list[dict[str, Any]] = []
    ids: list[str] = []
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


def terms_from_blueprint(blueprint: dict[str, Any], source: str) -> list[dict[str, Any]]:
    """Build term records from the parsed blueprint.

    Emits the reflectometer endstation, one term per stage, the directed beam-path
    stage terms, one term per device plus its PV and Ophyd-class terms, operating
    rules as ``Condition`` terms, and the AXIS-SXR-40 detector.

    Args:
        blueprint: Output of :func:`parse_blueprint_html`.
        source: Provenance string stamped on every term.

    Returns:
        The list of term dicts. Duplicate IDs are expected and are merged later by
        :func:`merge_terms`.
    """
    terms: list[dict[str, Any]] = []
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
    stage_id_by_dev: dict[str, str] = {}
    stage_ids: dict[str, str] = {}
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


def beamline_seed(als: dict[str, Any], blueprint: dict[str, Any], als_source: str, bp_source: str) -> dict[str, Any]:
    """Build the root beamline term that links to the other top-level terms.

    Args:
        als: Output of :func:`parse_als_page`.
        blueprint: Output of :func:`parse_blueprint_html`.
        als_source: Provenance string for the ALS page.
        bp_source: Provenance string for the blueprint.

    Returns:
        The term for ``BEAMLINE_ID``. Its relations point at the endstation,
        detector, software catalog, techniques, geometries, energy and polarization
        terms; those targets are built elsewhere. Motors, detectors and plans are
        attached later by :func:`ingest_records`.
    """
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


def software_catalog() -> list[dict[str, Any]]:
    """Return the fixed catalog of software terms (Bluesky, Tiled, Finch and related components)."""
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
    symbols: list[dict[str, Any]],
    snippets: list[dict[str, Any]],
    compose_services: list[dict[str, str]],
    md_summaries: list[tuple[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Turn one repo's extracted payload into term records.

    Args:
        spec: Repo the payload came from.
        root: Local clone root. Unused.
        sha: Commit SHA of the clone; may be empty.
        symbols: Records from :func:`extract_python_symbols`.
        snippets: Curated code snippets for the repo.
        compose_services: Records from :func:`parse_compose_services`.
        md_summaries: ``(relative_path, summary)`` pairs; empty summaries are skipped.

    Returns:
        ``(terms, snippets)``. ``snippets`` is the input list, unchanged. Terms
        include the repo term, symbols, compose services and Markdown docs, plus
        documented data-path or config terms for a few specific repos.
    """
    terms: list[dict[str, Any]] = []
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


def merge_terms(batches: Iterable[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Merge term batches into one list with unique IDs.

    On an ID collision the first record is kept and extended in place: the longer
    definition wins, ``source_papers`` and relations are unioned (relations keyed
    by relation and target), and other fields are filled only if absent.

    Args:
        batches: Iterables of term dicts. Colliding terms are mutated.

    Returns:
        The merged terms sorted by ID.
    """
    merged: dict[str, dict[str, Any]] = {}
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
                key: Any = (rel.get("relation"), rel.get("related_id") or rel.get("related_term"))  # Any: `key` is reused as a str below
                if key not in seen:
                    rels.append(rel)
                    seen.add(key)
            for key, value in term.items():
                if key in existing or value in (None, "", [], {}):
                    continue
                existing[key] = value
    return [merged[k] for k in sorted(merged)]


def sort_graph(graph: dict[str, Any]) -> dict[str, Any]:
    """Sort ``things`` by ID and ``associations`` by (subject, predicate, object) for deterministic output.

    Mutates ``graph`` and returns it.
    """
    graph["things"] = sorted(graph.get("things") or [], key=lambda n: n.get("id") or "")
    graph["associations"] = sorted(
        graph.get("associations") or [],
        key=lambda e: (e.get("subject") or "", e.get("predicate") or "", e.get("object") or ""),
    )
    return graph


# ---------------------------------------------------------------------------
# v4 motor → stage linking (JSON-first; mapping file is the source of truth)
# ---------------------------------------------------------------------------

def load_motor_stage_map(path: Path | None = None) -> dict[str, Any]:
    """Load ophyd_name → stage assignments. Missing file → empty map."""
    dest = path or MOTOR_STAGE_MAP_PATH
    if not dest.exists():
        LOGGER.warning("Motor-stage map missing: %s", dest)
        return {"version": 0, "motors": {}, "aliases": {}, "leave_on_beamline": {}}
    data = yaml.safe_load(dest.read_text(encoding="utf-8")) or {}
    motors: dict[str, str] = {}
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


def _stage_id_by_name(nodes: Iterable[dict[str, Any]]) -> dict[str, str]:
    ids: dict[str, str] = {}
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


def _motor_by_ophyd(nodes: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
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
    part_of: dict[str, list[str]],
    stage_ids: set[str],
) -> str | None:
    for obj in part_of.get(motor_id, []):
        if obj in stage_ids:
            return obj
    return None


def _append_term_rel(term: dict[str, Any], relation: str, related_id: str, evidence: str) -> bool:
    rels = term.setdefault("relations", [])
    seen = {(r.get("relation"), r.get("related_id") or r.get("related_term")) for r in rels}
    key = (relation, related_id)
    if key in seen:
        return False
    rels.append(_rel(term["id"], relation, related_id, evidence))
    return True


def apply_motor_stage_map_to_terms(
    terms: list[dict[str, Any]],
    mapping: dict[str, Any] | None = None,
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
    graph: dict[str, Any],
    mapping: dict[str, Any] | None = None,
    *,
    evidence: str = "bl1101_motor_stage_map.yaml",
) -> tuple[dict[str, Any], dict[str, int]]:
    """JSON-first: add `rel:part_of` (and alias `rel:related_to`) on an ops KG."""
    mapping = mapping or load_motor_stage_map()
    things = list(graph.get("things") or [])
    assocs = list(graph.get("associations") or [])
    seen = {(e.get("subject"), e.get("predicate"), e.get("object")) for e in assocs}
    stage_ids_map = _stage_id_by_name(things)
    stage_id_set = set(stage_ids_map.values())
    motors = _motor_by_ophyd(things)
    part_of: dict[str, list[str]] = {}
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
    graph: dict[str, Any],
    version: int,
    *,
    kg_dir: Path | None = None,
) -> Path:
    """Write ``graph`` as the ``matkg_bl1101_v<version>.json`` snapshot.

    Updates ``graph['metadata']`` in place with snapshot info (name, version,
    timestamp, repo-relative path, sha256, node and edge counts) and the ingest
    version, then writes through a temporary file that is moved into place.

    Args:
        graph: Graph dict with ``things`` and ``associations``. Mutated.
        version: Snapshot number N.
        kg_dir: Output directory. Defaults to ``KG_DIR``.

    Returns:
        Path of the written file.
    """
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
    version: int | None = None,
    map_path: Path | None = None,
    kg_dir: Path | None = None,
    terms_dir: Path | None = None,
) -> dict[str, Any]:
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
    als: dict[str, Any],
    blueprint: dict[str, Any],
    als_source: str,
    blueprint_source: str,
) -> list[dict[str, Any]]:
    """Typed Person / BeamlineScientist nodes in beamline: only. No science-KG hubs."""
    people: list[dict[str, str]] = []
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
    terms: list[dict[str, Any]] = [
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


if __name__ == "__main__":
    raise SystemExit(main())


# A patch of a re-exported name (``monkeypatch.setattr(bl1101_ingest, ...)``) must also reach the split modules.
_compat.install(sys.modules[__name__], (_common, _paths, _fetch, _parse_als, _parse_blueprint, _symbols))
