# Proposed per-job model and settings contract

**Draft v5. O03 remains pending owner approval.** This is a proposed strengthening of the audited FORGE paper-job behavior, not a claim that FORGE already snapshots every effective setting.

## 1. Recommendation

At submission, resolve the selected frontend/headless F2W model through the existing classes/settings and record effective nonsecret generation settings once for that job. New frontend selections affect new jobs/turns. Running jobs retain the submitted resolved settings. An intentional changed-model resume is a new settings/execution revision with explicit provenance, not a silent mutation of the old run.

Proposed metadata includes settings_revision/hash, requested and resolved provider/model identifiers, configuration source, generation parameters, prompt revision and available model revision. Store credential references separately, never keys. Record unavailable provider/model revision metadata as unknown; a named model alias alone is not a reproducible model-weight pin.

## 2. Resume behavior

Resume compatible completed stages only. Same resolved settings/source/schema/pipeline can reuse their validated artifacts. If a selected model, prompt or relevant parser/schema revision changes, show the revision difference and invalidate only dependent stages, with owner-approved policy. Preserve prior outputs and lineage. Do not relabel old model output as newly produced by another model. No automatic paid-model fallback when the original model is unavailable.

The JSON snapshot manifest records the effective settings used by contributing extraction runs; it need not pretend a multi-run corpus was created by one model. Importing that snapshot into Tiled never requires an inference model at all.

## 3. What remains live

Cancellation, credential revocation, access checks, provider backoff/rate limits, administrative pause and budget ceilings remain effective during a job. Capturing generation settings never freezes permission or grants unlimited spending. A configured concurrency value is an upper bound, not a guarantee of admission when shared capacity or provider throttling changes.

Do not copy FORGE's environment-reading model/default transport into F2W. Reuse existing F2W clients and setting precedence. Persist only safe metadata, not a copy of os.environ. Hosted SDK/FastAPI/jobs share the same resolver and admission controls.

## 4. Integration and tests

T01/H1 records the owner decision. T02/T10 implement the approved behavior, T11 adds binary cases and T18/T15 demonstrate UI/SDK/FORGE consistency. Proposed tests: start model A, change UI to B, observe A for the first job and B for the next; restart with changed defaults; explicitly revise a model on resume; revoke access or tighten a rate limit while the job runs; prove import calls no LLM. Until O03 is approved, do not claim these proposal-specific tests define an agreed F2W policy.
