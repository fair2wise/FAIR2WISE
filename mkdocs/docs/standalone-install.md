# Standalone install from a pulled image (scoping plan)

Status: plan, nothing here is built. Requirement (owner): install FAIR2WISE on a fresh machine from a
pulled image, the shipped KGs working, every external connection set in **one env file**, no Keycloak, no
Traefik. The `:local` image already runs without either (`F2W_AUTHORIZATION_MODE=local_trusted`,
`docker/profiles/local.json`); what is missing is the packaging around it. Items marked *unverified* were
not checked against a running system. How the image is run today: [The :local image](local-image.md).

## 1. Prerequisites before any container starts

CBORG authorizes by **source address**. A refusal is HTTP 403 with body type `ip_not_authorized`
(`app/modules/cborg_errors.py`); the app treats it as terminal and shows one fixed message, never the address.
The 403 body may contain your public address, so the checks below print a state, not the body.

```sh
# CBORG_BASE_URL and CBORG_API_KEY come from your env file; the key goes in on stdin, never in argv or output.
set -a; . ./f2w.env; set +a
probe() {  # $1 = -4 or -6
  out=$(printf 'header = "Authorization: Bearer %s"\n' "$CBORG_API_KEY" |
        curl "$1" -sS -m 15 -K - -w '\n%{http_code}' "${CBORG_BASE_URL%/}/models" 2>&1) || { echo "$1 no connection"; return; }
  case $out in *ip_not_authorized*) echo "$1 REFUSED ip_not_authorized";; *) echo "$1 http ${out##*$'\n'}";; esac
}
probe -4; probe -6
```

| Result | Meaning |
|---|---|
| `http 200` | this address family is authorized; the app can list models |
| `REFUSED ip_not_authorized` | CBORG sees an address it has not authorized: use LBLnet or the VPN, or authorize the address in CBORG key management |
| `-6 no connection` | the host has no IPv6 route; if only IPv6 is authorized, get one (or use VPN) |
| `http 401` | address accepted, key wrong or missing (*unverified*: that CBORG checks the key after the address) |

The endpoint is `{CBORG_BASE_URL}/models` (`app/modules/model_catalog.py`; the code default for the base URL is
`DEFAULT_CBORG_BASE` there). If only `-6` succeeds, the containers must leave over IPv6 too.

**Giving containers that egress.** Docker's default bridge is IPv4-only, so a container otherwise reaches CBORG
over the host's IPv4 address.

| Option | How | Notes |
|---|---|---|
| IPv6 on the Docker network | daemon `"ipv6": true` plus `fixed-cidr-v6`, and compose network `enable_ipv6: true` | the container gets a ULA and NAT66s out; glibc then prefers IPv4, so also mount `docker/gai.conf` at `/etc/gai.conf:ro` (what `run_local_container.sh` does). Whether `CBORG_FORCE_IPV6=1` (pins the source address in `app/modules/cborg_http.py`) makes the mount unnecessary in a container: *unverified* |
| Host networking | `network_mode: host` | Linux only; Docker Desktop on macOS runs a VM, see `compose.hostnet.yaml`. The image is one container (supervisor runs API, worker, gateway), so there is no separate API/worker pair to switch |
| Host-side forwarder | the pilot's approach | see the block below |

!!! warning "TODO(owner): paste forwarder details"
    Pilot host-side IPv6 forwarder: what it is, how it is started, how the container is pointed at it
    (env var or `extra_hosts`), and its health check. Not written here on purpose: not known to the agent,
    and no host, address or port of the pilot belongs on this page.

**Proxies, as the code does them.**

- `NO_PROXY` / `HTTP(S)_PROXY` are plain process environment, honored by `httpx`/`requests`. The Ollama model
  list (`app/modules/ollama_catalog.py`) says it explicitly: an Ollama host on a LAN must be named in `NO_PROXY`
  wherever an egress proxy is set, and the app does not add it. The supervisor appends only a non-public OTLP
  collector host to `NO_PROXY`. Whether the CBORG client honors proxies when it pins an address (explicit
  transport) is *unverified*.
