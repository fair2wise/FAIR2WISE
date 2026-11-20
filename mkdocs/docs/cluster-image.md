# The `:cluster` image (Kubernetes, restricted Pod Security)

`:cluster` is the `:local` trust model and bundled Tiled 0.2.18, packaged for a pod that runs under
Pod Security `restricted`: a fixed numeric non-root user, a read-only root filesystem, all
capabilities dropped, and an ingress (Traefik, oauth2-proxy) that terminates TLS and sign-in in
front of it. Build, supervisor and generic `docker run` details are in `docker/profiles/README.md`;
this page covers what is different.

```sh
docker build --platform linux/arm64 --target cluster -t fair2wise:cluster .
```

Trust model: `F2W_DEPLOYMENT_PROFILE` stays `local` (`local_trusted`, every caller is the operator).
The image therefore has **no sign-in of its own**. Anyone who can reach the pod port can read raw
traces and publish to the bundled Tiled. Put a NetworkPolicy in front of it that admits only the
ingress or oauth2-proxy pods. The API logs the AUD-16 network-exposure warning at start when it binds
beyond loopback (`F2W_GATEWAY=off`); that is expected here.

## Gateway: `F2W_GATEWAY=off|on`

| Value | Processes | Listener |
|---|---|---|
| `off` (default in this image) | tiled, api, worker | the API process itself on `0.0.0.0:${F2W_GATEWAY_PORT}` (8080) |
| `on` | tiled, api, worker, nginx | nginx on `${F2W_GATEWAY_PORT}`, API on `127.0.0.1:8090` (as `:local`) |

Any other value exits 78 before a process starts. Only the `cluster` profile reads the setting.

**Who serves the UI when the gateway is off.** The API process does
(`app/modules/deployment/front.py`, `FrontApp`): it serves the built UI and the MkDocs site from
`/usr/share/nginx/html/fair2wise`, removes `/fair2wise/api` once, forwards `/fair2wise/model-api`
as `/model-api`, and answers `/healthz` and `/fair2wise/healthz`, with the same table as
`docker/nginx.conf` (tests: `tests/deployment/test_front.py`). It is not a general proxy: the one
upstream is the application in the same process.

Costs and differences, stated plainly:

- **No request buffering, no `client_max_body_size`, no HTTP/2.** uvicorn (h11) faces the ingress
  directly. The API's own 128 MiB body cap still applies. Slow-client protection is the ingress's job.
- **Forwarded headers.** uvicorn believes `X-Forwarded-For/Proto` only from `FORWARDED_ALLOW_IPS`
  (default `127.0.0.1`, which is not the ingress). Set `FORWARDED_ALLOW_IPS` to the ingress pod
  addresses (or `*` only when a NetworkPolicy admits nothing but the ingress). The front blanks
  `X-Forwarded-Host` and `X-Forwarded-Prefix` and passes `X-Forwarded-Proto` only when it is literally
  `http` or `https`, as nginx did, because the origin gate reads that header. The ingress **must
  overwrite `X-Forwarded-Proto`** with the scheme the browser used (README, "Edge proxy requirement").
- **Static files are served by Python** (Starlette `FileResponse`, ETag and range support, symlinks
  and `..` refused). That is adequate for a pilot; it is not a CDN.
- **When to keep nginx (`F2W_GATEWAY=on`).** If you want nginx's buffering, its logs, or an
  identical request path to `:local`. It runs unprivileged on the high port 8080 (no `setcap`; the
  default `F2W_GATEWAY_PORT=8080`, any port above 1024 works), with its pid, temp files and rendered
  configuration under `/run/fair2wise/nginx`, so it needs only the `/run/fair2wise` mount.

## Probes

Both endpoints are answered before routing, authentication and CORS, need no credentials, and show
only fixed check names (no paths, versions, users or secrets) (`app/api/probes.py`).

| Endpoint | Meaning | Status |
|---|---|---|
| `GET /livez` | the API process answers; no dependency is called | 200 |
| `GET /readyz` | `state` (state directory writable), `worker` (supervisor status says running and the pid exists), `tiled` (bundled Tiled answers `/healthz` on loopback) | 200, or 503 listing the failing checks |

`F2W_READYZ_CHECKS` selects the checks (default `state,worker,tiled`); use `state,worker` to keep a
pod ready while a failed Tiled leaves the JSON workflows serving. The kubelet reaches the pod IP, so
probe the **gateway port**: the API's loopback port is not reachable from outside the container.
The same paths work with the gateway on or off:

```yaml
        startupProbe:
          httpGet: {path: /fair2wise/api/livez, port: 8080}
          periodSeconds: 5
          failureThreshold: 60        # up to 5 minutes (model and Tiled start)
        livenessProbe:
          httpGet: {path: /fair2wise/api/livez, port: 8080}
          periodSeconds: 10
          timeoutSeconds: 3
          failureThreshold: 3
        readinessProbe:
          httpGet: {path: /fair2wise/api/readyz, port: 8080}
          periodSeconds: 10
          timeoutSeconds: 3
          failureThreshold: 2
```

