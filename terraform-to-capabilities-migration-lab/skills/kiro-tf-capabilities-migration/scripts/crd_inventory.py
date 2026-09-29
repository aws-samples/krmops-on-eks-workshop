"""Inventory the CRDs installed on the target cluster.

Sole source of truth for what mappings the target cluster supports. No static
registry is bundled — running this against a cluster with no ACK controllers
installed yields an empty list, and the skill treats every TF resource as
unsupported for that cluster.

With `--schema` it also returns, per Kind, the four facts the skill needs before
it writes a single line of YAML:

    required   — the OpenAPI `required` list on .spec
    immutable  — spec keys carrying x-kubernetes-validations (self == oldSelf)
    spec       — every spec property name
    status     — every status property name (for readyWhen)

Those are read from the STORAGE version, not versions[0]: the two coincide on
today's single-version ACK CRDs but not in general, and the storage version is
the one the API server persists against.

Usage:
    python3 crd_inventory.py [--kubeconfig PATH] [--context CTX]
                             [--filter services.k8s.aws] [--out FILE]
                             [--schema] [--name NAME ...] [--format text|json]
                             [--cache-dir DIR] [--refresh] [--max-age SECONDS]

The filter is a substring match on `metadata.name`. Default matches every ACK
controller CRD (all live under *.services.k8s.aws). `--name` restricts the
result to exactly those CRD names and is the cheap path once the skill knows
which Kinds it needs.

Idempotency: the result is cached per (API server, context, filter, name set,
schema flag). A second invocation with the same inputs inside --max-age is a
local file read and makes no API call, so re-running a phase costs nothing.
Pass --refresh to force a round trip.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import click
from kubernetes import client, config

# One migration run is minutes long and a cluster's CRD set does not change
# during it, so the cache only has to outlive a single run. An hour covers a
# run plus an operator re-reading the output, and is short enough that an
# operator who installs a controller mid-session is not stuck with stale data
# for long. --refresh is the explicit escape hatch.
DEFAULT_MAX_AGE_SECONDS = 3600
DEFAULT_CACHE_DIR = Path(
    os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
) / "kiro-tf-migration" / "crd-inventory"


def load_kubeconfig(path: str | None, context: str | None) -> None:
    if path:
        config.load_kube_config(config_file=path, context=context)
    else:
        try:
            config.load_kube_config(context=context)
        except config.ConfigException:
            config.load_incluster_config()


def _storage_version(crd) -> object | None:
    """The version the API server persists against.

    Falls back to the first served version, then to the first version at all,
    so a CRD with no storage flag set still yields a schema rather than None.
    """
    versions = crd.spec.versions or []
    for v in versions:
        if v.storage:
            return v
    for v in versions:
        if v.served:
            return v
    return versions[0] if versions else None


def _schema_digest(crd) -> dict:
    """required / immutable / spec / status for the storage version."""
    empty = {"version": None, "required": [], "immutable": [], "spec": [], "status": []}
    v = _storage_version(crd)
    if v is None or not v.schema or not v.schema.open_apiv3_schema:
        return empty

    root = v.schema.open_apiv3_schema
    props = getattr(root, "properties", None) or {}

    spec_schema = props.get("spec")
    spec_props = (getattr(spec_schema, "properties", None) or {}) if spec_schema else {}
    required = list(getattr(spec_schema, "required", None) or []) if spec_schema else []

    status_schema = props.get("status")
    status_props = (getattr(status_schema, "properties", None) or {}) if status_schema else {}

    immutable = [
        key
        for key, sub in spec_props.items()
        if getattr(sub, "x_kubernetes_validations", None)
    ]

    return {
        "version": v.name,
        "required": sorted(required),
        "immutable": sorted(immutable),
        "spec": sorted(spec_props.keys()),
        "status": sorted(status_props.keys()),
    }


def list_crds(
    name_filter: str,
    include_schema: bool = False,
    names: set[str] | None = None,
) -> list[dict]:
    api = client.ApiextensionsV1Api()
    crds = api.list_custom_resource_definition()

    out: list[dict] = []
    for crd in crds.items:
        name = crd.metadata.name
        if names is not None:
            if name not in names:
                continue
        elif name_filter and name_filter not in name:
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

        entry = {
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
        if include_schema:
            entry["schema"] = _schema_digest(crd)
        out.append(entry)

    out.sort(key=lambda c: (c["group"], c["kind"]))
    return out


# --- cache ----------------------------------------------------------------


def _api_host() -> str:
    try:
        return client.Configuration.get_default_copy().host or "?"
    except Exception:
        return "?"


def _cache_key(name_filter: str, include_schema: bool, names: set[str] | None,
               context: str | None) -> str:
    material = json.dumps(
        {
            "host": _api_host(),
            "context": context,
            "filter": name_filter,
            "names": sorted(names) if names else None,
            "schema": include_schema,
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def _cache_read(path: Path, max_age: float) -> list[dict] | None:
    if max_age <= 0 or not path.exists():
        return None
    if time.time() - path.stat().st_mtime > max_age:
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        # A corrupt cache entry must never be fatal and must never be silent:
        # drop it and fall through to a live read.
        click.echo(f"cache entry unreadable, refetching: {path}", err=True)
        return None


def _cache_write(path: Path, payload: list[dict]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(path)  # atomic: a concurrent reader never sees a half file
    except OSError as e:
        click.echo(f"could not write cache ({e}); continuing without it", err=True)


# --- rendering ------------------------------------------------------------


def render_text(inventory: list[dict]) -> str:
    """The per-Kind summary the skill reads before authoring.

    Replaces the shell loop + python heredoc this command was extracted from.
    """
    lines: list[str] = []
    for c in inventory:
        lines.append(f"== {c['kind']}  ({c['name']})")
        lines.append(f"   plural   : {c['plural']}")
        s = c.get("schema")
        if s is None:
            served = ",".join(v["name"] for v in c["versions"] if v["served"])
            lines.append(f"   versions : {served}   (re-run with --schema for fields)")
            continue
        lines.append(f"   version  : {s['version']}")
        lines.append(f"   required : {s['required']}")
        lines.append(f"   immutable: {s['immutable']}")
        lines.append(f"   spec     : {s['spec']}")
        lines.append(f"   status   : {s['status']}")
    if not lines:
        lines.append("(no CRDs matched)")
    return "\n".join(lines) + "\n"


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
    "--name",
    "names",
    multiple=True,
    help="Restrict to this exact CRD name. Repeatable. Overrides --filter.",
)
@click.option(
    "--schema",
    "include_schema",
    is_flag=True,
    default=False,
    help="Also emit required / immutable / spec / status per Kind (storage version).",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["json", "text"]),
    default="json",
    show_default=True,
    help="text = the per-Kind authoring summary; json = the full inventory.",
)
@click.option(
    "--out",
    default="-",
    show_default=True,
    help="Write output to this path. '-' means stdout.",
)
@click.option(
    "--cache-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=DEFAULT_CACHE_DIR,
    show_default=True,
    help="Where to cache inventories between invocations.",
)
@click.option(
    "--max-age",
    default=DEFAULT_MAX_AGE_SECONDS,
    show_default=True,
    type=float,
    help="Reuse a cached inventory younger than this (seconds). 0 disables the cache.",
)
@click.option("--refresh", is_flag=True, default=False, help="Ignore any cached inventory.")
def main(
    kubeconfig: str | None,
    context: str | None,
    name_filter: str,
    names: tuple[str, ...],
    include_schema: bool,
    output_format: str,
    out: str,
    cache_dir: Path,
    max_age: float,
    refresh: bool,
) -> None:
    load_kubeconfig(kubeconfig, context)

    name_set = set(names) if names else None
    cache_path = cache_dir / f"{_cache_key(name_filter, include_schema, name_set, context)}.json"

    inventory = None if refresh else _cache_read(cache_path, max_age)
    source = "cache"
    if inventory is None:
        inventory = list_crds(name_filter, include_schema=include_schema, names=name_set)
        source = "cluster"
        if max_age > 0:
            _cache_write(cache_path, inventory)

    if name_set:
        missing = sorted(name_set - {c["name"] for c in inventory})
        if missing:
            # Not an error: an absent CRD is a classification answer ("this
            # controller is not installed"). But it must be stated, or the
            # caller cannot tell "absent" from "I forgot to ask".
            click.echo(
                "not installed on this cluster: " + ", ".join(missing),
                err=True,
            )

    click.echo(f"{len(inventory)} CRDs from {source}", err=True)

    payload = render_text(inventory) if output_format == "text" else json.dumps(inventory, indent=2) + "\n"
    if out == "-":
        sys.stdout.write(payload)
    else:
        with open(out, "w", encoding="utf-8") as f:
            f.write(payload)


if __name__ == "__main__":
    main()
