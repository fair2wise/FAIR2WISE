"""``kg_update``: the Splash wipe path is retired (T18E, owner decision Q11) and publication replaces it.

The last binary known failure (``test_splash_reimport_refuses_wipe_without_explicit_allow``) is retired by
``test_the_splash_wipe_path_no_longer_exists``.
"""
import re
from pathlib import Path

from app.modules.f2w_agent import cli, coordinator, kg_update

REPO = Path(__file__).resolve().parents[1]


def test_the_splash_wipe_path_no_longer_exists():
    for name in ("splash_reimport", "_wipe_splash_via_graphql", "_splash_db_path", "_splash_is_local"):
        assert not hasattr(kg_update, name), name
    assert "allow_splash_wipe" not in coordinator.CoordinatorConfig.__dataclass_fields__
    assert "--allow-splash-wipe" not in cli.build_parser().format_help()
    # A mutation that deletes Splash entities is not defined anywhere in the module either.
    assert "deleteEntity" not in Path(kg_update.__file__).read_text(encoding="utf-8")


def test_no_image_script_or_doc_entrypoint_passes_the_wipe_flag():
    scripts = [p for p in (REPO / "scripts").rglob("*") if p.is_file()]
    scripts += [REPO / "Dockerfile", REPO / "compose.yaml", REPO / "docker-compose.yml"]
    scripts += [p for p in (REPO / "docker").rglob("*") if p.is_file()]
    hits = [str(p.relative_to(REPO)) for p in scripts
            if re.search(r"allow[-_]splash[-_]wipe|ALLOW_SPLASH_WIPE", p.read_text(encoding="utf-8", errors="ignore"),
                         re.IGNORECASE)]
    assert hits == []
    assert not (REPO / "scripts" / "wipe_splash_db.sh").exists()
    assert not (REPO / "scripts" / "reimport_merged_kg.sh").exists()


def test_publish_extraction_without_a_publisher_publishes_and_deletes_nothing():
    result = kg_update.publish_extraction("terms.json")
    assert result["status"] == "skipped" and "not re-imported" in result["message"]  # legacy fields unchanged
    # D-owner 2026-11-03: a distinct explicit state, not just "skipped".
    assert result["publish_state"] == "json_available_tiled_unavailable"
    assert result["indicator"] == "JSON available, Tiled unavailable"
    assert result["message"].startswith("JSON available, Tiled unavailable")


def test_publish_extraction_hands_the_terms_and_caller_to_the_publisher():
    seen = []

    class Publisher:
        def publish(self, ctx, terms):
            seen.append((ctx, terms))
            return {"status": "awaiting_review", "message": "m"}

    result = kg_update.publish_extraction("terms.json", publisher=Publisher(), ctx="caller")
    assert result == {"status": "awaiting_review", "message": "m", "publish_state": "awaiting_review"}
    assert seen == [("caller", "terms.json")]


def test_publish_extraction_keeps_a_state_the_publisher_set_and_maps_the_others():
    class Publisher:
        def __init__(self, result):
            self.result = result

        def publish(self, ctx, terms):
            return self.result

    kept = kg_update.publish_extraction("t", publisher=Publisher({"status": "success", "publish_state": "x"}))
    assert kept["publish_state"] == "x"
    assert kg_update.publish_extraction("t", publisher=Publisher({"status": "success"}))["publish_state"] == "tiled_published"
    assert kg_update.publish_extraction("t", publisher=Publisher({"status": "error"}))["publish_state"] == "error"
    assert "publish_state" not in kg_update.publish_extraction("t", publisher=Publisher({"status": "other"}))
