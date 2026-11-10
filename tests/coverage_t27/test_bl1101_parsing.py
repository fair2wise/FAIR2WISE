"""bl1101 ingest: helpers, ALS/blueprint HTML parsing, beam-path ordering and git syncing (fakes)."""

from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from app.modules import bl1101_ingest as bi


# ----------------------------------------------------------------------------- tiny helpers
def test_slug_beam_id_strip_tags_and_hashes():
    assert bi.slug(" Mono 101 / (JJ) ") == "Mono-101-JJ" and bi.slug("***") == "unnamed" and bi.slug(None) == "unnamed"
    assert bi.beam_id("Motor", "", "Sample X") == "beamline:Motor-Sample-X"
    assert bi.strip_tags("<b>a&amp;b</b>\n  <i>c</i>") == "a&b c" and bi.strip_tags(None) == ""
    assert bi.sha256_bytes(b"abc") == bi.hashlib.sha256(b"abc").hexdigest()
    assert bi.utc_now().endswith("Z") and "." not in bi.utc_now()


def test_snapshot_version_and_paths(tmp_path):
    assert bi.next_snapshot_version(tmp_path / "missing") == 1
    for n in (1, 3, 10):
        (tmp_path / f"matkg_bl1101_v{n}.json").write_text("{}")
    (tmp_path / "matkg_bl1101_vX.json").write_text("{}")
    (tmp_path / "other.json").write_text("{}")
    assert bi.next_snapshot_version(tmp_path) == 11
    kg, terms = bi.snapshot_paths(4, kg_dir=tmp_path / "k", terms_dir=tmp_path / "t")
    assert kg == tmp_path / "k/matkg_bl1101_v4.json" and terms == tmp_path / "t/extracted_terms_bl1101_v4.json"
    assert bi.snapshot_paths(2)[0].name == "matkg_bl1101_v2.json"


def test_secret_and_skip_rules():
    P = Path
    assert bi.is_secret_path(P("a/.env")) and bi.is_secret_path(P(".env.production"))
    assert not bi.is_secret_path(P(".env.example")) and bi.is_secret_path(P("id_rsa"))
    assert not bi.is_secret_path(P("README.md"))
    assert bi.should_skip_file(P("data/x.H5")) and bi.should_skip_file(P("repo/.git/config"))
    assert bi.should_skip_file(P("repo/node_modules/x/y.js")) and bi.should_skip_file(P("ExampleData2/a.txt"))
    assert bi.should_skip_file(P("repo/secrets.yaml")) and not bi.should_skip_file(P("repo/src/a.py"))


def test_rel_and_make_term_shapes():
    assert bi._rel("t", "hasPart", "x") == {"relation": "hasPart", "related_term": "x", "related_id": "x"}
    assert bi._rel("t", "hasPart", "x", "ev")["evidence"] == ["ev"]
    t = bi.make_term("beamline:A", "A", "Motor", "def", "src", [bi._rel("A", "r", "B")], pv="X:1", empty="", none=None,
                     lst=[], type="custom")
    assert t["id_prefix"] == "beamline" and t["source_papers"] == ["src"]
    assert t["pv"] == "X:1" and "empty" not in t and "none" not in t and "lst" not in t and t["type"] == "custom"
    plain = bi.make_term("i", "n", "c", "d", "s")
    assert plain["relations"] == [] and plain["type"] == "beamline:c"


