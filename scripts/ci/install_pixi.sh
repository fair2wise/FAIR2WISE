#!/usr/bin/env bash
# Install the pinned pixi release for CI (linux x86_64) and verify its sha256 before it runs.
#
# pixi 0.66.0 is the version that wrote pixi.lock and the one the Dockerfile uses (pixi-app stage).
# The digest is the one GitHub publishes for the release asset `pixi-x86_64-unknown-linux-musl`
# (release v0.66.0). No third-party action and no `curl | sh`: bytes that do not match are never
# executed. To move pixi: download the new asset, record its digest here, update the Dockerfile
# pin and regenerate pixi.lock with the same version.
set -euo pipefail

PIXI_VERSION="0.66.0"
PIXI_SHA256="4d8124d3fbe6c201482437873bc67e735e35f3506568e706136c4e079c26929e"
DEST="${PIXI_BIN_DIR:-${RUNNER_TEMP:-/tmp}/pixi-bin}"

mkdir -p "$DEST"
curl --proto '=https' --tlsv1.2 -fsSL \
  "https://github.com/prefix-dev/pixi/releases/download/v${PIXI_VERSION}/pixi-x86_64-unknown-linux-musl" \
  -o "$DEST/pixi"
echo "${PIXI_SHA256}  $DEST/pixi" | sha256sum -c -
chmod +x "$DEST/pixi"
if [[ -n "${GITHUB_PATH:-}" ]]; then
  echo "$DEST" >> "$GITHUB_PATH"
fi
"$DEST/pixi" --version
