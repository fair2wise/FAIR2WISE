#!/usr/bin/env python3
"""Regenerate the T23A json2kg compatibility fixtures from a baseline checkout.

Run this only against the frozen baseline (222b1e7) export, never against a
changed json2kg. The expected outputs are the contract; regenerating them from
modified code would hide a behavior change.

    env -i PATH=<venv>/bin:/usr/bin:/bin HOME=<tmp> \
        <venv>/bin/python tests/contracts/json_kg/tools/generate_fixtures.py \
        --repo-root <baseline-export>

Writes tests/contracts/json_kg/fixtures/expected/* and fixtures/MANIFEST.json
relative to this file. No network, no Tiled, no model calls: json2kg imports
only the standard library.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / "fixtures"
INPUTS = FIXTURES / "inputs"
EXPECTED = FIXTURES / "expected"

SUCCESS_CASES = ("core_dict", "list_input", "empty_metadata", "custom_prefix")
ERROR_CASES = ("error_no_terms_key", "error_relation_not_object", "error_invalid_json")


def sha256_bytes(data: bytes) -> str:
    """Hex SHA-256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def run_cli(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess:
    """Run the baseline json2kg CLI exactly as the README documents it."""
    return subprocess.run(
        [sys.executable, str(repo_root / "app/modules/json2kg.py"), *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", type=Path, required=True)
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    sys.path.insert(0, str(repo_root))
    from app.modules import json2kg  # noqa: E402

    EXPECTED.mkdir(parents=True, exist_ok=True)
    cli_record: dict = {"success": {}, "errors": {}}

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        for case in SUCCESS_CASES:
            src = INPUTS / f"{case}.terms.json"
            out_cli = tmpdir / f"{case}.cli.graph.json"
            proc = run_cli(repo_root, [str(src), str(out_cli)])
            if proc.returncode != 0:
                raise SystemExit(f"{case}: CLI failed: {proc.stdout}{proc.stderr}")
            out_api = tmpdir / f"{case}.api.graph.json"
            json2kg.convert_terms_to_graph(src, out_api)
            cli_bytes = out_cli.read_bytes()
            if cli_bytes != out_api.read_bytes():
                raise SystemExit(f"{case}: CLI and convert_terms_to_graph bytes differ")
            (EXPECTED / f"{case}.graph.json").write_bytes(cli_bytes)
            cli_record["success"][case] = {
                "exit_code": proc.returncode,
                "stdout": proc.stdout.replace(str(out_cli), "{output}"),
                "stderr": proc.stderr,
            }
        for case in ERROR_CASES:
            src = INPUTS / f"{case}.terms.json"
            out = tmpdir / f"{case}.graph.json"
            proc = run_cli(repo_root, [str(src), str(out)])
            cli_record["errors"][case] = {
                "exit_code": proc.returncode,
                "stdout_observed": proc.stdout.replace(str(src), "{input}"),
                "stderr": proc.stderr,
                "output_written": out.exists(),
            }
        missing = tmpdir / "missing.terms.json"
        proc = run_cli(repo_root, [str(missing), str(tmpdir / "missing.graph.json")])
        cli_record["errors"]["error_missing_input"] = {
            "exit_code": proc.returncode,
            "stdout_observed": proc.stdout.replace(str(missing), "{input}"),
            "stderr": proc.stderr,
            "output_written": (tmpdir / "missing.graph.json").exists(),
        }
        proc = run_cli(repo_root, [])
        cli_record["errors"]["error_no_arguments"] = {
            "exit_code": proc.returncode,
            "stdout_observed": proc.stdout,
            "stderr_first_line": proc.stderr.splitlines()[0] if proc.stderr else "",
            "output_written": False,
        }
        proc = run_cli(repo_root, ["--help"])
        cli_record["help"] = {"exit_code": proc.returncode, "stdout": proc.stdout}
        verbose_out = tmpdir / "verbose.graph.json"
        proc = run_cli(repo_root, ["-v", str(INPUTS / "core_dict.terms.json"), str(verbose_out)])
        cli_record["verbose_core_dict"] = {
            "exit_code": proc.returncode,
            "stdout": proc.stdout.replace(str(verbose_out), "{output}"),
            "bytes_equal_non_verbose": verbose_out.read_bytes()
            == (EXPECTED / "core_dict.graph.json").read_bytes(),
        }

    (EXPECTED / "cli_behavior.json").write_text(
        json.dumps(cli_record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    api = json.loads((INPUTS / "api_cases.json").read_text(encoding="utf-8"))
    api_out: dict = {"cases": {}, "make_id": [], "code_snippet_ids": []}
    for name, case in api["cases"].items():
        graph = json2kg.build_graph(
            case["terms"], code_snippets=case["code_snippets"], **case["kwargs"]
        )
        api_out["cases"][name] = graph
    for term, prefix in api["make_id"]:
        api_out["make_id"].append([term, prefix, json2kg.make_id(term, prefix=prefix)])
    for snip in api["code_snippet_ids"]:
        api_out["code_snippet_ids"].append(json2kg.make_code_snippet_node(snip)["id"])
    (EXPECTED / "api_cases.expected.json").write_text(
        json.dumps(api_out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    files = sorted(p for p in FIXTURES.rglob("*") if p.is_file() and p.name != "MANIFEST.json")
    manifest = {
        "contract": "T23A json2kg compatibility fixtures",
        "baseline_commit": "222b1e7b130567dbf4a3242697ac2b465794a38d",
        "generated_from_json2kg_sha256": sha256_bytes(
            (repo_root / "app/modules/json2kg.py").read_bytes()
        ),
        "python": platform.python_version(),
        "files": {
            str(p.relative_to(FIXTURES)).replace("\\", "/"): sha256_bytes(p.read_bytes())
            for p in files
        },
    }
    (FIXTURES / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(files)} fixture files + MANIFEST.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