# ----------------------------------------------------------------------------- fetch_url
# fetch_url goes through literature.legacy_download.hardened_get_bytes (owner decision 2026-11-09, seventeenth set).
# These two tests used to patch ``bi.urlopen`` and read the ``Request`` header and the positional timeout; they
# now fake the hardened getter and keep what they proved: the User-Agent header and the timeout reach the getter,
# the success record carries hash, size, status and the repo-relative path, and a failure is a record, not a raise.
def test_fetch_url_success_records_hash_and_repo_relative_path(monkeypatch):
    dest = bi.REPO_ROOT / ".cache" / "t27_test_fetch" / "page.html"
    seen = {}

    def fake_get(url, *, headers, timeout, **_kwargs):
        seen.update(url=url, ua=headers["User-Agent"], accept=headers["Accept"], timeout=timeout)
        return b"hello"

    monkeypatch.setattr(bi, "hardened_get_bytes", fake_get)
    try:
        rec = bi.fetch_url("https://example.org/p", dest, timeout=7)
        assert rec["status"] == "ok" and rec["bytes"] == 5 and rec["sha256"] == bi.sha256_bytes(b"hello")
        assert rec["path"] == ".cache/t27_test_fetch/page.html" and rec["http_status"] == 200
        assert dest.read_bytes() == b"hello" and seen["timeout"] == 7 and "FAIR2WISE" in seen["ua"]
        assert seen["url"] == "https://example.org/p" and seen["accept"].startswith("text/html")
    finally:
        import shutil

        shutil.rmtree(dest.parent, ignore_errors=True)


def test_fetch_url_failure_is_reported_not_raised_and_writes_nothing(monkeypatch, tmp_path):
    def boom(url, **_kwargs):
        raise bi.JSONFetchError("network error (ConnectError): no route")

    monkeypatch.setattr(bi, "hardened_get_bytes", boom)
    out = bi.fetch_url("https://example.org/p", tmp_path / "x" / "p.html")
    assert out["status"] == "failed" and "no route" in out["reason"] and not (tmp_path / "x").exists()


# ----------------------------------------------------------------------------- ALS page / people
ALS_HTML = """
<html><body><h1> Beamline 11.0.1.2 </h1>
<table>
 <tr><th>Primary contact(s)</th><td>Cheng Wang (cwang@lbl.gov) | Thomas Ferron; Primary Contact</td></tr>
 <tr><th>Technique category</th><td>Scattering | RSoXS; Reflectivity / xx</td></tr>
 <tr><th>Minimum energy</th><td>165 eV</td></tr><tr><th>only one cell</th></tr>
</table>
<p>First paragraph.</p><p>   </p><p>Second one.</p></body></html>
"""


def test_parse_als_page_extracts_facts_techniques_paragraphs_and_contacts():
    page = bi.parse_als_page(ALS_HTML)
    assert page["title"] == "Beamline 11.0.1.2"
    assert page["facts"]["Minimum energy"] == "165 eV" and "only one cell" not in page["facts"]
    assert page["techniques"] == ["Scattering", "RSoXS", "Reflectivity"]
    assert "RSoXS" in page["techniques"] and "xx" not in page["techniques"]  # tokens of <= 2 chars dropped
    assert page["paragraphs"] == ["First paragraph.", "Second one."]
    assert [p["name"] for p in page["people"]] == ["Cheng Wang", "Thomas Ferron"]
    assert page["people"][0]["email"] == "cwang@lbl.gov" and page["people"][1]["email"] == ""
    assert page["people"][0]["role"] == "BeamlineScientist" and page["text"]


def test_parse_als_page_defaults_title_without_h1():
    assert bi.parse_als_page("<html><body><p>x</p></body></html>")["title"] == "Beamline 11.0.1.2"


def test_clean_person_name_rejects_non_people():
    assert bi._clean_person_name("  (Cheng Wang);") == "Cheng Wang"
    for bad in ("", "Single", "Primary Contact", "Sample Stage", "John of"):
        assert bi._clean_person_name(bad) == ""
    assert bi._clean_person_name("Jane Q Do") == ""  # surname shorter than 3 chars after stripping dots
    assert bi._clean_person_name("Jane Doe.") == "Jane Doe."


def test_contacts_fall_back_to_page_text_and_dedupe():
    people = bi.parse_als_contact_people({}, "Cheng Wang at x. Cheng Wang again. Beam Current stays.")
    assert [p["name"] for p in people] == ["Cheng Wang"]
    assert bi.parse_als_contact_people({"Contacts": "Ada Lovelace | Ada Lovelace"})[0]["evidence"].startswith("ALS primary")


