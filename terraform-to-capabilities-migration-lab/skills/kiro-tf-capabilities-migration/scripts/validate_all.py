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
    spec_findings: list
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
    """Run `scripts/run …` returning (findings list, rc).

    A validator that produced no parseable JSON did not run. Turn that into a
    finding rather than an empty list: an empty list is indistinguishable from
    "clean" to every caller downstream, which is how a crashed validator used to
    be rendered as a passing tuple.
    """
    proc = subprocess.run(args, capture_output=True, text=True)
    try:
        parsed = json.loads(proc.stdout) if proc.stdout.strip() else {"findings": []}
    except json.JSONDecodeError:
        parsed = {"findings": []}

    findings = parsed.get("findings", [])

    # rc 0 = clean, rc 1 = ran and found severity=error. Anything else means the
    # validator (or the scripts/run wrapper, which exits 2 on a missing venv)
    # failed to execute.
    if proc.returncode not in (0, 1):
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        findings = findings + [
            {
                "severity": "error",
                "file": args[2] if len(args) > 2 else "?",
                "message": (
                    f"validator did not run: {' '.join(args[1:2])} exited {proc.returncode}"
                    + (f" — {detail[0][:300]}" if detail else " with no output")
                ),
            }
        ]
    return findings, proc.returncode


GATE_DIR_NAME = ".gates"
# Gate name -> the path whose mtime decides whether that gate is still valid.
GATE_SUBJECTS = {"manifest": "resources", "cel": "rgd.yaml", "spec": "."}


def _newest_mtime(path: Path) -> float:
    """Newest mtime under path (or of path itself). -inf when nothing is there."""
    if path.is_file():
        return path.stat().st_mtime
    if not path.is_dir():
        return float("-inf")
    times = [
        p.stat().st_mtime
        for ext in ("*.yaml", "*.yml")
        for p in path.rglob(ext)
        if GATE_DIR_NAME not in p.parts
    ]
    return max(times) if times else float("-inf")


