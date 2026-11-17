#!/usr/bin/env bash
# Install the pinned pixi release for CI (linux x86_64 or aarch64) and verify its sha256 before it runs.
#
# pixi 0.84.2 is the version that wrote pixi.lock and the one the Dockerfile uses (PIXI_IMAGE, pixi-app
# stage). (0.82.1 was the first release to carry the certificate-validation fix after the eMudhra CA
# compromise; 0.84.2 is the latest stable release as of 2026-11-17.) The digests are the ones GitHub
# publishes for the release assets `pixi-x86_64-unknown-linux-musl` and `pixi-aarch64-unknown-linux-musl`
# (release v0.84.2), re-computed from the downloaded bytes; the matching .tar.gz assets' own
# .sha256 files were checked against the same release. No third-party action and no `curl | sh`:
# bytes that do not match are never executed. To move pixi: download the new assets, record both
# digests here, update the Dockerfile PIXI_IMAGE pin and regenerate pixi.lock with the same version.
set -euo pipefail

PIXI_VERSION="0.84.2"
case "${PIXI_ARCH:-$(uname -m)}" in
  x86_64|amd64)
    ASSET="pixi-x86_64-unknown-linux-musl"
    PIXI_SHA256="32523b4d1f43ca1583579252bbf7f977f83e4ce16f1333225379ece9f46bff0c" ;;
  aarch64|arm64)
    ASSET="pixi-aarch64-unknown-linux-musl"
    PIXI_SHA256="68995ac4a33d821f0080c1af4fb6fec4efd6dc3022c490d9dd904a2a7065610c" ;;
  *)
    echo "install_pixi.sh: unsupported architecture ${PIXI_ARCH:-$(uname -m)}" >&2
    exit 1 ;;
esac
DEST="${PIXI_BIN_DIR:-${RUNNER_TEMP:-/tmp}/pixi-bin}"

mkdir -p "$DEST"
curl --proto '=https' --tlsv1.2 -fsSL \
  "https://github.com/prefix-dev/pixi/releases/download/v${PIXI_VERSION}/${ASSET}" \
  -o "$DEST/pixi"
echo "${PIXI_SHA256}  $DEST/pixi" | sha256sum -c -
chmod +x "$DEST/pixi"
if [[ -n "${GITHUB_PATH:-}" ]]; then
  echo "$DEST" >> "$GITHUB_PATH"
fi
"$DEST/pixi" --version
