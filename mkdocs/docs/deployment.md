# Fresh-machine deployment

This runbook installs FAIR2WISE on a computer that has no existing images,
volumes, or development environment. Docker Compose is the supported default:
the clone supplies all application source and each computer builds its own
images.

## Before cloning

Install:

- Git;
- Docker Engine with the Compose v2 plugin, or Docker Desktop; and
- a browser on the same computer.

Confirm the Docker daemon is running:

```bash
docker version
docker compose version
```

FAIR2WISE uses the hosted CBORG model by default. Obtain a CBORG API key and
authorize the computer's outbound address before expecting chat or extraction
to work. Image builds and graph browsing do not themselves prove CBORG access.

## Clone and configure

```bash
git clone https://github.com/matesuu/FAIRtoWISE-FORUM-AI.git
cd FAIRtoWISE-FORUM-AI
cp .env.example .env
```

Edit `.env`, replace the required placeholder, and blank optional keys you do
not use. The minimum Compose configuration is:

```dotenv
CBORG_API_KEY=replace-with-your-key
CBORG_BASE_URL=https://api.cborg.lbl.gov
CBORG_IP_FAMILY=ipv6
```

The root `.env` is ignored by Git and excluded from Docker build contexts. Use
one `KEY=value` assignment per line and do not commit the file. Compose
automatically reads it from the project directory.

### Environment checklist

Only variables explicitly passed by `compose.yaml` affect the standard
container deployment:

| Variable | Required | Use |
|---|---|---|
| `CBORG_API_KEY` | Yes | Authenticates hosted LLM requests |
| `CBORG_BASE_URL` | No | Defaults to `https://api.cborg.lbl.gov` |
| `CBORG_IP_FAMILY` | No | Defaults to `ipv6`; accepts `ipv6`, `ipv4`, or `auto` |
| `OPENALEX_EMAIL` | Recommended | Identifies polite OpenAlex/Unpaywall traffic |
| `MP_API_KEY` | No | Enables Materials Project validation |
| `GITHUB_TOKEN` | No | Raises GitHub API limits for linked source-code extraction |
| `F2W_UI_PORT` | No | Changes the sole loopback-published port from `5173` |

Globus and Academy values in `.env.example` are for the optional NERSC path;
the standard Compose stack does not pass them into its services. See
[Configuration](configuration.md) for the complete local-development matrix.

Validate interpolation without printing the resolved configuration into a
shared log:

```bash
docker compose config --quiet
```

## CBORG authorization and IPv6

