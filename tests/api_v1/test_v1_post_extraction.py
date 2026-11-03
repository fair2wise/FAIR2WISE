"""T18E: ALS never auto-approves post-extraction publication (owner decision Q11).

Runs the real :class:`PostExtractionPublisher` on the ALS ``V1App`` (fake identity port, fake publish step, the
real publication job). Even when the importer calls the plan safe, the job stops at review; the only way on is one
verified admin's approval through the jobs API, whose record has no policy reason.
"""
import pytest

from app.api.post_extraction import PostExtractionPublisher
from app.modules.contracts.jobs import JobState
from tests.api_v1.test_v1_als import _ctx


def _publisher(app, *, safe):
    return PostExtractionPublisher(app.ops, freeze=lambda _terms: "rsoxs/v1", is_safe=lambda _plan: safe,
                                   target=app.target, worker_owner="pe")


@pytest.mark.parametrize("safe", [True, False])
def test_als_always_stops_at_review_and_creates_no_approval(als, safe):
    admin = _ctx(als, "admin")
    result = _publisher(als, safe=safe).publish(admin, "terms.json")
    assert result["status"] == "awaiting_review" and result["auto_approved"] is False, result
    assert result["approval_ids"] == [] and "ALS" in result["message"]
    job_id = result["job_id"]
    assert als.ops.get_job(admin, job_id).state is JobState.AWAITING_REVIEW
    assert als.ops.job_view(admin, job_id)["publication"]["approvals"] == []
    assert als.ops.publication_pointer(admin, als.target) is None, "nothing was published"


def test_als_one_verified_admin_approval_publishes_and_has_no_policy_reason(als):
    admin = _ctx(als, "admin")
    result = _publisher(als, safe=True).publish(admin, "terms.json")
    plan = als.ops.current_plan(admin, result["job_id"])
    approved = als.ops.approve_publication(admin, result["job_id"], plan.plan_sha256,
                                           approver_credential=als.store.get("cookie-admin"))
    assert approved["approval"]["approved_by"] == "uuid-admin" and approved["approval"]["reason"] is None
    assert als.work() == (result["job_id"], JobState.SUCCEEDED)
    assert als.ops.job_view(admin, result["job_id"])["ui_state"] == "published"
