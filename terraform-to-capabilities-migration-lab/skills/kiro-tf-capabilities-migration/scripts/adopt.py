"""Adoption verification: apply ACK CRs, then poll each until ACK.ResourceSynced=True.

Consumes a decisions.json (produced by the skill) with an `adopted_resources`
list mapping TF addresses to the ACK CR (group/version/kind/namespace/name)
that adopts them. Applies the CRs (unless --skip-apply) and polls each until
ACK.ResourceSynced=True or timeout, reporting which resources are confirmed
adopted by ACK.

This tool NEVER modifies Terraform state (Key Principle 7). The `terraform.tfstate`
is deliberately left untouched so it remains a valid rollback backup: Terraform
and ACK co-manage the live resources during and after the migration, and TF can
resume management at any time. Nothing here runs `terraform state rm`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import click
from kubernetes import client, config


def load_kubeconfig(path: str | None, context: str | None) -> None:
    if path:
        config.load_kube_config(config_file=path, context=context)
    else:
        try:
            config.load_kube_config(context=context)
        except config.ConfigException:
            config.load_incluster_config()


def get_cr(group: str, version: str, plural: str, namespace: str, name: str) -> dict | None:
    api = client.CustomObjectsApi()
    try:
        return api.get_namespaced_custom_object(
            group=group, version=version, namespace=namespace, plural=plural, name=name
        )
    except client.rest.ApiException as e:
        if e.status == 404:
            return None
        raise


def is_synced(cr: dict) -> tuple[bool, str]:
    """ACK reports ACK.ResourceSynced=True on status.conditions."""
    status = (cr or {}).get("status") or {}
    for cond in status.get("conditions", []) or []:
        if cond.get("type") == "ACK.ResourceSynced":
            return cond.get("status") == "True", cond.get("message", "")
    return False, "no ACK.ResourceSynced condition"


def kubectl_apply(manifest: Path, kubeconfig: str | None, context: str | None, logger) -> bool:
    env = os.environ.copy()
    if kubeconfig:
        env["KUBECONFIG"] = kubeconfig
    cmd = ["kubectl", "apply", "-f", str(manifest)]
    if context:
        cmd.extend(["--context", context])
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if proc.returncode == 0:
        logger(f"  applied {manifest.name}")
        return True
    logger(f"  ✗ kubectl apply {manifest}: {proc.stderr.strip() or proc.stdout.strip()}")
    return False


@click.command()
@click.option("--decisions", "decisions_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--manifests-dir", type=click.Path(exists=True, file_okay=False, path_type=Path), default=None)
@click.option("--kubeconfig", default=None)
@click.option("--context", default=None)
@click.option("--skip-apply", is_flag=True, default=False, help="Assume CRs are already applied; only poll for ACK.ResourceSynced=True.")
@click.option("--poll-interval", default=10.0, show_default=True, type=float, help="Seconds between status checks.")
@click.option("--timeout", default=1800.0, show_default=True, type=float, help="Per-resource timeout in seconds.")
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), default=None, help="Write result JSON here.")
def main(
    decisions_path: Path,
    manifests_dir: Path | None,
    kubeconfig: str | None,
    context: str | None,
    skip_apply: bool,
    poll_interval: float,
    timeout: float,
    out: Path | None,
) -> None:
    def log(msg: str) -> None:
        print(msg, flush=True)

    decisions = json.loads(decisions_path.read_text())
    items: list[dict] = decisions.get("adopted_resources") or []
    if not items:
        log("no `adopted_resources` in decisions.json — nothing to do")
        return

    load_kubeconfig(kubeconfig, context)

    if not skip_apply:
        if manifests_dir is None:
            raise click.UsageError("--manifests-dir is required unless --skip-apply is set")
        for path in sorted(manifests_dir.rglob("*.yaml")):
            kubectl_apply(path, kubeconfig, context, log)

    results: list[dict[str, Any]] = []
    for item in items:
        address = item["address"]
        group = item["group"]
        version = item["version"]
        plural = item["plural"]
        namespace = item.get("namespace", "default")
        name = item["name"]
        log(f"verify {address} → {group}/{version} {plural}/{name} in {namespace}")

        deadline = time.time() + timeout
        synced = False
        message = ""
        while time.time() < deadline:
            cr = get_cr(group, version, plural, namespace, name)
            if cr is None:
                message = "CR not found in cluster"
            else:
                synced, message = is_synced(cr)
                if synced:
                    break
            time.sleep(poll_interval)

        if synced:
            log(f"  ✓ {address} adopted (ACK.ResourceSynced=True) — TF state left untouched (backup)")
        else:
            log(f"  ✗ {address} not synced: {message}")

        results.append(
            {
                "address": address,
                "cr": {
                    "group": group,
                    "version": version,
                    "kind": item.get("kind"),
                    "namespace": namespace,
                    "name": name,
                },
                "synced": synced,
                "message": message,
            }
        )

    payload = {"results": results, "tfstate_modified": False}
    if out:
        out.write_text(json.dumps(payload, indent=2))
    else:
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")

    if any(not r["synced"] for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
