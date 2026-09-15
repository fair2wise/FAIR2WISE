# Getting started

## Prerequisites

For the recommended Compose deployment:

- Docker Engine or Docker Desktop with Docker Compose v2;
- `CBORG_API_KEY` in an untracked `.env` (runtime inject, never baked into the image).

## Three-command Docker startup

With Docker Compose v2 and a CBORG key in `.env`:

```bash
git clone https://github.com/matesuu/FAIRtoWISE-FORUM-AI.git
cd FAIRtoWISE-FORUM-AI
cp .env.example .env
docker compose up
```

Set `CBORG_API_KEY` in `.env` and leave `CBORG_IPV6_BIND` unset (the process
probes a live global IPv6). Never commit `.env` or bake keys into the image.

Open `http://127.0.0.1:5175`. The browser calls the agent at
`http://127.0.0.1:8090`. **Never bind host 5174** (SSH).

On macOS Docker Desktop, CBorg IPv6 from a container is usually blocked; use
`./scripts/start_rsoxs_stack.sh` instead (daemonized agent+UI; does not stop
paper extract). Linux Docker should enable IPv6 on the Compose network.

The Splash stack remains `docker compose -f compose.splash.yaml up` (UI on 5173).


## Local-development prerequisites

- Python 3.12
- Node.js 20 or newer, including npm 10 or newer
- Pixi for the vendored Splash Links environment
- `curl` for readiness checks
- A CBORG API key for the default hosted model, or a local Ollama server

No global Vite installation is needed. `npm ci` installs the repository-local
Vite version from `ui/package-lock.json`.

## Local-development setup

```bash
git clone https://github.com/matesuu/FAIRtoWISE-FORUM-AI.git
cd FAIRtoWISE-FORUM-AI

cp .env.example .env
python3.12 -m pip install -r requirements.txt

cd ui
npm ci
cd ..

./scripts/install_pixi.sh
```

Use `python3.12 -m pip install -r requirements.txt -r requirements/dev.txt`
when contributing code or documentation. The `.txt` files are compiled Python
3.12 locks; edit their corresponding `.in` files and regenerate them with
pip-tools rather than hand-editing locks. `requirements/semantic.in` is
included by `requirements.txt` and is not compiled.

Set `CBORG_API_KEY` in `.env` when using CBORG. For Ollama, set the backend and
model variables described in [Configuration](configuration.md).

## Start without Docker

```bash
./scripts/start_all.sh
```

The launcher:

1. checks ports `8081`, `8090`, and `5173`;
2. starts `pixi run serve` inside `splash_links`;
3. waits for the Splash health endpoint;
4. starts the agent API;
5. waits for `/health`;
6. starts Vite; and
7. records managed PIDs in `.run/`.

Press **Ctrl+C** in the launcher terminal to stop all managed processes.

For the RSoXS JSON demo without Splash, prefer Docker or the daemonized host stack
(survives Cursor session exit; does not stop `scripts/run.py` extract):

```bash
./scripts/start_rsoxs_stack.sh
```

UI is `http://127.0.0.1:5175`, agent `http://127.0.0.1:8090`. Stop with
`./scripts/start_rsoxs_stack.sh stop`.


## Verify local-development services

```bash
curl -fsS http://127.0.0.1:8081/splash_links/health
curl -fsS http://127.0.0.1:8090/health
curl -fsS http://127.0.0.1:5173/ >/dev/null
```

Open `http://127.0.0.1:5173` in a browser.

## Run components separately

=== "Splash Links"

    ```bash
    cd splash_links
    pixi run serve
    ```

=== "Agent API"

    ```bash
    ./scripts/start_agent_backend.sh
    ```

=== "Frontend"

    ```bash
    ./scripts/start_agent_frontend.sh
    ```

## Command-line agent

Inspect resolved agent settings:

```bash
python3 -m app.modules.launchers.f2w_agent status
```

Ask once:

```bash
python3 -m app.modules.launchers.f2w_agent \
  --kg-mode splash \
  --workdir runs/cli_session \
  ask "What is grazing-incidence X-ray scattering used for?"
```

Start an interactive session:

```bash
python3 -m app.modules.launchers.f2w_agent --kg-mode splash chat
```

Downloads and extraction require explicit approval unless
`--auto-approve` is supplied.

## Build documentation

```bash
mkdocs build -f mkdocs/mkdocs.yml --strict
```

For live authoring:

```bash
mkdocs serve -f mkdocs/mkdocs.yml
```

The documentation server defaults to `http://127.0.0.1:8000`.