CBORG authorizes a public source address in addition to checking the API key.
Visit the [CBORG key-management page](https://api.cborg.lbl.gov/key/manage)
from the deployment computer, sign in, and authorize the displayed address.
Activation can take up to about one minute.

The browser used for authorization is not the API transport. Chrome, Firefox,
and other browsers only perform the authorization step; the Python process in
the `agent` container makes the actual CBORG request.

Compose handles the common IPv6 mismatch in two places:

1. the default Compose network has IPv6 enabled; and
2. `CBORG_IP_FAMILY=ipv6` tells the CBORG HTTP client to resolve and connect
   with IPv6.

After the stack starts, inspect the address visible from the agent container:

```bash
docker compose exec agent curl -6 -fsS https://api64.ipify.org
```

That address should match an address authorized by CBORG. If the command fails,
the host or Docker runtime does not currently have usable IPv6 egress. In that
case connect through LBLnet/VPN, or set `CBORG_IP_FAMILY=ipv4`, restart the
agent, determine its IPv4 egress, and authorize that address instead:

```bash
docker compose exec agent curl -4 -fsS https://api.ipify.org
docker compose up -d --force-recreate agent frontend
```

An `ip_not_authorized` 403 is an address-family/network problem, not a browser
problem and not evidence that the database is incomplete.

## First start

Start the complete application:

```bash
docker compose up
```

The first start is intentionally slower than later starts. Expect these phases:

1. Docker downloads base images and builds the frontend, agent, and Splash
   images.
2. `splash-db-init` validates the tracked `splash_links/links.sqlite` seed and
   copies it into the new `splash-data` volume.
3. Splash starts and passes its health check.
4. The agent starts, reads the graph from private Splash, and passes `/health`.
5. Nginx starts the frontend and its same-origin `/api` proxy.

No separate `pip install`, `npm install`, or graph import is required. A
successful terminal state shows `splash-db-init` exited with code 0 and the
three long-running services healthy. Open `http://127.0.0.1:5173`.

In another terminal, verify the public entry point:

```bash
docker compose ps
curl -fsS http://127.0.0.1:5173/healthz
curl -fsS http://127.0.0.1:5173/api/health | python3 -m json.tool
```

The frontend is the only published service. Splash `8081` and agent `8090`
remain private, but the agent API is deliberately reachable through
`http://127.0.0.1:5173/api/`.

## Persistent state

Container replacement does not erase named volumes:

| Volume | Durable data |
|---|---|
| `splash-data` | Editable Splash SQLite graph |
| `agent-runs` | Downloaded PDFs, terms, session graph, workflow, and memory |
| `agent-cache` | Model and PyStow caches |

The seed initializer does not overwrite an initialized `splash-data` volume.
Pulling a newer tracked seed therefore does not replace a graph that users have
edited. `docker compose down` retains all three volumes;
`docker compose down --volumes` permanently removes them.

## Upgrade procedure

Treat the Splash graph and agent runs as data, not rebuildable containers.
Before an upgrade, note the current revision and make a cold backup:

```bash
git rev-parse HEAD
mkdir -p backups/pre-upgrade
docker compose stop frontend agent splash
docker compose cp splash:/data/links.sqlite backups/pre-upgrade/links.sqlite
docker compose cp agent:/app/runs backups/pre-upgrade/runs
docker compose start splash agent frontend
```

Keep the backup directory outside shared/public storage because agent runs can
contain questions and downloaded publications.

Then update and rebuild:

```bash
git pull --ff-only
docker compose build --pull
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:5173/api/health | python3 -m json.tool
```

Review `docker compose logs --tail=200 splash agent frontend` before declaring
the upgrade complete. Exercise one graph read and one known-evidence question;
do not use an extraction as the first smoke test because extraction mutates the
graph.

## Rollback procedure

An application rollback normally keeps the existing volumes:

```bash
docker compose down
git switch --detach PREVIOUS_COMMIT_OR_TAG
docker compose build
docker compose up -d
```

Use a release branch/tag rather than a detached revision for a long-lived
deployment. Do not restore data merely because an image rollback occurred. A
data restore discards edits made after the backup and is appropriate only when
the upgrade migrated, corrupted, or incompatibly changed the graph.

To restore the cold Splash backup:

```bash
docker compose down
docker compose run --rm --no-deps \
  -v "$PWD/backups/pre-upgrade:/backup:ro" \
  splash-db-init sh -c 'cp /backup/links.sqlite /data/links.sqlite'
docker compose up -d
```

The existing seed marker causes subsequent starts to retain the restored file.
Verify `/api/health` and inspect a known node after restoration. Agent-run
restoration is usually unnecessary; if workflow continuity matters, restore
the matching `runs` backup before accepting new sessions. Restoring runs is
destructive because it replaces all newer session state:

```bash
docker compose down
docker compose run --rm --no-deps \
  -v "$PWD/backups/pre-upgrade:/backup:ro" \
  agent sh -c 'find /app/runs -mindepth 1 -delete && cp -a /backup/runs/. /app/runs/'
docker compose up -d
```

See [Local and Docker operation](operations.md) for routine lifecycle and
diagnostic commands, [Security model](security.md) before changing bind
addresses, and [Data provenance](provenance.md) before replacing graph data.
