# Getting started

## Prerequisites

- Python 3.12
- Node.js 20 or newer, including npm 10 or newer
- Pixi for the vendored Splash Links environment
- `curl` for readiness checks
- A CBORG API key for the default hosted model, or a local Ollama server

No global Vite installation is needed. `npm ci` installs the repository-local
Vite version from `ui/package-lock.json`.

## Local setup

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

Set `CBORG_API_KEY` in `.env` when using CBORG. For Ollama, set the backend and
model variables described in [Configuration](configuration.md).

## Start the complete application

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

## Verify the services

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
python3 f2w_agent.py status
```

Ask once:

```bash
python3 f2w_agent.py \
  --kg-mode splash \
  --workdir runs/cli_session \
  ask "What is grazing-incidence X-ray scattering used for?"
```

Start an interactive session:

```bash
python3 f2w_agent.py --kg-mode splash chat
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
