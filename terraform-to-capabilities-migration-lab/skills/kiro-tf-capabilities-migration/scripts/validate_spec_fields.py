"""Validate that every spec field in generated ACK manifests exists in the live CRD.

Why this gate exists (neither existing validator catches it):

  * `validate-manifest` runs `kubectl apply --dry-run=server`, but the API server
    **silently prunes** unknown fields in a custom resource (structural schema
    pruning). The apply "succeeds" and the field is simply lost — no error.
  * `validate-cel` is a CEL *grammar* gate only; it does not know CRD schemas.
  * Inside a kro RGD template an unknown field does fail, but only later, at RGD
    creation, with `schema not found for field <name>`.

The recurring root cause is that AWS SDK/API parameters and Terraform lifecycle
arguments are NOT the same set as ACK CRD spec fields (e.g. `skipFinalSnapshot`,
`applyImmediately` are Terraform/API-only and absent from the DBInstance CRD).
Reading the AWS service docs is not evidence that a CRD field exists.

What it checks:
  * standalone ACK CRs (apiVersion containing `.services.k8s.aws/`)
  * ACK resource templates nested inside a kro ResourceGraphDefinition
    (`spec.resources[].template`)

Limitation: only TOP-LEVEL `spec` keys are checked. Nested field names
(`spec.endpoint.port`) are not walked.

Usage:
    scripts/run validate-spec-fields <dir-or-file> [--context CTX] [--format json]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

import click
import yaml

ACK_GROUP_MARKER = ".services.k8s.aws/"


# --- kubectl helpers -------------------------------------------------------


def kubectl(args: list[str], context: str | None, kubeconfig: str | None) -> str:
    env = os.environ.copy()
    if kubeconfig:
        env["KUBECONFIG"] = kubeconfig
    cmd = ["kubectl", *args]
    if context:
        cmd.extend(["--context", context])
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "kubectl failed")
    return proc.stdout


def load_crd_index(context: str | None, kubeconfig: str | None) -> dict[tuple[str, str], str]:
    """Map (group, kind) -> CRD name.

    The plural is not reliably derivable from the Kind (Policy -> policies,
    DBInstance -> dbinstances), so ask the cluster instead of guessing.
    """
    jsonpath = (
        "{range .items[*]}{.metadata.name}{'\\t'}{.spec.group}{'\\t'}"
        "{.spec.names.kind}{'\\n'}{end}"
    ).replace("'", '"')
    out = kubectl(["get", "crd", "-o", f"jsonpath={jsonpath}"], context, kubeconfig)
    index: dict[tuple[str, str], str] = {}
    for line in out.splitlines():
        parts = line.strip().split("\t")
        if len(parts) != 3:
            continue
        name, group, kind = parts
        index[(group, kind)] = name
    return index


def crd_spec_keys(crd_name: str, context: str | None, kubeconfig: str | None) -> set[str]:
    jsonpath = "{.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties}"
    out = kubectl(["get", "crd", crd_name, "-o", f"jsonpath={jsonpath}"], context, kubeconfig).strip()
    if not out:
        return set()
    return set(json.loads(out).keys())


# --- manifest walking -----------------------------------------------------


def iter_yaml_files(target: Path) -> Iterable[Path]:
    if target.is_file():
        yield target
        return
    for ext in ("*.yaml", "*.yml"):
        yield from sorted(target.rglob(ext))


def is_ack(api_version: str | None) -> bool:
    return bool(api_version) and ACK_GROUP_MARKER in api_version


def split_group(api_version: str) -> str:
    return api_version.split("/", 1)[0]


def collect_targets(doc: dict, file_str: str) -> list[dict]:
    """Return the ACK spec blocks to check from one YAML document.

    Handles both a standalone ACK CR and ACK templates nested in an RGD.
    """
    out: list[dict] = []

    if doc.get("kind") == "ResourceGraphDefinition":
        resources = ((doc.get("spec") or {}).get("resources")) or []
        if not isinstance(resources, list):
            return out
        for i, entry in enumerate(resources):
            if not isinstance(entry, dict):
                continue
            tmpl = entry.get("template")
            if not isinstance(tmpl, dict):
                continue
            if not is_ack(tmpl.get("apiVersion")):
                continue
            out.append(
                {
                    "file": file_str,
                    "path": f"spec.resources[{i}].template",
                    "apiVersion": tmpl.get("apiVersion"),
                    "kind": tmpl.get("kind"),
                    "name": ((tmpl.get("metadata") or {}).get("name")) or entry.get("id") or "?",
                    "spec": tmpl.get("spec"),
                }
            )
        return out

    if is_ack(doc.get("apiVersion")):
        out.append(
            {
                "file": file_str,
                "path": "spec",
                "apiVersion": doc.get("apiVersion"),
                "kind": doc.get("kind"),
                "name": ((doc.get("metadata") or {}).get("name")) or "?",
                "spec": doc.get("spec"),
            }
        )
    return out


# --- CLI ------------------------------------------------------------------


@click.command()
@click.argument("target", type=click.Path(exists=True, path_type=Path))
@click.option("--kubeconfig", default=None, help="Path to kubeconfig.")
@click.option("--context", default=None, help="Kubeconfig context.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
def main(target: Path, kubeconfig: str | None, context: str | None, output_format: str) -> None:
    findings: list[dict] = []

    # Gather every ACK spec block first so we only talk to the cluster if needed.
    targets: list[dict] = []
    for path in iter_yaml_files(target):
        file_str = str(path)
        try:
            with path.open() as f:
                docs = list(yaml.safe_load_all(f))
        except yaml.YAMLError as e:
            findings.append(
                {"severity": "error", "file": file_str, "message": f"YAML parse error: {e}"}
            )
            continue
        for doc in docs:
            if isinstance(doc, dict):
                targets.extend(collect_targets(doc, file_str))

    if not targets:
        _emit(findings, output_format)
        return

    try:
        crd_index = load_crd_index(context, kubeconfig)
    except Exception as e:  # cluster unreachable -> warn, do not fail the run
        findings.append(
            {
                "severity": "warning",
                "file": str(target),
                "message": (
                    "cannot reach the cluster to list CRDs, spec fields NOT verified: "
                    f"{e}"
                ),
            }
        )
        _emit(findings, output_format)
        return

    keys_cache: dict[str, set[str]] = {}

    for t in targets:
        spec = t.get("spec")
        if not isinstance(spec, dict) or not spec:
            continue  # empty spec (strict adopt) — nothing to check

        group = split_group(t["apiVersion"])
        crd_name = crd_index.get((group, t["kind"]))
        if not crd_name:
            findings.append(
                {
                    "severity": "warning",
                    "file": t["file"],
                    "apiVersion": t["apiVersion"],
                    "kind": t["kind"],
                    "name": t["name"],
                    "message": (
                        f"no CRD installed for {group}/{t['kind']} — spec fields not verified "
                        "(is the controller installed on this cluster?)"
                    ),
                }
            )
            continue

        if crd_name not in keys_cache:
            try:
                keys_cache[crd_name] = crd_spec_keys(crd_name, context, kubeconfig)
            except Exception as e:
                keys_cache[crd_name] = set()
                findings.append(
                    {
                        "severity": "warning",
                        "file": t["file"],
                        "kind": t["kind"],
                        "name": t["name"],
                        "message": f"could not read spec schema from CRD {crd_name}: {e}",
                    }
                )

        allowed = keys_cache[crd_name]
        if not allowed:
            continue

        for key in sorted(spec.keys()):
            if key not in allowed:
                findings.append(
                    {
                        "severity": "error",
                        "file": t["file"],
                        "path": f"{t['path']}.{key}",
                        "apiVersion": t["apiVersion"],
                        "kind": t["kind"],
                        "name": t["name"],
                        "field": key,
                        "message": (
                            f"spec field '{key}' does not exist in CRD {crd_name}. "
                            "Remove it and record it in MIGRATION-NOTES.md under "
                            "'Unsupported TF attributes'."
                        ),
                    }
                )

    _emit(findings, output_format)


def _emit(findings: list[dict], output_format: str) -> None:
    if output_format == "json":
        sys.stdout.write(json.dumps({"findings": findings}, indent=2) + "\n")
    else:
        for f in findings:
            sev = f["severity"].upper()
            loc = f.get("file", "?")
            k = f.get("kind", "?")
            n = f.get("name", "?")
            p = f.get("path")
            where = f" {p}" if p else ""
            print(f"[{sev}] {loc} {k}/{n}{where}: {f['message']}")

    if any(f["severity"] == "error" for f in findings):
        sys.exit(1)


if __name__ == "__main__":
    main()
