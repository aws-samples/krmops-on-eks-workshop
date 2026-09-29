#!/usr/bin/env python3
"""Emit the derived half of the shared interface file handed to authoring subagents.

SKILL.md § Single-RGD fan-out requires ONE file that fixes the interface before the
fan-out is spawned, because agents that pick divergent schema field names produce an RGD
that does not typecheck. That file worked — it drove a 6-agent fan-out to zero schema
drift — but ~12 of its ~16 KB were transcription from JSON the skill already held, emitted
serially on the main thread while every agent waited.

This script emits the transcribed part. The skill appends only its DECISIONS:

    ./scripts/run emit-interface \
        --migrate-set /tmp/migrate-set.json \
        --crds /tmp/crds.json \
        --out /tmp/shared-interface.md
    # then append: schema field names, resource ids, literal-vs-CEL assignments,
    # dependency edges, run parameters

Sections emitted here map to SKILL.md's (d)(e)(g) plus the tag-shape exceptions:
  - per-resource tfstate values, with sensitive fields already redacted by discover
  - live-CRD required / immutable / absent-field lists per Kind
  - the verified TF-type -> ACK Kind mapping table, with adoption-fields keys
  - known spec-shape exceptions (e.g. PodIdentityAssociation.spec.tags is a map, not a list)

What it deliberately does NOT emit: anything that is a decision rather than a fact.
Schema field names, resource ids and the literal-vs-CEL split are judgement calls made once
by the skill; generating a guess here would put a second source of truth in front of the
agents, which is the failure this file exists to prevent.

--crds is optional. Without it the CRD section is replaced by an explicit NOT AVAILABLE
marker rather than being silently omitted: an agent that cannot see the required-field list
must know it is missing, not infer that there are no required fields.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click


CONTRACT = Path(__file__).resolve().parent.parent / "references" / "authoring-contract.json"

# Spec shapes that differ from the obvious ACK convention. Agents get these wrong in a way
# no validator catches until kro's type check, because both forms are valid YAML.
SPEC_SHAPE_EXCEPTIONS = {
    "PodIdentityAssociation": "spec.tags is a MAP (key: value), not the ACK-usual list of "
                              "{key, value} objects.",
    "Secret": "spec.name is immutable; keep it a literal, never a CEL expression.",
    "DBInstance": "spec.dbInstanceIdentifier and spec.availabilityZone are immutable; keep "
                  "them literals.",
}


def _load(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception as e:
        raise click.ClickException(f"cannot read {label} ({path}): {type(e).__name__}: {e}")


def _fmt_value(v: Any, limit: int = 120) -> str:
    """One-line rendering suitable for a markdown table cell."""
    if isinstance(v, (dict, list)):
        s = json.dumps(v, separators=(",", ":"), default=str)
    else:
        s = str(v)
    s = s.replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def section_resources(migrate_set: dict, address_filter: set[str] | None) -> list[str]:
    """Per-resource tfstate values. This is (g) in SKILL.md's list."""
    out = ["## Per-resource values from tfstate", ""]
    resources = migrate_set.get("resources") or []
    if address_filter:
        resources = [r for r in resources if r.get("address") in address_filter]
    if not resources:
        out += ["_No managed resources in the migrate set._", ""]
        return out

    for r in sorted(resources, key=lambda r: r.get("address", "")):
        out.append(f"### `{r.get('address', '?')}`")
        out.append("")
        out.append(f"- TF type: `{r.get('type', '?')}`")
        if r.get("module"):
            out.append(f"- Module: `{r['module']}`")
        idty = r.get("identity") or {}
        if idty:
            out.append("- Identity: " + ", ".join(f"`{k}={v}`" for k, v in sorted(idty.items())))
        sens = r.get("sensitive_fields") or []
        if sens:
            out.append(
                "- **Redacted by discover (do NOT invent values):** "
                + ", ".join(f"`{s}`" for s in sorted(sens))
            )
        attrs = r.get("attributes") or {}
        if attrs:
            out += ["", "| Attribute | Value |", "|---|---|"]
            for k in sorted(attrs):
                out.append(f"| `{k}` | `{_fmt_value(attrs[k])}` |")
        out.append("")
    return out


