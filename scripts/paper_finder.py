#!/usr/bin/env python3
"""Paper Finder: human-paced review queue for paywalled RSoXS papers.

Companion to ``harvest_rsoxs.py``. Walks ``papers/rsoxs/manifest.json`` rows
with ``ingestion.status`` in ``{skipped, failed}`` and no PDF on disk, and
gives a person one paper at a time:

* Open — ``webbrowser.open`` on the DOI (or arXiv). No HTTP GET of the PDF.
* Save the PDF into ``papers/staging/`` (watched; env ``PAPER_FINDER_STAGING``).
* **Assign** moves it to ``papers/rsoxs/{year}/{stem}.pdf``. Never silent-file.

The publisher-facing request happens because a human clicked a link in a
real browser tab. This script never scripts a GET/POST against a publisher
for the PDF. That is categorically different from bulk publisher fetch.

Usage (from repo root, Lab VPN on, human-paced):

  python3 scripts/paper_finder.py serve
  python3 scripts/paper_finder.py serve --port 5057 --dry-run
  python3 scripts/paper_finder.py reset
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from flask import Flask, jsonify, redirect, render_template_string, request, url_for
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import harvest_rsoxs as hv  # noqa: E402

LOGGER = logging.getLogger("paper_finder")

BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 5057
BLOCKED_PORTS = frozenset({5174, 5175, 8090})
PENDING_STATUSES = frozenset({"skipped", "failed"})
QUEUE_STATE_FILENAME = ".paper_finder_queue.json"
STAGING_ENV = "PAPER_FINDER_STAGING"
DOWNLOADS_ENV = "PAPER_FINDER_DOWNLOADS"
DEFAULT_STAGING = Path("papers") / "staging"
OPEN_DEBOUNCE_SEC = 20.0


def local_bind(host: str, port: int) -> Tuple[str, int]:
    """Return a loopback bind pair, or raise if the bind would be unsafe.

    Args:
        host: Requested bind host. Must be ``127.0.0.1``.
        port: Requested TCP port. Must not be 5174, 5175, or 8090.

    Returns:
        The ``(host, port)`` pair to pass to Flask.

    Raises:
        ValueError: Host is not loopback or the port is reserved.
    """
    if host != BIND_HOST:
        raise ValueError(f"Paper Finder binds {BIND_HOST} only, not {host!r}")
    if port in BLOCKED_PORTS:
        raise ValueError(
            f"Port {port} is reserved (SSH 5174 / UI 5175 / agent 8090)"
        )
    if port <= 0 or port > 65535:
        raise ValueError(f"Invalid port {port}")
    return host, port


def default_staging_dir() -> Path:
    """Project-local staging folder to watch for human-saved PDFs.

    Resolution order: ``PAPER_FINDER_STAGING``, then the older
    ``PAPER_FINDER_DOWNLOADS`` override, else ``<repo>/papers/staging``.

    Returns:
        Absolute or repo-relative path to the watch directory.
    """
    for env_name in (STAGING_ENV, DOWNLOADS_ENV):
        override = str(os.environ.get(env_name) or "").strip()
        if override:
            return Path(override).expanduser()
    return hv.REPO_ROOT / DEFAULT_STAGING


def ensure_staging_dir(path: Path) -> Path:
    """Create the staging folder if missing (``papers/`` is gitignored).

    Args:
        path: Watch directory.

    Returns:
        The same path, now existing as a directory.
    """
    path.mkdir(parents=True, exist_ok=True)
    return path


def _paper_key(paper: Dict[str, Any]) -> str:
    """Identity key matching ``harvest_rsoxs._upsert_paper``.

    Args:
        paper: Manifest row.

    Returns:
        Lowercase DOI key, else arXiv / path / title.
    """
    doi_key = hv._doi_key(paper.get("doi"))
    if doi_key:
        return doi_key
    return str(
        paper.get("arxiv_id") or paper.get("pdf_path") or paper.get("title") or ""
    ).lower()


def launch_user_browser(
    url: str,
    *,
    last_url: Optional[str] = None,
    last_at: float = 0.0,
    now: Optional[float] = None,
    debounce_sec: float = OPEN_DEBOUNCE_SEC,
) -> Tuple[bool, str, float]:
    """Open ``url`` in the operator's browser. Never HTTP-GETs it.

    Args:
        url: DOI or arXiv URL already built by ``open_url``.
        last_url: Previous URL passed to ``webbrowser.open``.
        last_at: ``time.monotonic()`` of that previous launch.
        now: Optional clock override for tests.
        debounce_sec: Ignore a repeat Open of the same URL within this window.

    Returns:
        ``(did_open, url, timestamp)``. ``did_open`` is False when debounced.
    """
    stamp = time.monotonic() if now is None else now
    if last_url == url and stamp - last_at < debounce_sec:
        LOGGER.info("Skipping duplicate Open (debounce %.0fs): %s", debounce_sec, url)
        return False, url, last_at
    # Operator's OS browser only. Do not urllib/requests the publisher.
    webbrowser.open(url)
    LOGGER.info("Opened in user browser (no HTTP GET): %s", url)
    return True, url, stamp


def open_url(paper: Dict[str, Any]) -> Optional[str]:
    """Browser URL for a human Open click: DOI resolver, then arXiv.

    Never fetches the URL. The caller may pass it to ``webbrowser.open``.

    Args:
        paper: Manifest row.

    Returns:
        ``https://doi.org/...`` or ``https://arxiv.org/abs/...``, else None.
    """
    doi = paper.get("doi")
    if doi:
        return hv._canonical_doi(str(doi)) or f"https://doi.org/{doi}"
    arxiv_id = str(paper.get("arxiv_id") or "").strip()
    if arxiv_id:
        return f"https://arxiv.org/abs/{arxiv_id}"
    return None


def journal_label(paper: Dict[str, Any]) -> str:
    """Best-effort venue line for the review page.

    Harvest rows often omit a journal field; fall back to source / PubTemp kind.

    Args:
        paper: Manifest row.

    Returns:
        Short venue string, possibly empty.
    """
    for key in ("journal", "host_venue", "venue", "container_title"):
        value = paper.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            name = str(value.get("display_name") or value.get("name") or "").strip()
            if name:
                return name
    loc = paper.get("primary_location")
    if isinstance(loc, dict):
        src = loc.get("source")
        if isinstance(src, dict):
            name = str(src.get("display_name") or "").strip()
            if name:
                return name
    kind = str(paper.get("pubtemp_kind") or "").strip()
    source = str(paper.get("source") or "").strip()
    bits = [bit for bit in (kind, source) if bit]
    return " · ".join(bits)


def queue_rows(
    papers: Sequence[Dict[str, Any]],
    skipped_keys: Set[str],
) -> List[Dict[str, Any]]:
    """Manifest rows still needing a human look.

    Args:
        papers: ``manifest['papers']``.
        skipped_keys: Session-skip identities (not ``ingestion.status``).

    Returns:
        Skipped/failed rows with no PDF on disk, minus session skips.
    """
    rows = [
        paper
        for paper in papers
        if str((paper.get("ingestion") or {}).get("status") or "")
        in PENDING_STATUSES
        and not hv._pdf_on_disk(paper)
        and _paper_key(paper) not in skipped_keys
    ]
    rows.sort(key=lambda paper: str(paper.get("title") or "").lower())
    return rows


def mark_restricted(paper: Dict[str, Any]) -> None:
    """Record that a human found no accessible copy (OA or institutional).

    Args:
        paper: Manifest row mutated in place.
    """
    paper["ingestion"] = {
        **(paper.get("ingestion") or {}),
        "status": "restricted",
        "error": "no institutional or OA access found (manual review)",
    }


def assign_pdf(
    paper: Dict[str, Any],
    src: Path,
    dest_root: Path,
    *,
    dry_run: bool,
) -> Optional[Path]:
    """Move a human-saved PDF into the harvest layout and stamp the row.

    Writes the same fields a successful ``harvest_rsoxs`` download writes:
    ``pdf_path``, ``pdf_sha256``, ``downloaded_at``,
    ``ingestion.status = pending``.

    Args:
        paper: Manifest row mutated in place (unless dry-run).
        src: File in ``papers/staging/`` that the operator assigned.
        dest_root: Corpus root (``papers/rsoxs``).
        dry_run: When True, log and return the would-be dest without writes.

    Returns:
        Destination path, or None if the move was refused.
    """
    year = str(paper.get("publication_year") or hv.year_from_work(paper) or "unknown")
    stem = hv._safe_name(paper)
    dest = dest_root / year / f"{stem}.pdf"
    if dry_run:
        LOGGER.info("[dry-run] would move %s -> %s", src, dest)
        return dest
    if not src.is_file() or src.stat().st_size <= 0:
        LOGGER.warning("Assign refused, not a non-empty file: %s", src)
        return None
    if dest.exists() and dest.resolve() != src.resolve():
        LOGGER.warning("Assign refused, destination already exists: %s", dest)
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    paper["pdf_path"] = str(dest.relative_to(hv.REPO_ROOT))
    paper["pdf_sha256"] = hv._sha256(dest)
    paper["downloaded_at"] = hv._now()
    paper["ingestion"] = {
        **(paper.get("ingestion") or {}),
        "status": "pending",
        "error": None,
    }
    LOGGER.info("Filed manual download: %s", paper["pdf_path"])
    return dest


def _wait_for_stable_file(
    path: Path, checks: int = 4, interval: float = 0.5
) -> bool:
    """Poll size until it stops changing so half-written downloads are skipped.

    Args:
        path: File to watch.
        checks: Consecutive stable reads required.
        interval: Seconds between size checks.

    Returns:
        True once size held steady for ``checks`` reads in a row.
    """
    last_size = -1
    stable_count = 0
    for _ in range(checks * 6):
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False
        if size == last_size and size > 0:
            stable_count += 1
            if stable_count >= checks:
                return True
        else:
            stable_count = 0
        last_size = size
        time.sleep(interval)
    return False


class StagingWatcher(FileSystemEventHandler):
    """Watch ``papers/staging/`` for completed PDFs. Never moves or assigns them."""

    def __init__(self, detected: List[Path], lock: threading.Lock) -> None:
        self._detected = detected
        self._lock = lock

    def _maybe_record(self, path: Path) -> None:
        if path.suffix.lower() != ".pdf":
            return
        try:
            if not _wait_for_stable_file(path):
                LOGGER.warning("Ignoring incomplete download: %s", path.name)
                return
        except OSError as exc:
            LOGGER.warning("Error watching %s: %s", path.name, exc)
            return
        with self._lock:
            if path not in self._detected:
                self._detected.append(path)
                LOGGER.info("Detected download (offer Assign): %s", path.name)

    def on_created(self, event: Any) -> None:
        if event.is_directory:
            return
        self._maybe_record(Path(str(event.src_path)))

    def on_moved(self, event: Any) -> None:
        if event.is_directory:
            return
        dest = getattr(event, "dest_path", None)
        if dest:
            self._maybe_record(Path(str(dest)))


@dataclass
class QueueState:
    """In-memory Paper Finder session (manifest path + skip cursor)."""

    dest_root: Path
    staging_dir: Path
    dry_run: bool = False
    current_index: int = 0
    skipped_keys: Set[str] = field(default_factory=set)
    detected_files: List[Path] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    last_browser_url: Optional[str] = None
    last_browser_open_at: float = 0.0

    def launch_open(self, url: str) -> bool:
        """``webbrowser.open`` with debounce. Never HTTP-GETs the publisher."""
        did_open, url, stamp = launch_user_browser(
            url,
            last_url=self.last_browser_url,
            last_at=self.last_browser_open_at,
        )
        if did_open:
            self.last_browser_url = url
            self.last_browser_open_at = stamp
        return did_open

    @property
    def manifest_path(self) -> Path:
        return self.dest_root / "manifest.json"

    @property
    def state_path(self) -> Path:
        return self.dest_root / QUEUE_STATE_FILENAME

    def load_persisted(self) -> None:
        """Restore current_index / skipped_keys from a prior session."""
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning(
                "Could not read %s (%s); starting fresh", self.state_path, exc
            )
            return
        self.current_index = int(data.get("current_index") or 0)
        self.skipped_keys = set(data.get("skipped_keys") or [])

    def save_persisted(self) -> None:
        """Write session skip cursor. No-op when dry-run."""
        if self.dry_run:
            LOGGER.info("[dry-run] would persist queue state to %s", self.state_path)
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {
                    "current_index": self.current_index,
                    "skipped_keys": sorted(self.skipped_keys),
                },
                indent=2,
            )
            + "\n"
        )

    def load_manifest(self) -> Dict[str, Any]:
        """Reload harvest manifest so parallel OA passes drop resolved rows."""
        return hv._load_manifest(self.manifest_path)

    def save_manifest(self, manifest: Dict[str, Any]) -> None:
        """Write manifest. No-op when dry-run."""
        if self.dry_run:
            LOGGER.info("[dry-run] would write manifest to %s", self.manifest_path)
            return
        hv._save_manifest(self.manifest_path, manifest)

    def pending(self, manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Papers still needing a human look."""
        return queue_rows(manifest.get("papers") or [], self.skipped_keys)

    def current(
        self, manifest: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], int, int]:
        """The paper to show now, 1-based position, and queue length."""
        rows = self.pending(manifest)
        if not rows:
            return None, 0, 0
        index = min(self.current_index, len(rows) - 1)
        self.current_index = index
        return rows[index], index + 1, len(rows)

    def clamp_index(self, manifest: Dict[str, Any]) -> None:
        """Keep current_index inside the live queue."""
        rows = self.pending(manifest)
        self.current_index = min(self.current_index, max(0, len(rows) - 1))


