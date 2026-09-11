"""Parse Terraform state + HCL into a single migrate-set.json for the skill.

Contract:
    - Input:  a Terraform source tree (--tf-path) with .tf files, optionally a
              tfstate file (--state), and optionally a kubeconfig to a target
              EKS cluster (--kubeconfig).
    - Output: JSON document with source metadata, live-cluster CRD inventory
              (empty when --kubeconfig is not supplied), managed resources with
              attributes and sensitive-field redaction, HCL variable/output/
              locals/data blocks, and a cross-reference graph derived from
              state `dependencies` and HCL interpolations.

Zero static TF→ACK mapping is applied here. The skill decides mappings by
reasoning about the emitted `cluster.ack_crds` list vs. resource types.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import click
import hcl2

from crd_inventory import list_crds, load_kubeconfig


# --- state parsing --------------------------------------------------------


SENSITIVE_NAME_PATTERNS = (
    "password",
    "secret",
    "token",
    "private_key",
    "privatekey",
    "api_key",
    "apikey",
    "credentials",
    "passphrase",
)

PROVIDER_RE = re.compile(r'^provider\["(?P<qualified>[^"]+)"\]$')


def short_provider_from_state(qualified: str) -> str:
    m = PROVIDER_RE.match(qualified)
    payload = m.group("qualified") if m else qualified
    return payload.rsplit("/", 1)[-1] if "/" in payload else payload


def classify_provider(resource_type: str) -> str:
    if resource_type == "null_resource":
        return "null"
    if resource_type == "terraform_data":
        return "terraform"
    return resource_type.split("_", 1)[0] if "_" in resource_type else resource_type


def build_address(module: str, mode: str, res_type: str, name: str, idx: int, key: Any, n_instances: int) -> str:
    prefix = f"{module}." if module else ""
    if mode == "data":
        prefix += "data."
    base = f"{prefix}{res_type}.{name}"
    if key is None:
        if n_instances > 1:
            return f"{base}[{idx}]"
        return base
    if isinstance(key, str):
        return f'{base}["{key}"]'
    if isinstance(key, (int, float)):
        return f"{base}[{int(key)}]"
    return f"{base}[{key}]"


def sensitive_fields(attrs: dict, raw_sensitive: Any) -> list[str]:
    seen: set[str] = set()

    for k in attrs.keys():
        lower = k.lower()
        if any(p in lower for p in SENSITIVE_NAME_PATTERNS):
            seen.add(k)

    if isinstance(raw_sensitive, list):
        for entry in raw_sensitive:
            if isinstance(entry, str):
                seen.add(entry)
            elif isinstance(entry, dict) and isinstance(entry.get("value"), str):
                seen.add(entry["value"])

    return sorted(seen)


def redact_attributes(attrs: dict, sensitive: list[str]) -> dict:
    if not sensitive:
        return attrs
    sset = set(sensitive)
    return {k: ("***REDACTED***" if k in sset else v) for k, v in attrs.items()}


def extract_identity(attrs: dict) -> dict:
    idty: dict[str, str] = {}
    for key in ("arn", "id", "name", "region"):
        v = attrs.get(key)
        if isinstance(v, str) and v:
            idty[{"id": "resource_id"}.get(key, key)] = v
    return idty


def parse_state(state_path: Path) -> tuple[list[dict], dict]:
    """Return (resources_list, dependencies_by_address)."""
    with state_path.open() as f:
        st = json.load(f)

    if st.get("version", 0) < 4:
        raise SystemExit(f"state {state_path}: unsupported version {st.get('version')} (need >=4)")

    out_resources: list[dict] = []
    deps: dict[str, list[str]] = {}

    for sr in st.get("resources", []):
        if sr.get("mode") != "managed":
            continue

        module = sr.get("module", "") or ""
        res_type = sr["type"]
        name = sr["name"]
        qualified = sr.get("provider", "")
        provider = short_provider_from_state(qualified) or classify_provider(res_type)

        instances = sr.get("instances", [])
        for i, inst in enumerate(instances):
            attrs = inst.get("attributes", {}) or {}
            raw_sensitive = inst.get("sensitive_attributes")
            key = inst.get("index_key")
            addr = build_address(module, sr.get("mode", "managed"), res_type, name, i, key, len(instances))
            sens = sensitive_fields(attrs, raw_sensitive)
            redacted = redact_attributes(attrs, sens)

            out_resources.append(
                {
                    "address": addr,
                    "type": res_type,
                    "provider": provider,
                    "module": module,
                    "identity": extract_identity(attrs),
                    "attributes": redacted,
                    "sensitive_fields": sens,
                }
            )

            dep_list = inst.get("dependencies") or []
            deps[addr] = list(dep_list)

    return out_resources, deps


# --- HCL parsing ----------------------------------------------------------


HCL_REF_RE = re.compile(r"\$\{([^}]+)\}")


def _unquote_key(k: Any) -> Any:
    """python-hcl2 renders block labels as JSON-quoted strings.

    Example: `resource "aws_vpc" "this"` produces top-level key `'"aws_vpc"'`
    with inner key `'"this"'`. We strip the surrounding double quotes so
    downstream code sees clean identifiers.
    """
    if isinstance(k, str) and len(k) >= 2 and k[0] == '"' and k[-1] == '"':
        return k[1:-1]
    return k


def _norm_hcl_value(v: Any) -> Any:
    """python-hcl2 yields lists of length-1 for single-value blocks; unwrap them.

    Also un-quotes block labels — see `_unquote_key`.
    """
    if isinstance(v, list) and len(v) == 1:
        return _norm_hcl_value(v[0])
    if isinstance(v, dict):
        return {_unquote_key(k): _norm_hcl_value(vv) for k, vv in v.items()}
    if isinstance(v, list):
        return [_norm_hcl_value(x) for x in v]
    return v


def parse_hcl_tree(tf_root: Path) -> dict:
    """Walk *.tf files under tf_root; return {variables, outputs, locals, data, resources}."""
    variables: list[dict] = []
    outputs: list[dict] = []
    locals_map: dict[str, Any] = {}
    data_blocks: list[dict] = []
    resources: list[dict] = []

    for path in sorted(tf_root.rglob("*.tf")):
        # Skip hidden dirs and Terraform cache.
        parts = path.relative_to(tf_root).parts
        if any(p.startswith(".") for p in parts):
            continue

        try:
            with path.open() as f:
                doc = hcl2.load(f)
        except Exception:
            # Non-fatal: complex expressions can trip python-hcl2; the skill
            # can reach for the raw file if it needs a block we missed.
            continue

        rel = str(path.relative_to(tf_root))

        for block in doc.get("variable", []):
            for varname, body in block.items():
                body = _norm_hcl_value(body) or {}
                variables.append(
                    {
                        "name": _unquote_key(varname),
                        "type": _stringify_type(body.get("type")),
                        "default": body.get("default"),
                        "description": body.get("description", ""),
                        "sensitive": bool(body.get("sensitive", False)),
                        "file": rel,
                    }
                )

        for block in doc.get("output", []):
            for outname, body in block.items():
                body = _norm_hcl_value(body) or {}
                outputs.append(
                    {
                        "name": _unquote_key(outname),
                        "value_expr": _stringify_expr(body.get("value")),
                        "description": body.get("description", ""),
                        "sensitive": bool(body.get("sensitive", False)),
                        "file": rel,
                    }
                )

        for block in doc.get("locals", []):
            for k, v in (_norm_hcl_value(block) or {}).items():
                locals_map[_unquote_key(k)] = _stringify_expr(v)

        for block in doc.get("data", []):
            for data_type, name_body in block.items():
                for name, body in name_body.items():
                    body = _norm_hcl_value(body) or {}
                    data_blocks.append(
                        {
                            "type": _unquote_key(data_type),
                            "name": _unquote_key(name),
                            "config": body,
                            "file": rel,
                        }
                    )

        for block in doc.get("resource", []):
            for res_type, name_body in block.items():
                for name, body in name_body.items():
                    body = _norm_hcl_value(body) or {}
                    resources.append(
                        {
                            "type": _unquote_key(res_type),
                            "name": _unquote_key(name),
                            "config": body,
                            "file": rel,
                            "references": _collect_refs(body),
                        }
                    )

    return {
        "variables": variables,
        "outputs": outputs,
        "locals": locals_map,
        "data": data_blocks,
        "resources": resources,
    }


def _stringify_type(v: Any) -> str:
    if v is None:
        return "string"
    if isinstance(v, str):
        # python-hcl2 wraps `type = list(string)` as "${list(string)}"
        return HCL_REF_RE.sub(lambda m: m.group(1), v)
    return json.dumps(v)


def _stringify_expr(v: Any) -> Any:
    """Return the raw expression text for HCL values that look like references."""
    if isinstance(v, str):
        return v
    return v


def _collect_refs(body: Any, out: list[str] | None = None) -> list[str]:
    """Extract `${resource.type.name.field}` references from a nested body."""
    if out is None:
        out = []
    if isinstance(body, str):
        for m in HCL_REF_RE.finditer(body):
            out.append(m.group(1).strip())
    elif isinstance(body, dict):
        for v in body.values():
            _collect_refs(v, out)
    elif isinstance(body, list):
        for v in body:
            _collect_refs(v, out)
    return sorted(set(out))


# --- cross-references -----------------------------------------------------


def cross_refs_from_state(state_deps: dict[str, list[str]]) -> list[dict]:
    out: list[dict] = []
    for addr, deps in state_deps.items():
        for dep in deps:
            out.append({"from": addr, "to": dep, "source": "state"})
    return out


def cross_refs_from_hcl(hcl_resources: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in hcl_resources:
        from_addr = f"{r['type']}.{r['name']}"
        for ref in r.get("references", []):
            # HCL refs like "aws_vpc.this.id" — first two segments identify the
            # target, remainder is the referenced field.
            parts = ref.split(".", 2)
            if len(parts) < 2:
                continue
            to_addr = f"{parts[0]}.{parts[1]}"
            field = parts[2] if len(parts) == 3 else ""
            if to_addr == from_addr:
                continue
            out.append(
                {
                    "from": from_addr,
                    "to": to_addr,
                    "to_field": field,
                    "source": "hcl",
                }
            )
    return out


# --- CLI ------------------------------------------------------------------


@click.command()
@click.option("--tf-path", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--state", type=click.Path(dir_okay=False, path_type=Path), help="Path to terraform.tfstate.")
@click.option("--kubeconfig", type=click.Path(dir_okay=False, path_type=Path), help="If given, populates cluster.ack_crds.")
@click.option("--context", default=None, help="Kubeconfig context.")
@click.option("--crd-filter", default="services.k8s.aws", show_default=True)
@click.option("--out", default="-", show_default=True, help="Output JSON path. '-' means stdout.")
def main(
    tf_path: Path,
    state: Path | None,
    kubeconfig: Path | None,
    context: str | None,
    crd_filter: str,
    out: str,
) -> None:
    # Resolve state path.
    state_path: Path | None = state
    if state_path is None:
        candidate = tf_path / "terraform.tfstate"
        if candidate.exists():
            state_path = candidate

    resources: list[dict] = []
    state_deps: dict[str, list[str]] = {}
    if state_path is not None:
        resources, state_deps = parse_state(state_path)

    hcl = parse_hcl_tree(tf_path)

    ack_crds: list[dict] = []
    cluster_meta: dict[str, Any] = {"context": None, "region": None, "ack_crds": []}
    if kubeconfig is not None:
        load_kubeconfig(str(kubeconfig), context)
        ack_crds = list_crds(crd_filter)
        cluster_meta = {
            "kubeconfig": str(kubeconfig),
            "context": context,
            "ack_crds": ack_crds,
        }

    payload = {
        "source": {
            "type": "tf",
            "path": str(tf_path.resolve()),
            "state_location": str(state_path.resolve()) if state_path else None,
        },
        "cluster": cluster_meta,
        "resources": resources,
        "hcl": hcl,
        "cross_refs": cross_refs_from_state(state_deps) + cross_refs_from_hcl(hcl["resources"]),
    }

    body = json.dumps(payload, indent=2, default=str)
    if out == "-":
        sys.stdout.write(body + "\n")
    else:
        with open(out, "w", encoding="utf-8") as f:
            f.write(body + "\n")


if __name__ == "__main__":
    main()
