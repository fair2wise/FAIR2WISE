# Deployment tests (T24E)

Two layers:

1. **Static and harness tests, run in the normal suite** (`pytest tests/deployment -q`, no
   Docker, no network):
   - `test_deploy_static.py`: the Ansible handoff under `deploy/` parses; plays target the
     `fair2wise` group with FQCN modules; the container image is `repository@digest`, validated by
     `^sha256:[0-9a-f]{64}$`, and no tag reference exists anywhere; no ALS hostname, real digest,
     vault blob, key or literal secret is committed and the example inventory uses
     `.example.invalid` names, an all-zero digest and `vault_*` references; the edge template sets
     `X-Forwarded-Proto $scheme` (never relays `$http_x_forwarded_proto`), forwards `/fair2wise`
     with a URI-less `proxy_pass` and streams unbuffered, and renders cleanly with the example
     inventory; on ALS the only `/var/lib/fair2wise` mount is `state` (plus the read-only secret
     file) and `:local` adds `tiled`; `F2W_CORS_ORIGINS` and `F2W_TILED_WRITER_SECRET_FILE` are
     provided and no locked image value is overridden; every stop/recreate uses
     `stop_timeout: 120`; health goes to `/fair2wise/healthz`, `/fair2wise/api/health` and the
     supervisor status; the writer secret file is `0600`, uid 10001, `no_log`, and that rule is
     the one `tiled_config.check_private_file` enforces; every `fair2wise_*` variable the plays
     reference is declared in the defaults or the example inventory; rollout records the digest
     and rollback re-points to the recorded previous one.
   - `test_deploy_state_archive.py`: `deploy/scripts/f2w_state_archive.py` and
     `f2w_release_record.py` against a temp directory shaped like the state mount, with a real
     `JobStore` database: restore is byte-identical; an open-writer database is copied
     consistently; a tampered member, manifest or sidecar is refused before anything is written;
     a path-escaping member, a foreign profile and a non-empty destination are refused; the
     release record carries previous and new digest on every rollout and rollback and refuses
     tags, unknown digests, profile/repository changes and a state-schema mismatch.
2. **Container-level checks, skipped until D3.11** (`test_deploy_container_required.py`,
   `@pytest.mark.skipif(not os.environ.get("F2W_CONTAINER_TESTS"))`). Their bodies are a first
   draft of the procedure and have never been executed.

## What still needs a running container

Owner decision D3.11 (local `docker build` / `docker run` by the integrator) gates everything
below. Nothing contacts an ALS host; the two ALS checks that need the central Tiled are
owner-run on a disposable staging host.

