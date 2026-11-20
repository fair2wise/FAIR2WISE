# OpenTelemetry to Jaeger: end-to-end evidence

Date: 2026-11-20. Branch `forge_merge-otel-jaeger` (from `forge_merge` 1532ca3). Host: the owner's Mac (colima, Docker 29.3.0).
Test: `tests/tracing/test_jaeger_live.py` (skipped unless `F2W_JAEGER_TEST_OTLP` and `F2W_JAEGER_TEST_UI` name a throwaway
Jaeger; nothing in the suite starts a container). No secret, prompt text or document text is involved; the model call
is a fake one (label `KG-RAG-judge`, 25 ms, a 2-character prompt and response).

## What was run

1. `docker run -d --name f2w-otel-jaeger-test -p 127.0.0.1:16687:16686 -p 127.0.0.1:4319:4318 jaegertracing/jaeger:2.3.0`
   (the version of the hub's compose; Jaeger v2.3.0 serves the v1 query API at `/api/traces`).
2. The test interpreter (app dependencies + `opentelemetry-exporter-otlp-proto-http` 1.45.0, a throwaway venv outside
   the repo) set `F2W_OTEL_ENABLED=1`, `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4319` (the **base** URL, as the hub
   compose sets it; the exporter posts to `/v1/traces`), `OTEL_SERVICE_NAME=f2w`, left `F2W_OTEL_TRACE_FUNCTIONS` unset
   (the built-in default list), called `tracing.init(process="api")` and produced one trace: a fake request span
   `POST /chat` (server kind), a model call recorded through `trace.record_llm_call` (25 ms), the default-list function
   `retrieval_agent._off_domain`, and the default-list function `cborg_http.call_with_deadline` raising `ZeroDivisionError`.
   `flush()` forced the export.
3. The test polled Jaeger's query API for that trace id and asserted the result. The same test then ran against
   Jaeger **2.22.0**, started from the digest pinned in `deploy/k3s/jaeger/deployment.yaml` with the ConfigMap's
   `config.yaml` under the container hardening the Deployment declares (`--read-only --cap-drop ALL --user 10001:10001
   --security-opt no-new-privileges --tmpfs /tmp`). That release serves only `/api/v3`, so the test reads whichever
   API exists; this also proves the ConfigMap's configuration starts (`/status` on 13133 answered 200, no warnings).
4. Both containers and the pulled images were removed afterwards.

## What arrived (Jaeger 2.3.0, query API v1)

Services listed: `f2w`, `jaeger`. One trace of 4 spans, all in service `f2w`:

| Span | Duration | Parent | Error |
|---|---|---|---|
| `POST /chat` | 56.87 ms | (root) | no |
| `llm.call KG-RAG-judge` | 25.00 ms | `POST /chat` | no |
| `f2w_agent.retrieval_agent._off_domain` | 0.17 ms | `POST /chat` | no |
| `cborg_http.call_with_deadline` | 0.27 ms | `POST /chat` | **yes** (`otel.status_code=ERROR`, `error=true`, `error.type=ZeroDivisionError`) |

Asserted by the test: parent/child nesting (all three children are CHILD_OF the request span, which is the root);
the model call's duration is 25 ms (20 to 40 ms accepted; it is recorded as a span that ends when the call is
recorded and starts its reported latency earlier); the request span lasts at least as long as the model call plus
the function; the failing span has error status and the exception class name only (no message); span attributes
`gen_ai.request.model=lbl/cborg-chat`, `f2w.job_id=<marker>`; process tags `f2w.profile=cluster`, `f2w.process=api`,
`f2w.code_commit` present, plus `service.name`, `service.version`, `host.name`, `process.pid`, runtime name and
version, `service.instance.id` and the SDK's `telemetry.sdk.*`. Checked by hand against the same container, the operations list of service `f2w` contained the four
span names above.

Jaeger 2.22.0 (query API v3, the manifests' image): the same four spans, durations 25.00 ms (model call), 30.73 ms
(request), 0.06 ms and 0.20 ms, same nesting and the same error. The final test passed 3 consecutive times on 2.3.0 and once on 2.22.0
after fixing one timing assertion of the test itself (the fake model call reported 25 ms without taking them;
the test now sleeps 30 ms first).

## What this does not prove

- The hub's collector (`otel/opentelemetry-collector-contrib:0.120.0`), Traefik, Prometheus and Grafana were **not run**;
  the spans went straight from F2W to Jaeger's own OTLP receiver. The hub path (`f2w -> otel-collector:4318 -> jaeger`)
  is the same OTLP/HTTP protocol, but it is untested here.
- The K3s manifests were **not applied** to a cluster (no `kubectl` or `kubeconform` here; a binary download for
  `kubeconform` was refused by the sandbox). They were parsed and checked structurally (Pod Security `restricted`
  fields, kustomization contents, ports, NetworkPolicy rules) and the ConfigMap's Jaeger configuration was run in a
  hardened container as described above. NetworkPolicy behaviour, the IngressRoute and Flux adoption are unverified.
- The F2W image was not built or run with tracing (no image builds in this task); the `cluster` profile, supervisor
  and entrypoint changes are covered by the static tests in `tests/images` and `tests/tracing`.
- A `requests` proxy bypass was tested through `requests.utils.get_environ_proxies` with the supervisor's merged
  `NO_PROXY`, not through a Squid instance.
