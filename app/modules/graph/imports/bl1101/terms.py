"""Term assembly for the BL11.0.1.2 ingest: records from the parsed sources, merging, graph ordering, motor-stage linking."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import re
from typing import Any

import yaml

from .common import LOGGER, _rel_to_repo, beam_id
from .parse_als import _clean_person_name, parse_als_contact_people
from .parse_blueprint import canonical_stage_name, parse_blueprint_people
from .paths import MOTOR_STAGE_MAP_PATH
from .specs import ALS_PAGE_URL, BEAM_PATH_RELATIONS, BEAMLINE_ID, NAMESPACE, SOFTWARE_ALIAS, RepoSpec


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
