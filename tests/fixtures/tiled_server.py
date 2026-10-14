"""Disposable Tiled 0.2.18 test servers (T20B; ``docs/adr/tiled-deployment.md`` section 7).

Each instance runs the same :func:`tiled_lifecycle.start_managed` path that ``:local`` uses,
with the ``test`` profile:

* storage: a fresh ``mkdtemp(prefix="f2w-tiled-")`` root under a fixture base;
* port: an ephemeral ``127.0.0.1`` port (retried if taken between pick and bind);
* credentials: a random writer key (single-user) or generated passwords (``auth=True``),
  kept inside the instance root with mode 0600;
* environment: the Tiled interpreter named by ``F2W_TILED_PYTHON``, started with ``-I``
  and an explicit minimal environment (nothing ambient, no ``.env``);
* ownership: a manifest written before the process starts; teardown signals only that
  process group and deletes only that root, then writes a receipt under
  ``<base>/receipts/``.

Without ``F2W_TILED_PYTHON`` every fixture here skips with a reason, so the default suite
needs no Tiled. When it is set, a server that fails to start fails the test (never skips,
never falls back to another server). These fixtures never read central URLs or keys.

Install the server environment (see ``docs/adr/evidence/tiled/capabilities_0.2.18.md``)::

    uv venv --python <py3.12> venv-tiled
    uv pip install --python venv-tiled/bin/python -r req.txt   # tiled[server] @ <wheel> --hash=sha256:416079...
    export F2W_TILED_PYTHON=$PWD/venv-tiled/bin/python
"""

from __future__ import annotations

import atexit
import json
import os
import random
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from urllib.parse import urlsplit

import pytest
import requests

from app.modules.deployment import tiled_lifecycle as lifecycle
from app.modules.deployment.tiled_config import (
    OWNER_TEST,
    AuthnSpec,
    ManagedTiledConfig,
    validate_external_url,
)

ENV_VAR = "F2W_TILED_PYTHON"
SKIP_REASON = (
    f"{ENV_VAR} is not set: Tiled server tests need an isolated tiled[server]==0.2.18 "
    "environment (never the app venv); see tests/fixtures/tiled_server.py"
)
SEED = 20260916
READY_TIMEOUT = float(os.environ.get("F2W_TILED_READY_TIMEOUT", "60"))

_LIVE: Dict[str, "TiledTestInstance"] = {}


def tiled_python() -> Optional[Path]:
    """The isolated Tiled interpreter from ``F2W_TILED_PYTHON``, or ``None``."""
    value = os.environ.get(ENV_VAR, "").strip()
    return Path(value) if value else None


def tiled_python_or_skip() -> Path:
    """Return the Tiled interpreter or skip the calling test with :data:`SKIP_REASON`."""
    path = tiled_python()
    if path is None:
        pytest.skip(SKIP_REASON)
    return path


class GraphQLFailed(AssertionError):
    """A GraphQL call returned ``errors``."""