PAGE_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="referrer" content="no-referrer">
<title>Paper Finder</title>
<style>
  body { font-family: -apple-system, sans-serif; max-width: 720px; margin: 40px auto;
         padding: 0 20px; color: #222; }
  h1 { font-size: 1.1rem; color: #888; font-weight: normal; }
  .banner { background: #fff8e5; border: 1px solid #e6d9a8; border-radius: 8px;
            padding: 12px 16px; margin-bottom: 16px; font-size: 0.9rem; }
  .paper { border: 1px solid #ddd; border-radius: 8px; padding: 24px; margin-bottom: 20px; }
  .paper h2 { margin-top: 0; font-size: 1.25rem; }
  .meta { color: #666; font-size: 0.9rem; margin-bottom: 16px; }
  .actions form { display: inline-block; margin: 0 8px 8px 0; }
  button { font-size: 0.95rem; padding: 8px 14px; border-radius: 6px; border: 1px solid #ccc;
           background: #f7f7f7; cursor: pointer; }
  button.primary { background: #2d6cdf; color: white; border-color: #2d6cdf; }
  button.danger { background: #fff0f0; border-color: #e0a0a0; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .detected { background: #eefaf0; border: 1px solid #b6e5c0; border-radius: 8px;
              padding: 16px; margin-bottom: 20px; }
  .empty { color: #666; }
  .hint { color: #999; font-size: 0.85rem; margin-top: 24px; }
  kbd { background: #eee; border-radius: 4px; padding: 1px 6px; font-size: 0.85em; }
</style>
</head>
<body>
<h1>Paper Finder · {{ position }} of {{ total }} remaining{% if dry_run %} · dry-run{% endif %}</h1>
<div class="banner">
  Lab VPN on. Save the PDF into <code>papers/staging/</code>, then Assign.
  This app never downloads the PDF. Open only launches your browser;
  RSC 429 means wait, don’t retry in a burst.
</div>

{% if detected %}
<div class="detected">
  <strong>New file{{ 's' if detected|length > 1 else '' }} in papers/staging — Assign, do not auto-file:</strong>
  {% for f in detected %}
    <div style="margin-top:8px;">
      {{ f }}
      {% if paper %}
      <form method="post" action="{{ url_for('action') }}" style="display:inline;">
        <input type="hidden" name="action" value="assign">
        <input type="hidden" name="filename" value="{{ f }}">
        <button class="primary" type="submit" id="assign-btn">Assign to current paper</button>
      </form>
      {% endif %}
      <form method="post" action="{{ url_for('action') }}" style="display:inline;">
        <input type="hidden" name="action" value="dismiss">
        <input type="hidden" name="filename" value="{{ f }}">
        <button type="submit">Dismiss</button>
      </form>
    </div>
  {% endfor %}
</div>
{% endif %}

{% if paper %}
<div class="paper">
  <h2>{{ paper.title }}</h2>
  <div class="meta">
    {% if journal %}{{ journal }} · {% endif %}{{ paper.publication_year or '' }}
    {% if paper.doi %} · DOI: {{ paper.doi }}{% endif %}
    {% if paper.arxiv_id %} · arXiv: {{ paper.arxiv_id }}{% endif %}
  </div>
  <div class="actions">
    <form method="post" action="{{ url_for('action') }}">
      <input type="hidden" name="action" value="open">
      <button class="primary" type="submit" id="open-btn"{% if not open_href %} disabled{% endif %}>Open in browser</button>
    </form>
    <form method="post" action="{{ url_for('action') }}">
      <input type="hidden" name="action" value="unavailable">
      <button class="danger" type="submit" id="unavailable-btn">Not accessible</button>
    </form>
    <form method="post" action="{{ url_for('action') }}">
      <input type="hidden" name="action" value="skip">
      <button type="submit" id="skip-btn">Skip for now</button>
    </form>
  </div>
</div>
<div class="hint">
  <kbd>o</kbd> open · <kbd>x</kbd> not accessible ·
  <kbd>s</kbd> skip · <kbd>a</kbd> assign
</div>
{% else %}
<p class="empty">Nothing left to review. Every paper is on disk, restricted, or skipped this session.</p>
{% endif %}

<script>
window.__pfDetected = {{ detected | tojson }};
document.addEventListener('keydown', function (ev) {
  if (ev.target.tagName === 'INPUT' || ev.target.tagName === 'TEXTAREA') return;
  var id = null;
  if (ev.key === 'o') id = 'open-btn';
  if (ev.key === 'x') id = 'unavailable-btn';
  if (ev.key === 's') id = 'skip-btn';
  if (ev.key === 'a') id = 'assign-btn';
  if (id) {
    ev.preventDefault();
    var btn = document.getElementById(id);
    if (btn && !btn.disabled) btn.click();
  }
});
setInterval(function () {
  fetch('/detected', { cache: 'no-store' })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      var next = data.detected || [];
      if (JSON.stringify(next) !== JSON.stringify(window.__pfDetected)) {
        location.reload();
      }
    })
    .catch(function () {});
}, 5000);
</script>
</body>
</html>
"""


def create_app(state: QueueState) -> Flask:
    """Build the local Paper Finder Flask app (does not bind a port).

    Args:
        state: Session bound to a corpus root.

    Returns:
        Flask app whose routes reload the harvest manifest on every page.
    """
    app = Flask("paper_finder")
    app.config["PAPER_FINDER_STATE"] = state

    def _state() -> QueueState:
        return app.config["PAPER_FINDER_STATE"]

    @app.get("/")
    def index() -> str:
        st = _state()
        manifest = st.load_manifest()
        paper, position, total = st.current(manifest)
        with st.lock:
            detected = [path.name for path in st.detected_files]
        return render_template_string(
            PAGE_TEMPLATE,
            paper=paper,
            position=position,
            total=total,
            detected=detected,
            dry_run=st.dry_run,
            journal=journal_label(paper) if paper else "",
            open_href=open_url(paper) if paper else None,
        )

    @app.get("/detected")
    def detected() -> Any:
        """Local staging filenames only. No publisher URLs."""
        st = _state()
        with st.lock:
            names = [path.name for path in st.detected_files]
        return jsonify({"detected": names})

    @app.post("/action")
    def action() -> Any:
        st = _state()
        manifest = st.load_manifest()
        paper, _, _ = st.current(manifest)
        act = request.form.get("action")

        if act == "open":
            if paper:
                url = open_url(paper)
                if url:
                    # Human click → OS browser. Do not urllib/requests the PDF.
                    st.launch_open(url)
                else:
                    LOGGER.warning(
                        "No DOI or arXiv id for %r; nothing to open",
                        paper.get("title"),
                    )
            return redirect(url_for("index"))

        if act == "unavailable":
            if paper:
                mark_restricted(paper)
                st.save_manifest(manifest)
                st.clamp_index(manifest)
            st.save_persisted()
            return redirect(url_for("index"))

        if act == "skip":
            if paper:
                st.skipped_keys.add(_paper_key(paper))
                st.clamp_index(manifest)
            st.save_persisted()
            return redirect(url_for("index"))

        if act == "assign":
            filename = request.form.get("filename") or ""
            with st.lock:
                match = next(
                    (path for path in st.detected_files if path.name == filename),
                    None,
                )
            if match and paper:
                assigned = assign_pdf(
                    paper, match, st.dest_root, dry_run=st.dry_run
                )
                if assigned is not None:
                    st.save_manifest(manifest)
                    with st.lock:
                        if match in st.detected_files:
                            st.detected_files.remove(match)
                    st.clamp_index(manifest)
            st.save_persisted()
            return redirect(url_for("index"))

        if act == "dismiss":
            filename = request.form.get("filename") or ""
            with st.lock:
                match = next(
                    (path for path in st.detected_files if path.name == filename),
                    None,
                )
                if match:
                    st.detected_files.remove(match)
            return redirect(url_for("index"))

        LOGGER.warning("Unknown action: %r", act)
        return redirect(url_for("index"))

    return app


def reset_queue(dest_root: Path) -> bool:
    """Delete persisted Paper Finder session state.

    Args:
        dest_root: Corpus root (``papers/rsoxs``).

    Returns:
        True if a state file was removed.
    """
    state_path = dest_root / QUEUE_STATE_FILENAME
    if state_path.exists():
        state_path.unlink()
        LOGGER.info("Cleared %s", state_path)
        return True
    LOGGER.info("No saved queue state at %s", state_path)
    return False


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse Paper Finder CLI arguments.

    Args:
        argv: Argument list without the program name. ``None`` uses sys.argv.

    Returns:
        Parsed namespace with ``command`` of ``serve`` or ``reset``.
    """
    parser = argparse.ArgumentParser(
        prog="paper_finder",
        description="Paper Finder: human review queue for paywalled RSoXS papers.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve_p = sub.add_parser(
        "serve",
        help="Start the local review UI on 127.0.0.1 (never fetches publisher PDFs).",
    )
    serve_p.add_argument(
        "--dest",
        type=Path,
        default=hv.REPO_ROOT / "papers" / "rsoxs",
        help="Corpus root (year folders + manifest.json). Same default as harvest_rsoxs.py.",
    )
    serve_p.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Loopback port (default {DEFAULT_PORT}; not 5174/5175/8090).",
    )
    serve_p.add_argument(
        "--staging-dir",
        "--downloads-dir",
        dest="staging_dir",
        type=Path,
        default=None,
        help=(
            "Folder to watch for human-saved PDFs "
            f"(default <repo>/{DEFAULT_STAGING}; env ${STAGING_ENV}). "
            "--downloads-dir is an alias."
        ),
    )
    serve_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Log file moves and manifest writes instead of applying them.",
    )
    serve_p.add_argument("--log-level", default="INFO")

    reset_p = sub.add_parser(
        "reset",
        help="Clear the persisted skip list and queue position.",
    )
    reset_p.add_argument(
        "--dest",
        type=Path,
        default=hv.REPO_ROOT / "papers" / "rsoxs",
        help="Corpus root whose .paper_finder_queue.json should be removed.",
    )
    reset_p.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def serve(
    *,
    dest: Path,
    port: int,
    staging_dir: Path,
    dry_run: bool,
) -> None:
    """Bind Paper Finder to loopback and serve until interrupted.

    Args:
        dest: Corpus root.
        port: TCP port (rejected if reserved).
        staging_dir: Folder watched for new PDFs (created if missing).
        dry_run: When True, no manifest / file / state writes.
    """
    host, port = local_bind(BIND_HOST, port)
    ensure_staging_dir(staging_dir)
    state = QueueState(
        dest_root=dest, staging_dir=staging_dir, dry_run=dry_run
    )
    state.load_persisted()
    observer = Observer()
    observer.schedule(
        StagingWatcher(state.detected_files, state.lock),
        str(staging_dir),
    )
    observer.daemon = True
    observer.start()
    LOGGER.info("Watching staging folder %s for PDFs", staging_dir)

    app = create_app(state)
    url = f"http://{host}:{port}/"
    LOGGER.info(
        "Paper Finder at %s (dry_run=%s). Open that URL yourself — "
        "this process will not auto-open a browser or any DOI.",
        url,
        dry_run,
    )
    try:
        app.run(host=host, port=port, debug=False, use_reloader=False)
    finally:
        observer.stop()
        observer.join(timeout=2)


def main(argv: Optional[Sequence[str]] = None) -> None:
    """CLI entry point."""
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    if args.command == "reset":
        reset_queue(args.dest)
        return
    staging = args.staging_dir or default_staging_dir()
    serve(
        dest=args.dest,
        port=args.port,
        staging_dir=staging,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
