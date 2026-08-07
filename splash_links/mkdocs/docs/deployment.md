# Deployment

## Pixi service

```bash
cd splash_links
pixi install
SPLASH_LINKS_DB=links.sqlite pixi run serve
```

Use `pixi run serve-dev` only for development; it enables Uvicorn reload.

## Standalone container

```bash
docker build -t splash-links -f Containerfile .
docker run --rm \
  -p 127.0.0.1:8081:8081 \
  -v "$(pwd)/data:/data" \
  -e SPLASH_LINKS_DB=/data/links.sqlite \
  splash-links
```

The image uses a Pixi build stage and copies the resolved environment into a
Debian runtime stage. It runs Uvicorn on container port `8081`.

## Standalone PostgreSQL Compose

The Compose file in this directory is for Splash development, independently of
FAIR2WISE:

```bash
export POSTGRES_USER=splash_links
export POSTGRES_PASSWORD='replace-me'
docker compose up --build
```

It starts PostgreSQL 16 and Splash, persists PostgreSQL data in `pgdata`,
publishes PostgreSQL only on host loopback, and publishes Splash on `8081`.
Do not commit database passwords.

## FAIR2WISE root Compose

From the FAIR2WISE repository root:

```bash
docker compose up
```

The root stack builds the same `Containerfile` but uses SQLite. It runs a
one-shot `splash-db-init` service before Splash, copies the tracked seed into a
named volume, and keeps Splash private. Only the FAIR2WISE frontend is
host-published.

Do not add a Splash `ports:` entry to the root Compose file merely for browser
access. The frontend talks to the private agent, and the agent talks to Splash
over the Compose network.

## Health and logs

For a directly published standalone service:

```bash
curl -fsS http://127.0.0.1:8081/splash_links/health
```

For the private FAIR2WISE service:

```bash
docker compose exec splash python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8081/splash_links/health').read().decode())"
docker compose logs --tail=100 splash splash-db-init
```

## Persistence and backups

SQLite persistence is the selected database file; PostgreSQL persistence is
the database server's volume. Container images are replaceable and should not
be treated as backups.

Stop writers before copying SQLite:

```bash
cp links.sqlite links.sqlite.backup
```

For root Compose, back up the named volume through a controlled maintenance
procedure. `docker compose down` preserves named volumes; `docker compose down
-v` deletes them.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SPLASH_LINKS_DB` | `:memory:` in `create_app`; `links.sqlite` in normal launchers | Plain SQLite path or SQLAlchemy URL |
| `SPLASH_LINKS_STATIC_DIR` | empty | Optional directory mounted at `/splash_links` |
| `SPLASH_LINKS_URI` | `splash://localhost:8081` | Remote client CLI target |
| `POSTGRES_USER` | none | Standalone PostgreSQL Compose credential |
| `POSTGRES_PASSWORD` | none | Standalone PostgreSQL Compose credential |

## Security boundary

Splash has no built-in API authentication or authorization. Treat graph
mutations and embedding deletion as trusted-network operations. Keep the
service private behind an authenticated application or reverse proxy when
deploying beyond loopback. GraphiQL is enabled by the application factory, so
do not publish it directly on an untrusted network.