Inside the container (for `kubectl exec`) the API port serves `GET /livez` and `GET /readyz` on
`127.0.0.1:8090` with `F2W_GATEWAY=on`; with `off` use port 8080 and the `/fair2wise/api` prefix.
`/fair2wise/healthz` stays a cheap static answer of the front or of nginx.

## Proxies

The supervisor gives the whole environment to every child (api, worker, tiled), so `HTTP_PROXY`,
`HTTPS_PROXY` and `NO_PROXY` (either case) reach all of them; the image strips only the legacy Tiled
names of `:local`. The cluster profile adds `127.0.0.1,localhost,::1` to `NO_PROXY` and `no_proxy`
(your entries stay first, nothing is removed), so the gateway upstream, the API port and the bundled
Tiled are never proxied. Supervisor health checks never use a proxy either. Add every internal or
direct host yourself, for example Ollama and the cluster domains:

```yaml
        env:
          - {name: HTTPS_PROXY, valueFrom: {secretKeyRef: {name: f2w-proxy, key: url}}}
          - {name: HTTP_PROXY,  valueFrom: {secretKeyRef: {name: f2w-proxy, key: url}}}
          - {name: NO_PROXY, value: "ollama.llm.svc,.svc,.cluster.local,10.0.0.0/8"}
          - {name: F2W_SAFE_FETCH_TRUST_PROXY, value: "1"}
```

`F2W_SAFE_FETCH_TRUST_PROXY=1` (default `0`) lets the guarded PDF fetcher
(`app/modules/literature/safe_fetch.py`) work through that proxy. Without it every fetch is refused
(`blocked:private_address`) because the connected peer is the proxy's private cluster address. With
it, only the proxy's own address is excused for a request that really goes through the proxy; the
target host is still resolved by the application and refused unless every address is public
(private, loopback, link-local and metadata addresses, IP literals and redirect targets included),
and a `NO_PROXY` host keeps the strict peer check. Costs: the pod's DNS must resolve public
names (the application resolves them itself), and the proxy resolves again, so DNS rebinding between
the two lookups can only be stopped by the proxy's own allowlist. Proxy credentials are never logged
or echoed. The other `requests`/`httpx` callers honour the proxy variables without any guard (see the
list in `docs/cluster-profile.md`).

## Every path the application writes

With a read-only root filesystem, each path below must be a mount. `/var/lib/fair2wise/state` and
`/var/lib/fair2wise/tiled` are checked by preflight as mounts; the other three only have to exist and
be writable (`"ephemeral": true` in `docker/profiles/cluster.json`). Python does not write bytecode
(`PYTHONDONTWRITEBYTECODE=1`); the model weights (`HF_HOME=/opt/fair2wise/hf`, Docling and RapidOCR
under `/opt/fair2wise/docling`) are baked read-only, and `HF_HUB_OFFLINE=1` keeps them that way.

| Path | Purpose | Size | Persist | Written by | Relocate with |
|---|---|---|---|---|---|
| `/var/lib/fair2wise/state` | sessions, job/curation/audit SQLite stores, traces, library index, KG bundles | 1 GiB to tens of GiB | **persist** (PVC) | api, worker | `F2W_WORKDIR`, `F2W_JOB_DB`, `F2W_TRACE_DIR`, `HYBRID_RAG_LITERATURE_INDEX` |
| `/var/lib/fair2wise/tiled` | Tiled catalog, writable storage, log, `secrets/writer.key` | 1 GiB and up | **persist** (PVC) | tiled | `F2W_TILED_DATA_DIR` (locked here) |
| `/var/lib/fair2wise/cache` | HOME and caches | under 200 MiB | ephemeral (emptyDir) | api, worker | `HOME`, `XDG_CACHE_HOME`, `MPLCONFIGDIR`, `PYSTOW_HOME` |
| `/var/lib/fair2wise/cache/home` | HOME of the process | small | ephemeral | api, worker | `HOME` |
| `/var/lib/fair2wise/cache/xdg` | fontconfig and XDG caches | small | ephemeral | api, worker | `XDG_CACHE_HOME` |
| `/var/lib/fair2wise/cache/matplotlib` | matplotlib config and font cache | small | ephemeral | api, worker | `MPLCONFIGDIR` |
| `/var/lib/fair2wise/cache/pystow` | pystow data (ontology downloads) | small | ephemeral | api, worker | `PYSTOW_HOME` |
| `/run/fair2wise` | supervisor status, nginx config, pid, temp | under 64 MiB | ephemeral (memory emptyDir) | supervisor, preflight, nginx | `F2W_SUPERVISOR_STATUS` |
| `/run/fair2wise/supervisor.json` | supervisor status, read by `/readyz` | tiny | ephemeral | supervisor | `F2W_SUPERVISOR_STATUS` |
| `/run/fair2wise/nginx` | rendered gateway config, nginx pid and temp files (`F2W_GATEWAY=on` only) | small | ephemeral | preflight, nginx | not relocatable |
| `/tmp` | tempfile scratch: uploads, PDF rendering, Docling and RapidOCR work files | up to a few GiB | ephemeral | api, worker, tiled | `TMPDIR` |
| `/var/lib/fair2wise/library` | reference papers for the Library tab (optional) | as large as the library | **persist** (PVC) | api, worker | `F2W_LIBRARY_DIR` |