- `F2W_SAFE_FETCH_TRUST_PROXY=1` (also `true|yes|on`; default off) affects **only** third-party PDF fetches
  (`app/modules/literature/safe_fetch.py`). That code refuses any private peer address; with the flag, a request
  that really goes through the configured `HTTP(S)_PROXY` (and is not excluded by `NO_PROXY`) may connect to the
  proxy's own address. The target host must still resolve to public addresses only. It does not touch CBORG,
  Ollama or OpenAlex calls.

## 2. Which `.env.example` is authoritative

The **root `.env.example`** is. It was reconciled with the code (section 7, item 1) and is the file to copy for
the launchers and for an image run. `scripts/.env.example` is a smaller copy for the `scripts/` stack; it only
lost two names nothing reads (`KG_RAG_GRAPH`, `KG_RAG_SPLASH_PAGE_SIZE`).

How each loader treats it: `scripts/run_local_container.sh` reads **only** `CBORG_API_KEY` from the dotenv file
and passes `OPENALEX_API_KEY` only when exported in the shell. The image sets `F2W_DOTENV=off`; the supervisor
loads one file only if `F2W_DOTENV` names it. `OPENALEX_API_KEY` is *process-only* by owner decision
(`app/modules/dotenv_entry.py`): a `.env` file never supplies it, so it must arrive as real environment
(`docker run --env-file`, compose `env_file:`, or `export`). Profile `env` values fill only **blank** names
(`f2w_supervisor.py`), so a value in the file wins over the image profile.

**What the root file now does**

- `KG_RAG_GRAPH_SOURCE=json` (it was `splash`, which is not in the image). Splash stays available as a commented
  legacy block.
- Lists every setting `app/` reads that the old files missed: `OLLAMA_URL`, `F2W_EXTRACT_BACKEND`,
  `OPENALEX_API_KEY`, `F2W_LIBRARY_DIR`, `F2W_FOLDER_ROOTS`, `F2W_KG_RELEASE_DIR`, `F2W_SAFE_FETCH_TRUST_PROXY`,
  `NO_PROXY` and the proxy variables, `CBORG_MAX_CONCURRENCY`, `F2W_GRAPH`, and the opt-in, off-by-default
  `F2W_METRICS_ENABLED`, `F2W_OTEL_ENABLED`, `OTEL_EXPORTER_OTLP_ENDPOINT`.
- Marks each secret (`CBORG_API_KEY`, `OPENALEX_API_KEY`, `CORE_API_KEY`, `MP_API_KEY`, `GITHUB_TOKEN`), keeps
  them empty, and says `OPENALEX_API_KEY` is process-only.
- Puts every comment on its own line. An inline `# comment` after a value survives in `docker run --env-file`
  values; compose `env_file:` strips it.
- `CBORG_IP_FAMILY=ipv6` and `CBORG_FORCE_IPV6=1` are now commented out: they are right only on a host whose
  global IPv6 address CBORG authorized, and a pinned client ignores `HTTP(S)_PROXY` (explicit transport in
  `app/modules/cborg_http.py`). Uncomment them on such a host.
- Removed because nothing reads them: `KG_RAG_GRAPH` (the graph comes from `F2W_GRAPH`), `KG_RAG_SPLASH_PAGE_SIZE`,
  `PYSTOW_HOME`, `GLOBUS_PROJECT_ID`, `ACADEMY_GLOBUS_CLIENT_ID`, `ACADEMY_GLOBUS_CLIENT_SECRET`. Removed because
  the `:local` profile strips them: `TILED_URI`, `TILED_API_KEY` (the file also carried a development key
  value), `F2W_LIVE_TILED`. `GLOBUS_COMPUTE_ENDPOINT_ID` stays, commented (read by the Academy launcher).
- Kept, noted as launcher settings: `F2W_UI_PORT`, `F2W_AGENT_PORT`, `F2W_WORKERS` (the shell launchers under
  `scripts/` read them; `F2W_WORKERS` is also read by the extraction coordinator).

`tests/deployment/test_env_example.py` keeps it that way: no inline comments, JSON graph source, no value that
looks like a key, and every listed name referenced in `app/` or `scripts/`. It exempts `F2W_METRICS_ENABLED`
until the metrics code is merged into this branch.