| Check | Binary case | Command (after `docker build --target local -t f2w:local .` etc.) |
| --- | --- | --- |
| UI and assets through the real gateway under `/fair2wise` (SPA, 404 for a missing asset, `/fair2wise` -> `/fair2wise/`) | DEP-07, DEP-22 | `F2W_CONTAINER_TESTS=1 F2W_IMAGE_LOCAL=f2w:local F2W_CONTAINER_PORT=18080 pytest tests/deployment/test_deploy_container_required.py::test_ui_and_assets_are_served_under_the_prefix_through_the_real_gateway -q` |
| SSE through nginx: first event before the end, exactly one terminal event, no buffering | DEP-08 | `... pytest tests/deployment/test_deploy_container_required.py::test_sse_streams_through_nginx_with_one_terminal_event -q` |
| SSE through the **edge** template as well: render `deploy/roles/fair2wise/templates/edge-nginx.conf.j2` with a self-signed certificate, run `nginx:1.26-alpine` on a user network in front of the container, repeat the SSE check over HTTPS and confirm the API sees `X-Forwarded-Proto: https` even when the client sends `X-Forwarded-Proto: http` | DEP-08, DEP-16 | manual; see "Edge rehearsal" below |
| Real JSON -> Tiled import from **inside** the `:local` image against its supervised Tiled, then read back | DEP-11, TIL-* | `docker exec f2w-local python -m app.api.cli publish --help` for the exact subcommand, then the T08B import of a tracked `storage/kg/*.json` snapshot; compare with `tests/contracts/legacy_ui/closure/test_closure_tiled.py` |
| Restart/recreate keeps the `state` and `tiled` named volumes; ordered stop exits 0 within `--stop-timeout 120`; required-service death is visible in `/run/fair2wise/supervisor.json` | DEP-10, DEP-18, DEP-20 | `... pytest tests/deployment/test_deploy_container_required.py::test_restart_keeps_state_and_tiled_volumes_and_the_supervisor_stops_in_order -q` |
| Backup and restore of the state named volume with `f2w_state_archive.py`, then a second container on the restored volume | DEP-17 | `... pytest tests/deployment/test_deploy_container_required.py::test_state_volume_backup_and_restore_round_trip_with_the_host_tool -q` |
| ALS image startup contract: exit 78 without the state mount or `F2W_CORS_ORIGINS`, with a group-readable secret, or with `F2W_AUTHORIZATION_MODE=local_trusted` | DEP-16, SEC-* | `... F2W_IMAGE_ALS_DEV=f2w:als-dev pytest tests/deployment/test_deploy_container_required.py -k als_image -q` |
| `:local` no-login: every inventoried route works without a session cookie **through the gateway** (the in-process version is `tests/security_live/test_live_local_no_login.py`) | LOCAL-01..05, DEP-19 | run `tests/security_live/test_live_local_no_login.py` with `F2W_TILED_PYTHON` today; the container repeat is the same requests against `http://127.0.0.1:18080/fair2wise/api` |
| ALS reader/admin sessions against the real staging Tiled: sign-in round trip, reader chat-only, admin edit takes effect, role downgrade denies (the in-process version is `tests/security_live/test_live_als_app.py`) | DEP-19, AUTH-* | **owner-run only** on a disposable host with `F2W_CORS_ORIGINS` and a staging writer secret; the ALS images lock `F2W_TILED_URL`, so this cannot run against a disposable Tiled |
| Ansible plays against a disposable host (`deploy.yml`, `backup.yml`, `restore.yml`, `rollback.yml`), `ansible-lint` and `--syntax-check` | DEP-16, DEP-17, DEP-21 | `ansible-playbook -i <your inventory> deploy/playbooks/deploy.yml -l als_dev -e fair2wise_image_digest=sha256:<digest> --check` first; needs the O07 inputs and `community.docker` |

### Edge rehearsal (manual, once D3.11 is approved)

```sh
openssl req -x509 -newkey rsa:2048 -nodes -subj /CN=f2w.test -keyout key.pem -out cert.pem -days 2
python - <<'PY'
import jinja2, pathlib
t = jinja2.Template(pathlib.Path("deploy/roles/fair2wise/templates/edge-nginx.conf.j2").read_text())
pathlib.Path("edge.conf").write_text(t.render(fair2wise_fqdn="f2w.test", fair2wise_profile="local",
    fair2wise_base_path="/fair2wise", fair2wise_gateway_bind="f2w-local", fair2wise_gateway_host_port=8080,
    fair2wise_tls_certificate_path="/certs/cert.pem", fair2wise_tls_key_path="/certs/key.pem",
    fair2wise_edge_stream_timeout="900s", fair2wise_edge_client_max_body_size="128m"))
PY
docker network create f2w-edge
docker run -d --name f2w-local --network f2w-edge --stop-timeout 120 -v f2w-state:/var/lib/fair2wise/state -v f2w-tiled:/var/lib/fair2wise/tiled f2w:local
docker run -d --name f2w-edge --network f2w-edge -p 127.0.0.1:18443:443 -v $PWD/edge.conf:/etc/nginx/conf.d/default.conf:ro -v $PWD:/certs:ro nginx:1.26-alpine
curl -k -H 'X-Forwarded-Proto: http' https://127.0.0.1:18443/fair2wise/api/docs -o /dev/null -w '%{http_code}\n'   # 200; openapi URL must be https
curl -k -N https://127.0.0.1:18443/fair2wise/api/chat/stream -H 'Content-Type: application/json' -d '{"message":"ping"}'
```
