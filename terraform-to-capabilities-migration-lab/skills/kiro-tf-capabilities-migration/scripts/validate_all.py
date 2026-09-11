"""Fan out validation across every (mode, rgd) tuple under an output tree.

Implements Parallel Phase C from SKILL.md — the validation sweep. Discovers
`<mode>/<rgd>/resources/` and `<mode>/<rgd>/rgd.yaml` under the given output
directory, then runs validate-manifest + validate-cel concurrently across all
tuples using a bounded worker pool.

Aggregates findings per mode into `<mode>/findings.json` so render-report has
one input per bundle.

Usage:
    scripts/run validate-all --output migration-output/ --context rekoncile-demo
    scripts/run validate-all --output migration-output/ --modes adopt
    scripts/run validate-all --output ... --workers 8

Layout expected:
    <output>/adopt/<rgd-name>/rgd.yaml
    <output>/adopt/<rgd-name>/resources/*.yaml
    <output>/create/<rgd-name>/rgd.yaml
    <output>/create/<rgd-name>/resources/*.yaml
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

import click


SCRIPT_DIR = Path(__file__).resolve().parent
RUN = str(SCRIPT_DIR / "run")


@dataclass
class TupleResult:
    mode: str
    rgd: str
    manifest_findings: list
    cel_findings: list
    duration_seconds: float
    ok: bool
    error: str = ""


def discover_tuples(output_dir: Path, modes: list[str]) -> list[tuple[str, str]]:
    """Return [(mode, rgd_name), ...] for every valid subtree."""
    tuples = []
    for mode in modes:
        mode_dir = output_dir / mode
        if not mode_dir.is_dir():
            continue
        for rgd_dir in sorted(mode_dir.iterdir()):
            if not rgd_dir.is_dir():
                continue
            has_rgd = (rgd_dir / "rgd.yaml").is_file()
            has_resources = (rgd_dir / "resources").is_dir()
            if has_rgd and has_resources:
                tuples.append((mode, rgd_dir.name))
    return tuples


def _run_json(args: list[str]) -> tuple[list, int]:
    """Run `scripts/run …` returning (findings list, rc)."""
    proc = subprocess.run(args, capture_output=True, text=True)
    try:
        parsed = json.loads(proc.stdout) if proc.stdout.strip() else {"findings": []}
    except json.JSONDecodeError:
        parsed = {"findings": [], "_stderr": proc.stderr, "_stdout": proc.stdout}
    return parsed.get("findings", []), proc.returncode


def validate_tuple(mode: str, rgd: str, output_dir: Path, context: str | None, kubeconfig: str | None) -> TupleResult:
    start = time.time()
    resources_dir = output_dir / mode / rgd / "resources"
    rgd_file = output_dir / mode / rgd / "rgd.yaml"

    manifest_args = [RUN, "validate-manifest",
                     "--dir", str(resources_dir),
                     "--mode", mode,
                     "--format", "json"]
    if context:
        manifest_args += ["--context", context]
    if kubeconfig:
        manifest_args += ["--kubeconfig", kubeconfig]

    manifest_findings, mrc = _run_json(manifest_args)

    cel_args = [RUN, "validate-cel", str(rgd_file), "--format", "json"]
    cel_findings, crc = _run_json(cel_args)

    error = ""
    ok = True
    # non-zero rc from validate-manifest is expected when there are severity=error
    # findings; not a wrapper failure. Same for validate-cel.
    if mrc not in (0, 1) or crc not in (0, 1):
        ok = False
        error = f"validator rc mrc={mrc} crc={crc}"

    return TupleResult(
        mode=mode,
        rgd=rgd,
        manifest_findings=manifest_findings,
        cel_findings=cel_findings,
        duration_seconds=round(time.time() - start, 2),
        ok=ok,
        error=error,
    )


@click.command()
@click.option("--output", "output_dir",
              required=True,
              type=click.Path(exists=True, file_okay=False, path_type=Path),
              help="Root directory containing <mode>/<rgd>/ subtrees.")
@click.option("--modes", "modes_csv",
              default="adopt,create",
              show_default=True,
              help="Comma-separated modes to validate.")
@click.option("--context", default=None, help="Kubeconfig context.")
@click.option("--kubeconfig", default=None, help="Kubeconfig path.")
@click.option("--workers", default=8, show_default=True, type=int,
              help="Max concurrent tuples.")
@click.option("--format", "output_format",
              type=click.Choice(["text", "json"]),
              default="text",
              show_default=True)
def main(output_dir: Path, modes_csv: str, context: str | None, kubeconfig: str | None,
         workers: int, output_format: str) -> None:
    modes = [m.strip() for m in modes_csv.split(",") if m.strip()]
    tuples = discover_tuples(output_dir, modes)
    if not tuples:
        click.echo(f"no tuples found under {output_dir} for modes={modes}", err=True)
        sys.exit(2)

    if output_format == "text":
        click.echo(f"validating {len(tuples)} tuples across {len(modes)} modes with {workers} workers", err=True)

    started = time.time()
    results: list[TupleResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(validate_tuple, m, r, output_dir, context, kubeconfig)
                   for m, r in tuples]
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())

    total_elapsed = round(time.time() - started, 2)

    # Aggregate findings per mode; write mode-level findings.json for render-report.
    per_mode: dict[str, list] = {m: [] for m in modes}
    per_mode_cel: dict[str, list] = {m: [] for m in modes}
    for r in results:
        per_mode.setdefault(r.mode, []).extend(r.manifest_findings)
        per_mode_cel.setdefault(r.mode, []).extend(r.cel_findings)

    for mode in modes:
        combined = per_mode.get(mode, []) + per_mode_cel.get(mode, [])
        target = output_dir / mode / "findings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"findings": combined}, indent=2))

    # Any error-severity finding = overall non-zero exit.
    any_errors = any(
        any(f.get("severity") == "error" for f in r.manifest_findings + r.cel_findings)
        for r in results
    )

    if output_format == "json":
        payload = {
            "total_elapsed_seconds": total_elapsed,
            "results": [asdict(r) for r in results],
            "per_mode_findings_written": {m: str(output_dir / m / "findings.json") for m in modes},
        }
        click.echo(json.dumps(payload, indent=2))
    else:
        for r in sorted(results, key=lambda x: (x.mode, x.rgd)):
            m_err = sum(1 for f in r.manifest_findings if f.get("severity") == "error")
            m_warn = sum(1 for f in r.manifest_findings if f.get("severity") == "warning")
            c_err = sum(1 for f in r.cel_findings if f.get("severity") == "error")
            marker = "✓" if (m_err == 0 and c_err == 0) else "✗"
            click.echo(f"  {marker} {r.mode}/{r.rgd:20s} manifest_err={m_err} warn={m_warn} cel_err={c_err} {r.duration_seconds:.2f}s")
        click.echo(f"total wall-clock: {total_elapsed}s   ({len(tuples)} tuples, {workers} workers)")

    if any_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