Still true and not in the file: the `:local` profile forbids `F2W_TILED_URL` and the writer secret/key names
(exit 78 if set). The writer key is generated by the managed Tiled into the tiled volume (`secrets/writer.key`);
`F2W_TILED_WRITER_SECRET_FILE` is an ALS-image setting. `--kg-mode` is not an env var: it is argv of the `api`
service in the profile (`splash`, the editable alias, on `:local`; `json` on ALS).

The proposed standalone file (`f2w.env`) differs from the root file only in adding host-side names for a compose
file that does not exist yet (`F2W_IMAGE`, `F2W_HOST_PORT`, `F2W_LIBRARY_HOST_DIR`, `F2W_PAPERS_HOST_DIR`); add
them with section 3.

## 3. A compose entry point

`docker/profiles/` already has `local.json`: no OIDC, loopback gateway on `8080`, managed Tiled, state and tiled
volumes required, library optional. What it lacks is everything *around* the container. There is **one**
service (the supervisor runs tiled, API, worker and nginx), plus optionally Ollama. The sketch mirrors
`run_local_container.sh`; not run.

```yaml
services:
  f2w:
    image: ${F2W_IMAGE}                  # pulled tag, see section 6
    env_file: f2w.env                    # compose strips inline comments; secrets stay out of the file's argv
    ports: ["127.0.0.1:${F2W_HOST_PORT:-8080}:8080"]
    stop_grace_period: 120s              # supervisor stop order needs it (profiles README)
    environment: { F2W_FOLDER_ROOTS: /data/papers }
    volumes:
      - f2w-state:/var/lib/fair2wise/state
      - f2w-tiled:/var/lib/fair2wise/tiled
      - ${F2W_LIBRARY_HOST_DIR}:/var/lib/fair2wise/library     # must be writable by uid 10001
      - ${F2W_PAPERS_HOST_DIR}:/data/papers:ro
      - ./docker/gai.conf:/etc/gai.conf:ro                     # only with the IPv6 network option
    networks: [f2w]
  # ollama: optional, behind a compose profile; OLLAMA_URL then names it and NO_PROXY must list it
volumes: { f2w-state: {}, f2w-tiled: {} }
networks: { f2w: { enable_ipv6: true } }
```

Not yet covered anywhere: a compose file in a form someone can use without the repo (the repo's `compose.yaml`
targets the legacy `agent` image), `${VAR}` substitution reading a different file than `env_file` (use
`docker compose --env-file f2w.env`), and the bind-mount ownership rule (preflight refuses a library or state dir
not owned by uid 10001).

## 4. KG seeding

The image carries 59 flat `storage/kg/matkg_*.json` files, 273 MB raw (nested `v*/graph.json` copies are excluded
by `.dockerignore`), plus `storage/terminology` at 306 MB, the larger share. The chat/agent reads the files named by
`F2W_GRAPH` straight from `/app/storage/kg`, so no seeding step is needed for it (end-to-end on a fresh pull:
*unverified*). Seeding exists to adopt a release and publish it into the volume's Tiled.

**Trimming.** `storage_graph_files` (`app/modules/operations/knowledge.py`) already lists only the highest `vN`
per id, so older versions are dead weight for Operations: `matkg_bl1101_v1..v9` and `matkg_rsoxs_v1..v4` leave
about 70 MB of the 273 MB (compressed saving *unverified*). Two caveats: `local.json` pins `matkg_rsoxs_v1` and
`matkg_bl1101_v2` in `F2W_GRAPH`, so trimming needs the profile repointed to the latest (an owner data choice, and a
`docker/` change); and the 40-byte empty `*_20260603_*` stubs can go regardless.

**`f2w-init`** (proposed, runs inside the container before the supervisor starts services, first start only):
create `.../state/session/storage_kg` and `.../state/kg_releases` (the app never creates the latter;
`run_local_container.sh` does it by `docker exec`), adopt the chosen flat files with `f2w-kg-adopt adopt` as
step 1 of `scripts/demo/seed.sh` does, then write a marker file in the state volume so later starts skip it.
It must **not** stage or approve a publication: publishing writes about 117,000 records over roughly 65 minutes and
approval is the owner's act (`seed.sh --i-approve`). Gap: `f2w-kg-adopt` needs `--code-commit`; `seed.sh` takes it
from the image label via `docker inspect`, which is not visible inside the container, so the image must expose its
commit another way.

