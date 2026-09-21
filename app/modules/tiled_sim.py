"""Simulated Tiled Graph identity overlay for the BL 11.0.1.2 ops KG.

Mirrors splash_links / Tiled GraphQL *entityType* strings (ESAF, Proposal,
Sample, BlueskyRun) as JSON ``category`` values and writes them into the next
``matkg_bl1101_vN.json`` snapshot. ``--to-tiled`` writes the same identity
graph into local Tiled GraphQL so PageESAFs is live, not a JSON fallback.

Reproducible: seed ``20260916`` in ``storage/schema/tiled_sim_seed.yaml``.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import yaml

from app.modules import json2kg
from app.modules.bl1101_ingest import (
    BEAMLINE_ID,
    KG_DIR,
    NAMESPACE,
    REPO_ROOT,
    SCHEMA_PATH,
    TERMS_DIR,
    _rel,
    _rel_to_repo,
    beam_id,
    make_term,
    next_snapshot_version,
    sha256_file,
    snapshot_paths,
    sort_graph,
    utc_now,
    write_kg_snapshot,
)

LOGGER = logging.getLogger("tiled_sim")

SEED_PATH = REPO_ROOT / "storage/schema/tiled_sim_seed.yaml"
FIXTURE_PATH = REPO_ROOT / "storage/fixtures/tiled_sim_rsoxs.json"
PROMOTE_KIND = "tiled_sim"
DEFAULT_SEED = 20260916
PROV_PREDICATES = {
    "wasDerivedFrom": "prov:wasDerivedFrom",
    "used": "prov:used",
    "wasGeneratedBy": "prov:wasGeneratedBy",
    "wasAttributedTo": "prov:wasAttributedTo",
    "wasAssociatedWith": "prov:wasAssociatedWith",
}
TILED_PUSH_DEFAULT_URI = "http://127.0.0.1:8001"
GRAPHQL_IDENTITY_RELS = {
    "hasProposal": "rel:hasProposal",
    "hasScan": "rel:hasScan",
    "hasSample": "rel:hasSample",
    "used": "prov:used",
}
_CREATE_ENTITY = """
mutation CreateIdentityEntity($input: CreateEntityInput!) {
  createEntity(input: $input) { id name uri entityType }
}
"""
_CREATE_LINK = """
mutation CreateIdentityLink($input: CreateLinkInput!) {
  createLink(input: $input) { id predicate }
}
"""
_PAGE_ENTITIES = """
query PageIdentityEntities($entityType: String, $limit: Int!, $offset: Int!) {
  entities(entityType: $entityType, limit: $limit, offset: $offset) {
    id name uri entityType
  }
}
"""
_PAGE_LINKS = """
query PageIdentityLinks($predicate: String, $limit: Int!, $offset: Int!) {
  links(predicate: $predicate, limit: $limit, offset: $offset) {
    id
    predicate
    subject { id uri }
    object { id uri }
  }
}
"""

_RSOXS_SNAPSHOT_RE = re.compile(r"^matkg_rsoxs_v(\d+)\.json$")


def load_seed(path: Optional[Path] = None) -> Dict[str, Any]:
    dest = path or SEED_PATH
    data = yaml.safe_load(dest.read_text(encoding="utf-8")) or {}
    data["path"] = _rel_to_repo(dest)
    data.setdefault("seed", DEFAULT_SEED)
    return data


def _choice(rng: random.Random, items: Sequence[Any]) -> Any:
    if not items:
        raise ValueError("empty choice list")
    return items[rng.randrange(len(items))]


def _uuid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128)))


def _energy_range(seed: Dict[str, Any], edge: str) -> Tuple[float, float]:
    table = seed.get("energy_eV") or {}
    pair = table.get(edge) or table.get("default") or [165.0, 1500.0]
    return float(pair[0]), float(pair[1])


def generate_fixture(
    seed: Optional[Dict[str, Any]] = None,
    *,
    rng_seed: Optional[int] = None,
) -> Dict[str, Any]:
    """Build the ESAF → Proposal → Sample / BlueskyRun tree (no KG writes)."""
    seed = seed or load_seed()
    documented = int(seed.get("seed") or DEFAULT_SEED)
    rng = random.Random(documented if rng_seed is None else rng_seed)
    prop_lo, prop_hi = seed.get("proposals_per_esaf") or [0, 10]
    scan_lo, scan_hi = seed.get("scans_per_proposal") or [0, 20]
    samp_lo, samp_hi = seed.get("samples_per_proposal") or [1, 20]
    themes = seed.get("themes") or {}
    plans = seed.get("plans") or []
    motors = list(seed.get("motors") or [])
    polarizations = seed.get("polarizations") or []
    origin = datetime(2026, 3, 1, tzinfo=timezone.utc)
    esafs: List[Dict[str, Any]] = []
    scan_counter = 140

    for spec in seed.get("esafs") or []:
        theme = themes.get(spec.get("theme") or "") or {}
        materials = list(theme.get("materials") or ["P3HT"])
        techniques = list(theme.get("techniques") or ["RSoXS"])
        edges = list(theme.get("edges") or ["C K-edge"])
        contrasts = list(theme.get("contrast") or ["composition contrast"])
        geometries = list(theme.get("geometries") or ["transmission"])
        scan_modes = list(theme.get("scan_modes") or ["azimuth scan"])
        n_proposals = rng.randint(int(prop_lo), int(prop_hi))
        esaf_number = str(spec["esaf_number"])
        esaf_id = beam_id("ESAF", esaf_number)
        proposals: List[Dict[str, Any]] = []
        for p_idx in range(n_proposals):
            proposal_code = f"P{esaf_number.replace('-', '')}-{p_idx + 1:02d}"
            proposal_id = beam_id("Proposal", proposal_code)
            n_samples = rng.randint(int(samp_lo), int(samp_hi))
            n_scans = rng.randint(int(scan_lo), int(scan_hi))
            samples: List[Dict[str, Any]] = []
            for s_idx in range(n_samples):
                material = _choice(rng, materials)
                sample_code = f"{proposal_code}-S{s_idx + 1:02d}"
                samples.append(
                    {
                        "id": beam_id("Sample", sample_code),
                        "name": f"{material} {sample_code}",
                        "sample_code": sample_code,
                        "material": material,
                        "bar_position": s_idx + 1,
                    }
                )
            scans: List[Dict[str, Any]] = []
            for _ in range(n_scans):
                plan = _choice(rng, plans) if plans else {"name": "count", "id": beam_id("plan", "count")}
                sample = _choice(rng, samples)
                edge = _choice(rng, edges)
                lo, hi = _energy_range(seed, edge)
                energy = round(rng.uniform(lo, hi), 2)
                pol = _choice(rng, polarizations) if polarizations else {"name": "linear polarization"}
                uid = _uuid(rng)
                scan_counter += 1
                started = origin + timedelta(hours=rng.randint(0, 2000), minutes=rng.randint(0, 59))
                motor_ids = [motors[i] for i in sorted(rng.sample(range(len(motors)), k=min(3, len(motors))))] if motors else []
                scans.append(
                    {
                        "id": beam_id("BlueskyRun", uid),
                        "uid": uid,
                        "scan_id": scan_counter,
                        "name": f"scan {scan_counter} {plan.get('name')}",
                        "plan_name": plan.get("name"),
                        "plan_id": plan.get("id"),
                        "plan_region": plan.get("region"),
                        "repo_id": plan.get("repo_id") or seed.get("tiled_repo_id"),
                        "start_time": started.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                        "catalog_path": [esaf_number, proposal_code, uid],
                        "nodeId": f"sim:tiled:/{esaf_number}/{proposal_code}/{uid}",
                        "uri": f"tiled:run/{uid}",
                        "photon_energy_eV": energy,
                        "absorption_edge": edge,
                        "polarization": pol.get("name"),
                        "polarization_id": pol.get("id"),
                        "geometry": _choice(rng, geometries),
                        "scan_mode": _choice(rng, scan_modes),
                        "scattering_technique": _choice(rng, techniques),
                        "contrast": _choice(rng, contrasts),
                        "material": sample["material"],
                        "sample_id": sample["id"],
                        "motor_ids": motor_ids,
                        "detector_id": seed.get("detector_id"),
                    }
                )
            proposals.append(
                {
                    "id": proposal_id,
                    "proposal_code": proposal_code,
                    "name": f"Proposal {proposal_code}",
                    "samples": samples,
                    "scans": scans,
                }
            )
        esafs.append(
            {
                "id": esaf_id,
                "esaf_number": esaf_number,
                "name": f"ESAF {esaf_number}",
                "title": spec.get("title"),
                "scientist": spec.get("scientist"),
                "scientist_id": spec.get("scientist_id"),
                "co_scientist": spec.get("co_scientist"),
                "co_scientist_id": spec.get("co_scientist_id"),
                "theme": spec.get("theme"),
                "proposals": proposals,
            }
        )

    stats = fixture_stats(esafs)
    return {
        "seed": documented if rng_seed is None else rng_seed,
        "seed_path": seed.get("path") or _rel_to_repo(SEED_PATH),
        "generated_at": utc_now(),
        "graphql_entity_types": ["ESAF", "Proposal", "Sample", "BlueskyRun"],
        "live_tiled": False,
        "esafs": esafs,
        "stats": stats,
    }


def fixture_stats(esafs: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    proposals = [p for e in esafs for p in e.get("proposals") or []]
    scans = [s for p in proposals for s in p.get("scans") or []]
    samples = [s for p in proposals for s in p.get("samples") or []]
    return {
        "esafs": len(esafs),
        "proposals": len(proposals),
        "scans": len(scans),
        "samples": len(samples),
    }


def write_fixture(fixture: Dict[str, Any], path: Optional[Path] = None) -> Path:
    dest = path or FIXTURE_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return dest


def load_fixture(path: Optional[Path] = None) -> Dict[str, Any]:
    dest = path or FIXTURE_PATH
    return json.loads(dest.read_text(encoding="utf-8"))


def _load_repo_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_path, override=False)


def resolve_push_uri(uri: Optional[str] = None) -> str:
    """Local Tiled only (loopback). Never ALS production."""
    from app.modules.tiled_graph import is_als_production_tiled_host, normalize_tiled_uri

    raw = (uri or os.environ.get("TILED_URI") or TILED_PUSH_DEFAULT_URI).strip()
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    host = (parsed.hostname or "").lower()
    if is_als_production_tiled_host(host):
        raise ValueError(
            "Refusing to write to ALS production Tiled. Populate 127.0.0.1:8001 only."
        )
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(f"Refusing non-local Tiled host {host!r}; use 127.0.0.1:8001.")
    return normalize_tiled_uri(raw, default=TILED_PUSH_DEFAULT_URI)


def _compact_props(values: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, val in values.items():
        if val is None or val == "":
            continue
        if isinstance(val, (str, int, float, bool)):
            out[key] = val
        elif isinstance(val, (list, tuple)):
            items = [item for item in val if item not in (None, "")]
            if items:
                out[key] = list(items)
        elif isinstance(val, dict):
            nested = _compact_props(val)
            if nested:
                out[key] = nested
    return out


def _scan_uri(scan: Dict[str, Any]) -> str:
    return str(scan.get("uri") or scan.get("id") or "").strip()


def iter_identity_entities(fixture: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Flatten the sim fixture into Tiled createEntity payloads."""
    rows: List[Dict[str, Any]] = []
    for esaf in fixture.get("esafs") or []:
        rows.append(
            {
                "entityType": "ESAF",
                "name": str(esaf.get("name") or esaf.get("esaf_number") or esaf["id"]),
                "uri": str(esaf["id"]),
                "properties": _compact_props(
                    {
                        "esaf_number": esaf.get("esaf_number"),
                        "title": esaf.get("title"),
                        "scientist": esaf.get("scientist"),
                        "co_scientist": esaf.get("co_scientist"),
                        "theme": esaf.get("theme"),
                    }
                ),
            }
        )
        for proposal in esaf.get("proposals") or []:
            rows.append(
                {
                    "entityType": "Proposal",
                    "name": str(proposal.get("name") or proposal.get("proposal_code") or proposal["id"]),
                    "uri": str(proposal["id"]),
                    "properties": _compact_props(
                        {
                            "proposal_code": proposal.get("proposal_code"),
                            "esaf_number": esaf.get("esaf_number"),
                        }
                    ),
                }
            )
            for sample in proposal.get("samples") or []:
                rows.append(
                    {
                        "entityType": "Sample",
                        "name": str(sample.get("name") or sample.get("sample_code") or sample["id"]),
                        "uri": str(sample["id"]),
                        "properties": _compact_props(
                            {
                                "sample_code": sample.get("sample_code"),
                                "material": sample.get("material"),
                                "bar_position": sample.get("bar_position"),
                                "proposal_code": proposal.get("proposal_code"),
                            }
                        ),
                    }
                )
            for scan in proposal.get("scans") or []:
                uri = _scan_uri(scan)
                rows.append(
                    {
                        "entityType": "BlueskyRun",
                        "name": str(scan.get("name") or scan.get("uid") or uri),
                        "uri": uri,
                        "properties": _compact_props(
                            {
                                "uid": scan.get("uid"),
                                "scan_id": scan.get("scan_id"),
                                "plan_name": scan.get("plan_name"),
                                "start_time": scan.get("start_time"),
                                "sample_id": scan.get("sample_id"),
                                "catalog_path": scan.get("catalog_path"),
                                "photon_energy_eV": scan.get("photon_energy_eV"),
                                "absorption_edge": scan.get("absorption_edge"),
                                "polarization": scan.get("polarization"),
                                "scattering_technique": scan.get("scattering_technique"),
                                "proposal_code": proposal.get("proposal_code"),
                                "esaf_number": esaf.get("esaf_number"),
                            }
                        ),
                    }
                )
    return rows


