# Dependency management moved from pip-compile locks to pixi (H7 review packet note)

Requested by the owner on 2026-10-30 ("switch to pixi for dependencies", scope: everything including the
Docker images). This changes how the images are built, which owner decisions O06 D2.1-D2.10 and audit finding
AUD-08 / T28C (hash-locked installs) govern. Review it as a new decision, not as cleanup.

## What changed

- `pixi.toml` / `pixi.lock` (pixi 0.66.0, platforms linux-64, linux-aarch64, osx-arm64) replace
  `requirements.txt`, `requirements/*.in|txt` (including `runtime.lock.txt`, the hashed image lock) and
  `docker/profiles/tiled-server/constraints*.txt`. Environments: `default` (application), `dev`
  (default + tests + quality + docs), `docs`, `globus`, `legacy`, `tiled-server`.
- The guarantee of AUD-08 is kept: `pixi.lock` records a sha256 for every conda package and every PyPI
  artifact, and images and CI install with `pixi install --locked`. The conda part is new coverage (the old lock
  hashed only PyPI artifacts). `torch` is the PyTorch CPU build on every platform; no CUDA wheel is locked.
- Application pins are the versions of the old `requirements/runtime.txt`, migrated unchanged. The semantic stack
  (faiss-cpu, sentence-transformers, torch) stays unpinned in `pixi.toml` by design and is fixed per platform
  by the lock; `transformers==5.20.1` and `huggingface-hub==1.33.0` are pinned because the resolver's
  newer/older choices changed behaviour (see the comments in `pixi.toml`).
- Dockerfile: new stages `pixi-app` (pixi 0.66.0 image), `app-env-build`, `docs-build` and
  `tiled-server-build` build locked environments at the path they run from (conda prefixes are not
  relocatable). `runtime-base` copies `/opt/fair2wise/pixi/.pixi/envs/default` and puts it first on `PATH`;
  `local` copies the Tiled environment and links `/opt/tiled/venv` to it (so `F2W_TILED_PYTHON` keeps its
  documented value). No pixi binary reaches a final image (the legacy `agent` target keeps its own pixi for
  `splash_links`).
- CI installs pixi with `scripts/ci/install_pixi.sh` (pinned release, sha256 verified before it runs): no
  new third-party action, so the reviewed-action list is unchanged.
- `scripts/ci/release_manifest.py` records `pixi.toml` and `pixi.lock` hashes instead of the retired files.
- `tests/images`, `tests/ci` and `tests/packaging` were rewritten to assert the same guarantees against the new
  design (locked installs only, owner-pinned Tiled wheel hash in `pixi.lock`, Tiled only in its own
  environment, CPU-only torch, no pixi tool in final images). `tests/packaging/test_requirements_locks.py`
  became `tests/packaging/test_pixi_lock.py`.
- Found and fixed on the way: the baked embedding models had no `refs/main`, so loading them by bare model
  name with `HF_HUB_OFFLINE=1` failed (`KG_RAG_EMBED_MODEL` defaults are bare names). The Dockerfile now
  writes `refs/main` for each baked snapshot. Verified by loading both models by name offline from a copy of
  the cache; not yet verified in a rebuilt image (see below).
- `scripts/nersc_remote_setup.sh` now installs the locked `default` environment with pixi and writes a
  `.venv/bin/activate` shim, so the other NERSC scripts keep working unchanged.

## Not verified (needs the owner or CI)

- The `linux-64` (amd64) image build, which is the only platform O06 D2.8 builds. Verified locally: the `local`
  target builds natively for arm64 (about 7.5 minutes, 8.5 GB), runs as uid 10001, has no pixi binary,
  isolates Tiled 0.2.18 under `/opt/tiled/venv`, imports the application, and has CPU-only torch 2.15.0.
  The rebuild after the last lock change and the `scripts/ci/image_checks.py` post-build checks were not run
  because the local Docker VM disk (colima) is full.
- `scripts/nersc_remote_setup.sh` on Perlmutter (pixi must be on `PATH` there).
- The GitHub Actions workflows (YAML and static tests pass; nothing was run on a runner).
- Image size against the previous pip-based images (not measured).

## Owner decisions needed

1. Re-approve O06 D2.1-D2.10 for the pixi install path (locked, hash-verified, conda plus PyPI).
2. Accept that `click` is overridden in the dev environment (deepeval caps `<8.4`, `huggingface-hub` 1.33 needs
   `>=8.4.2`; the previous pip environments carried the same violation).
3. Decide whether the legacy Splash `agent` image should move to the same pixi version (it pins 0.70.2).