Ownership: a fresh emptyDir is writable by every uid; a PVC is owned by root unless the pod sets
`fsGroup: 10001`. Preflight never chowns. The image makes the state mount point `0700`; a PVC takes the mode its storage class gives it.
Size the `/tmp` emptyDir (`sizeLimit`) for the largest PDF you parse; Docling renders pages there.

## Fixed numeric user

The image runs as `USER 10001:10001`, a bare number: nothing at start looks up a user name
(`su`, `gosu`, `getpwuid` and `getpass` are absent from the entrypoint), and `HOME` is set explicitly,
so a pod that runs the same uid with no passwd entry, or an arbitrary uid with `fsGroup`, starts the same
way. `runtime-base` already created the numeric user; `cluster` adds no user.

## Example Deployment fragment (documentation only)

Nothing in this repository applies it.

```yaml
spec:
  securityContext:
    runAsNonRoot: true
    runAsUser: 10001
    runAsGroup: 10001
    fsGroup: 10001
    seccompProfile: {type: RuntimeDefault}
  terminationGracePeriodSeconds: 120
  containers:
    - name: fair2wise
      image: fair2wise@sha256:REPLACE_WITH_THE_IMPORTED_DIGEST
      ports: [{containerPort: 8080}]
      env:
        - {name: F2W_GATEWAY, value: "off"}
        - {name: FORWARDED_ALLOW_IPS, value: "10.42.0.0/16"}   # the ingress / oauth2-proxy pods
        - name: CBORG_API_KEY
          valueFrom: {secretKeyRef: {name: f2w-llm, key: key}}
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        runAsNonRoot: true
        runAsUser: 10001
        capabilities:
          drop: ["ALL"]
        seccompProfile: {type: RuntimeDefault}
      volumeMounts:
        - {name: state, mountPath: /var/lib/fair2wise/state}
        - {name: tiled, mountPath: /var/lib/fair2wise/tiled}
        - {name: cache, mountPath: /var/lib/fair2wise/cache}
        - {name: run, mountPath: /run/fair2wise}
        - {name: tmp, mountPath: /tmp}
  volumes:
    - {name: state, persistentVolumeClaim: {claimName: f2w-state}}
    - {name: tiled, persistentVolumeClaim: {claimName: f2w-tiled}}
    - {name: cache, emptyDir: {sizeLimit: 512Mi}}
    - {name: run, emptyDir: {medium: Memory, sizeLimit: 64Mi}}
    - {name: tmp, emptyDir: {sizeLimit: 8Gi}}
```

Stop: use `terminationGracePeriodSeconds: 120` (the supervisor stops the worker, API and Tiled in
order; running jobs return to `queued`).

## Tracing to Jaeger (optional)

OpenTelemetry tracing is off unless `F2W_OTEL_ENABLED=1`; nothing in the table of writable paths changes (spans are
exported from memory). Example manifests for the collector side are in `deploy/k3s/jaeger/` (namespace
`observability`, Jaeger v2 pinned by digest, a NetworkPolicy, a Traefik IngressRoute that must sit behind the
forward-auth middleware). The F2W side is an environment fragment on the Deployment above; the namespace that runs
F2W must carry the label the Jaeger NetworkPolicy admits (`kubectl label namespace <ns>
f2w.observability/otlp-client=true`):

```yaml
        env:
          - {name: F2W_OTEL_ENABLED, value: "1"}
          - {name: OTEL_EXPORTER_OTLP_ENDPOINT, value: "http://jaeger.observability.svc:4318"}
          - {name: OTEL_SERVICE_NAME, value: "f2w"}
          # Optional: OTEL_TRACES_SAMPLER=parentbased_traceidratio, OTEL_TRACES_SAMPLER_ARG=0.25,
          #           F2W_OTEL_TRACE_FUNCTIONS / F2W_OTEL_TRACE_MODULES / F2W_OTEL_MIN_SPAN_MS
          - {name: HTTPS_PROXY, valueFrom: {secretKeyRef: {name: f2w-proxy, key: url}}}   # unchanged: egress via Squid
          - {name: NO_PROXY, value: ".svc,.cluster.local,10.0.0.0/8"}                      # your own entries
```

`NO_PROXY` and the Squid allowlist: spans are posted with `requests`, which obeys `HTTPS_PROXY`/`NO_PROXY`. When
tracing is on and the endpoint host is not a public name (a `.svc`, `.local` or `.cluster.local` name, a
single-label name, a private or loopback address) the supervisor adds that exact host to `NO_PROXY` and `no_proxy`
next to the profile's own entries, so spans go straight to the Service and Squid never sees them. A public
collector host is left to the proxy: add its domain to the Squid allowlist. Ports: OTLP/HTTP is 4318 (the endpoint
above); gRPC (4317) is not used. Settings, span names and troubleshooting: `mkdocs/docs/observability.md`
(published at `/fair2wise/docs/observability/`).
