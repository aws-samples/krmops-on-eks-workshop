"""Render a single-page HTML that is both a decision-making record and a
migration report.

Inputs (all JSON, produced by the skill during its Phase_Checkpoint pauses):

  --migrate-set  discover.py output
  --decisions    { "path": "adopt|create|both",
                   "checkpoints": [
                     { "phase": "Phase 1 (Adopt_Path)",
                       "decisions": [ { "title": "...", "detail": "..." } ],
                       "needs_attention": [ "..." ],
                       "operator_response": "Confirm|Correct|Proceed",
                       "operator_notes": "..." }
                   ],
                   "class_a": [ addr, ... ],
                   "class_b": [ addr, ... ],
                   "unsupported": [ { "address": ..., "reason": ... } ],
                   "resource_groups": [ { "name": "...", "resources": [addr] } ] }
  --manifests-dir  directory the skill wrote (ACK CRs + RGD + instance)
  --findings       optional JSON from validate_manifest.py / validate_cel.py
  --out            output HTML path

The resource graph is Cytoscape.js. Cytoscape source is inlined at render
time so the report opens without network access.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import click
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape


ACK_GROUP_MARKER = ".services.k8s.aws/"

CYTOSCAPE_CDN_URL = "https://cdn.jsdelivr.net/npm/cytoscape@3.30.2/dist/cytoscape.min.js"
DAGRE_CDN_URL = "https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js"
CYTO_DAGRE_CDN_URL = "https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.min.js"


def _fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=15) as resp:
        return resp.read().decode("utf-8")


# Shown in place of the graph when the assets could not be fetched. Keeps the rest of
# the report (decisions, findings, manifest tables) usable.
_GRAPH_UNAVAILABLE_JS = (
    "window.__kroGraphUnavailable = true;\n"
    "document.addEventListener('DOMContentLoaded', function () {\n"
    "  var el = document.getElementById('graph');\n"
    "  if (el) { el.innerHTML = '<p style=\"padding:1rem;color:#666\">"
    "Resource graph unavailable: Cytoscape assets could not be downloaded and were not "
    "in the local cache. Re-run <code>render-report</code> with network access to "
    "populate it.</p>'; }\n"
    "});\n"
)


def inline_cytoscape(cache_dir: Path) -> tuple[str, str, str]:
    """Download-or-cache Cytoscape + dagre so the HTML works offline.

    A cache miss with no network must NOT abort the run: this function is also on the
    path of MIGRATION-NOTES.md generation, and the notes are an operator runbook that
    does not depend on the graph. On failure the graph degrades to an inline message and
    a warning goes to stderr; every other section still renders.
    """
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        sys.stderr.write(f"warning: cannot create asset cache {cache_dir}: {e}\n")
        return _GRAPH_UNAVAILABLE_JS, "", ""

    outputs: list[str] = []
    for url in (CYTOSCAPE_CDN_URL, DAGRE_CDN_URL, CYTO_DAGRE_CDN_URL):
        name = url.rsplit("/", 1)[-1]
        cached = cache_dir / name
        try:
            if not cached.exists():
                cached.write_text(_fetch(url), encoding="utf-8")
            outputs.append(cached.read_text(encoding="utf-8"))
        except Exception as e:  # URLError, timeout, OSError, decode errors
            sys.stderr.write(
                f"warning: could not obtain {name} ({type(e).__name__}: {e}); "
                "rendering without the resource graph\n"
            )
            return _GRAPH_UNAVAILABLE_JS, "", ""
    return outputs[0], outputs[1], outputs[2]


def load_manifest_index(manifests_dir: Path) -> dict:
    """Read every YAML in manifests_dir and index by Kind + name for graph nodes."""
    ack_crs: list[dict] = []
    rgds: list[dict] = []
    instances: list[dict] = []

    for path in sorted(manifests_dir.rglob("*.yaml")):
        try:
            with path.open() as f:
                for doc in yaml.safe_load_all(f):
                    if not isinstance(doc, dict):
                        continue
                    kind = doc.get("kind")
                    meta = doc.get("metadata") or {}
                    name = meta.get("name") or "?"
                    api = doc.get("apiVersion", "?")
                    entry = {
                        "kind": kind,
                        "name": name,
                        "apiVersion": api,
                        "file": str(path.relative_to(manifests_dir)),
                        "annotations": (meta.get("annotations") or {}),
                        "spec": doc.get("spec"),
                    }
                    if kind == "ResourceGraphDefinition":
                        rgds.append(entry)
                    elif api.endswith(".services.k8s.aws/v1alpha1") or ".services.k8s.aws/" in api:
                        ack_crs.append(entry)
                    else:
                        # Assume anything that isn't RGD and isn't ACK is an Instance CR.
                        instances.append(entry)
        except yaml.YAMLError:
            continue

    return {"rgds": rgds, "ack_crs": ack_crs, "instances": instances}


def build_graph_elements(
    migrate_set: dict,
    decisions: dict,
    manifests: dict,
) -> list[dict]:
    """Build Cytoscape elements: RGD → ACK CRs → underlying AWS/TF resources.

    Nodes:
      layer 0 (root):   RGD abstraction (one per emitted RGD)
      layer 1 (mid):    ACK CR (child of RGD)
      layer 2 (leaf):   underlying AWS resource (TF address from migrate-set)
      distinct:         TF-retained (Class A) resources — different color
    """
    elements: list[dict] = []
    seen_nodes: set[str] = set()

    def add_node(node_id: str, label: str, group: str, extra: dict | None = None) -> None:
        if node_id in seen_nodes:
            return
        seen_nodes.add(node_id)
        data = {"id": node_id, "label": label, "group": group}
        if extra:
            data.update(extra)
        elements.append({"data": data})

    def add_edge(source: str, target: str, kind: str = "child") -> None:
        elements.append({"data": {"source": source, "target": target, "kind": kind}})

    # Address → TF resource map from the migrate set.
    addr_to_res = {r["address"]: r for r in migrate_set.get("resources", [])}

    # Layer 0/1: RGDs and their child resource template references.
    for rgd in manifests["rgds"]:
        rgd_id = f"rgd::{rgd['name']}"
        add_node(rgd_id, rgd["name"], "rgd", {"file": rgd["file"]})
        spec = rgd.get("spec") or {}
        for r_tmpl in spec.get("resources", []) or []:
            if not isinstance(r_tmpl, dict):
                continue
            tmpl_id = r_tmpl.get("id") or r_tmpl.get("name") or "?"
            tmpl = r_tmpl.get("template") or {}
            kind = tmpl.get("kind", "?")
            cr_id = f"cr::{rgd['name']}::{tmpl_id}"
            add_node(
                cr_id,
                f"{kind}/{tmpl_id}",
                "ack",
                {"kind": kind, "template_id": tmpl_id},
            )
            add_edge(rgd_id, cr_id, "child")

    # Layer 1 fallback: standalone ACK CRs not referenced by an RGD.
    for cr in manifests["ack_crs"]:
        cr_id = f"cr::standalone::{cr['name']}"
        add_node(cr_id, f"{cr['kind']}/{cr['name']}", "ack", {"kind": cr["kind"], "file": cr["file"]})

    # Layer 2: underlying AWS resources from the migrate set, grouped by
    # resource-group where the skill assigned one.
    group_lookup: dict[str, str] = {}
    for rg in decisions.get("resource_groups", []) or []:
        for addr in rg.get("resources", []) or []:
            group_lookup[addr] = rg["name"]

    class_a = set(decisions.get("class_a") or [])

    for addr, res in addr_to_res.items():
        node_id = f"aws::{addr}"
        group = "tf_retained" if addr in class_a else "aws"
        label = f"{res.get('type', '?')}\n{addr}"
        add_node(
            node_id,
            label,
            group,
            {
                "address": addr,
                "type": res.get("type"),
                "provider": res.get("provider"),
                "module": res.get("module"),
                "identity": res.get("identity", {}),
                "resource_group": group_lookup.get(addr),
            },
        )
        rg_name = group_lookup.get(addr)
        if rg_name:
            rgd_id = f"rgd::{rg_name}"
            if rgd_id in seen_nodes:
                add_edge(rgd_id, node_id, "manages")

    # Cross-references (informational edges).
    addr_to_node = {addr: f"aws::{addr}" for addr in addr_to_res.keys()}
    for xref in migrate_set.get("cross_refs", []) or []:
        src = addr_to_node.get(xref.get("from"))
        dst = addr_to_node.get(xref.get("to"))
        if src and dst and src != dst:
            elements.append(
                {"data": {"source": src, "target": dst, "kind": "reference"}}
            )

    return elements


# --- MIGRATION-NOTES.md ---------------------------------------------------
#
# The notes are an operator runbook and are fully derivable from the inputs this script
# already loads, so they are templated rather than authored by an agent. Two reasons that
# matter: (1) the same content was being transcribed from migrate-set.json and
# controller-permissions.md on an agent's critical path, ~15 KB serially; (2) a prose rule
# asking for brevity ("a ~15 KB MIGRATION-NOTES.md is a smell") varies with the model,
# whereas a template does not.

PERMISSIONS_DATA = Path(__file__).resolve().parent / "data" / "ack-service-permissions.json"


def load_service_permissions() -> dict:
    """Per-service ACK permission table. Missing/corrupt file degrades to empty, loudly."""
    try:
        data = json.loads(PERMISSIONS_DATA.read_text())
    except Exception as e:
        sys.stderr.write(
            f"warning: could not read {PERMISSIONS_DATA.name} "
            f"({type(e).__name__}: {e}); MIGRATION-NOTES.md will omit the permissions table\n"
        )
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _service_of(api_version: str | None) -> str | None:
    """iam.services.k8s.aws/v1alpha1 -> iam"""
    if not api_version or ACK_GROUP_MARKER not in api_version:
        return None
    return api_version.split("/", 1)[0].split(".", 1)[0]


def _plural_of(kind: str) -> str:
    """Display-only pluralisation for the RBAC table. Irregulars come from the CRD when
    migrate-set carries it; this is the fallback for the informational listing."""
    low = kind.lower()
    if low.endswith("y") and not low.endswith(("ay", "ey", "oy", "uy")):
        return low[:-1] + "ies"
    if low.endswith(("s", "x", "z", "ch", "sh")):
        return low + "es"
    return low + "s"


def build_notes_context(
    migrate_set: dict,
    decisions: dict,
    findings: dict,
    manifests: dict,
    mode: str,
) -> dict:
    """Assemble everything migration-notes.md.j2 needs. Pure derivation, no I/O."""
    source = migrate_set.get("source") or {}
    cluster = migrate_set.get("cluster") or {}
    tf_resources = migrate_set.get("resources") or []

    # Plural lookup from the live CRD inventory; falls back to _plural_of.
    plural_by_kind = {
        c.get("kind"): c.get("plural")
        for c in (cluster.get("ack_crds") or [])
        if c.get("kind")
    }

    # One row per generated ACK CR, keyed off the manifests actually written.
    resources: list[dict] = []
    for cr in manifests.get("ack_crs", []):
        ann = cr.get("annotations") or {}
        resources.append(
            {
                "kind": cr.get("kind") or "?",
                "name": cr.get("name") or "?",
                "tf_address": ann.get("rekoncile.io/from-tf-address"),
                "adoption_fields": ann.get("services.k8s.aws/adoption-fields"),
                "service": _service_of(cr.get("apiVersion")),
                "plural": plural_by_kind.get(cr.get("kind")) or _plural_of(cr.get("kind") or "x"),
            }
        )
    resources.sort(key=lambda r: (r["kind"], r["name"]))

    # Apply order comes from the RGD's own resources[] list — kro derives creation order
    # from template references, so restating a topological sort here would be a second,
    # drift-prone source of truth.
    by_name = {r["name"]: r for r in resources}
    rgds: list[dict] = []
    for rgd in manifests.get("rgds", []):
        entries = []
        for entry in ((rgd.get("spec") or {}).get("resources") or []):
            if not isinstance(entry, dict):
                continue
            tmpl = entry.get("template") or {}
            cr_name = ((tmpl.get("metadata") or {}).get("name")) or ""
            kind = tmpl.get("kind") or "?"
            entries.append(
                {
                    "id": entry.get("id") or "?",
                    "kind": kind,
                    "name": by_name.get(cr_name, {}).get("name") or cr_name or "?",
                }
            )
        rgd_dir = Path(rgd.get("file", "")).parent
        rgds.append(
            {
                "name": rgd.get("name") or "?",
                "rgd_file": rgd.get("file") or "rgd.yaml",
                "instance_file": str(rgd_dir / "instance.yaml") if str(rgd_dir) != "." else "instance.yaml",
                "resources": entries,
            }
        )

    # Permissions: only the services this migration touches.
    perm_table = load_service_permissions()
    services = sorted({r["service"] for r in resources if r["service"]})
    permissions = [dict(perm_table[s], service=s) for s in services if s in perm_table]
    for s in services:
        if s not in perm_table:
            sys.stderr.write(
                f"warning: no permission data for ACK service '{s}' in "
                f"{PERMISSIONS_DATA.name}; it is omitted from the notes table\n"
            )

    ack_suffix = ACK_GROUP_MARKER.strip(".").rstrip("/")  # "services.k8s.aws"
    api_groups = [
        {
            "group": f"{s}.{ack_suffix}",
            "plurals": sorted({r["plural"] for r in resources if r["service"] == s}),
        }
        for s in services
    ]

    # Namespace: every template carries it explicitly per the authoring contract.
    namespace = None
    for rgd in manifests.get("rgds", []):
        for entry in ((rgd.get("spec") or {}).get("resources") or []):
            if not isinstance(entry, dict):
                continue
            ns = ((entry.get("template") or {}).get("metadata") or {}).get("namespace")
            if ns and "${" not in str(ns):
                namespace = ns
                break
        if namespace:
            break

    all_findings = findings.get("findings") or []
    modules = sorted({r.get("module") for r in tf_resources if r.get("module")})
    region = cluster.get("region") or next(
        (r.get("identity", {}).get("region") for r in tf_resources if (r.get("identity") or {}).get("region")),
        None,
    )

    return {
        "migration_name": (rgds[0]["name"] if rgds else Path(source.get("path", "migration")).name),
        "mode": mode,
        "strategy": {
            "adopt": "Class B — adopt existing resources, zero downtime",
            "create": "Class C — self-serve provisioning blueprint",
        }.get(mode),
        "source_path": source.get("path") or "?",
        "state_location": source.get("state_location"),
        "modules": modules,
        "region": region,
        "namespace": namespace,
        "rgds": rgds,
        "resources": resources,
        "permissions": permissions,
        "api_groups": api_groups,
        # Optional keys the skill may add to decisions.json; absent ones render as omitted
        # sections rather than empty headings.
        "consolidated": decisions.get("consolidated") or [],
        "unsupported": decisions.get("unsupported") or [],
        "class_a": decisions.get("class_a") or [],
        "omitted_fields": decisions.get("omitted_fields") or [],
        "owner_references": bool(decisions.get("owner_references")),
        "managed_capability": bool(decisions.get("managed_capability")),
        "data_sources": ((migrate_set.get("hcl") or {}).get("data")) or [],
        "findings_summary": {
            "errors": sum(1 for f in all_findings if f.get("severity") == "error"),
            "warnings": sum(1 for f in all_findings if f.get("severity") == "warning"),
        },
    }


@click.command()
@click.option("--migrate-set", "migrate_set_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--decisions", "decisions_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--manifests-dir", type=click.Path(exists=True, file_okay=False, path_type=Path), required=True)
@click.option("--findings", "findings_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option(
    "--notes-out",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Also render MIGRATION-NOTES.md here. The notes are templated, not authored — "
    "do not hand-write them.",
)
@click.option(
    "--mode",
    type=click.Choice(["adopt", "create"]),
    default=None,
    help="Mode the notes describe. Defaults to decisions.json 'path' when it is adopt or create.",
)
@click.option(
    "--offline-cache",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path.home() / ".cache" / "kiro-tf-migration",
    show_default=True,
    help="Where to cache Cytoscape assets between runs.",
)
def main(
    migrate_set_path: Path,
    decisions_path: Path,
    manifests_dir: Path,
    findings_path: Path | None,
    out: Path,
    notes_out: Path | None,
    mode: str | None,
    offline_cache: Path,
) -> None:
    migrate_set = json.loads(migrate_set_path.read_text())
    decisions = json.loads(decisions_path.read_text())
    findings = json.loads(findings_path.read_text()) if findings_path else {"findings": []}
    manifests = load_manifest_index(manifests_dir)
    elements = build_graph_elements(migrate_set, decisions, manifests)

    cyto_js, dagre_js, cyto_dagre_js = inline_cytoscape(offline_cache)

    env = Environment(
        loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
        autoescape=select_autoescape(["html"]),
    )
    tmpl = env.get_template("report.html.j2")

    html = tmpl.render(
        migrate_set=migrate_set,
        decisions=decisions,
        findings=findings,
        manifests=manifests,
        elements_json=json.dumps(elements),
        cytoscape_js=cyto_js,
        dagre_js=dagre_js,
        cytoscape_dagre_js=cyto_dagre_js,
    )

    out.write_text(html, encoding="utf-8")
    sys.stdout.write(f"wrote {out}\n")

    if notes_out is None:
        return

    # 'both' is not a notes mode: one file describes one mode. Fall back to adopt only
    # when the caller gave nothing usable, and say so.
    resolved_mode = mode or decisions.get("path")
    if resolved_mode not in ("adopt", "create"):
        sys.stderr.write(
            f"warning: --mode not given and decisions.path is {resolved_mode!r}; "
            "rendering MIGRATION-NOTES.md as 'adopt'. Pass --mode to be explicit.\n"
        )
        resolved_mode = "adopt"

    notes_env = Environment(
        loader=FileSystemLoader(str(Path(__file__).parent / "templates")),
        autoescape=False,  # markdown output: escaping would mangle backticks and pipes
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    notes_ctx = build_notes_context(migrate_set, decisions, findings, manifests, resolved_mode)
    notes_md = notes_env.get_template("migration-notes.md.j2").render(notes=notes_ctx)

    notes_out.parent.mkdir(parents=True, exist_ok=True)
    notes_out.write_text(notes_md, encoding="utf-8")
    sys.stdout.write(
        f"wrote {notes_out} ({len(notes_md.encode())} B, "
        f"{len(notes_ctx['resources'])} resources, {len(notes_ctx['permissions'])} services)\n"
    )


if __name__ == "__main__":
    main()