def test_blueprint_people_from_header_and_html():
    bp = {"header": {"sources": "Reviewed with Thomas Ferron on 2026", "Rev": "beamline scientist: Cheng Wang"}}
    people = bi.parse_blueprint_people(bp, "also review with Thomas Ferron; and reviewed with Sample Stage")
    assert [p["name"] for p in people] == ["Thomas Ferron", "Cheng Wang"]
    assert people[0]["source"] == bi.BLUEPRINT_PATH.name and people[0]["email"] == ""
    assert bi.parse_blueprint_people({}, "") == []


# ----------------------------------------------------------------------------- blueprint HTML
BLUEPRINT_HTML = """
<html><body>
<header class="titleblock"><div><span class="k">Rev</span><span class="v">E</span></div>
 <div><span class="k"> </span></div></header>
<div class="sources">Sources: reviewed with Thomas Ferron</div>
<table id="corr">
<tr id="dev-m1"><td>M1 drawn</td><td>Mirror one<span data-tip="&lt;span class=&quot;tip-k&quot;&gt;Motor&lt;/span&gt;&lt;span class=&quot;tip-m&quot;&gt;Steers beam&lt;/span&gt;">i</span></td>
  <td>IOC:rec</td><td><span class="pv">X:M1</span></td><td><span class="hd">d.motors["m1"]</span></td><td>EpicsMotor</td><td>map.vi</td></tr>
<tr id="dev-cam"><td>cam</td><td>Camera detector</td><td>IOC:cam</td><td><span class="pv">X:CAM</span></td><td><span class="hd">odd handle</span></td><td>AreaDetector</td></tr>
<tr id="dev-dio"><td>d</td><td>Valve</td><td>I</td><td><span class="pv">X:D</span></td><td><span class="hd">d.dio["v"]</span></td><td>DIO box</td></tr>
<tr id="dev-ai"><td>a</td><td>Diode</td><td>I</td><td><span class="pv">X:A</span></td><td><span class="hd">d.ai["pd"]</span></td><td>analog reader</td></tr>
<tr id="dev-short"><td>only</td><td>two</td></tr>
</table>
<div class="stage"><span class="stage-id">A.1</span><span class="stage-name">Mono 101</span>
 <div class="stage-what">Grating <b>mono</b></div><div class="stage-does">Selects energy</div>
 <div class="stage-range">165-1500 eV</div><a href="#dev-m1">m1</a><a href="#dev-cam">cam</a><a href="#other">x</a></div>
<div class="stage"><span class="stage-name"></span></div>
<div class="rule"><span class="rule-k">Safety</span> keep <i>closed</i></div>
<section id="sheet-a"><figure><svg>
 <text class="big" x="50" y="10">EPU</text><text class="big" x="10" y="10">Storage ring</text>
 <text class="big" x="10" y="30">Exit slits</text><text class="big" x="10" y="20">M103</text>
 <text class="big" x="10" y="25">Sample · θ = 0</text><text class="big" x="1" y="40">Chamber</text>
 <text class="big" x="2" y="10">EPU</text><text class="big"> </text>
</svg><figcaption>Path: mono → M103 → exit slits → PZT shutter</figcaption></figure></section>
</body></html>
"""


def test_parse_blueprint_devices_kinds_and_handles():
    bp = bi.parse_blueprint_html(BLUEPRINT_HTML)
    assert bp["header"]["Rev"] == "E" and "Thomas Ferron" in bp["header"]["sources"]
    d = bp["devices"]
    assert set(d) == {"m1", "cam", "dio", "ai"}  # short row skipped
    assert d["m1"].kind == "motor" and d["m1"].category == "Motor" and d["m1"].meaning == "Steers beam"
    assert (d["m1"].ophyd_name, d["m1"].collection, d["m1"].mapping_file) == ("m1", "motors", "map.vi")
    assert d["cam"].kind == "detector" and d["cam"].category == "Detector"
    assert d["cam"].ophyd_name == "cam" and d["cam"].collection == "" and d["cam"].mapping_file == ""
    assert d["dio"].kind == "digital i/o" and d["dio"].category == "DigitalIO"
    assert d["ai"].kind == "analog input" and d["ai"].category == "AnalogInput"


