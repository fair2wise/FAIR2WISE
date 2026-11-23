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

Neither is, for the image. `scripts/run_local_container.sh` reads **only** `CBORG_API_KEY` from the dotenv file
and passes `OPENALEX_API_KEY` only when exported in the shell. The image sets `F2W_DOTENV=off`; the supervisor
loads one file only if `F2W_DOTENV` names it, and `OPENALEX_API_KEY` is *process-only* by owner decision
(`app/modules/dotenv_entry.py`), so it must arrive as real environment (`docker run --env-file`, compose
`env_file:`). Today the root `.env.example` is the superset and serves the legacy launchers; `scripts/.env.example`
is a smaller copy for the `scripts/` stack.

**Contradictions found**

- Root `KG_RAG_GRAPH_SOURCE=splash`, `scripts/.env.example` says `json`, README line 24 says JSON is the default
  everywhere. Profile `env` values only fill **blank** names (`f2w_supervisor.py`), so copying the root file
  into an image run would switch the graph source to a Splash that is not in the image.
- Both files point `KG_RAG_GRAPH` at `matkg_with_code.json`; the image profile uses `F2W_GRAPH` (rsoxs v1 and
  bl1101 v2).
- Root file sets `TILED_URI`, `TILED_API_KEY`, `F2W_LIVE_TILED`: the `:local` profile strips them (ignored).
  `F2W_TILED_URL` and the writer secret/key names are *forbidden* on `:local` and exit 78 if set.
- Root file lists `F2W_UI_PORT`, `F2W_AGENT_PORT`, `F2W_WORKERS` (read only by shell launchers) and Globus names
  (not read in `app/`).
- Inline `# comments` on a value line (`TILED_API_KEY=... # ...`) survive in `docker run --env-file` values;
  compose `env_file:` strips them. Proposed file puts comments on their own lines.

**Missing from both** (read in `app/`): `OLLAMA_URL`, `F2W_EXTRACT_BACKEND`, `OPENALEX_API_KEY`,
`F2W_LIBRARY_DIR`, `F2W_FOLDER_ROOTS`, `F2W_KG_RELEASE_DIR`, `F2W_SAFE_FETCH_TRUST_PROXY`, `NO_PROXY`,
`CBORG_MAX_CONCURRENCY`, `F2W_BACKEND`/`F2W_MODEL`/`F2W_GRAPH`, `F2W_CORS_ORIGINS`, the `F2W_OTEL_*` group.
The writer key is generated by the managed Tiled into the tiled volume (`secrets/writer.key`);
`F2W_TILED_WRITER_SECRET_FILE` is an ALS-image setting and has no use on `:local`. `--kg-mode` is not an env
var: it is argv of the `api` service in the profile (`splash`, the editable alias, on `:local`; `json` on ALS).

```sh
# f2w.env  (proposed .env.example for the standalone install). One file, KEY=VALUE, comments on their own lines.
# Never commit a filled copy. Secrets are empty placeholders.

# ---- CBORG (LLM gateway). Authorizes by source address: run the section-1 probe first.
CBORG_API_KEY=
CBORG_BASE_URL=
# CBORG_MAX_CONCURRENCY=
# IPv6 pinning, only after reading section 1 (unverified inside a container):
# CBORG_IP_FAMILY=ipv6
# CBORG_FORCE_IPV6=1

# ---- Literature sources
# Process-only: must reach the container as environment (env_file / --env-file), not via F2W_DOTENV.
OPENALEX_API_KEY=
OPENALEX_EMAIL=
CORE_API_KEY=
MP_API_KEY=
GITHUB_TOKEN=

# ---- Optional local models. No localhost default for extraction: set explicitly.
OLLAMA_URL=
# F2W_EXTRACT_BACKEND=ollama
# Hosts that must not go through an egress proxy (the Ollama host, internal names):
NO_PROXY=
# HTTP_PROXY=
# HTTPS_PROXY=
# Only when PDF downloads must pass through the allowlist proxy above:
# F2W_SAFE_FETCH_TRUST_PROXY=1

# ---- Storage (container paths; the image profile defaults are shown)
# F2W_LIBRARY_DIR=/var/lib/fair2wise/library
# F2W_FOLDER_ROOTS=/data/papers
# F2W_KG_RELEASE_DIR=/var/lib/fair2wise/state/kg_releases

# ---- Behaviour (profile defaults are shown; do not set KG_RAG_GRAPH_SOURCE=splash)
# F2W_BACKEND=cborg
# F2W_MODEL=lbl/cborg-chat
# KG_RAG_RETRIEVAL_BACKEND=lexical

# ---- Host side, read by compose only (not the app)
F2W_IMAGE=
F2W_HOST_PORT=8080
F2W_LIBRARY_HOST_DIR=
F2W_PAPERS_HOST_DIR=
```

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
| 1 | Reconcile `.env.example` (section 2), fix `splash` default | Agent | S, 1-2 h | none |
| 2 | `f2w-doctor` command and tests | Agent | M, 6-10 h | 1 |
| 3 | `f2w-init` (dirs, adopt, marker) and image commit exposure | Agent + Deploy (entrypoint, Dockerfile) | M, 6-8 h | owner call on commit exposure |
| 4 | Standalone compose file and install page | Deploy | S-M, 3-5 h | 1, 3 |
| 5 | KG trim in image, profile `F2W_GRAPH` repoint | Deploy | S, 2-3 h | owner data choice |
| 6 | Verify IPv6 egress options and forwarder doc (section 1) | Deploy + owner | M, 4-8 h | owner forwarder details |
| 7 | Publishable tag: variable, environments, merge or tag, visibility | Owner | S, 1-2 h plus a CI run | 2-5 merged |
| 8 | Fresh-machine rehearsal from the pulled tag | Owner + agent | M, 3-4 h | 7 |

Commit order: 1, 2, 3, then 4 and 5 (Deploy, owner-gated), 6 as docs once the owner supplies the forwarder, then 7
and 8. Items 1 to 3 touch no gated path except 3's entrypoint part.
