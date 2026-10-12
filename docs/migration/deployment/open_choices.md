# Open choices O06, O07, O08 (T24A)

**Status: all three are UNRESOLVED.** The options and recommendations below are inputs for the owner at H1 (O06) and H1/H7 (O07, O08). None of them is a decision. Nothing in the repository was changed to implement any of them.

---

## O06: Which events move each image tag

### Facts from the source

- The only existing image workflow publishes on `push` to `main` and on tags matching `v*` (`.github/workflows/publish-image.yml:3-6`).
- It builds only `target: agent` (`:43`).
- Tags come from `docker/metadata-action` defaults (`:32-36`), so it produces branch, tag and semver names. It never produces `:local` or `:als-*`.
- CI_CD_CONTRACT.md proposes this policy:
  - PRs build and test only.
  - Trusted `forge_merge` pushes build candidates.
  - An owner-approved, protected release promotes `:als-prod` to the digest that was tested.

### Options

| Option | `:local` moves on | `:als-dev` moves on | `:als-prod` moves on | Trade-off |
| --- | --- | --- | --- | --- |
| **A. Channel promotion** | Trusted push to `forge_merge` after all checks pass | Same as `:local` | A GitHub Release or protected `workflow_dispatch` in a GitHub Environment `als-prod` with required reviewers. It retags the already-tested digest without rebuilding (e.g. `docker buildx imagetools create`). | Fast dev loop, and prod is gated by review. Needs environment protection rules. |
| B. Release only | `v*` tag | `v*` tag | `v*` tag plus environment approval | Simple. Staging rarely gets fresh builds, and `forge_merge` work is not deployable to dev. |
| C. All manual | Dispatch | Dispatch | Protected dispatch | Most control. Easy to forget, so channels drift. |
| D. Keep `main`/`v*` | `main` push | `main` push | `v*` | Matches today. `forge_merge` never produces images until merged, which conflicts with the plan's `forge_merge` CI requirement. |

### Recommendation (unresolved)

**Option A.** Add these rules:

- Every publish also writes an immutable `sha-<commit>-<variant>` reference and a release manifest with the digests.
- Moves of the same tag are serialized with a `concurrency` group per tag.
- A monotonic check stops an older commit from moving a channel backwards.
- PRs never get `packages: write`.
- Moving an `:als-prod` tag is not a deployment and not a data import. Ansible rolls out by digest (H7).

The owner needs to confirm two things: whether `:local` should follow `forge_merge`, or only releases; and who the required reviewers for `als-prod` are.

---

## O07: Ansible, storage, secrets and backups

### Facts from the source

- The repository contains no Ansible role, inventory, edge-proxy definition or backup configuration (DEPLOYMENT_SOURCE_AUDIT.md, last paragraph).
- Current state lives in Docker named volumes (`compose.yaml:96-98`: `agent-runs`, `agent-cache`; `docker-compose.yml:121-123`: `tiled_data`) and in source-tree paths (`scripts/start_agent_backend.sh:216`: `storage/tiled_catalog.db`).
- No backup procedure exists.

### Options: host storage

| Option | Description | Trade-off |
| --- | --- | --- |
| **S1. Host bind mounts on the deployment host's managed storage** | Ansible creates `/var/lib/fair2wise/*` sources from inventory roots, sets UID/GID, and mounts them into the container. | Explicit, auditable and backup-friendly. Needs the owner to name the storage roots. |
| S2. Docker named volumes | Docker manages the paths. | Easy to set up. Hard to back up consistently, and host paths are hidden. |
| S3. Network filesystem (NFS/GPFS) | Shared storage. | Locking and latency risk for SQLite job state. Needs a single-writer guarantee. |

### Options: backups

| Option | Description |
| --- | --- |
| **B1. The facility's existing backup system** | It backs up the host mount roots. The app provides a consistency hook. |
| B2. A tool run by Ansible (e.g. restic) | Writes to a destination the owner names. |
| B3. Snapshots only | **Not sufficient**, because it gives no restore guarantee (DEPLOYMENT_STRATEGY.md section 6). |

### Options: secrets

| Option | Description |
| --- | --- |
| **K1. The facility's standard secret backend** | It renders files into `/run/secrets/*`, with mode 0400 and owned by the app UID. |
| K2. ansible-vault | Encrypts values in the deployment repository. |

Whichever backend is chosen, the container's contract is the same: **file references, never inline environment values**.

### Recommendation (unresolved)

**S1 + B1 + K1, plus an application backup hook.** Under that combination:

- The image provides `fair2wise-admin backup-prepare` and `backup-verify`, which T24E builds. They quiesce jobs, take a consistent SQLite or state snapshot, and record the import receipts that match the snapshot. The facility backup copies the result.
- `:local` documents the same hook for operators.
- Central Tiled databases are backed up by the central operators, not by F2W.
- A restore is proven by restoring onto a new disposable instance before H7.

The owner still has to supply these values; none of them is known yet:

- Ansible repository and role
- Storage roots
- UID/GID
- Secret backend
- Backup owner, destination and retention
- RPO/RTO and encryption policy
- Writer principal identifiers

---

## O08: Bundled local inference, and supported CPU platforms

### Facts from the source and host

- **Host:** this workstation is `arm64` macOS 26.6.2. Docker Desktop's server architecture is `arm64`.
- **Images are not multi-arch.**
  - `publish-image.yml` has no `platforms:` input and no `setup-qemu-action` or `setup-buildx-action` step (`:38-46`).
  - It runs on `ubuntu-latest` (`:14`), so the published GHCR image is single-platform (the runner's `linux/amd64`).
  - On this Mac, that image would run under emulation. A local `docker compose build` produces native `linux/arm64`.
- **Base images:** `node:20-bookworm-slim`, `nginx:1.27-alpine`, `python:3.12-slim` and `ghcr.io/prefix-dev/pixi:0.70.2` (`Dockerfile:3,15,28,31`) are pinned by tag only. The official ones are generally published multi-arch, but I did not check the registries. Registry access was outside this task's network permissions.
- **Dependencies:** `requirements/semantic.in:4-6` brings in `torch>=2.12.0`, `faiss-cpu`, `sentence-transformers` with no platform lock. `KG_RAG_FORCE_CPU=1` (`Dockerfile:43`).
- **Tiled server extra:** it includes native-wheel packages such as `numba`, `duckdb<1.4.0`, `pyarrow`, `h5py`, `blosc2` and the ADBC drivers. linux/aarch64 wheel availability for the final 0.2.19 lock was **not checked**.
- **Inference today:** `F2W_BACKEND=cborg | cborg-openai | ollama` (`scripts/start_agent_backend.sh:21`). CBorg needs IPv6 egress, and Docker Desktop on macOS is documented as unreliable for it (`compose.yaml:22-25`, `compose.hostnet.yaml:4-6`).
- **No bundled model server exists.** The `kg_rag_api` service is an OpenAI/Ollama-compatible *proxy* over the KG (`app/modules/kg_rag_api.py:209-313`), not a model server.

### Options: inference

| Option | Description | Trade-off |
| --- | --- | --- |
| **I1. No bundled inference** | `:local` uses a configured external endpoint: CBorg, or Ollama on the host via `host.docker.internal`. | Small image, and the current F2W provider classes are preserved. Needs a reachable endpoint. CBorg from Docker Desktop on macOS is a known problem. |
| I2. Bundle an Ollama process in `:local`, without weights | Weights are mounted or pulled only by an explicit operator command. | More processes to supervise and a larger image. DEPLOYMENT_STRATEGY.md section 2 forbids adding it by interpretation. |
| I3. A separate optional `:local-inference` image or compose service | Inference runs outside the app image. | Keeps `:local` clean, but adds a fourth artifact that the owner has not requested. |

### Options: platforms

| Option | Description | Trade-off |
| --- | --- | --- |
| **P1** | `:local` for `linux/amd64` + `linux/arm64`, built and **tested on native runners** (GitHub `ubuntu-24.04-arm` for arm64). `:als-*` for `linux/amd64` only until the ALS host architecture is confirmed. | Native performance on this arm64 Mac. Needs an arm64 lock check for torch, faiss, PyMuPDF and the Tiled server. |
| P2 | Everything `linux/amd64` | Simplest. Slow and emulated on Apple Silicon. |
| P3 | Everything multi-arch via QEMU | CI_CD_CONTRACT.md forbids claiming support from emulated builds alone. |

### Recommendation (unresolved)

**I1 + P1.**

- Do not bundle an inference server.
- Document how to point `:local` at host Ollama or CBorg.
- Publish arm64 for `:local` only if the arm64 lock resolves and the full `:local` test suite passes on a native arm64 runner. This includes the managed Tiled lifecycle and the PyMuPDF/parser paths. PyMuPDF is to be replaced by Docling later (H0 E2), so the arm64 lock check must be repeated after that switch.
- `:als-*` platforms wait for the ALS host architecture (an O07 input).

The release manifest lists only the platforms that have actually been tested.