def iter_identity_links(fixture: Dict[str, Any]) -> List[Tuple[str, str, str]]:
    """Cookbook identity edges: ESAF→Proposal→Sample/Scan, Scan→Sample."""
    edges: List[Tuple[str, str, str]] = []
    seen = set()
    for esaf in fixture.get("esafs") or []:
        esaf_uri = str(esaf["id"])
        for proposal in esaf.get("proposals") or []:
            proposal_uri = str(proposal["id"])
            edges.append((esaf_uri, GRAPHQL_IDENTITY_RELS["hasProposal"], proposal_uri))
            for sample in proposal.get("samples") or []:
                edges.append((proposal_uri, GRAPHQL_IDENTITY_RELS["hasSample"], str(sample["id"])))
            for scan in proposal.get("scans") or []:
                scan_uri = _scan_uri(scan)
                edges.append((proposal_uri, GRAPHQL_IDENTITY_RELS["hasScan"], scan_uri))
                sample_uri = str(scan.get("sample_id") or "").strip()
                if sample_uri:
                    edges.append((scan_uri, GRAPHQL_IDENTITY_RELS["used"], sample_uri))
    unique: List[Tuple[str, str, str]] = []
    for edge in edges:
        if edge in seen or not edge[0] or not edge[2]:
            continue
        seen.add(edge)
        unique.append(edge)
    return unique