@dataclass
class TiledTestInstance:
    """A running disposable server plus helpers. Credentials stay in memory and the root."""

    server: lifecycle.ManagedServer
    base: Path
    writer_key: Optional[str] = None
    passwords: Dict[str, str] = field(default_factory=dict)
    seed_receipt: Optional[Dict[str, Any]] = None
    receipts: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def config(self) -> ManagedTiledConfig:
        """Instance configuration."""
        return self.server.config

    @property
    def url(self) -> str:
        """Loopback base URL."""
        return self.server.url

    @property
    def root(self) -> Path:
        """Owned storage root."""
        return self.config.data_dir

    @property
    def provider(self) -> Optional[str]:
        """Authentication provider name (``auth=True`` only)."""
        return self.config.authn.provider if self.config.authn else None

    def headers(self, credential: Optional[str] = "writer", scheme: str = "Apikey") -> Dict[str, str]:
        """``Authorization`` header; ``"writer"`` means this instance's writer key."""
        if credential == "writer":
            credential = self.writer_key
        return {"Authorization": f"{scheme} {credential}"} if credential else {}

    def graphql_raw(self, query: str, variables: Optional[Dict[str, Any]] = None, *,
                    credential: Optional[str] = "writer", scheme: str = "Apikey") -> Dict[str, Any]:
        """POST to ``/api/graphql`` and return the whole body."""
        resp = requests.post(f"{self.url}/api/graphql", json={"query": query, "variables": variables or {}},
                             headers=self.headers(credential, scheme), timeout=30)
        resp.raise_for_status()
        return resp.json()

    def graphql(self, query: str, variables: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        """Like :meth:`graphql_raw` but return ``data`` and fail on ``errors``."""
        body = self.graphql_raw(query, variables, **kwargs)
        if body.get("errors"):
            raise GraphQLFailed(json.dumps(body["errors"])[:2000])
        return body.get("data") or {}

    def login(self, username: str) -> Dict[str, Any]:
        """Password login (``auth=True``): returns Tiled's access/refresh token payload."""
        assert self.provider, "login needs an auth=True instance"
        resp = requests.post(f"{self.url}/api/v1/auth/provider/{self.provider}/token",
                             data={"username": username, "password": self.passwords[username]}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def stop(self) -> Dict[str, Any]:
        """Normal stop: the data is kept."""
        receipt = lifecycle.stop_managed(self.server)
        self.receipts.append(receipt)
        _LIVE.pop(self.server.nonce, None)
        return receipt

    def restart(self) -> "TiledTestInstance":
        """Start a new server process on the same root (stopping the current one first)."""
        if lifecycle.find_managed(self.config) is not None or (
                self.server.process is not None and self.server.process.poll() is None):
            self.stop()
        self.server = lifecycle.start_managed(self.config, ready_timeout=READY_TIMEOUT)
        _LIVE[self.server.nonce] = self
        return self

    def destroy(self) -> Dict[str, Any]:
        """Stop and remove this instance's owned root; write a receipt under ``<base>/receipts``."""
        _LIVE.pop(self.server.nonce, None)
        receipt = lifecycle.destroy_owned(self.server, base=self.base)
        receipt_dir = self.base / "receipts"
        receipt_dir.mkdir(parents=True, exist_ok=True)
        (receipt_dir / f"{self.server.nonce}.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        return receipt


def start_tiled_instance(base: Path, *, auth: bool = False, seed: bool = True, port: int = 0,
                         python: Optional[Path] = None) -> TiledTestInstance:
    """Start a disposable server under ``base`` (skips if ``F2W_TILED_PYTHON`` is unset).

    Args:
        base: Fixture base directory; the instance root is created inside it.
        auth: Multi-user variant (``DictionaryAuthenticator`` with ``admin`` and ``reader``,
            ``admin`` in ``tiled_admins``) instead of a single-user writer key.
        seed: Load the deterministic seed catalog (:func:`seed_catalog`).
        port: ``0`` for ephemeral; a fixed port only for port-conflict tests.
        python: Override the Tiled interpreter.
    """
    interpreter = python or tiled_python_or_skip()
    base.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="f2w-tiled-", dir=str(base)))
    cfg = ManagedTiledConfig(profile="test", data_dir=root, tiled_python=interpreter, port=port,
                             owner=OWNER_TEST, authn=AuthnSpec() if auth else None)
    server = lifecycle.start_managed(cfg, ready_timeout=READY_TIMEOUT)
    instance = TiledTestInstance(server=server, base=base)
    _LIVE[server.nonce] = instance
    try:
        # Refuse anything but this loopback instance (never an ambient/central target).
        validate_external_url("test", instance.url)
        assert urlsplit(instance.url).hostname == "127.0.0.1"
        instance.writer_key = lifecycle.writer_credential(cfg)
        instance.passwords = lifecycle.authn_passwords(cfg)
        if seed:
            instance.seed_receipt = seed_catalog(instance)
    except BaseException:
        instance.destroy()
        raise
    return instance


SEED_PAPERS = ("Resonant soft X-ray scattering of block copolymers",
               "Polarized RSoXS of organic photovoltaics",
               "Spectroscopic ptychography of battery cathodes")
SEED_MATERIALS = ("PS-b-PMMA", "P3HT:PCBM", "LiNi0.8Mn0.1Co0.1O2", "Y6")
SEED_TECHNIQUES = ("RSoXS", "NEXAFS", "ptychography")


def seed_catalog(instance: TiledTestInstance) -> Dict[str, Any]:
    """Load a small deterministic literature graph plus a FORGE-owned sentinel.

    The production importer (T08B) does not exist yet; this seed uses the same GraphQL
    mutations it will use. Names, properties and links are a pure function of
    :data:`SEED`. A receipt is written to ``<root>/seed-receipt.json``.
    """
    rng = random.Random(SEED)
    credential: Optional[str] = "writer"
    scheme = "Apikey"
    if instance.config.authn is not None:
        credential, scheme = instance.login("admin")["access_token"], "Bearer"
    create_entity = ("mutation($i: CreateEntityInput!) { createEntity(input: $i) { id name entityType uri } }")
    create_link = ("mutation($i: CreateLinkInput!) { createLink(input: $i) { id predicate } }")
    ids: Dict[str, str] = {}

    def entity(kind: str, name: str, uri: str, props: Dict[str, Any]) -> str:
        row = instance.graphql(create_entity, {"i": {"entityType": kind, "name": name, "uri": uri,
                                                     "properties": props}},
                               credential=credential, scheme=scheme)["createEntity"]
        ids[uri] = row["id"]
        return row["id"]

    for idx, title in enumerate(SEED_PAPERS):
        entity("Paper", title, f"f2w:test/paper/{idx}", {"year": 2015 + rng.randrange(10), "corpus": "f2w-test"})
    for idx, name in enumerate(SEED_MATERIALS):
        entity("Material", name, f"f2w:test/material/{idx}", {"corpus": "f2w-test"})
    for idx, name in enumerate(SEED_TECHNIQUES):
        entity("Technique", name, f"f2w:test/technique/{idx}", {"corpus": "f2w-test"})
    sentinel = entity("ESAF", "FORGE sentinel ESAF", "forge:sentinel/esaf/1",
                      {"owner": "forge", "sentinel": True})
    links = []
    for idx in range(len(SEED_PAPERS)):
        paper = ids[f"f2w:test/paper/{idx}"]
        for kind, pool, predicate in (("material", SEED_MATERIALS, "studies"),
                                      ("technique", SEED_TECHNIQUES, "uses")):
            target = ids[f"f2w:test/{kind}/{rng.randrange(len(pool))}"]
            row = instance.graphql(create_link, {"i": {"subjectId": paper, "predicate": predicate,
                                                       "objectId": target, "properties": {"seed": SEED}}},
                                   credential=credential, scheme=scheme)["createLink"]
            links.append(row["id"])
    receipt = {"seed": SEED, "entities": len(ids), "links": len(links), "sentinel_id": sentinel,
               "ids": ids, "link_ids": links}
    (instance.root / "seed-receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


@atexit.register
def _stop_leftovers() -> None:  # pragma: no cover - safety net for aborted sessions
    for instance in list(_LIVE.values()):
        try:
            instance.destroy()
        except Exception:
            try:
                lifecycle.stop_managed(instance.server)
            except Exception:
                pass


def _managed(base: Path, **kwargs: Any) -> Iterator[TiledTestInstance]:
    instance = start_tiled_instance(base, **kwargs)
    try:
        yield instance
    finally:
        instance.destroy()


@pytest.fixture(scope="session")
def tiled_base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Session fixture base; every instance root and receipt lives below it."""
    return tmp_path_factory.mktemp("f2w-tiled-base")


@pytest.fixture
def tiled_instance(tiled_base: Path) -> Iterator[TiledTestInstance]:
    """A fresh seeded single-user server per test."""
    yield from _managed(tiled_base)


@pytest.fixture
def tiled_empty_instance(tiled_base: Path) -> Iterator[TiledTestInstance]:
    """A fresh unseeded single-user server per test."""
    yield from _managed(tiled_base, seed=False)


@pytest.fixture(scope="session")
def tiled_session_instance(tiled_base: Path) -> Iterator[TiledTestInstance]:
    """One seeded single-user server shared by read-only tests."""
    yield from _managed(tiled_base)


@pytest.fixture(scope="session")
def tiled_auth_instance(tiled_base: Path) -> Iterator[TiledTestInstance]:
    """One multi-user server (``admin`` with the admin role, ``reader`` without)."""
    yield from _managed(tiled_base, auth=True)