def section_crds(crds: dict | list | None) -> list[str]:
    """Live-CRD required / immutable / spec / status per Kind. This is (e)."""
    out = ["## Live CRD schemas (authoritative — the cluster wins over upstream)", ""]
    if crds is None:
        out += [
            "> **NOT AVAILABLE.** `--crds` was not supplied, so required-field, immutable-field "
            "and absent-field lists are UNKNOWN for this run. Do not treat this as \"no required "
            "fields\". Enumerate them before writing any spec field:",
            "",
            "```bash",
            "./scripts/run crd-inventory --schema",
            "```",
            "",
        ]
        return out

    # Accept both crd-inventory --schema output and a raw `kubectl get crd -o json`.
    items: list[dict]
    if isinstance(crds, dict) and "items" in crds:
        items = [
            {
                "kind": ((c.get("spec") or {}).get("names") or {}).get("kind"),
                "schema": _digest_raw_crd(c),
            }
            for c in crds["items"]
        ]
    elif isinstance(crds, list):
        items = [{"kind": c.get("kind"), "schema": c.get("schema")} for c in crds]
    else:
        raise click.ClickException(
            "--crds must be crd-inventory --schema output (a list) or kubectl get crd -o json"
        )

    items = [i for i in items if i.get("kind") and i.get("schema")]
    if not items:
        out += ["_No CRD schemas present in the supplied file._", ""]
        return out

    for i in sorted(items, key=lambda i: i["kind"]):
        s = i["schema"] or {}
        out.append(f"### `{i['kind']}`")
        out.append("")
        out.append(f"- Required: {_join_or_none(s.get('required'))}")
        out.append(f"- Immutable (literals only, never CEL): {_join_or_none(s.get('immutable'))}")
        out.append(f"- Spec fields: {_join_or_none(s.get('spec'))}")
        out.append(f"- Status fields: {_join_or_none(s.get('status'))}")
        if i["kind"] in SPEC_SHAPE_EXCEPTIONS:
            out.append(f"- **⚠️ Shape exception:** {SPEC_SHAPE_EXCEPTIONS[i['kind']]}")
        out.append("")
    out += [
        "**Any field absent from the Spec list above MUST NOT be written.** It would be pruned "
        "in a CR and would fail kro's type check in an RGD template. Record the omission for "
        "`decisions.json` → `omitted_fields`.",
        "",
    ]
    return out


def _digest_raw_crd(crd: dict) -> dict | None:
    """required/immutable/spec/status from a raw CRD, storage version preferred."""
    versions = (crd.get("spec") or {}).get("versions") or []
    chosen = next((v for v in versions if v.get("storage")), versions[0] if versions else None)
    if not chosen:
        return None
    props = (((chosen.get("schema") or {}).get("openAPIV3Schema") or {}).get("properties")) or {}
    spec = props.get("spec") or {}
    spec_props = spec.get("properties") or {}
    status_props = (props.get("status") or {}).get("properties") or {}
    return {
        "required": sorted(spec.get("required") or []),
        "immutable": sorted(k for k, v in spec_props.items() if v.get("x-kubernetes-validations")),
        "spec": sorted(spec_props),
        "status": sorted(status_props),
    }


def _join_or_none(values: Any) -> str:
    if not values:
        return "_none_"
    return ", ".join(f"`{v}`" for v in values)


