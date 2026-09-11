"""Validate ACK/KRO manifests against a live cluster.

Two layers:
  1. kubectl apply --dry-run=server per file — the CRD schema check that
     grounds LLM-authored YAML against real controllers installed on the
     target cluster. No local schema copies.
  2. Adoption-annotation invariants — enforced structurally against the
     SKILL.md router rules:
       - adopt CRs MUST carry services.k8s.aws/adoption-policy AND
         services.k8s.aws/deletion-policy: retain
       - create CRs MUST NOT carry any services.k8s.aws/adoption-* or
         deletion-policy annotation
     Mode is inferred per-file from the presence of adoption annotations,
     or forced via --mode.

Findings are emitted as newline-delimited JSON when --format json.
Exit code is non-zero if any finding has severity=error.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

import click
import yaml


ADOPT_POLICY = "services.k8s.aws/adoption-policy"
ADOPT_FIELDS = "services.k8s.aws/adoption-fields"
DELETION_POLICY = "services.k8s.aws/deletion-policy"


def load_docs(path: Path) -> list[dict]:
    with path.open() as f:
        return [d for d in yaml.safe_load_all(f) if isinstance(d, dict)]


def is_adopt_doc(doc: dict) -> bool:
    annos = ((doc.get("metadata") or {}).get("annotations") or {})
    return ADOPT_POLICY in annos


def is_ack_cr(doc: dict) -> bool:
    """Adoption invariants only apply to ACK Custom Resources.

    KRO ResourceGraphDefinitions, Instance CRs, and native k8s objects are
    out of scope for annotation checks.
    """
    api = (doc.get("apiVersion") or "")
    return ".services.k8s.aws/" in api


def annotation_findings(path: Path, doc: dict, mode: str) -> list[dict]:
    kind = doc.get("kind", "?")
    name = ((doc.get("metadata") or {}).get("name")) or "?"
    api_version = doc.get("apiVersion", "?")
    annos = ((doc.get("metadata") or {}).get("annotations") or {})

    findings: list[dict] = []

    def add(severity: str, msg: str) -> None:
        findings.append(
            {
                "severity": severity,
                "file": str(path),
                "apiVersion": api_version,
                "kind": kind,
                "name": name,
                "message": msg,
            }
        )

    # Non-ACK docs (KRO RGDs, native k8s objects, instance CRs) skip annotation
    # checks entirely — the invariants below are ACK-specific.
    if not is_ack_cr(doc):
        return findings

    inferred_mode = mode or ("adopt" if is_adopt_doc(doc) else "create")

    if inferred_mode == "adopt":
        # ACK adoption-policy value determines whether spec is empty (strict
        # adopt) or fully populated (adopt-or-create). Two policies, two rules.
        policy_value = annos.get(ADOPT_POLICY)
        if ADOPT_POLICY not in annos:
            add("error", f"adopt CR missing {ADOPT_POLICY}")
        elif policy_value not in ("adopt", "adopt-or-create"):
            add(
                "error",
                f"adopt CR has unexpected {ADOPT_POLICY}={policy_value!r} (want 'adopt' or 'adopt-or-create')",
            )
        if annos.get(DELETION_POLICY) != "retain":
            add(
                "error",
                f"adopt CR missing/incorrect {DELETION_POLICY}: retain (found {annos.get(DELETION_POLICY)!r})",
            )
        if ADOPT_FIELDS not in annos:
            add("warning", f"adopt CR without {ADOPT_FIELDS} — lookup fields will be empty")

        # adoption-policy=adopt: ideal spec is {} — ACK reads live state.
        #   In practice many CRDs still require the OpenAPI-required subset;
        #   SKILL.md guidance is to populate ONLY those required fields.
        #   We warn (not error) so the operator can compare each spec against
        #   the CRD schema at Phase_Checkpoint.
        # adoption-policy=adopt-or-create: spec MUST be fully populated
        #   (ACK creates from it if the resource is missing).
        spec = doc.get("spec")
        spec_is_empty = spec is None or (isinstance(spec, dict) and not spec)
        if policy_value == "adopt" and not spec_is_empty:
            add(
                "warning",
                "adoption-policy=adopt ideally uses spec: {} (ACK populates from live state). "
                "Populated spec is acceptable ONLY to satisfy CRD OpenAPI required fields — "
                "verify each field is CRD-required, not optional.",
            )
        if policy_value == "adopt-or-create" and spec_is_empty:
            add(
                "error",
                "adoption-policy=adopt-or-create requires a fully populated spec "
                "(ACK reconciles toward it, or creates from it if absent).",
            )
    else:  # create
        for a in (ADOPT_POLICY, ADOPT_FIELDS, DELETION_POLICY):
            if a in annos:
                add("error", f"create CR must not carry adoption annotation {a}")

    return findings


_ERR_HEADER_RE = re.compile(r'^(?:The\s+([A-Za-z0-9]+)\s+"([^"]+)"\s+is invalid:|error validating "([^"]+)":)')


def kubectl_dry_run(path: Path, kubeconfig: str | None, context: str | None) -> list[dict]:
    cmd = ["kubectl", "apply", "--dry-run=server", "-o", "json", "-f", str(path)]
    env = os.environ.copy()
    if kubeconfig:
        env["KUBECONFIG"] = kubeconfig
    if context:
        cmd.extend(["--context", context])

    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.returncode == 0:
        return []

    stderr = proc.stderr.strip() or proc.stdout.strip()
    lines = [ln for ln in stderr.splitlines() if ln.strip()]
    if not lines:
        lines = [stderr or "kubectl apply --dry-run failed with no stderr"]

    findings: list[dict] = []
    current_kind = "?"
    current_name = "?"
    for ln in lines:
        header = _ERR_HEADER_RE.match(ln)
        if header:
            # `The <Kind> "<name>" is invalid:` header — start of a new group.
            if header.group(1):
                current_kind = header.group(1)
                current_name = header.group(2)
            findings.append(
                {"severity": "error", "file": str(path), "kind": current_kind, "name": current_name, "message": ln}
            )
            continue
        findings.append(
            {"severity": "error", "file": str(path), "kind": current_kind, "name": current_name, "message": ln}
        )
    return findings


def iter_manifests(dir_or_file: Path) -> Iterable[Path]:
    if dir_or_file.is_file():
        yield dir_or_file
        return
    for ext in ("*.yaml", "*.yml"):
        yield from sorted(dir_or_file.rglob(ext))


@click.command()
@click.option(
    "--dir",
    "manifest_dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Directory of YAML manifests. Mutually exclusive with --manifest.",
)
@click.option(
    "--manifest",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Single manifest file. Mutually exclusive with --dir.",
)
@click.option("--kubeconfig", default=None, help="Path to kubeconfig.")
@click.option("--context", default=None, help="Kubeconfig context.")
@click.option(
    "--mode",
    type=click.Choice(["adopt", "create", "auto"]),
    default="auto",
    show_default=True,
    help="Force annotation invariants for a mode. 'auto' infers per file.",
)
@click.option(
    "--skip-dry-run",
    is_flag=True,
    default=False,
    help="Only run structural annotation checks; do not shell out to kubectl.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
def main(
    manifest_dir: Path | None,
    manifest: Path | None,
    kubeconfig: str | None,
    context: str | None,
    mode: str,
    skip_dry_run: bool,
    output_format: str,
) -> None:
    if bool(manifest_dir) == bool(manifest):
        raise click.UsageError("provide exactly one of --dir or --manifest")

    target: Path = manifest_dir if manifest_dir else manifest  # type: ignore[assignment]
    findings: list[dict] = []

    for path in iter_manifests(target):
        try:
            docs = load_docs(path)
        except yaml.YAMLError as e:
            findings.append(
                {"severity": "error", "file": str(path), "message": f"YAML parse error: {e}"}
            )
            continue

        for doc in docs:
            annotation_mode = "" if mode == "auto" else mode
            findings.extend(annotation_findings(path, doc, annotation_mode))

        if not skip_dry_run:
            findings.extend(kubectl_dry_run(path, kubeconfig, context))

    if output_format == "json":
        sys.stdout.write(json.dumps({"findings": findings}, indent=2) + "\n")
    else:
        for f in findings:
            loc = f.get("file", "?")
            sev = f["severity"].upper()
            k = f.get("kind", "?")
            n = f.get("name", "?")
            print(f"[{sev}] {loc} {k}/{n}: {f['message']}")

    if any(f["severity"] == "error" for f in findings):
        sys.exit(1)


if __name__ == "__main__":
    main()
