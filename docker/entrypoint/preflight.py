#!/usr/bin/env python3
"""Image preflight (task T24B): validate the fixed profile before any service starts.

Run by the supervisor with the environment it resolved (``docker_profile_contract.md``
section 6). Every check uses the application's own validators, so the image refuses
exactly what the API would refuse later:

1. ``DeploymentSettings`` (``app.api.legacy_ui.deployment_settings_from``): profile,
   authorization mode and Tiled target agree (an ALS image never becomes local-trusted).
2. ``load_tiled_settings``: Tiled mode equals the profile's ``tiled_mode``; ALS targets are
   their one fixed URL; ``:local`` is managed on loopback.
3. Image content: ALS images carry no Tiled server environment (``/opt/tiled``);
   ``:local`` has the isolated Tiled interpreter.
4. Mounts (``ansible_input_contract.md`` section 3): each ``required_mounts`` path exists,
   is a writable directory and, where ``/proc/self/mountinfo`` exists, is (or lies on) a
   mount point other than ``/``, so state never silently lands in the container layer.
   Nothing is created or chowned.
5. ``F2W_TILED_WRITER_SECRET_FILE``, when set, is a readable file (its value is never read
   into the output).
6. The UI bundle's ``f2w-build.json`` matches ``F2W_PUBLIC_PREFIX`` and the gateway
   template renders for it (``app.modules.deployment.gateway``), written to the runtime dir.

Prints one JSON summary; exit 0 when every check passes, 78 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

EXIT_REFUSED = 78
MOUNTINFO = Path("/proc/self/mountinfo")


def mount_points(mountinfo: Path) -> Optional[set]:
    """Mount points listed in a ``/proc/<pid>/mountinfo`` file; ``None`` when unavailable."""
    try:
        text = mountinfo.read_text(encoding="utf-8")
    except OSError:
        return None
    points = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) > 4:
            points.add(fields[4].replace("\\040", " "))
    return points


def _on_mount(path: Path, points: set) -> bool:
    """``path`` is a mount point or lies below one other than ``/`` (one volume for all state)."""
    text = str(path).rstrip("/")
    return any(p != "/" and (text == p or text.startswith(p.rstrip("/") + "/")) for p in points)


def check_mounts(mounts: Sequence[Mapping[str, Any]], *, mountinfo: Path = MOUNTINFO) -> List[str]:
    """Problems with the required mounts (empty when all are fine)."""
    problems: List[str] = []
    points = mount_points(mountinfo)
    for item in mounts:
        path = Path(str(item["path"]))
        purpose = item.get("purpose", "")
        if not path.is_dir():
            problems.append(f"{path} is missing: mount persistent storage there ({purpose})")
            continue
        if item.get("writable", True) and not os.access(path, os.W_OK | os.X_OK):
            problems.append(f"{path} is not writable by uid {os.getuid()}: give the mount to that uid "
                            "(the image never chowns host data)")
        if points is not None and not _on_mount(path, points):
            problems.append(f"{path} is not a mount point: its data would be lost with the container ({purpose})")
    return problems


def check(env: Mapping[str, str], profile: Mapping[str, Any], *, mountinfo: Path = MOUNTINFO,
          tiled_root: Path = Path("/opt/tiled")) -> Dict[str, Any]:
    """Run every check; returns ``{"ok": bool, "problems": [...], "facts": {...}}``."""
    problems: List[str] = []
    facts: Dict[str, Any] = {"profile": profile.get("profile")}
    name = profile.get("profile")
    if env.get("F2W_DEPLOYMENT_PROFILE") != name:
        problems.append(f"F2W_DEPLOYMENT_PROFILE={env.get('F2W_DEPLOYMENT_PROFILE')!r} differs from the image "
                        f"profile {name!r}")

    from app.api.legacy_ui import deployment_settings_from
    from app.modules.security.authorization import StartupConfigError

    try:
        settings = deployment_settings_from(env)
        facts.update(authorization_mode=settings.mode.value, tiled_target=settings.tiled_target)
        if profile.get("authorization_mode") and settings.mode.value != profile["authorization_mode"]:
            problems.append(f"authorization mode {settings.mode.value} differs from the profile's "
                            f"{profile['authorization_mode']}")
    except StartupConfigError as exc:
        problems.append(f"deployment settings refused: {exc}")

    from app.modules.deployment.tiled_config import TiledConfigError, load_tiled_settings

    try:
        tiled = load_tiled_settings(env)
        facts["tiled_mode"] = tiled.mode
        if tiled.mode != profile.get("tiled_mode"):
            problems.append(f"Tiled mode {tiled.mode} differs from the profile's {profile.get('tiled_mode')}")
        if tiled.external is not None:
            facts["tiled_url"] = tiled.external.url
            if profile.get("tiled_url") and tiled.external.url != str(profile["tiled_url"]).rstrip("/"):
                problems.append(f"Tiled URL {tiled.external.url} is not the profile's {profile['tiled_url']}")
        if tiled.managed is not None:
            facts["tiled_data_dir"] = str(tiled.managed.data_dir)
            python = tiled.managed.tiled_python
            if not (python.exists() and os.access(python, os.X_OK)):
                problems.append(f"managed Tiled interpreter {python} is missing from the image")
    except TiledConfigError as exc:
        problems.append(f"Tiled settings refused: {exc}")
    if profile.get("tiled_mode") == "external" and tiled_root.exists():
        problems.append(f"{tiled_root} exists: an external-Tiled image must not carry a Tiled server")

    problems += check_mounts(profile.get("required_mounts", []), mountinfo=mountinfo)
    secret = (env.get("F2W_TILED_WRITER_SECRET_FILE") or "").strip()
    if secret and not (Path(secret).is_file() and os.access(secret, os.R_OK)):
        problems.append(f"F2W_TILED_WRITER_SECRET_FILE={secret} is not a readable file")
    facts["writer_secret_file"] = bool(secret)

    gateway = profile.get("gateway") or {}
    if gateway:
        from app.modules.deployment import gateway as gw

        try:
            layout = gw.layout_from_env(env)
            gw.check_build_agreement(gw.read_build_manifest(Path(gateway["build_manifest"])), layout)
            text = gw.render_nginx_conf(Path(gateway["template"]).read_text(encoding="utf-8"),
                                        upstream=env.get(gw.API_UPSTREAM_ENV) or gw.DEFAULT_API_UPSTREAM,
                                        layout=layout)
            out = Path(gateway["rendered"])
            out.parent.mkdir(parents=True, exist_ok=True)
            for sub in gateway.get("runtime_dirs", []):
                Path(sub).mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            facts["gateway_conf"] = str(out)
        except (gw.PrefixConfigError, OSError, ValueError, KeyError) as exc:
            problems.append(f"gateway: {exc}")
    return {"ok": not problems, "problems": problems, "facts": facts}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="f2w-preflight", description=__doc__.splitlines()[0])
    parser.add_argument("--profile-file", type=Path, required=True)
    parser.add_argument("--mountinfo", type=Path, default=MOUNTINFO)
    parser.add_argument("--tiled-root", type=Path, default=Path("/opt/tiled"))
    args = parser.parse_args(argv)
    profile = json.loads(args.profile_file.read_text(encoding="utf-8"))
    result = check(os.environ, profile, mountinfo=args.mountinfo, tiled_root=args.tiled_root)
    print(json.dumps(result, indent=2), file=sys.stderr if not result["ok"] else sys.stdout)
    return 0 if result["ok"] else EXIT_REFUSED


if __name__ == "__main__":
    sys.exit(main())