def _read_cached_gate(rgd_dir: Path, gate: str) -> list | None:
    """Findings written by an inline per-agent gate, or None if unusable.

    Reuse is only sound while no artifact the gate covers has changed since it
    ran. A stale cache is silently discarded and the gate re-runs — the failure
    mode of reusing one is a validated-looking tuple that was never validated,
    which is exactly the class of defect this module was hardened against.
    """
    cached = rgd_dir / GATE_DIR_NAME / f"{gate}.json"
    if not cached.is_file():
        return None
    subject = rgd_dir / GATE_SUBJECTS[gate]
    if cached.stat().st_mtime < _newest_mtime(subject):
        return None
    try:
        payload = json.loads(cached.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    findings = payload.get("findings")
    return findings if isinstance(findings, list) else None


def validate_tuple(
    mode: str,
    rgd: str,
    output_dir: Path,
    context: str | None,
    kubeconfig: str | None,
    reuse_gates: bool = False,
) -> TupleResult:
    start = time.time()
    rgd_dir = output_dir / mode / rgd
    resources_dir = rgd_dir / "resources"
    rgd_file = rgd_dir / "rgd.yaml"
    reused: list[str] = []

    manifest_args = [RUN, "validate-manifest",
                     "--dir", str(resources_dir),
                     "--mode", mode,
                     "--format", "json"]
    if context:
        manifest_args += ["--context", context]
    if kubeconfig:
        manifest_args += ["--kubeconfig", kubeconfig]

    cel_args = [RUN, "validate-cel", str(rgd_file), "--format", "json"]

    # Spec-field existence: run over the whole <mode>/<rgd>/ subtree so it covers
    # both resources/*.yaml and the ACK templates nested in rgd.yaml.
    spec_args = [RUN, "validate-spec-fields", str(rgd_dir), "--format", "json"]
    if context:
        spec_args += ["--context", context]
    if kubeconfig:
        spec_args += ["--kubeconfig", kubeconfig]

    # rc 0 stands in for a reused gate: it ran, under the agent that wrote it.
    def _gate(name: str, args: list[str]) -> tuple[list, int]:
        if reuse_gates:
            hit = _read_cached_gate(rgd_dir, name)
            if hit is not None:
                reused.append(name)
                return hit, 1 if any(f.get("severity") == "error" for f in hit) else 0
        return _run_json(args)

    manifest_findings, mrc = _gate("manifest", manifest_args)
    cel_findings, crc = _gate("cel", cel_args)
    spec_findings, src = _gate("spec", spec_args)

    error = ""
    ok = True
    # non-zero rc from a validator is expected when there are severity=error
    # findings; only an unexpected rc means the wrapper itself failed.
    if mrc not in (0, 1) or crc not in (0, 1) or src not in (0, 1):
        ok = False
        error = f"validator rc mrc={mrc} crc={crc} src={src}"

    return TupleResult(
        mode=mode,
        rgd=rgd,
        manifest_findings=manifest_findings,
        cel_findings=cel_findings,
        spec_findings=spec_findings,
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
@click.option("--reuse-gates/--no-reuse-gates", default=False, show_default=True,
              help="Reuse findings an inline per-agent gate already wrote to "
                   "<mode>/<rgd>/.gates/{manifest,cel,spec}.json instead of re-running that "
                   "validator. A cache older than the artifacts it covers is ignored and the "
                   "validator runs anyway.")
@click.option("--format", "output_format",
              type=click.Choice(["text", "json"]),
              default="text",
              show_default=True)
def main(output_dir: Path, modes_csv: str, context: str | None, kubeconfig: str | None,
         workers: int, reuse_gates: bool, output_format: str) -> None:
    modes = [m.strip() for m in modes_csv.split(",") if m.strip()]
    tuples = discover_tuples(output_dir, modes)
    if not tuples:
        click.echo(f"no tuples found under {output_dir} for modes={modes}", err=True)
        sys.exit(2)

    if output_format == "text":
        click.echo(f"validating {len(tuples)} tuples across {len(modes)} modes with {workers} workers"
                   + (" (reusing fresh inline gates)" if reuse_gates else ""), err=True)

    started = time.time()
    results: list[TupleResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(validate_tuple, m, r, output_dir, context, kubeconfig, reuse_gates)
                   for m, r in tuples]
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())

    total_elapsed = round(time.time() - started, 2)

    # Aggregate findings per mode; write mode-level findings.json for render-report.
    # Only for modes that actually produced tuples: pre-seeding every requested
    # mode used to materialise an empty create/findings.json (and a create/
    # directory) on an adopt-only run, which the operator then had to rm.
    modes_with_tuples = sorted({m for m, _ in tuples})
    per_mode: dict[str, list] = {m: [] for m in modes_with_tuples}
    per_mode_cel: dict[str, list] = {m: [] for m in modes_with_tuples}
    per_mode_spec: dict[str, list] = {m: [] for m in modes_with_tuples}
    for r in results:
        per_mode.setdefault(r.mode, []).extend(r.manifest_findings)
        per_mode_cel.setdefault(r.mode, []).extend(r.cel_findings)
        per_mode_spec.setdefault(r.mode, []).extend(r.spec_findings)

    written: dict[str, str] = {}
    for mode in modes_with_tuples:
        combined = (
            per_mode.get(mode, [])
            + per_mode_cel.get(mode, [])
            + per_mode_spec.get(mode, [])
        )
        target = output_dir / mode / "findings.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"findings": combined}, indent=2))
        written[mode] = str(target)

    # Non-zero exit on EITHER a severity=error finding OR a tuple whose
    # validators did not run. The second half is the important one: a crashed
    # validator yields no findings, so severity alone reported a clean sweep.
    any_errors = any(
        any(
            f.get("severity") == "error"
            for f in r.manifest_findings + r.cel_findings + r.spec_findings
        )
        for r in results
    )
    broken = [r for r in results if not r.ok]

    if output_format == "json":
        payload = {
            "total_elapsed_seconds": total_elapsed,
            "results": [asdict(r) for r in results],
            "per_mode_findings_written": written,
            "tuples_with_broken_validators": [f"{r.mode}/{r.rgd}: {r.error}" for r in broken],
        }
        click.echo(json.dumps(payload, indent=2))
    else:
        for r in sorted(results, key=lambda x: (x.mode, x.rgd)):
            m_err = sum(1 for f in r.manifest_findings if f.get("severity") == "error")
            m_warn = sum(1 for f in r.manifest_findings if f.get("severity") == "warning")
            c_err = sum(1 for f in r.cel_findings if f.get("severity") == "error")
            s_err = sum(1 for f in r.spec_findings if f.get("severity") == "error")
            # A tuple whose validators did not run is never a pass.
            marker = "✓" if (r.ok and m_err == 0 and c_err == 0 and s_err == 0) else "✗"
            click.echo(f"  {marker} {r.mode}/{r.rgd:20s} manifest_err={m_err} warn={m_warn} cel_err={c_err} spec_err={s_err} {r.duration_seconds:.2f}s")
            if not r.ok:
                click.echo(f"      ! {r.error}", err=True)
        click.echo(f"total wall-clock: {total_elapsed}s   ({len(tuples)} tuples, {workers} workers)")
        if broken:
            click.echo(
                f"{len(broken)} tuple(s) had a validator that did not run — results are INCOMPLETE",
                err=True,
            )

    if any_errors or broken:
        sys.exit(1)


if __name__ == "__main__":
    main()
