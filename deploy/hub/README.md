# FAIR2WISE in the ALS Computing Hub (for Dylan)

This folder is a drop-in for the hub's developer compose: one service, `f2w`, that runs the hardened
FAIR2WISE `cluster` image behind the hub's Traefik, and sends OpenTelemetry traces to the hub's
`otel-collector` (and from there to Jaeger). Nothing in this repository starts it; you paste or include
the file when you want it. **Not yet run against the hub's own collector (contrib 0.120.0), Traefik or Compose
files**: the F2W side was tested against a stand-alone `jaegertracing/jaeger:2.3.0`, and the compose file
was checked with `docker compose config` against a stub of your networks. Expect to adjust names.

## What you add to the hub compose

`compose.f2w.yaml` has everything:

| Piece | What it is |
|---|---|
| service `f2w` | image `f2w:cluster`, compose profile `f2w` (starts only when asked) |
| volumes `f2w-state`, `f2w-tiled` | persistent state and the bundled Tiled's data (named volumes) |
| networks `observability`, `web` | both already exist in the hub compose; redeclared identically (`web` has `name: web`) |
| Traefik labels | `Host(\`f2w.localhost\`)` on entrypoint `web`, port 8080, plus a redirect from `/` to `/fair2wise/` |

Either copy those three blocks into `docker-compose.yml`, or keep the file beside it:

```sh
podman-compose -f docker-compose.yml -f compose.f2w.yaml --profile f2w --profile observe up -d
```

(Docker Compose v2.20+ can `include:` it instead. `podman-compose` may not support `include:`; use `-f`.)

### Build the image

The image is built from the FAIR2WISE repository (the Mac Studio is arm64):

```sh
docker build --platform linux/arm64 --target cluster \
  --build-arg GIT_COMMIT="$(git rev-parse HEAD)" -t f2w:cluster .
```

The compose file also carries a commented `build:` block (relative context) if you prefer compose to build it.
`GIT_COMMIT` becomes the `f2w.code_commit` resource attribute on every trace.

### Start and open it

```sh
export CBORG_API_KEY=...                      # host environment; the file never contains a key
F2W_OTEL_ENABLED=true OTEL_ENABLED=true \
  podman-compose --profile f2w --profile observe up -d
```

* F2W: <http://f2w.localhost:8088/> (Traefik redirects to `/fair2wise/`; the UI and API live under that prefix,
  health is `/fair2wise/api/health`, probes `/fair2wise/api/livez` and `/readyz`).
* Jaeger: <http://127.0.0.1:16686>, search service **`f2w`**.

`*.localhost` resolves to loopback in current browsers and in `curl`; otherwise add `127.0.0.1 f2w.localhost`
to `/etc/hosts`.

**F2W has no sign-in in this image.** Every caller is "the operator" and can read raw chat traces and publish
to the bundled Tiled. Keep it reachable only through Traefik on `127.0.0.1` (the file publishes no port, joins
only `web` and `observability`, and has no public router rule). Do not widen that.

## Environment variables

| Variable | Set by the file | Meaning |
|---|---|---|
| `F2W_OTEL_ENABLED` | `${F2W_OTEL_ENABLED:-false}` | tracing switch (default off; nothing is exported and nothing changes). `OTEL_ENABLED` is honoured as an alias **inside the container only**, and the file does not forward it: set `F2W_OTEL_ENABLED` |
| `OTEL_SERVICE_NAME` | `f2w` | the service name shown in Jaeger |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `${F2W_OTEL_ENDPOINT:-http://otel-collector:4318}` | OTLP/HTTP base URL; F2W posts to `<base>/v1/traces` |
| `CBORG_API_KEY` | from the host | model key; never written to a file |
| `F2W_GATEWAY` | `off` | the API process serves the UI itself, no nginx |
| `F2W_OTEL_TRACE_FUNCTIONS` (commented) | unset | extra functions to trace by name; **leave unset** for the built-in list (an empty value turns function tracing off) |
| `F2W_OTEL_TRACE_MODULES`, `F2W_OTEL_MIN_SPAN_MS`, `OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG` (commented) | unset | prefix tracing, noise floor and sampling (see `mkdocs/docs/observability.md`) |