def _page_all(
    graphql: Callable[..., Dict[str, Any]],
    query: str,
    key: str,
    variables: Optional[Dict[str, Any]] = None,
    *,
    page_size: int = 100,
    max_pages: int = 20,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    offset = 0
    base = dict(variables or {})
    for _ in range(max_pages):
        data = graphql(query, {**base, "limit": page_size, "offset": offset}) or {}
        batch = data.get(key) or []
        if not isinstance(batch, list):
            break
        rows.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def _index_existing_entities(
    graphql: Callable[..., Dict[str, Any]],
) -> Tuple[Dict[str, str], Dict[Tuple[str, str], str], Dict[str, int]]:
    by_uri: Dict[str, str] = {}
    by_name: Dict[Tuple[str, str], str] = {}
    counts: Dict[str, int] = {}
    for entity_type in ("ESAF", "Proposal", "Sample", "BlueskyRun"):
        rows = _page_all(graphql, _PAGE_ENTITIES, "entities", {"entityType": entity_type})
        counts[entity_type] = len(rows)
        for row in rows:
            gql_id = str(row.get("id") or "").strip()
            if not gql_id:
                continue
            uri = str(row.get("uri") or "").strip()
            name = str(row.get("name") or "").strip()
            if uri:
                by_uri[uri] = gql_id
            if name:
                by_name[(entity_type, name)] = gql_id
    return by_uri, by_name, counts


def _existing_link_keys(
    graphql: Callable[..., Dict[str, Any]],
    by_uri: Dict[str, str],
) -> set:
    keys = set()
    predicates = sorted(set(GRAPHQL_IDENTITY_RELS.values()))
    for predicate in predicates:
        for row in _page_all(graphql, _PAGE_LINKS, "links", {"predicate": predicate}):
            subject = row.get("subject") if isinstance(row.get("subject"), dict) else {}
            obj = row.get("object") if isinstance(row.get("object"), dict) else {}
            subject_id = str(subject.get("id") or "").strip()
            object_id = str(obj.get("id") or "").strip()
            if subject_id and object_id:
                keys.add((subject_id, predicate, object_id))
            subject_uri = str(subject.get("uri") or "").strip()
            object_uri = str(obj.get("uri") or "").strip()
            if subject_uri:
                by_uri.setdefault(subject_uri, subject_id)
            if object_uri and object_id:
                by_uri.setdefault(object_uri, object_id)
    return keys


def push_fixture_to_tiled(
    fixture: Dict[str, Any],
    *,
    uri: Optional[str] = None,
    api_key: Optional[str] = None,
    graphql: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Idempotently create ESAF/Proposal/Sample/BlueskyRun nodes and cookbook links."""
    target = resolve_push_uri(uri)
    key = (api_key if api_key is not None else os.environ.get("TILED_API_KEY") or "").strip()
    if graphql is None and not key:
        raise RuntimeError("TILED_API_KEY is required to write the identity graph.")

    if graphql is None:
        from app.modules.tiled_graph import tiled_graphql

        def _call(query: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
            return tiled_graphql(query, variables, uri=target, api_key=key)

        gql = _call
    else:
        gql = graphql

    by_uri, by_name, before = _index_existing_entities(gql)
    created = {"ESAF": 0, "Proposal": 0, "Sample": 0, "BlueskyRun": 0}
    skipped = {"ESAF": 0, "Proposal": 0, "Sample": 0, "BlueskyRun": 0}
    payloads = iter_identity_entities(fixture)
    for payload in payloads:
        entity_type = str(payload["entityType"])
        entity_uri = str(payload["uri"])
        name = str(payload["name"])
        existing = by_uri.get(entity_uri) or by_name.get((entity_type, name))
        if existing:
            by_uri[entity_uri] = existing
            skipped[entity_type] = skipped.get(entity_type, 0) + 1
            continue
        created_row = gql(
            _CREATE_ENTITY,
            {
                "input": {
                    "entityType": entity_type,
                    "name": name,
                    "uri": entity_uri,
                    "properties": payload.get("properties") or {},
                }
            },
        ).get("createEntity") or {}
        gql_id = str(created_row.get("id") or "").strip()
        if not gql_id:
            raise RuntimeError(f"createEntity returned no id for {entity_type} {entity_uri}")
        by_uri[entity_uri] = gql_id
        by_name[(entity_type, name)] = gql_id
        created[entity_type] = created.get(entity_type, 0) + 1
        total_created = sum(created.values())
        if total_created % 50 == 0:
            LOGGER.info("Created %d identity entities in Tiled…", total_created)

    link_keys = _existing_link_keys(gql, by_uri)
    links_created = 0
    links_skipped = 0
    missing_endpoints = 0
    for subject_uri, predicate, object_uri in iter_identity_links(fixture):
        subject_id = by_uri.get(subject_uri)
        object_id = by_uri.get(object_uri)
        if not subject_id or not object_id:
            missing_endpoints += 1
            continue
        key_tuple = (subject_id, predicate, object_id)
        if key_tuple in link_keys:
            links_skipped += 1
            continue
        gql(
            _CREATE_LINK,
            {"input": {"subjectId": subject_id, "predicate": predicate, "objectId": object_id}},
        )
        link_keys.add(key_tuple)
        links_created += 1
        if links_created % 50 == 0:
            LOGGER.info("Created %d identity links in Tiled…", links_created)

    _, _, after = _index_existing_entities(gql)
    result = {
        "uri": target,
        "seed": fixture.get("seed"),
        "created": created,
        "skipped": skipped,
        "links_created": links_created,
        "links_skipped": links_skipped,
        "missing_endpoints": missing_endpoints,
        "existing_before": before,
        "existing_after": after,
        "planned": {
            "entities": len(payloads),
            "links": len(iter_identity_links(fixture)),
        },
    }
    LOGGER.info(
        "Tiled identity graph %s: created %s skipped %s links +%s",
        target,
        created,
        skipped,
        links_created,
    )
    return result


def fixture_to_overlay(fixture: Dict[str, Any], seed: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Convert a fixture into new ``things`` / ``associations`` (ops namespace).

    json2kg always uses the current term as the edge subject, so hasProposal /
    hasScan / hasSample are stored on ESAF and Proposal terms (not on the child).
    """
    seed = seed or load_seed()
    evidence = "tiled_sim"
    beamline_id = seed.get("beamline_id") or BEAMLINE_ID
    stage_id = seed.get("sample_stage_id") or beam_id("stage", "Sample-stage")
    terms: List[Dict[str, Any]] = []

    for esaf in fixture.get("esafs") or []:
        esaf_id = esaf["id"]
        esaf_rels = [_rel(esaf_id, "part_of", beamline_id, evidence)]
        if esaf.get("scientist_id"):
            esaf_rels.append(_rel(esaf_id, "wasAttributedTo", esaf["scientist_id"], evidence))
        if esaf.get("co_scientist_id"):
            esaf_rels.append(_rel(esaf_id, "wasAttributedTo", esaf["co_scientist_id"], evidence))
        for proposal in esaf.get("proposals") or []:
            pid = proposal["id"]
            esaf_rels.append(_rel(esaf_id, "hasProposal", pid, evidence))
            p_rels = [_rel(pid, "part_of", esaf_id, evidence)]
            for sample in proposal.get("samples") or []:
                sid = sample["id"]
                p_rels.append(_rel(pid, "hasSample", sid, evidence))
                terms.append(
                    make_term(
                        sid,
                        sample["name"],
                        "Sample",
                        f"Simulated endstation sample ({sample.get('material')}) for {proposal.get('proposal_code')}.",
                        evidence,
                        relations=[_rel(sid, "part_of", pid, evidence)],
                        sample_code=sample.get("sample_code"),
                        material=sample.get("material"),
                        literature_material=sample.get("material"),
                        bar_position=sample.get("bar_position"),
                    )
                )
            for scan in proposal.get("scans") or []:
                rid = scan["id"]
                p_rels.append(_rel(pid, "hasScan", rid, evidence))
                scan_rels = [
                    _rel(rid, "part_of", pid, evidence),
                    _rel(rid, "used", scan["sample_id"], evidence),
                    _rel(rid, "part_of", stage_id, evidence),
                ]
                if scan.get("plan_id"):
                    scan_rels.append(_rel(rid, "implementsPlan", scan["plan_id"], evidence))
                if scan.get("detector_id"):
                    scan_rels.append(_rel(rid, "hasDetector", scan["detector_id"], evidence))
                if scan.get("polarization_id"):
                    scan_rels.append(_rel(rid, "related_to", scan["polarization_id"], evidence))
                if scan.get("repo_id"):
                    scan_rels.append(_rel(rid, "configured_by", scan["repo_id"], evidence))
                if seed.get("tiled_software_id"):
                    scan_rels.append(_rel(rid, "runsSoftware", seed["tiled_software_id"], evidence))
                if seed.get("bluesky_software_id"):
                    scan_rels.append(_rel(rid, "runsSoftware", seed["bluesky_software_id"], evidence))
                for mid in scan.get("motor_ids") or []:
                    scan_rels.append(_rel(rid, "hasMotor", mid, evidence))
                terms.append(
                    make_term(
                        rid,
                        scan["name"],
                        "BlueskyRun",
                        (
                            f"Simulated Bluesky run {scan.get('uid')} "
                            f"({scan.get('plan_name')}, {scan.get('scattering_technique')}, "
                            f"{scan.get('absorption_edge')}). Catalog path is fake; no live Tiled."
                        ),
                        evidence,
                        relations=scan_rels,
                        uid=scan.get("uid"),
                        scan_id=scan.get("scan_id"),
                        plan_name=scan.get("plan_name"),
                        start_time=scan.get("start_time"),
                        nodeId=scan.get("nodeId"),
                        uri=scan.get("uri"),
                        catalog_path=scan.get("catalog_path"),
                        photon_energy_eV=scan.get("photon_energy_eV"),
                        absorption_edge=scan.get("absorption_edge"),
                        polarization=scan.get("polarization"),
                        geometry=scan.get("geometry"),
                        scan_mode=scan.get("scan_mode"),
                        scattering_technique=scan.get("scattering_technique"),
                        contrast=scan.get("contrast"),
                        material=scan.get("material"),
                        literature_technique=scan.get("scattering_technique"),
                    )
                )
                meas_id = beam_id("RSoXSMeasurement", scan.get("uid") or rid)
                terms.append(
                    make_term(
                        meas_id,
                        f"{scan.get('scattering_technique')} {scan.get('scan_id')}",
                        "RSoXSMeasurement",
                        (
                            f"Simulated measurement assembled from the run card: "
                            f"{scan.get('scattering_technique')} at {scan.get('photon_energy_eV')} eV "
                            f"({scan.get('absorption_edge')}, {scan.get('scan_mode')})."
                        ),
                        evidence,
                        relations=[_rel(meas_id, "wasDerivedFrom", rid, evidence)],
                        photon_energy_eV=scan.get("photon_energy_eV"),
                        absorption_edge=scan.get("absorption_edge"),
                        polarization=scan.get("polarization"),
                        scattering_technique=scan.get("scattering_technique"),
                        technique_type="rsoxs",
                    )
                )
            terms.append(
                make_term(
                    pid,
                    proposal["name"],
                    "Proposal",
                    f"Simulated Tiled proposal catalog {proposal.get('proposal_code')} under ESAF {esaf.get('esaf_number')}.",
                    evidence,
                    relations=p_rels,
                    proposal_code=proposal.get("proposal_code"),
                    esaf_number=esaf.get("esaf_number"),
                )
            )
        terms.append(
            make_term(
                esaf_id,
                esaf["name"],
                "ESAF",
                esaf.get("title") or f"Simulated ALS ESAF {esaf.get('esaf_number')} at 11.0.1.2.",
                evidence,
                relations=esaf_rels,
                esaf_number=esaf.get("esaf_number"),
                facility=seed.get("facility"),
                beamline_name=seed.get("beamline_name"),
            )
        )

    overlay_graph = json2kg.build_graph(terms, default_id_prefix=NAMESPACE, strict_snippets=False)
    merged_assocs = []
    seen = set()
    for edge in overlay_graph.get("associations") or []:
        pred = edge.get("predicate") or ""
        if pred.startswith("rel:") and pred[4:] in PROV_PREDICATES:
            pred = PROV_PREDICATES[pred[4:]]
        subject, obj = edge.get("subject"), edge.get("object")
        if not subject or not pred or not obj or subject == obj:
            continue
        key = (subject, pred, obj)
        if key in seen:
            continue
        seen.add(key)
        merged_assocs.append({**edge, "predicate": pred})
    overlay_graph["associations"] = merged_assocs
    return overlay_graph


def merge_overlay(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    things = list(base.get("things") or [])
    assocs = list(base.get("associations") or [])
    known = {n.get("id") for n in things}
    for node in overlay.get("things") or []:
        nid = node.get("id")
        if not nid or nid in known:
            continue
        things.append(node)
        known.add(nid)
    seen = {(e.get("subject"), e.get("predicate"), e.get("object")) for e in assocs}
    for edge in overlay.get("associations") or []:
        key = (edge.get("subject"), edge.get("predicate"), edge.get("object"))
        if not all(key) or key in seen:
            continue
        # Drop json2kg Unknown stubs created for missing targets: if the
        # object/subject is not in the merged graph, skip.
        if key[0] not in known or key[2] not in known:
            continue
        assocs.append(edge)
        seen.add(key)
    graph = dict(base)
    graph["things"] = things
    graph["associations"] = assocs
    return sort_graph(graph)


def run_tiled_sim_promote(
    *,
    source_kg: Path,
    fixture: Optional[Dict[str, Any]] = None,
    version: Optional[int] = None,
    kg_dir: Optional[Path] = None,
    terms_dir: Optional[Path] = None,
    seed: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Copy an ops KG, merge simulated ESAF/Proposal/run entities, write vN."""
    source_kg = source_kg.resolve()
    if not source_kg.exists():
        raise FileNotFoundError(source_kg)
    out_kg_dir = kg_dir or KG_DIR
    out_terms_dir = terms_dir or TERMS_DIR
    version = version if version is not None else next_snapshot_version(out_kg_dir)
    dest_kg, dest_terms = snapshot_paths(version, kg_dir=out_kg_dir, terms_dir=out_terms_dir)
    if dest_kg.resolve() == source_kg:
        raise ValueError(f"Refusing to overwrite source snapshot {source_kg}")
    seed = seed or load_seed()
    fixture = fixture or generate_fixture(seed)
    overlay = fixture_to_overlay(fixture, seed)
    graph = json.loads(source_kg.read_text(encoding="utf-8"))
    before_nodes = len(graph.get("things") or [])
    before_edges = len(graph.get("associations") or [])
    graph = merge_overlay(graph, overlay)
    stats = fixture.get("stats") or fixture_stats(fixture.get("esafs") or [])
    meta = graph.setdefault("metadata", {})
    promote = {
        "kind": PROMOTE_KIND,
        "source_graph": _rel_to_repo(source_kg),
        "seed": fixture.get("seed"),
        "seed_path": fixture.get("seed_path") or seed.get("path"),
        "live_tiled": False,
        "esafs": stats["esafs"],
        "proposals": stats["proposals"],
        "scans": stats["scans"],
        "samples": stats["samples"],
        "added_nodes": len(graph.get("things") or []) - before_nodes,
        "added_edges": len(graph.get("associations") or []) - before_edges,
    }
    meta["promote"] = promote
    meta["schema"] = str(SCHEMA_PATH.relative_to(REPO_ROOT))
    kg_path = write_kg_snapshot(graph, version, kg_dir=out_kg_dir)
    src_terms = TERMS_DIR / "extracted_terms_bl1101_v4.json"
    match = re.search(r"matkg_bl1101_v(\d+)\.json$", source_kg.name)
    if match:
        candidate = TERMS_DIR / f"extracted_terms_bl1101_v{match.group(1)}.json"
        if candidate.exists():
            src_terms = candidate
    if src_terms.exists():
        records = json.loads(src_terms.read_text(encoding="utf-8"))
        rec_meta = records.setdefault("metadata", {})
        rec_meta["promote"] = promote
        rec_meta["graph_snapshot"] = dict(graph.get("metadata", {}).get("graph_snapshot") or {})
        dest_terms.parent.mkdir(parents=True, exist_ok=True)
        dest_terms.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    LOGGER.info(
        "Promoted tiled sim %s → %s (ESAFs=%d proposals=%d scans=%d samples=%d)",
        source_kg,
        kg_path,
        stats["esafs"],
        stats["proposals"],
        stats["scans"],
        stats["samples"],
    )
    return {
        "kg_path": kg_path,
        "terms_path": dest_terms if dest_terms.exists() else None,
        "version": version,
        "nodes": len(graph.get("things") or []),
        "edges": len(graph.get("associations") or []),
        "stats": stats,
        "promote": promote,
        "graph": graph,
        "fixture": fixture,
    }


def _count_unique_papers(terms: Iterable[Dict[str, Any]]) -> int:
    papers: set[str] = set()
    for term in terms:
        for paper in term.get("source_papers") or []:
            if paper:
                papers.add(str(paper))
        if term.get("source_paper"):
            papers.add(str(term["source_paper"]))
    return len(papers)


def next_rsoxs_snapshot_version(kg_dir: Optional[Path] = None) -> int:
    kg_dir = kg_dir or KG_DIR
    versions: List[int] = []
    if kg_dir.exists():
        for path in kg_dir.iterdir():
            match = _RSOXS_SNAPSHOT_RE.match(path.name)
            if match:
                versions.append(int(match.group(1)))
    return (max(versions) + 1) if versions else 1


def snapshot_copy_terms(live_path: Path, dest: Optional[Path] = None) -> Path:
    """Copy the live extract file. Never writes *live_path*."""
    live_path = live_path.resolve()
    if dest is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = live_path.with_name(f"{live_path.stem}_{stamp}{live_path.suffix}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(live_path.read_bytes())
    json.loads(dest.read_text(encoding="utf-8"))  # refuse a torn copy
    return dest


def promote_rsoxs_snapshot(
    *,
    terms_snapshot: Path,
    output_kg: Optional[Path] = None,
    kg_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """json2kg a terms *copy* into the next ``matkg_rsoxs_vN.json``. Does not touch the live extract file."""
    terms_snapshot = terms_snapshot.resolve()
    if terms_snapshot.name == "extracted_terms_rsoxs_v1.json":
        raise ValueError("Refuse to json2kg the live extract file; pass a timestamped snapshot copy")
    kg_dir = kg_dir or KG_DIR
    version = next_rsoxs_snapshot_version(kg_dir) if output_kg is None else None
    dest = output_kg or (kg_dir / f"matkg_rsoxs_v{version}.json")
    if dest.exists() and output_kg is None:
        raise FileExistsError(dest)
    data = json.loads(terms_snapshot.read_text(encoding="utf-8"))
    terms = data.get("terms") if isinstance(data, dict) else data
    snippets = data.get("code_snippets", []) if isinstance(data, dict) else []
    meta = dict(data.get("metadata") or {}) if isinstance(data, dict) else {}
    graph = json2kg.build_graph(terms, code_snippets=snippets, default_id_prefix="matkg")
    paper_count = _count_unique_papers(terms or [])
    meta.update(
        {
            "schema_version": "rsoxs-1",
            "id_prefix": "matkg",
            "kg": "rsoxs",
            "source_terms": _rel_to_repo(terms_snapshot),
            "corpus_revision": {
                "kind": "extract_snapshot_promote",
                "live_file_untouched": "storage/terminology/extracted_terms_rsoxs_v1.json",
                "terms_snapshot": _rel_to_repo(terms_snapshot),
                "unique_papers": paper_count,
                "term_count": len(terms or []),
                "snippet_count": len(snippets or []),
            },
        }
    )
    graph["metadata"] = meta
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    digest = sha256_file(tmp)
    snap = {
        "name": dest.name.replace(".json", ""),
        "written_at": utc_now(),
        "sha256": digest,
        "nodes": len(graph["things"]),
        "edges": len(graph["associations"]),
        "path": _rel_to_repo(dest),
        "source_terms": _rel_to_repo(terms_snapshot),
        "unique_papers": paper_count,
        "term_count": len(terms or []),
    }
    graph["metadata"]["graph_snapshot"] = snap
    tmp.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(dest)
    LOGGER.info(
        "Wrote %s (%d nodes, %d edges) from %d terms / %d papers",
        dest,
        snap["nodes"],
        snap["edges"],
        snap["term_count"],
        paper_count,
    )
    return {"kg_path": dest, "graph": graph, "snapshot": snap, "terms_snapshot": terms_snapshot}


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulate Tiled Graph ESAF/Proposal/Scan overlay")
    parser.add_argument("--seed-yaml", type=Path, default=SEED_PATH)
    parser.add_argument("--fixture", type=Path, default=FIXTURE_PATH)
    parser.add_argument("--from-graph", type=Path, default=KG_DIR / "matkg_bl1101_v4.json")
    parser.add_argument("--snapshot", type=int, default=None, help="Write this bl1101 vN (default: next unused)")
    parser.add_argument("--generate-only", action="store_true", help="Write fixture JSON, do not ingest")
    parser.add_argument(
        "--to-tiled",
        action="store_true",
        help="Push fixture ESAF/Proposal/Sample/BlueskyRun entities into local Tiled GraphQL",
    )
    parser.add_argument(
        "--tiled-uri",
        default=None,
        help="Local Tiled URI (default: TILED_URI or http://127.0.0.1:8001)",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        stream=__import__("sys").stdout,
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    if args.to_tiled:
        _load_repo_env()
        if args.fixture.is_file():
            fixture = load_fixture(args.fixture)
        else:
            seed = load_seed(args.seed_yaml)
            fixture = generate_fixture(seed)
            write_fixture(fixture, args.fixture)
        stats = fixture.get("stats") or {}
        print(
            f"pushing fixture seed={fixture.get('seed')}: "
            f"{stats.get('esafs')} ESAFs, "
            f"{stats.get('proposals')} proposals, "
            f"{stats.get('scans')} scans, "
            f"{stats.get('samples')} samples → Tiled"
        )
        result = push_fixture_to_tiled(fixture, uri=args.tiled_uri)
        print(
            "tiled "
            f"{result['uri']}: created {result['created']} "
            f"skipped {result['skipped']} "
            f"links +{result['links_created']} skipped {result['links_skipped']} "
            f"after {result['existing_after']}"
        )
        return 0
    seed = load_seed(args.seed_yaml)
    fixture = generate_fixture(seed)
    write_fixture(fixture, args.fixture)
    print(
        f"fixture seed={fixture['seed']}: "
        f"{fixture['stats']['esafs']} ESAFs, "
        f"{fixture['stats']['proposals']} proposals, "
        f"{fixture['stats']['scans']} scans, "
        f"{fixture['stats']['samples']} samples → {args.fixture}"
    )
    if args.generate_only:
        return 0
    result = run_tiled_sim_promote(
        source_kg=args.from_graph,
        fixture=fixture,
        version=args.snapshot,
        seed=seed,
    )
    print(
        f"bl1101 v{result['version']}: {result['nodes']} nodes, {result['edges']} edges → {result['kg_path']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
