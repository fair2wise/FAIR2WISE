# Observability: OpenTelemetry traces in Jaeger

FAIR2WISE can export OpenTelemetry **traces** (OTLP over HTTP) to Jaeger, directly or through an
OpenTelemetry collector. It is **off by default**: with `F2W_OTEL_ENABLED` unset nothing is imported,
exported or changed in any image (`:local`, `:als-dev`, `:als-prod`, `:cluster`). Use it to see one chat
turn or one job as a tree of timed calls: the request, the F2W functions it runs, every model call,
every HTTP call, and the worker's job stages.

```
 browser ──▶ API process ──────────────┐   spans (batched, in memory)      ┌─ Jaeger UI
              POST /chat               ├──▶ OTLP/HTTP :4318 ──▶ collector ─┤
              └ f2w functions          │    (never blocks a request)       └─ Prometheus (optional)
              └ llm.call / HTTP calls  │
 worker process ── job.run ── job.stage┘   (same trace: the API stores the traceparent with the job)
```

The existing per-turn JSON trace (`F2W_TRACE_ENABLED`, `F2W_TRACE_DIR`) is unchanged. When OpenTelemetry is
on, its turn record also carries `otel_trace_id` (and the summary line too), and every traced response has an
`X-F2W-Trace-Id` header, so a turn file and its Jaeger trace find each other. No response body changes.

## Settings

All settings are environment variables, read at process start. Standard OpenTelemetry names are honoured as the
OpenTelemetry specification defines them.

