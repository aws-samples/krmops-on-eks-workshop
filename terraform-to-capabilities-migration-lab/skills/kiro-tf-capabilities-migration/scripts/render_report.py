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


CYTOSCAPE_CDN_URL = "https://cdn.jsdelivr.net/npm/cytoscape@3.30.2/dist/cytoscape.min.js"
DAGRE_CDN_URL = "https://cdn.jsdelivr.net/npm/dagre@0.8.5/dist/dagre.min.js"
CYTO_DAGRE_CDN_URL = "https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.min.js"


def _fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=15) as resp:
        return resp.read().decode("utf-8")


def inline_cytoscape(cache_dir: Path) -> tuple[str, str, str]:
    """Download-or-cache Cytoscape + dagre so the HTML works offline."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[str] = []
    for url in (CYTOSCAPE_CDN_URL, DAGRE_CDN_URL, CYTO_DAGRE_CDN_URL):
        name = url.rsplit("/", 1)[-1]
        cached = cache_dir / name
        if not cached.exists():
            cached.write_text(_fetch(url), encoding="utf-8")
        outputs.append(cached.read_text(encoding="utf-8"))
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


@click.command()
@click.option("--migrate-set", "migrate_set_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--decisions", "decisions_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--manifests-dir", type=click.Path(exists=True, file_okay=False, path_type=Path), required=True)
@click.option("--findings", "findings_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), default=None)
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), required=True)
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


if __name__ == "__main__":
    main()