def test_parse_blueprint_stages_rules_and_beam_path():
    bp = bi.parse_blueprint_html(BLUEPRINT_HTML)
    (stage,) = bp["stages"]
    assert (stage.name, stage.section, stage.what) == ("Mono 101", "A.1", "Grating mono")
    assert stage.does == "Selects energy" and stage.range_text == "165-1500 eV" and stage.device_ids == ["cam", "m1"]
    assert bp["rules"][0]["name"] == "Safety" and "closed" in bp["rules"][0]["text"]
    # sorted by (y, x); the duplicate EPU label and the sheet-B "Sample ·" label are dropped
    assert bp["beam_path_svg"] == ["EPU", "Storage ring", "M103", "Exit slits", "Chamber"]
    assert bp["beam_path"] == ["EPU", "Storage ring", "M103", "Exit slits", "Sample stage", "Detector"]
    assert [p["name"] for p in bp["people"]] == ["Thomas Ferron"]
    assert bp["beam_note"] == bp["header"]["sources"]


def test_arrow_sequence_and_canonical_names():
    assert bi.parse_arrow_sequence("no arrows") == []
    assert bi.parse_arrow_sequence("a -> b") == []  # a chain needs at least three stages
    chain = bi.parse_arrow_sequence("mono → kirkpatrick-baez → exit slit → exit slits")
    assert chain == ["Mono 101", "M103", "Exit slits"]
    assert bi.canonical_stage_name("  PIEZOSHUTTER ") == "PZT shutter" and bi.canonical_stage_name("Unknown  Stage") == "Unknown Stage"
    assert bi.canonical_stage_name("") == ""


def test_m103_reordering_and_finalisation():
    assert bi.ensure_m103_before_exit_slits(["Mono 101", "Exit slits", "M103"]) == ["Mono 101", "M103", "Exit slits"]
    assert bi.ensure_m103_before_exit_slits(["M103", "Exit slits"]) == ["M103", "Exit slits"]
    assert bi.finalize_beam_path([]) == []
    out = bi.finalize_beam_path(["EPU", "M101", "Mono 101", "Chamber"])
    assert out == ["Storage ring", "EPU", "M101", "Mono 101", "Sample stage", "Detector"]
    assert bi.finalize_beam_path(["Storage ring", "EPU", "Detector", "Sample stage"]).count("Detector") == 1


def test_resolve_beam_path_prefers_svg_then_caption_then_bcs2sim_never_stage_dom():
    r = bi.resolve_beam_path_order
    assert r(svg_names=["EPU"], caption_names=["X"])[-2:] == ["Sample stage", "Detector"]
    assert "Y" in r(svg_names=[], caption_names=["Y"]) and "Z" in r(svg_names=[], caption_names=[], bcs2sim_names=["Z"])
    assert r(svg_names=[], caption_names=[], stage_names=["A", "B"]) == []


def test_bcs2sim_beam_path_reads_matching_knowledge_files(tmp_path):
    assert bi.parse_bcs2sim_beam_path(None) == [] and bi.parse_bcs2sim_beam_path(tmp_path / "missing") == []
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "unrelated_knowledge.md").write_text("mono → a → b")  # no M103 / exit slits
    (tmp_path / "docs" / "bl11012_knowledge.md").write_text("mono → M103 → exit slits → sample")
    assert bi.parse_bcs2sim_beam_path(tmp_path) == ["Mono 101", "M103", "Exit slits", "Sample stage"]


