"""Typed definitions for the BL11.0.1.2 (``bl1101``) operations-graph ingest.

Data only: identity constants, the upstream repository specs, blueprint row
dataclasses, the stage-name alias table and the text-matching patterns the
ingest uses. Ported from the flat ``app.modules.bl1101_ingest`` (T17B/H8
proposal P3, slice a); that module imports these definitions and keeps its
behaviour. Importing this module has no side effects: no file, network,
subprocess or environment access, and no paths (those stay with the code that
reads or writes files).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

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
    """Which upstream GitHub repo to ingest and which files to read from it.

    Attributes:
        name: Short repo name; also the on-disk clone directory name and the
            ``repo_name`` stamped on terms and snippets.
        url: Clone URL.
        mode: Ingest depth label (``full``, ``docs``, ``stack`` or ``readme``);
            recorded in the source metadata.
        python_globs: Globs, relative to the clone root, of Python files to scan
            for device classes and plan functions.
        md_globs: Globs of Markdown files to summarise.
        yaml_globs: Globs of YAML config files.
        compose_globs: Globs of docker-compose files to list services from.
        readme_only: If true, clone sparsely and check out only ``README.md``.
    """
    name: str
    url: str
    mode: str  # full | docs | stack | readme
    python_globs: tuple[str, ...] = ()
    md_globs: tuple[str, ...] = ("README.md",)
    yaml_globs: tuple[str, ...] = ()
    compose_globs: tuple[str, ...] = ()
    readme_only: bool = False


REPO_SPECS: tuple[RepoSpec, ...] = (
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


@dataclass
class BlueprintDevice:
    """One device row from the blueprint's correspondence table.

    Attributes:
        dev_id: Row anchor ID with the ``dev-`` prefix removed.
        name: Human-readable device name.
        kind: Lowercased device kind (for example ``motor`` or ``detector``).
        category: Ontology category derived from ``kind`` via ``KIND_CATEGORY``.
        pv: EPICS process-variable name, possibly empty.
        handle: Python handle string such as ``d.motors["x"]``.
        ophyd_name: Device name parsed from ``handle``.
        ophyd_class: Ophyd class name listed in the table.
        ioc_record: IOC record text from the table.
        mapping_file: LabVIEW mapping file text, empty if the column is absent.
        drawn: Label of the device as drawn on the blueprint.
        meaning: Tooltip description of the device.
        collection: Device collection parsed from ``handle``, possibly empty.
    """
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
    """One beamline stage block from the blueprint.

    Attributes:
        name: Stage name.
        section: Blueprint section label for the stage.
        what: Description of what the stage is.
        does: Description of what the stage does.
        range_text: Documented operating range text.
        device_ids: Sorted ``dev_id`` values of devices linked from the stage.
    """
    name: str
    section: str
    what: str
    does: str
    range_text: str
    device_ids: list[str] = field(default_factory=list)
