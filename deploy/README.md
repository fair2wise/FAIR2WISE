# Ansible handoff for the two ALS profiles (T24E)

Status: skeleton for the deployment repository, written against
`docs/migration/companion/ANSIBLE_HANDOFF.md` and `DEPLOYMENT_STRATEGY.md` sections 3, 6
and 8. It has never been run against a host: running containers locally and against ALS
hosts waits on owner decision D3.11 and the O07 inputs (deployment repository, host mount
roots, secret backend, backup destination). Nothing here is an inventory: ALS FQDNs, host
names, certificate paths, the writer secret reference and the approved digest are supplied by
the operator in a copy of `inventory.example.yml` kept outside this repository.

## What is here

| Path | Purpose |
| --- | --- |
| `playbooks/deploy.yml` | Roll out one approved **digest** (`<repository>@sha256:...`); the role refuses tags |
| `playbooks/rollback.yml` | Re-point the container to the previously recorded digest; mounts kept; graph/JSON rollback is a separate operation (D31) |
| `playbooks/backup.yml` | Consistent archive of the state mount with a manifest and a `.sha256` sidecar |
| `playbooks/restore.yml` | Verified restore onto a (new, disposable) host, then start the approved digest |
| `roles/fair2wise/` | Validate inputs, prepare directories and the writer secret file, back up, pull by digest, stop within the drain timeout, start, edge proxy, health, record |
| `roles/fair2wise/templates/edge-nginx.conf.j2` | TLS edge: forwards `/fair2wise` unchanged to the gateway on `127.0.0.1:8080` and **overwrites `X-Forwarded-Proto` with `$scheme`** |
| `scripts/f2w_state_archive.py` | Host tool: `backup` / `verify` / `restore` (stdlib only) |
| `scripts/f2w_release_record.py` | Host tool: per-host digest history for rollout and rollback receipts |
| `inventory.example.yml` | Placeholders only (`.example.invalid`, all-zero digest, `vault_*` references) |

Static tests: `tests/deployment/` (`pytest tests/deployment -q`). `ansible-lint` and
`ansible-playbook --syntax-check` are not in the test environment; the YAML is validated
structurally there (plays, FQCN modules, variables declared, digest-only images, no secrets
or hostnames, proxy header, mounts, `--stop-timeout 120`, health URL).

## Contract the role implements

- **Image**: `fair2wise_image_repository@fair2wise_image_digest`, digest matched against
  `^sha256:[0-9a-f]{64}$`; pulled explicitly, then the container is recreated from it. The
  digest comes from the publish receipt (`scripts/ci/release_manifest.py`,
  `release-publication.json` `reference`). The friendly tag is for humans.
- **Mounts on ALS**: `fair2wise_state_host_path` -> `/var/lib/fair2wise/state` (rw, owned
  by uid 10001) and the writer secret file -> `/run/secrets/tiled_writer` (ro). Nothing
  else. `:local` adds `/var/lib/fair2wise/tiled`. Papers/artifacts/kg mounts of
  DEPLOYMENT_STRATEGY section 6 are not proposed here because the ALS images do not
  require them yet (profile `required_mounts`); add them when the application does.
- **Environment**: `F2W_CORS_ORIGINS` (required by the ALS profiles; `https://host[:port]`
  list, must include the FQDN) and `F2W_TILED_WRITER_SECRET_FILE=/run/secrets/tiled_writer`.
  The role never sets `F2W_TILED_URL`, the authorization mode or the profile: the image
  locks them and exits 78 when contradicted.
- **Writer secret file**: regular file, `0600`, owned by uid 10001, written from
  `fair2wise_tiled_writer_secret` with `no_log` (the rule of
  `app/modules/deployment/tiled_config.check_private_file`, AUD-15). The value is a
  reference into the operator's secret backend, never a literal.
- **Stop**: `--stop-timeout 120` on every stop/recreate (the supervisor's ordered drain
  returns running jobs to `queued`); `validate.yml` refuses a smaller value.
- **Edge**: TLS at the edge, `/fair2wise` forwarded byte for byte (no URI on `proxy_pass`),
  `X-Forwarded-Proto $scheme` overwritten, `X-Forwarded-Host`/`-Prefix` cleared, SSE
  unbuffered with a 900 s read timeout, 128 MiB body limit, everything else 404.
- **Health**: `GET /fair2wise/healthz` and `/fair2wise/api/health` on the host port, the
  supervisor status (`/run/fair2wise/supervisor.json`: every required service `running`),
  then `https://<fqdn>/fair2wise/healthz` through the edge. No health check imports or infers.
- **Record**: every rollout appends `{digest, previous_digest, state_schema, reason}` to
  `fair2wise_release_record`; `rollback.yml` re-points to the recorded previous digest and
  writes an entry carrying both digests. `fair2wise_state_schema` labels the state format a
  digest was approved for; a rollback to a digest recorded under another label is refused
  (DEP-21: state compatibility is validated, not assumed).
- **Backup**: `f2w_state_archive.py backup` copies a quiescent SQLite database raw (byte
  identical) and one with an open writer through the online backup API (consistent), writes
  `manifest.json` (schema, profile, digest, per-file sha256) first in the tar and a
  `.sha256` sidecar. `restore` verifies sidecar, manifest and every member before writing a
  byte, refuses another profile's archive (`--allow-profile-change` to override) and a
  non-empty destination (`--replace`). Mounting is not backup: the archive's off-host
  destination, retention and RPO/RTO are O07 inputs.

## Owner inputs still open

ANSIBLE_HANDOFF.md "Owner questions": existing role/repository and its conventions (this
tree uses the `fair2wise_` prefix so it can be folded into an existing role), registry
acceptance, host architectures, secret mechanism, host mount roots (`fair2wise_host_root`
defaults to `/srv/fair2wise`), backup owner/destination/retention (O07), D3.11 for the
container-level rehearsal (`tests/deployment/README.md`).
