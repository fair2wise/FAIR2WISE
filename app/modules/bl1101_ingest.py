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

Compatibility path. The implementation lives in ``app.modules.graph.imports.bl1101`` (T17B/H8 P3):
``specs`` (definitions), ``paths``, ``common``, ``fetch``, ``parse_als``, ``parse_blueprint``, ``symbols``,
``terms``, ``snapshot`` and ``cli``. This module re-exports every name from those modules (the same objects) and
installs :mod:`.graph.imports.bl1101.compat`, so ``monkeypatch.setattr(bl1101_ingest, name, value)`` reaches the
module that uses ``name``. A plain ``sys.modules`` alias to one module could not do that once the code is split.
"""

from __future__ import annotations

import hashlib as hashlib  # re-exported: tests reach bl1101_ingest.hashlib
import json as json
import shutil as shutil
import subprocess as subprocess  # re-exported: tests patch bl1101_ingest.subprocess.run
import sys
from urllib.request import urlopen as urlopen  # re-exported: tests patch bl1101_ingest.urlopen

from .cli_output import output as output
from .graph.imports.bl1101 import cli as _cli
from .graph.imports.bl1101 import common as _common
from .graph.imports.bl1101 import compat as _compat
from .graph.imports.bl1101 import fetch as _fetch
from .graph.imports.bl1101 import parse_als as _parse_als
from .graph.imports.bl1101 import parse_blueprint as _parse_blueprint
from .graph.imports.bl1101 import paths as _paths
from .graph.imports.bl1101 import snapshot as _snapshot
from .graph.imports.bl1101 import specs as _specs
from .graph.imports.bl1101 import symbols as _symbols
from .graph.imports.bl1101 import terms as _terms
from .graph.imports.bl1101.cli import _clear_work as _clear_work
from .graph.imports.bl1101.cli import _ingest_repos as _ingest_repos
from .graph.imports.bl1101.cli import ingest_fixture as ingest_fixture
from .graph.imports.bl1101.cli import ingest_records as ingest_records
from .graph.imports.bl1101.cli import main as main
from .graph.imports.bl1101.cli import parse_args as parse_args
from .graph.imports.bl1101.cli import records_to_graph as records_to_graph
from .graph.imports.bl1101.cli import run_ingest as run_ingest
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
from .graph.imports.bl1101.fetch import ALLOWED_SCHEMES as ALLOWED_SCHEMES
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
from .graph.imports.bl1101.snapshot import _terms_snapshot_for_kg as _terms_snapshot_for_kg
from .graph.imports.bl1101.snapshot import next_snapshot_version as next_snapshot_version
from .graph.imports.bl1101.snapshot import run_motor_link_promote as run_motor_link_promote
from .graph.imports.bl1101.snapshot import snapshot_paths as snapshot_paths
from .graph.imports.bl1101.snapshot import write_kg_snapshot as write_kg_snapshot
from .graph.imports.bl1101.specs import ALS_PAGE_URL as ALS_PAGE_URL
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
from .graph.imports.bl1101.terms import _append_term_rel as _append_term_rel
from .graph.imports.bl1101.terms import _beam_path_stage_terms as _beam_path_stage_terms
from .graph.imports.bl1101.terms import _energy_from_sources as _energy_from_sources
from .graph.imports.bl1101.terms import _motor_by_ophyd as _motor_by_ophyd
from .graph.imports.bl1101.terms import _rel as _rel
from .graph.imports.bl1101.terms import _stage_id_by_name as _stage_id_by_name
from .graph.imports.bl1101.terms import _stage_of_motor as _stage_of_motor
from .graph.imports.bl1101.terms import apply_motor_stage_map_to_graph as apply_motor_stage_map_to_graph
from .graph.imports.bl1101.terms import apply_motor_stage_map_to_terms as apply_motor_stage_map_to_terms
from .graph.imports.bl1101.terms import attach_repo_terms as attach_repo_terms
from .graph.imports.bl1101.terms import beamline_seed as beamline_seed
from .graph.imports.bl1101.terms import load_motor_stage_map as load_motor_stage_map
from .graph.imports.bl1101.terms import make_term as make_term
from .graph.imports.bl1101.terms import merge_terms as merge_terms
from .graph.imports.bl1101.terms import software_catalog as software_catalog
from .graph.imports.bl1101.terms import sort_graph as sort_graph
from .graph.imports.bl1101.terms import terms_from_als as terms_from_als
from .graph.imports.bl1101.terms import terms_from_blueprint as terms_from_blueprint
from .graph.imports.bl1101.terms import terms_from_people as terms_from_people

# A patch of a re-exported name must also reach the split modules that bind it.
_compat.install(
    sys.modules[__name__],
    (_specs, _paths, _common, _fetch, _parse_als, _parse_blueprint, _symbols, _terms, _snapshot, _cli),
)

if __name__ == "__main__":
    raise SystemExit(main())
