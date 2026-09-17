"""List ACK CRDs installed on a live Kubernetes cluster.

Sole source of truth for what mappings the target cluster supports. No static
registry is bundled — running this against a cluster with no ACK controllers
installed yields an empty list, and the skill treats every TF resource as
unsupported for that cluster.

Usage:
    python3 crd_inventory.py [--kubeconfig PATH] [--context CTX]
                             [--filter services.k8s.aws] [--out FILE]

The filter is a substring match on `metadata.name`. Default matches every ACK
controller CRD (all live under *.services.k8s.aws).
"""

from __future__ import annotations

import json
import sys

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


def list_crds(name_filter: str) -> list[dict]:
    api = client.ApiextensionsV1Api()
    crds = api.list_custom_resource_definition()

    out: list[dict] = []
    for crd in crds.items:
        name = crd.metadata.name
        if name_filter and name_filter not in name:
            continue

        versions = []
        for v in crd.spec.versions or []:
            versions.append(
                {
                    "name": v.name,
                    "served": bool(v.served),
                    "storage": bool(v.storage),
                    "has_schema": bool(v.schema and v.schema.open_apiv3_schema),
                }
            )

        short_names = list(crd.spec.names.short_names or [])
        categories = list(crd.spec.names.categories or [])

        out.append(
            {
                "name": name,
                "group": crd.spec.group,
                "kind": crd.spec.names.kind,
                "plural": crd.spec.names.plural,
                "singular": crd.spec.names.singular,
                "short_names": short_names,
                "categories": categories,
                "scope": crd.spec.scope,
                "versions": versions,
            }
        )

    out.sort(key=lambda c: (c["group"], c["kind"]))
    return out


@click.command()
@click.option("--kubeconfig", default=None, help="Path to kubeconfig file.")
@click.option("--context", default=None, help="Kubeconfig context to use.")
@click.option(
    "--filter",
    "name_filter",
    default="services.k8s.aws",
    show_default=True,
    help='Substring match on CRD name. Empty string = all CRDs.',
)
@click.option(
    "--out",
    default="-",
    show_default=True,
    help="Write JSON to this path. '-' means stdout.",
)
def main(kubeconfig: str | None, context: str | None, name_filter: str, out: str) -> None:
    load_kubeconfig(kubeconfig, context)
    inventory = list_crds(name_filter)
    payload = json.dumps(inventory, indent=2)
    if out == "-":
        sys.stdout.write(payload + "\n")
    else:
        with open(out, "w", encoding="utf-8") as f:
            f.write(payload + "\n")


if __name__ == "__main__":
    main()