# ----------------------------------------------------------------------------- git
def _cp(stdout="deadbeefcafe\n", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def test_git_sha_and_run_git_arguments(monkeypatch):
    calls = []
    monkeypatch.setattr(bi.subprocess, "run", lambda args, **kw: calls.append((args, kw)) or _cp())
    assert bi.git_sha(Path("/repo")) == "deadbeefcafe"
    assert calls[0][0] == ["git", "-C", "/repo", "rev-parse", "HEAD"] and calls[0][1]["check"] is True
    bi._run_git(["git", "status"], cwd=Path("/x"))
    assert calls[1][1]["cwd"] == "/x" and calls[1][1]["timeout"] == 180
    bi._run_git(["git", "status"])
    assert calls[2][1]["cwd"] is None


def _spec(readme_only=False):
    return bi.RepoSpec(name="r", url="https://example.org/r.git", mode="docs", readme_only=readme_only)


def test_sync_repo_clone_update_and_sparse_paths(monkeypatch):
    dest = bi.REPO_ROOT / ".cache" / "t27_sync" / "clone"
    cmds = []
    monkeypatch.setattr(bi, "_run_git", lambda args, cwd=None: cmds.append(list(args)) or _cp())
    monkeypatch.setattr(bi, "git_sha", lambda p: "a" * 40)
    try:
        rec = bi.sync_repo(_spec(), dest)
        assert rec["status"] == "ok" and rec["sha"] == "a" * 40 and rec["path"] == ".cache/t27_sync/clone"
        assert cmds[-1][:4] == ["git", "clone", "--depth", "1"]
        bi.sync_repo(_spec(readme_only=True), dest)
        assert "--sparse" in cmds[-2] and cmds[-1][:3] == ["git", "sparse-checkout", "set"]
        (dest / ".git").mkdir(parents=True)
        bi.sync_repo(_spec(), dest)
        assert cmds[-2][:3] == ["git", "fetch", "--depth"] and cmds[-1][:3] == ["git", "reset", "--hard"]
    finally:
        import shutil

        shutil.rmtree(bi.REPO_ROOT / ".cache" / "t27_sync", ignore_errors=True)


def test_sync_repo_tolerates_failed_sparse_checkout_and_skips_unavailable_repos(monkeypatch, tmp_path):
    state = {"n": 0}

    def run(args, cwd=None):
        if args[1] == "sparse-checkout":
            raise subprocess.CalledProcessError(1, args, stderr="old git")
        return _cp()

    monkeypatch.setattr(bi, "_run_git", run)
    monkeypatch.setattr(bi, "git_sha", lambda p: "b" * 40)
    dest = bi.REPO_ROOT / ".cache" / "t27_sync2" / "c"
    try:
        assert bi.sync_repo(_spec(readme_only=True), dest)["status"] == "ok"
    finally:
        import shutil

        shutil.rmtree(bi.REPO_ROOT / ".cache" / "t27_sync2", ignore_errors=True)

    def denied(args, cwd=None):
        raise subprocess.CalledProcessError(128, args, stderr=" fatal: repository not found \n")

    monkeypatch.setattr(bi, "_run_git", denied)
    out = bi.sync_repo(_spec(), tmp_path / "p" / "c")
    assert out["status"] == "skipped" and out["reason"] == "fatal: repository not found"
    monkeypatch.setattr(bi, "_run_git", lambda a, cwd=None: (_ for _ in ()).throw(subprocess.TimeoutExpired("git", 180)))
    assert bi.sync_repo(_spec(), tmp_path / "q" / "c")["status"] == "skipped"


def test_expand_globs_dedupes_and_filters_secrets_and_dirs(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x")
    (tmp_path / "src" / "b.py").write_text("x")
    (tmp_path / "src" / ".env").write_text("S=1")
    (tmp_path / "src" / "data.h5").write_text("x")
    (tmp_path / "src" / "pkg.py").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "z.py").write_text("x")
    got = bi.expand_globs(tmp_path, ["src/*.py", "src/a.py", "**/*.py", "src/.env", "src/*.h5"])
    assert [p.name for p in got] == ["a.py", "b.py"]