## 5. `f2w-doctor` spec

`docker run --rm --env-file f2w.env --entrypoint f2w-doctor IMAGE` (the image entrypoint is the supervisor; also usable via `docker exec`). Prints states only.

| Check | States |
|---|---|
| CBORG | `ok`, `ip_not_authorized` (distinct, from `is_ip_not_authorized`), `key_missing`, `rejected`, `unreachable` |
| OpenAlex, arXiv | `ok`, `unreachable`, `proxy_blocked` |
| Ollama | `unset`, `ok`, `empty`, `unreachable`, `error` (the states of `ollama_catalog.py`) |
| Library dir, state dir | `writable`, `read_only`, `missing`, `wrong_owner` |

Exit codes: 0 all required ok; 1 an optional check failed (Ollama, OpenAlex); 2 a required one failed (CBORG,
state dir); 78 the env file is refused by the profile (as the supervisor). Never prints a key, host, address, URL or
response body; reuses `cborg_errors`, `model_catalog` and `ollama_catalog`, which already return fixed messages.

```text
f2w-doctor
  cborg      ip_not_authorized   connect via LBLnet/VPN or authorize the address
  openalex   ok
  arxiv      ok
  ollama     unset
  library    writable
  state      writable
exit 2
```

## 6. Publishing a pullable tag

`publish-image.yml` runs on push to `main`, on `vX.Y.Z` tags and on dispatch (promote/rollback of an already
published digest). Its `gate` job fails unless the repository variable `O06_APPROVED` is `"true"`, the ref is
protected, and a branch push is the **default branch**; writer jobs sit in the `ghcr-publish` and `ghcr-als-prod`
environments, limited to `main` and `v*`. A push to `forge_merge` builds no images (`ci.md`); a
`build-app.yml` dispatch on `forge_merge` makes candidates as workflow artifacts only, nothing pullable. So a
pullable tag from this work exists only after it lands on `main` (or the owner cuts a release tag). Nothing is
proposed that bypasses the gate.

Needs owner approval: setting `O06_APPROVED` and creating the two environments (current state *unverified*);
merging `forge_merge` to `main` or cutting a tag; making the package public, which GitHub only allows after the
first publication (D2.4 asked for public; the images carry the tracked KG JSON and terminology). Until the package is
public, a fresh machine needs `docker login ghcr.io` to pull. Note: the pointer in the task, "D2.8 about ghcr
visibility in `owner_decisions_2026-11-03.md`", does not match the files: that file has no D2.x entries; visibility is
D2.4 and D2.8 is platforms (amd64 only, since widened to amd64+arm64 in the workflows), both in
`docs/migration/reviews/H6/o06_tag_rules_proposal.md`.

## 7. Work items

Agent = app code or docs in the repo; Deploy = `docker/`, `deploy/`, workflows (owner-gated paths).

| # | Item | Side | Effort | Depends on |
|---|---|---|---|---|
| 1 | Reconcile `.env.example` (section 2), fix `splash` default | Agent, **done** | S, 1-2 h | none |
| 2 | `f2w-doctor` command and tests | Agent | M, 6-10 h | 1 |
| 3 | `f2w-init` (dirs, adopt, marker) and image commit exposure | Agent + Deploy (entrypoint, Dockerfile) | M, 6-8 h | owner call on commit exposure |
| 4 | Standalone compose file and install page | Deploy | S-M, 3-5 h | 1, 3 |
| 5 | KG trim in image, profile `F2W_GRAPH` repoint | Deploy | S, 2-3 h | owner data choice |
| 6 | Verify IPv6 egress options and forwarder doc (section 1) | Deploy + owner | M, 4-8 h | owner forwarder details |
| 7 | Publishable tag: variable, environments, merge or tag, visibility | Owner | S, 1-2 h plus a CI run | 2-5 merged |
| 8 | Fresh-machine rehearsal from the pulled tag | Owner + agent | M, 3-4 h | 7 |

Commit order: 1, 2, 3, then 4 and 5 (Deploy, owner-gated), 6 as docs once the owner supplies the forwarder, then 7
and 8. Items 1 to 3 touch no gated path except 3's entrypoint part.
