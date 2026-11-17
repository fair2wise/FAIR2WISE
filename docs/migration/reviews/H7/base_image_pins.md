# Base image pins, Node 22, pixi 0.84.2 (K3s pilot build)

Resolved 2026-11-17. Every `FROM` in the `Dockerfile` goes through a build ARG whose default is
`name:tag@sha256:<digest>`. The digest is the **multi-arch index digest** (`docker buildx imagetools
inspect <tag>`, the `Digest:` line of the index), so linux/amd64 and linux/arm64 both resolve from
the same pin. Override at build time with `--build-arg NODE_IMAGE=...` (and so on) to use a
mirror or a newer pin; behaviour with the defaults is unchanged apart from the pins below.

| ARG | Default | Index digest | Source |
| --- | --- | --- | --- |
| `PYTHON_IMAGE` | `python:3.12-slim` | `sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b` | Docker Hub, `docker buildx imagetools inspect python:3.12-slim`, 2026-11-17 |
| `NODE_IMAGE` | `node:22-bookworm-slim` | `sha256:e9f565d32859f0cbfb52125eafe8be39390f24dac302dc3cdf566e8ba3ab21ee` | Docker Hub, `docker buildx imagetools inspect node:22-bookworm-slim`, 2026-11-17 (was `node:20-bookworm-slim`, unpinned) |
| `NGINX_IMAGE` | `nginx:1.27-alpine` | `sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10` | Docker Hub, `docker buildx imagetools inspect nginx:1.27-alpine`, 2026-11-17 |
| `PIXI_IMAGE` | `ghcr.io/prefix-dev/pixi:0.84.2` | `sha256:8201f134655a8261f382ee565ddf1498e5f215887bb14e812e1f47c09f4f7196` | ghcr.io, `docker buildx imagetools inspect ghcr.io/prefix-dev/pixi:0.84.2`, 2026-11-17 (was 0.66.0) |
| `PIXI_LEGACY_IMAGE` | `ghcr.io/prefix-dev/pixi:0.70.2` | `sha256:c2d3c9b7041293d90039857bb3cff4c0bbe8c78af1d68254b230575154b85f65` | ghcr.io, same command, 2026-11-17; legacy `agent` target only (splash_links keeps its own pixi) |

`tests/images/test_images_dockerfile.py::test_every_from_is_pinned_by_digest_through_an_overridable_build_arg`
fails if a `FROM` names a literal image, or an ARG default has no `@sha256:` digest.

Cost: a pinned tag no longer receives upstream security rebuilds until someone moves the pin. Refresh
by re-running the `imagetools inspect` commands above, editing the ARG defaults and this table, and
rebuilding. The release manifest still records the resolved digests of the base images (D2.9).

## Node 22

The UI build stage moved from Node 20 to Node 22 (`node:22-bookworm-slim`, Node v22.23.5 / npm 10.9.9
at the pinned digest) and `forge-merge-ci.yml` `NODE_VERSION` is now `"22"`. `ui/package.json`
`engines.node` stays `>=20` on purpose: it admits 22, and `ui/` is the frozen UI tree (editing it
changes `FROZEN_UI_TREE`, an owner decision). Checked inside the pinned stage image (native arm64):
`npm ci` ok, `npm run build` ok (`public_prefix` is `/fair2wise`), `npm test` (vitest) 407 to 410 of
411 pass; the failures are 5000 ms timeouts of `GraphMockupLarge.test.tsx` under the VM's CPU
load (1 to 4 tests, a different set each run), and all 6 pass alone with `--testTimeout=60000`.
Playwright was not run.

## pixi 0.84.2

0.84.2 is the latest stable release (2026-11-10); 0.82.1 (2026-10-21) fixed certificate validation
after the eMudhra CA compromise. Every release from 0.82.1 on reads only `pixi.lock` format v7, so the
lock was regenerated with 0.84.2 (`pixi lock`): format v6 to v7, conda packages unchanged, 23
PyPI packages re-resolved to newer versions (see the commit message for the list). `pixi.lock` still
covers linux-64, linux-aarch64 and osx-arm64 (plus the `osx-arm64-macos-14-0` platform pixi derives from
`[system-requirements]`). `scripts/ci/install_pixi.sh` pins the release assets
`pixi-x86_64-unknown-linux-musl` (`32523b4d...46bff0c`) and `pixi-aarch64-unknown-linux-musl`
(`68995ac4...065610c`), checked against the digests GitHub publishes for the v0.84.2 assets and
the bytes downloaded; the `.tar.gz` assets match their `.sha256` files.