| Variable | Default | Meaning |
|---|---|---|
| `F2W_OTEL_ENABLED` | unset (off) | `1`, `true`, `yes` or `on` turns tracing on. Anything else is off |
| `OTEL_ENABLED` | unset | alias for `F2W_OTEL_ENABLED` (the ALS Computing Hub's own switch name); `F2W_OTEL_ENABLED` wins when both are set |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` | collector base URL; spans go to `<base>/v1/traces` (OTLP/HTTP, protobuf) |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | unset | full traces URL, used as given (overrides the base URL) |
| `OTEL_EXPORTER_OTLP_HEADERS` | unset | `k=v,k2=v2` request headers (for example a token for a hosted collector); never logged |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` | only `http/protobuf` is supported; anything else logs a warning and uses it |
| `OTEL_SERVICE_NAME` | `f2w` | the service name in Jaeger (api and worker share it; the `f2w.process` resource attribute tells them apart) |
| `OTEL_RESOURCE_ATTRIBUTES` | unset | extra resource attributes, `k=v,k2=v2` |
| `OTEL_TRACES_SAMPLER` | `parentbased_always_on` | `always_on`, `always_off`, `traceidratio`, `parentbased_always_on`, `parentbased_always_off`, `parentbased_traceidratio` |
| `OTEL_TRACES_SAMPLER_ARG` | unset (1.0) | ratio for the `*traceidratio` samplers, 0 to 1 |
| `F2W_OTEL_TRACE_FUNCTIONS` | unset | comma list of functions to trace by name (below). **Unset: the built-in list. Set but empty: no function tracing** |
| `F2W_OTEL_TRACE_MODULES` | unset | comma list of module or package prefixes whose public functions are all traced |
| `F2W_OTEL_MIN_SPAN_MS` | `0` | spans of `F2W_OTEL_TRACE_MODULES` functions shorter than this many milliseconds are dropped (errors are always kept) |
| `F2W_OTEL_EXPORT_TIMEOUT_S` | `3` | timeout of one export request (seconds) |
| `F2W_OTEL_QUEUE_SIZE` | `2048` | spans queued for export; beyond it new spans are dropped, never the request |
| `F2W_OTEL_TRACE_HTTP` | `1` | `0` turns the `httpx` client spans off |
| `F2W_CODE_COMMIT`, `GIT_COMMIT` | image build argument | resource attribute `f2w.code_commit` (a checkout falls back to `git rev-parse HEAD`) |
| `F2W_IMAGE_PROFILE` | unset | resource attribute `f2w.profile`; unset reads the `profile` name of the baked profile file (`F2W_PROFILE_FILE`, default `/etc/fair2wise/profile.json`), then `F2W_DEPLOYMENT_PROFILE` |

Resource attributes on every span: `service.name`, `service.version`, `f2w.code_commit`, `f2w.profile`,
`f2w.process` (`api`, `worker`...), `host.name`, `process.pid`, runtime name and version.

## What is traced

**Request spans.** Every API request is a `SERVER` span named `<METHOD> <route template>` (for example
`POST /chat`, `GET /v1/jobs/{job_id}`) with `http.request.method`, `http.route`, `url.path` (the template, never the
query string), `http.response.status_code`, `f2w.http.duration_ms`, and error status for a 5xx or an exception.
`/livez`, `/readyz`, `/health` and `/healthz` are not traced. An incoming `traceparent` header becomes the parent.

**Function spans.** The built-in list below, plus whatever you add (next section). A call is one span with
`code.function` and `code.namespace`; nesting follows the call tree, so a turn shows its retrieval, judge,
leeway and answer stages in order, each with its timing.

**Model calls.** Every model call (judge, leeway, general answer, planner, extraction, session summary...)
passes `trace.record_llm_call`, which now also emits an `llm.call <label>` span: `gen_ai.request.model`,
`f2w.llm.label`, `f2w.llm.status`, `f2w.llm.latency_ms`, and the sizes `f2w.llm.prompt_chars` and
`f2w.llm.response_chars`. Token counts are not recorded (the call sites do not have them).

**HTTP calls.** `httpx` clients (model API, OpenAlex, downloads, identity) produce `HTTP <METHOD>` spans with
`server.address`, `server.port`, `url.scheme`, `http.response.status_code`. `requests`-based clients (the Tiled
GraphQL client) have no HTTP span; their Tiled functions are on the function list instead.

**Jobs.** The API stores the `traceparent` of the submitting request in the additive table `job_trace_context` of
the job database (created on open; no migration, no change to the job rows or identity). The worker's `job.run`
span is a child of that request and each stage is a `job.stage` span (`f2w.job_id`, `f2w.job_kind`, `f2w.stage`).
A pause, stop or review wait ends the span without an error (`f2w.status` names it). Limits: a job resubmitted
with identical inputs keeps the first request's trace; jobs submitted while tracing was off have no parent
and start a new trace.

### What is never recorded, and why

`docs/adr/content-disclosure.md` restricts what may leave the protected store, and a trace backend is outside
it. Spans carry **ids, names, timings, counts, model names, status and sizes**. They never carry prompt text,
retrieved passages, document text, file contents, API keys, tokens, headers, request or response bodies, query
strings or e-mail addresses. This is enforced, not trusted:

- every attribute goes through `app/modules/tracing/safe.py`: the key must be on a short allowlist or end in an
  id/count/size/name/status suffix and must not name content (`prompt`, `text`, `content`, `passage`, `body`,
  `query`, `key`, `token`, `url`...); the value must be a number, a bool or a short single-line string with no
  e-mail address, URL query or value of a secret-named environment variable;
- an error records only the exception **class name** (`error.type`, status description), never its message
  (messages quote content);
- `tests/tracing/test_no_content.py` pushes sentinel prompt text and a fake API key through a chat turn, model
  calls, HTTP calls and failures and asserts that no span name, attribute, event or resource contains them.

## Tracing functions by name

`F2W_OTEL_TRACE_FUNCTIONS` wraps functions at startup without editing them:

```sh
F2W_OTEL_TRACE_FUNCTIONS="app.modules.f2w_agent.retrieval_agent:RetrievalAgent.query,app.modules.library.publish:*"
```

- `package.module:Class.method` or `package.module:function`: that callable (sync or async; static and class methods too);
- `package.module:*`: every public (no leading underscore) function defined in the module;
- append `@<ms>` to drop spans shorter than that: `app.module:fn@5`.

Rules: a callable is never wrapped twice; a target that cannot be imported or found is logged at warning level
and skipped (startup never fails); generators are skipped; a module-level function that another module already
imported by name is re-bound there too, so import order does not matter. Setting the variable **replaces** the
built-in list; set it to the list below plus your additions to extend it. Setting it to an empty string turns
function tracing off.

`F2W_OTEL_TRACE_MODULES=app.modules.library` traces every public function of a package and its submodules
(it imports them). Pair it with `F2W_OTEL_MIN_SPAN_MS=5` to cut the noise: a short span is still created, then
dropped before export.

In code, where wrapping by name is not enough:

```python
from app.modules import tracing

@tracing.traced("kg.load", attributes=lambda path: {"f2w.size": len(path)})
def load(path: str) -> dict: ...

with tracing.span("library.estimate", **{"f2w.corpus": "rsoxs"}) as s:
    s.set_attribute("f2w.paper_count", 12)
```

### The built-in list

Applied when tracing is on and `F2W_OTEL_TRACE_FUNCTIONS` is unset (`app/modules/tracing/defaults.py`; a test
imports every target and a drift test checks this page lists each one).

**Chat turn**

| Target (`module:qualname`) | Span name |
|---|---|
| `app.modules.f2w_agent.api:AgentPipelineService.ask` | `f2w_agent.api.AgentPipelineService.ask` |
| `app.modules.f2w_agent.api:AgentPipelineService.ask_with_progress` | `f2w_agent.api.AgentPipelineService.ask_with_progress` |
| `app.modules.f2w_agent.api:AgentPipelineService._handle_pending_message` | `f2w_agent.api.AgentPipelineService._handle_pending_message` |
| `app.modules.f2w_agent.api:AgentPipelineService._judge_agent_requirement` | `f2w_agent.api.AgentPipelineService._judge_agent_requirement` |
| `app.modules.f2w_agent.api:AgentPipelineService._generate_general_answer` | `f2w_agent.api.AgentPipelineService._generate_general_answer` |
| `app.modules.f2w_agent.api:AgentPipelineService._generate_json_graph_leeway_answer` | `f2w_agent.api.AgentPipelineService._generate_json_graph_leeway_answer` |
| `app.modules.f2w_agent.retrieval_agent:RetrievalAgent.query` | `f2w_agent.retrieval_agent.RetrievalAgent.query` |
| `app.modules.f2w_agent.retrieval_agent:RetrievalAgent._ensure_graphs` | `f2w_agent.retrieval_agent.RetrievalAgent._ensure_graphs` |
| `app.modules.f2w_agent.retrieval_agent:RetrievalAgent._fanout_hits` | `f2w_agent.retrieval_agent.RetrievalAgent._fanout_hits` |
| `app.modules.f2w_agent.retrieval_agent:RetrievalAgent._intent_routed_retrieve` | `f2w_agent.retrieval_agent.RetrievalAgent._intent_routed_retrieve` |
| `app.modules.f2w_agent.retrieval_agent:RetrievalAgent._parallel_retrieve` | `f2w_agent.retrieval_agent.RetrievalAgent._parallel_retrieve` |
| `app.modules.f2w_agent.retrieval_agent:RetrievalAgent._synthesize_leeway_answer` | `f2w_agent.retrieval_agent.RetrievalAgent._synthesize_leeway_answer` |
| `app.modules.f2w_agent.retrieval_agent:RetrievalAgent.search_node_scores` | `f2w_agent.retrieval_agent.RetrievalAgent.search_node_scores` |
| `app.modules.f2w_agent.retrieval_agent:_off_domain` | `f2w_agent.retrieval_agent._off_domain` |
| `app.modules.cborg_http:call_with_deadline` | `cborg_http.call_with_deadline` |

**Knowledge graph load and Tiled**

| Target (`module:qualname`) | Span name |
|---|---|
| `app.modules.f2w_agent.corpus_union:ChatCorpusUnion.refresh` | `f2w_agent.corpus_union.ChatCorpusUnion.refresh` |
| `app.modules.f2w_agent.corpus_union:ChatCorpusUnion._entry` | `f2w_agent.corpus_union.ChatCorpusUnion._entry` |
| `app.modules.f2w_agent.corpus_union:ChatCorpusUnion._build` | `f2w_agent.corpus_union.ChatCorpusUnion._build` |
| `app.modules.graph.backends.tiled_client:TiledGraphClient.query` | `graph.backends.tiled_client.TiledGraphClient.query` |
| `app.modules.graph.backends.tiled_mutations:TiledMutationClient.mutate` | `graph.backends.tiled_mutations.TiledMutationClient.mutate` |
| `app.modules.graph.backends.tiled_mutations:TiledMutationClient.scan_entities` | `graph.backends.tiled_mutations.TiledMutationClient.scan_entities` |
| `app.modules.graph.backends.tiled_mutations:TiledMutationClient.scan_links` | `graph.backends.tiled_mutations.TiledMutationClient.scan_links` |
| `app.modules.graph.backends.tiled:TiledGraphBackend.get_entity` | `graph.backends.tiled.TiledGraphBackend.get_entity` |
| `app.modules.graph.backends.tiled:TiledGraphBackend.find_entities` | `graph.backends.tiled.TiledGraphBackend.find_entities` |
| `app.modules.graph.backends.tiled:TiledGraphBackend.neighborhood` | `graph.backends.tiled.TiledGraphBackend.neighborhood` |
| `app.modules.graph.backends.tiled:TiledGraphBackend.apply_plan` | `graph.backends.tiled.TiledGraphBackend.apply_plan` |
| `app.modules.graph.backends.tiled:TiledGraphBackend.list_complete` | `graph.backends.tiled.TiledGraphBackend.list_complete` |
| `app.modules.graph.legacy_save:TiledSaveBackend.prepare` | `graph.legacy_save.TiledSaveBackend.prepare` |
| `app.modules.graph.legacy_save:TiledSaveBackend.save` | `graph.legacy_save.TiledSaveBackend.save` |
| `app.modules.graph.legacy_save:TiledSaveBackend.recover` | `graph.legacy_save.TiledSaveBackend.recover` |

**Library publish and publication jobs**

| Target (`module:qualname`) | Span name |
|---|---|
| `app.modules.library.publish:LibraryPublisher.preview` | `library.publish.LibraryPublisher.preview` |
| `app.modules.library.publish:LibraryPublisher.submit` | `library.publish.LibraryPublisher.submit` |
| `app.modules.library.publish:LibraryPublisher._export` | `library.publish.LibraryPublisher._export` |
| `app.modules.library.publish:LibraryPublisher._adopt` | `library.publish.LibraryPublisher._adopt` |
| `app.modules.library.publish:LibraryPublisher._stage` | `library.publish.LibraryPublisher._stage` |
| `app.modules.library.publish:LibraryPublisher._plan` | `library.publish.LibraryPublisher._plan` |
| `app.modules.jobs.publication:Publication.submit` | `jobs.publication.Publication.submit` |
| `app.modules.jobs.publication:Publication._plan` | `jobs.publication.Publication._plan` |
| `app.modules.jobs.publication:Publication._review` | `jobs.publication.Publication._review` |
| `app.modules.jobs.publication:Publication._revalidate` | `jobs.publication.Publication._revalidate` |
| `app.modules.jobs.publication:Publication._import` | `jobs.publication.Publication._import` |
| `app.modules.jobs.publication:Publication._verify` | `jobs.publication.Publication._verify` |
| `app.modules.jobs.publication:Publication._receipt` | `jobs.publication.Publication._receipt` |
| `app.modules.jobs.publication:Publication._activate` | `jobs.publication.Publication._activate` |
| `app.modules.jobs.publication:Publication._op_plan` | `jobs.publication.Publication._op_plan` |
| `app.modules.jobs.publication:Publication._op_apply` | `jobs.publication.Publication._op_apply` |
| `app.modules.jobs.publication:Publication._op_record` | `jobs.publication.Publication._op_record` |

**Extraction and guarded fetch**

| Target (`module:qualname`) | Span name |
|---|---|
| `app.modules.library.extraction:LibraryExtractor.parse` | `library.extraction.LibraryExtractor.parse` |
| `app.modules.library.extraction:LibraryExtractor.run` | `library.extraction.LibraryExtractor.run` |
| `app.modules.library.extraction:LibraryExtractor._run_locked` | `library.extraction.LibraryExtractor._run_locked` |
| `app.modules.literature.safe_fetch:SafeFetcher.fetch_pdf` | `literature.safe_fetch.SafeFetcher.fetch_pdf` |

Model calls are not on the list: they come from `record_llm_call` (above). The jobs also get `job.run` and
`job.stage` spans from the job runner.

## Cost and overhead

- **Off:** nothing is imported or wrapped. A `@traced` function costs one boolean check (about 0.13 microseconds
  per call measured on an Apple-silicon laptop). The API middleware is not even added.
- **On:** about 30 microseconds per span measured on the same laptop (SDK span plus the content filter, with a
  collector that was down), so a chat turn with a few dozen spans adds around a millisecond. Export runs on a
  background thread in batches of up to 256 every 2 seconds.
- **Memory:** at most `F2W_OTEL_QUEUE_SIZE` finished spans (2048 by default, a few MB).
- **Network:** one POST per batch to the collector. A busy turn is a few KB.
- **Noise:** `F2W_OTEL_TRACE_MODULES` on a large package can create many tiny spans; use `F2W_OTEL_MIN_SPAN_MS`
  or a sampler (`OTEL_TRACES_SAMPLER=parentbased_traceidratio`, `OTEL_TRACES_SAMPLER_ARG=0.25`). The duration filter
  runs after the span exists, so it saves export volume, not span creation.
- **Read-only root filesystem:** tracing writes no file. The exporter keeps everything in memory, so the
  `cluster` image needs no new writable path (the mounts in `cluster-image.md` are unchanged).
- **Collector down:** spans are dropped, one warning is logged (`trace export to ... failed`), a later success
  logs one recovery line. No request waits for export; shutdown flush is bounded by the export timeout.

## Troubleshooting

| Symptom | Check |
|---|---|
| No `f2w` service in Jaeger | `F2W_OTEL_ENABLED` is set in the container (`kubectl exec ... env \| grep OTEL`); the log shows `tracing on: service=f2w endpoint=...` at start. `tracing requested but not started` means the exporter package is missing |
| `trace export to <url> failed` (once) | wrong endpoint, collector down, or blocked by a proxy or NetworkPolicy. The image supervisor adds a non-public collector host (a single-label name such as `otel-collector`, a `.svc` or `.cluster.local` name, a private IP) to `NO_PROXY` automatically; a public host goes through the proxy and must be on its allowlist |
| Through a Squid allowlist | Prefer an in-cluster collector (no proxy). If you must export to a public host, add that domain to the Squid allowlist; HTTPS `CONNECT` is what Squid sees |
| Traces missing, service present | sampling: check `OTEL_TRACES_SAMPLER` and `_ARG`; `parentbased_*` follows the caller's decision |
| Too many tiny spans | `F2W_OTEL_MIN_SPAN_MS`, a narrower `F2W_OTEL_TRACE_MODULES`, or sampling |
| A function you listed has no spans | the warning at start names it (`trace target ... skipped`); generators are skipped; a module imported only after startup is wrapped only if it is on the list |
| Worker spans start a new trace | the job was submitted while tracing was off, or it is a resubmission of identical inputs |
| Jaeger UI shows an old trace id for a turn | the turn JSON file's `otel_trace_id` and the `X-F2W-Trace-Id` response header are the trace of the request that opened the turn |

## The ALS Computing Hub (developer compose)

`deploy/hub/compose.f2w.yaml` is a compose fragment for the hub's own `docker-compose.yml`: service `f2w`
(image `f2w:cluster`, hardened: read-only root, no capabilities, uid 10001), routed by the hub's Traefik at
`f2w.localhost` with a redirect from `/` to `/fair2wise/`, exporting to `http://otel-collector:4318` on the
`observability` network, so traces flow `f2w -> otel-collector -> jaeger`. `deploy/hub/README.md` addresses the hub
maintainer: what to paste, the variables, what to look for in Jaeger, and the optional `spanmetrics` connector.
F2W has no sign-in of its own in that image, so it must stay on localhost behind Traefik. Start it with
`F2W_OTEL_ENABLED=true podman-compose --profile f2w --profile observe up -d`.

## The K3s pilot

`deploy/k3s/jaeger/` holds example manifests for the owner's Flux repository (nothing here applies them):
namespace `observability` (Pod Security `restricted`), Jaeger v2 pinned by image digest as a single-replica
Deployment with a read-only root filesystem and an in-memory trace store bounded by `max_traces`
(Badger-on-PVC and OpenSearch are the persistence upgrades, in the ConfigMap comments), a Service (OTLP gRPC
4317, OTLP HTTP 4318, UI 16686), a NetworkPolicy (OTLP only from namespaces labelled
`f2w.observability/otlp-client=true`, the UI only from the ingress controller's namespace) and a Traefik
IngressRoute for the UI that **must** be put behind the forward-auth middleware (a marked placeholder in the file).
The F2W side is an environment fragment on the existing Deployment, in `cluster-image.md` ("Tracing"):

```yaml
        env:
          - {name: F2W_OTEL_ENABLED, value: "1"}
          - {name: OTEL_EXPORTER_OTLP_ENDPOINT, value: "http://jaeger.observability.svc:4318"}
          - {name: OTEL_SERVICE_NAME, value: "f2w"}
          # the supervisor adds jaeger.observability.svc to NO_PROXY itself; keep your own entries too
```

What was and was not run: the exporter and the span shapes were checked against real Jaeger containers
(2.3.0 and 2.22.0), and the Jaeger configuration in the ConfigMap was run under the same container hardening
(read-only root, non-root, no capabilities). The manifests themselves were checked structurally, not applied to
a cluster, and the hub's collector (contrib 0.120.0) was not run.

## Metrics (OpenTelemetry over OTLP/HTTP to a collector, scraped by Prometheus)

FAIR2WISE can also export application **metrics** (request rates and latencies, jobs, model calls, harvest,
Tiled and library operations). Like tracing it is **off by default** and costs nothing when off: no SDK import,
and every instrument call returns at once. Recording is in memory only; the OpenTelemetry SDK exports on its
own thread every `F2W_METRICS_EXPORT_INTERVAL_S` seconds, so a slow or dead collector never delays or fails a
request or a job. A failed export logs one warning, and one note when exports recover.

Metrics are on only when **both** `F2W_METRICS_ENABLED` is truthy **and** `OTEL_EXPORTER_OTLP_ENDPOINT` is set
(there is no default endpoint). The API process and the worker process each export their own series
(`f2w.process` = `api` or `worker`).

| Variable | Default | Meaning |
| --- | --- | --- |
| `F2W_METRICS_ENABLED` | unset (off) | `1`, `true`, `yes` or `on` turns metrics on (and an endpoint must be set) |
| `F2W_METRICS_EXPORT_INTERVAL_S` | `15` | seconds between exports; values below `1` are raised to `1` |

Reused, not new (see the table above): `OTEL_EXPORTER_OTLP_ENDPOINT` (the metrics URL is the base URL plus
`/v1/metrics`; a trailing `/v1/traces` is stripped; `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` is **not** read),
`OTEL_EXPORTER_OTLP_HEADERS`, `OTEL_SERVICE_NAME`, `F2W_OTEL_EXPORT_TIMEOUT_S` (export timeout, default 3 s),
`F2W_CODE_COMMIT`/`GIT_COMMIT` and `F2W_IMAGE_PROFILE` (resource attributes). The exporter is the same
`requests`-based OTLP/HTTP exporter as tracing, so it honours `NO_PROXY` and never goes through the egress proxy.

**Temporality is pinned to cumulative** for counters, histograms and gauges, set explicitly in code;
`OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` is ignored.

**Resource.** A small, separate metrics resource built with the plain SDK constructor: `service.name`, `f2w.process`, and
`f2w.profile` when set. Nothing else: no `service.instance.id` (the SDK's default would be a random uuid per start,
which the collector turns into the Prometheus `instance` label), no `telemetry.sdk.*`, `process.pid`, `host.name`,
and no commit (traces keep `f2w.code_commit`). A restart or a new release therefore continues the same series.
The commit is exposed by one metric, `f2w.build.info`, instead.

### Instruments

| OTel name | Type, unit | Labels | Prometheus name |
| --- | --- | --- | --- |
| `f2w.build.info` | observable gauge, `{info}` | `commit`, `profile` | `f2w_build_info` (value 1; emitted by the API process only, not the worker) |
| `f2w.jobs` | counter, `{job}` | `kind`, `outcome` | `f2w_jobs_total` |
| `f2w.job.duration` | histogram, `s` | `kind`, `outcome` | `f2w_job_duration_seconds_bucket`, `_sum`, `_count` |
| `f2w.jobs.queue.depth` | observable gauge, `{job}` | `kind`, `state` | `f2w_jobs_queue_depth` (worker process only) |
| `f2w.llm.calls` | counter, `{call}` | `provider`, `outcome` | `f2w_llm_calls_total` |
| `f2w.llm.call.duration` | histogram, `s` | `provider`, `outcome` | `f2w_llm_call_duration_seconds_bucket`, `_sum`, `_count` |
| `f2w.harvest.fetches` | counter, `{fetch}` | `source`, `outcome` | `f2w_harvest_fetches_total` |
| `f2w.tiled.operations` | counter, `{operation}` | `operation`, `outcome` | `f2w_tiled_operations_total` |
| `f2w.library.operations` | counter, `{operation}` | `operation`, `outcome` | `f2w_library_operations_total` |
| `f2w.http.requests` | counter, `{request}` | `http_route`, `http_method`, `status_class` | `f2w_http_requests_total` |
| `f2w.http.request.duration` | histogram, `s` | `http_route`, `http_method`, `status_class` | `f2w_http_request_duration_seconds_bucket`, `_sum`, `_count` |

`http_route` is the matched route **template** (`/v1/jobs/{job_id}`), never the raw path; a request that matched
no route is `unmatched`. `status_class` is `2xx`..`5xx`. Health probes (`/livez`, `/readyz`, `/health`,
`/healthz`) are not counted. Histogram boundaries (seconds, set with OpenTelemetry Views):
`0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30`. Long streams (job events) fall in the top bucket.

Jobs: `kind` is a registered job kind (`ingest`, `build_snapshot`, `publish`, ...; anything else is `other`) and
`outcome` is `succeeded`, `failed`, `cancelled` or `paused` (budget, provider or review pause). A run that ends
requeued (interrupted) or loses its lease is not a finish and is not counted. Job duration boundaries (seconds):
`1, 5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600`. The queue depth gauge (`state` is `queued` or `running`) is
reported by the worker only, from one small SQLite query per export interval; the API reports no queue depth.

Model calls are counted where every call already passes (`record_llm_call` in `f2w_agent/trace.py`, fed by
the CBORG and Ollama chat clients, the Q&A loop and the planning model). `provider` is `cborg` or `ollama`
(`other` if a call site cannot say); `outcome` is `ok`, `ip_not_authorized` (CBORG's HTTP 403 network refusal, as
classified by `cborg_errors.py`), `auth` (other 401/403), `rate_limited` (429), `timeout` or `error`. LLM duration
boundaries (seconds): `0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 180, 300`. There is no model-name label.

Harvest (in the worker): `source` is `arxiv` or `openalex` (one search each per run) or `pdf` (one per download
attempt); `outcome` is `ok`, `failed`, `blocked` (network policy) or `refused` (too large, not a PDF). Tiled
(the graph client's GraphQL requests, one per request, so a paged scan counts every page): `operation` is `read`
or `write`. Library (`LibraryService`): `operation` is `refresh`, `upload`, `create_category`, `verify_paper` or
`verify_all`. For Tiled and library, `outcome` is `ok`, `invalid`, `denied`, `unavailable` (from the contract
error code) or `error`; no error text is read.

**Label safety.** Every label value comes from a closed set (or a route template the application declared);
anything else becomes `other`. DOIs, URLs, query strings, uuids, file names, node ids, paper titles, user ids
and prompts can never be a label value. Model names are not a label.

### Collector requirements

The collector (the hub's, or one you run) must provide:

- an `otlp` receiver with the `http` protocol on port 4318;
- a metrics pipeline `otlp` receiver to `prometheus` exporter, the exporter listening on an internal port;
- on the Prometheus exporter, `resource_constant_labels` with `included: ["f2w.process", "f2w.profile"]` (it takes
  precedence), so those resource attributes become the labels `f2w_process` and `f2w_profile`; safe, because the
  metrics resource carries no per-restart ids. `resource_to_telemetry_conversion` does the same for every
  attribute but is deprecated in collector 0.165.0; use it only as an alternative. `target_info` carries the
  resource attributes by default;
- `metric_expiration` of about `5m` on the Prometheus exporter, so series of a retired pod disappear;
- one Prometheus scrape job against the collector's exporter port;
- NetworkPolicies allowing Prometheus to the collector on the exporter port, and the FAIR2WISE pods to the
  collector on 4318;
- `NO_PROXY` in the FAIR2WISE Deployment must cover the collector Service name (otherwise the exporter would
  try the egress proxy).

**Single replica assumed.** FAIR2WISE runs as exactly one replica (SQLite stores, `Recreate` rollout), so the
metrics resource deliberately has no per-pod attribute: one API process and one worker process produce one series
each, and a restart continues it. If the Deployment were ever scaled beyond one replica, cumulative series from
the replicas would collide in the collector, and a per-pod attribute would have to be added (at the cost of series
churn on every restart).

The collector target is `opentelemetry-collector-contrib` 0.165.0; the Prometheus names above are confirmed
against it on the pilot, not before.