The container is hardened as the hub's other services should be: `read_only: true`, `cap_drop: [ALL]`,
`no-new-privileges`, `user: 10001:10001`; tmpfs on `/tmp` (up to 4 GiB; Docling renders PDF pages there),
`/run/fair2wise` and `/var/lib/fair2wise/cache`. No other path is written (the image documents every one of them).
A new named volume takes the ownership of the image's directory (uid 10001); if you reuse an old volume owned by
root, `chown 10001:10001` it once.

## How traces flow

```
f2w container ──OTLP/HTTP :4318──▶ otel-collector ──OTLP──▶ jaeger  (UI 127.0.0.1:16686)
 (api + worker                          │
  processes, one                        └─ spanmetrics ─▶ prometheus ─▶ grafana   (optional, below)
  service name f2w)
```

F2W never talks to Jaeger directly; it only needs `otel-collector:4318` on the `observability` network. Spans
are queued in memory and exported in batches by a background thread: if the collector is down, F2W logs one
warning, drops the spans, and keeps serving (no request waits for tracing).

One request is one trace: the `POST /chat` request span, the F2W functions it calls (retrieval, judge, leeway,
general answer), every model call and HTTP call as child spans, and, for jobs (extraction, publication), the
worker's `job.run` and `job.stage` spans in the same trace (the API stores the trace context with the job).
The JSON chat trace file (`F2W_TRACE_ENABLED`, on in this image) records the same trace id as `otel_trace_id`, and
every traced response carries it in the `X-F2W-Trace-Id` header.

## What to look for in Jaeger

Service **`f2w`** (process tags: `f2w.profile=cluster`, `f2w.process=api|worker`, `f2w.code_commit`). Example operations:

| Operation | What it shows |
|---|---|
| `POST /chat` | the whole chat turn (server span with route, status code, duration) |
| `f2w_agent.api.AgentPipelineService.ask` | the turn handler, with the stages below nested under it |
| `f2w_agent.retrieval_agent.RetrievalAgent.query` | KG retrieval plus the sufficiency judge |
| `llm.call KG-RAG-judge`, `llm.call KG-RAG-leeway`, `llm.call general-answer` | one span per model call: `gen_ai.request.model`, latency, prompt/response sizes in characters, status |
| `HTTP POST` | outgoing calls (`server.address`, `http.response.status_code`; never a path or query) |
| `f2w_agent.corpus_union.ChatCorpusUnion.refresh` | loading knowledge graphs for chat |
| `job.run`, `job.stage` (`f2w.stage`), `jobs.publication.Publication._import` ... | worker side of extraction and publication jobs |

Errors are red (status `ERROR`, `error.type` = exception class). Prompts, retrieved passages, document text, keys
and e-mail addresses are never recorded (see `docs/adr/content-disclosure.md`, enforced by a filter and tests).

## `config/otel/collector.yaml`: nothing is required

The collector already accepts OTLP/HTTP on 4318 and forwards traces to Jaeger, so F2W traces arrive with no
change. Optional: if you want F2W in Prometheus and Grafana by service name and your collector does not yet
derive span metrics, add the `spanmetrics` connector (it ships in contrib 0.120.0) and feed it from the traces
pipeline. A sketch to adapt (names of your exporters will differ; not run here):

```yaml
connectors:
  spanmetrics:
    histogram:
      explicit:
        buckets: [10ms, 50ms, 100ms, 500ms, 1s, 5s, 30s]
    dimensions:
      - name: http.route
      - name: f2w.stage
service:
  pipelines:
    traces:
      receivers: [otlp]
      exporters: [otlp/jaeger, spanmetrics]      # add spanmetrics next to your Jaeger exporter
    metrics/spanmetrics:
      receivers: [spanmetrics]
      exporters: [prometheus]                    # whatever your Prometheus exporter is called
```

If your pipeline already has a `spanmetrics` connector, F2W shows up automatically with `service_name="f2w"`.

## Troubleshooting

* No `f2w` service in Jaeger: check `podman logs f2w | grep -i tracing` (it logs `tracing on: service=f2w endpoint=...`
  at start). If it says `tracing requested but not started`, the exporter package is missing from the image.
* `trace export to ... failed` in the F2W log (once): the endpoint is wrong or the collector is down; check
  `OTEL_EXPORTER_OTLP_ENDPOINT` and that both containers are on the `observability` network.
* The F2W page loads but static files 404 at `/`: use `/fair2wise/` (the redirect label handles the bare host).
* HTTP proxies on the host: the F2W container adds a non-public collector name (`otel-collector`) to `NO_PROXY`
  by itself when tracing is on.
