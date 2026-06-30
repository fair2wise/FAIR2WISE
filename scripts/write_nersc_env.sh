#!/usr/bin/env bash
set -euo pipefail

# Copy selected local .env values into ~/.f2w_nersc_env on NERSC.
# Does not print secret values.

NERSC_USER="${NERSC_USER:-mateo}"
NERSC_HOST="${NERSC_HOST:-perlmutter.nersc.gov}"
OPENALEX_EMAIL="${OPENALEX_EMAIL:-mateoalado@lbl.gov}"
SSH_OPTS="${SSH_OPTS:-}"

cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

payload="$(
  OPENALEX_EMAIL="$OPENALEX_EMAIL" python3 - <<'PY'
import os
import shlex
from pathlib import Path

try:
    from dotenv import dotenv_values
except Exception as exc:
    raise SystemExit(f"python-dotenv missing locally: {exc}")

env = dotenv_values(Path(".env"))
key = (env.get("CBORG_API_KEY") or "").strip()
base = (env.get("CBORG_BASE_URL") or "https://api.cborg.lbl.gov").strip()
email = os.environ["OPENALEX_EMAIL"]

if not key:
    raise SystemExit("CBORG_API_KEY missing from local .env")

lines = {
    "CBORG_API_KEY": key,
    "CBORG_BASE_URL": base,
    "OPENALEX_EMAIL": email,
    "GITHUB_TOKEN": "",
}
for name, value in lines.items():
    print(f"export {name}={shlex.quote(value)}")
PY
)"

remote="${NERSC_USER}@${NERSC_HOST}"

# shellcheck disable=SC2086
printf "%s\n" "$payload" | ssh $SSH_OPTS "$remote" "cat > ~/.f2w_nersc_env && chmod 600 ~/.f2w_nersc_env"
echo "Wrote ~/.f2w_nersc_env on ${remote}"