def section_mapping(migrate_set: dict, contract: dict, address_filter: set[str] | None) -> list[str]:
    """TF type -> ACK Kind with adoption-fields keys. This is (d)."""
    out = [
        "## Verified mapping — TF type → ACK Kind",
        "",
        "Derived from the live CRD inventory in `migrate-set.json` plus the authoring contract. "
        "This table is authoritative for the run; do not re-derive it.",
        "",
    ]
    ack_crds = ((migrate_set.get("cluster") or {}).get("ack_crds")) or []
    by_kind = {c.get("kind"): c for c in ack_crds if c.get("kind")}
    adoption = {
        k: v for k, v in (contract.get("adoption_fields_by_kind") or {}).items()
        if not k.startswith("_")
    }
    consolidation = {
        k: v for k, v in (contract.get("consolidation_rules") or {}).items()
        if not k.startswith("_")
    }

    resources = migrate_set.get("resources") or []
    if address_filter:
        resources = [r for r in resources if r.get("address") in address_filter]
    tf_types = sorted({r.get("type") for r in resources if r.get("type")})

    out += ["| TF type | Status |", "|---|---|"]
    for t in tf_types:
        if t in consolidation:
            out.append(f"| `{t}` | **consolidated** — {_fmt_value(consolidation[t], 220)} |")
        else:
            out.append(f"| `{t}` | resolve against the CRD inventory below |")
    out.append("")

    out += [
        "### Installed ACK Kinds and their adoption-fields keys",
        "",
        "| Kind | Group | Plural | adoption-fields |",
        "|---|---|---|---|",
    ]
    for kind in sorted(by_kind):
        c = by_kind[kind]
        group = c.get("group", "?")
        key = f"{group}/{kind}"
        af = adoption.get(key)
        out.append(
            f"| `{kind}` | `{group}` | `{c.get('plural', '?')}` | "
            + (f"`{_fmt_value(af, 200)}`" if af else "_not cached — web-verify this one_")
            + " |"
        )
    out.append("")
    return out


@click.command()
@click.option("--migrate-set", "migrate_set_path", required=True,
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="discover.py output.")
@click.option("--crds", "crds_path", default=None,
              type=click.Path(exists=True, dir_okay=False, path_type=Path),
              help="crd-inventory --schema output, or kubectl get crd -o json. "
                   "Omitting it marks the CRD section NOT AVAILABLE rather than empty.")
@click.option("--addresses", default=None,
              help="Comma-separated TF addresses to scope to. Default: every managed resource.")
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), default=None,
              help="Write here. Default: stdout.")
def main(migrate_set_path: Path, crds_path: Path | None, addresses: str | None,
         out: Path | None) -> None:
    migrate_set = _load(migrate_set_path, "--migrate-set")
    crds = _load(crds_path, "--crds") if crds_path else None
    contract = _load(CONTRACT, "authoring contract")
    address_filter = {a.strip() for a in addresses.split(",") if a.strip()} if addresses else None

    if address_filter:
        known = {r.get("address") for r in (migrate_set.get("resources") or [])}
        missing = sorted(address_filter - known)
        if missing:
            raise click.ClickException(
                "--addresses names resources absent from the migrate set: " + ", ".join(missing)
            )

    lines = [
        "# Shared authoring interface — DERIVED SECTIONS",
        "",
        "Generated by `scripts/run emit-interface`. Every agent in the authoring fan-out reads "
        "this file **by path**; it is never inlined into a prompt.",
        "",
        "> **The skill MUST append its decisions below before spawning the fan-out:** final "
        "`schema.spec` field names, the resource `id` per Kind with its filename and CR "
        "`metadata.name`, run parameters (RGD name, generated Kind, namespace, mode), the "
        "literal-vs-CEL assignment per field, and the dependency-edge table. Those are "
        "judgement calls and are deliberately not generated here.",
        "",
        "---",
        "",
    ]
    lines += section_mapping(migrate_set, contract, address_filter)
    lines += ["---", ""]
    lines += section_crds(crds)
    lines += ["---", ""]
    lines += section_resources(migrate_set, address_filter)

    body = "\n".join(lines)
    if out is None:
        sys.stdout.write(body)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    n_res = len(
        [r for r in (migrate_set.get("resources") or [])
         if not address_filter or r.get("address") in address_filter]
    )
    sys.stdout.write(
        f"wrote {out} ({len(body.encode())} B, {n_res} resources, "
        f"crds={'yes' if crds is not None else 'NOT SUPPLIED'})\n"
    )


if __name__ == "__main__":
    main()
