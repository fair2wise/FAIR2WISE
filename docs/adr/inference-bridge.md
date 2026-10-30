# ADR: Preserve the existing F2W inference paths behind a thin bridge

Status: Accepted at H1 (2026-10-12) (T01; `docs/migration/reviews/H1/decision.json` H1-8: the ADR set is the H1 baseline)
Date: 2026-10-12
Superseded/amended by: item 3 approved as H1-3 (one shared CBORG admission controller for sync and async callers, 429-aware) and item 4 as H1-4 (fail closed when `CBORG_API_KEY` is unset); both applied in `1dfacd8`. O-R1 (2026-10-13): in `F2W_RUNTIME=langgraph` with `F2W_QA_ROUTE=supervisor`, a planner failure falls back to the direct `answer_from_knowledge` call. O-U2 (2026-10-16): the validation judge model is a deployment-wide setting chosen from the live model list (`/v1/validation/judge-model`, T11B), never a hardcoded model.

## Context

D05/D18: keep the existing F2W LLM classes, frontend-selected models and QA
constraints; respect limits for every caller. The H0 baseline
(`docs/migration/f2w_llm_qa_baseline.json`, section
`inference_paths_and_admission`) records four distinct paths:

| Path | Class / entry | Notable behavior (baseline) |
|---|---|---|
| Sync | `term_extractor/clients.py` `ChatClient.chat(prompt, *, temperature=0.0, timeout=240, trace_label=None)` | base URL read per call; openai errors pass through |
| Async | `kg_rag_api.py` `ChatClient.chat(messages)` / `call_llm` | temperature 0.4, timeout 120 fixed at import; 3 error types mapped to `RuntimeError` |
| Tool-bound | `Orchestrator._build_llm` -> `ChatOpenAI` + `bind_tools` | no timeout/max_retries set; untraced |
| Standalone | `extract_terms.py` client/factory | separate copy of the sync client; bypasses the CBORG limiter |

Admission today: `cborg_limiter` has separate thread and async semaphores of
`CBORG_MAX_CONCURRENCY` (default 5) each, per process, with no 429 backoff;
`extract_terms.py` and the `cborg-openai` orchestrator backend bypass it.

Model precedence today: UI `PUT /settings` > CLI `--model` > env
(`KG_RAG_CBORG_MODEL` > `KG_RAG_MODEL` > `F2W_MODEL`) > `config.yml` > hardcoded
(`lbl/cborg-chat`, `deepseek-r1:70b`).

## Decision

1. **Bridge, not replacement.** `app/modules/llm_adapters.py` exposes small
   capability adapters (`complete_text`, `complete_messages`, `bind_tools`)
   that wrap the existing classes. Ported FORGE services call these adapters;
   they never construct providers themselves.
2. **One settings resolution path.** Chat, headless jobs, evaluations and FORGE
   delegates resolve provider/model through the existing precedence above.
   Each inference attempt records the concrete non-secret settings it used.
3. **Shared admission (fixes the baseline gap, behind a flag).** Introduce one
   admission controller per provider account that both sync and async callers
   use, honors 429/`Retry-After`, and releases on cancel/error. Every path in
   the table must go through it, including `extract_terms.py` and the
   `cborg-openai` backend. Default numbers stay `CBORG_MAX_CONCURRENCY`. The
   combined cap changes effective concurrency from up to 10 to 5 per process,
   so it ships behind a flag with a parity measurement (T02/T10).
4. **No new provider, default or paid fallback.** Paid models stay reachable
   only through the user's existing CBORG selection. If `CBORG_API_KEY` is
   unset, fail closed instead of letting the openai SDK fall back to
   `OPENAI_API_KEY` (baseline risk recorded at H0).
5. **Fakes.** `app/modules/contracts/testing.py` provides deterministic fakes
   whose `chat()` signatures are tested against the real classes via AST.

## Consequences

- Behavior differences between paths (temperature, timeouts, error mapping)
  are preserved per path, not unified, unless the owner approves a change.
- Item 3 and item 4 are behavior changes. They need owner approval at H1 and
  parity evidence at H2.
