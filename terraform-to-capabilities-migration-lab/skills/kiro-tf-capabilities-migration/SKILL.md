---
name: kiro-tf-capabilities-migration
description: >-
  Migrate Terraform-managed AWS resources to ACK/KRO via two supported capabilities:
  (1) Adopt_Path — consumes a terraform.tfstate file and generates ACK adoption CRs
  with adoption annotations plus a KRO ResourceGraphDefinition for zero-downtime takeover;
  (2) Create_Path — consumes .tf HCL source files and generates ACK CRs without adoption
  annotations (full spec) plus a self-serve abstraction KRO ResourceGraphDefinition that
  developers consume to provision resources from scratch. Generation is limited to the
  AWS/ACK path.
---

# Terraform to ACK/KRO Migration Skill

Two supported AWS/ACK migration capabilities:

- **Adopt_Path (Class B)** — consumes a `terraform.tfstate` file and generates ACK adoption CRs (with adoption annotations) plus a KRO ResourceGraphDefinition. Zero-downtime takeover of resources Terraform already manages.
- **Create_Path (Class C)** — consumes `.tf` HCL source files and generates ACK CRs without adoption annotations (full spec derived from Terraform variables) plus a self-serve abstraction KRO ResourceGraphDefinition that platform teams publish and developers consume to provision resources on demand.

## Scope

Generation is limited to the **AWS/ACK path**. Native-Kubernetes adoption (Terraform `kubernetes`/`helm` providers), Helm chart conversion, and Kustomize conversion are out of scope.

## When to Use This Skill

| Input provided | User goal | Path selected |
|---|---|---|
| `terraform.tfstate` | Adopt/migrate existing resources into ACK/KRO | **Adopt_Path** |
| `.tf` HCL files | Create a self-serve provisioning blueprint / provision new resources | **Create_Path** |

- User provides a `terraform.tfstate` file and asks to migrate, adopt, or import resources into ACK/KRO → **Adopt_Path**
- User provides `.tf` files and asks to create, provision, or generate a self-serve API for developers → **Create_Path**

## What This Skill Does NOT Do

- Does NOT recreate resources that are being adopted — the Adopt_Path takes over existing resources with zero downtime
- Does NOT require Terraform CLI for either path
- Does NOT delete or modify the Terraform state file
- Does NOT generate resources outside the AWS/ACK path (no native K8s, Helm, or Kustomize)

---

## Path Selection (Router)

This table is the authoritative decision schema for path selection. The skill follows this table to determine which path to execute.

| Input | Goal | Selected Path | Behavior |
|---|---|---|---|
| `tfstate` (+ optional `.tf`) | adopt | **Adopt_Path** | Generate ACK adoption CRs + RGD |
| `.tf` HCL | create | **Create_Path** | Generate full-spec ACK CRs + self-serve RGD |
| `.tf` HCL | adopt | **Create_Path** | HCL has no runtime ARNs/IDs to adopt against → proceed via Create_Path |
| `tfstate` (+ optional `.tf`) | create | **Create_Path** (from state attributes) | Runtime IDs are ignored; ACK CRs derived from state attributes as if authoring from scratch. Emit both by default when the operator hasn't specified `--mode` |
| `tfstate` (+ optional `.tf`) | both (default) | **Both** | Emit Adopt_Path AND Create_Path outputs from one resolved migrate-set. This is the default when the operator provides state without an explicit goal |
| unknown / neither | any | **Reject** | Report input unsupported; list supported types: `tfstate`, `.tf` HCL |
| any (Class A resource targeted, Adopt_Path only) | adopt | **Exclude** | Report resource is retained in Terraform; exclude from generation |

### Edge Cases

**`.tf` + adopt:** HCL files contain no runtime ARNs or resource IDs, so adoption-by-lookup is impossible. The skill proceeds via Create_Path — the output generates full-spec CRs that can be deployed to create fresh resources, but cannot adopt existing ones.

**`tfstate` + create:** No longer treated as an inconsistency. A tfstate has all the information needed to author full-spec Create_Path CRs (the runtime IDs are simply ignored on this path). Both outputs are emitted by default so operators can bring up a new-but-identical environment from an existing stack. Use `--mode adopt`, `--mode create`, or `--mode both` (default) to override.

**Unknown input:** If the provided file is not recognizable as either a Terraform state file (JSON version 4 format) or `.tf` HCL, the skill rejects the input and lists the two supported types (`tfstate`, `.tf` HCL).

**Class A resource (Adopt_Path ONLY):** Foundation infrastructure (VPCs, subnets, the EKS cluster itself) that should remain managed by Terraform during **adoption**. When a Class A resource is targeted on the **Adopt_Path**, the skill reports it is excluded from generation and retained in Terraform.

**⚠️ CRITICAL: Class A exclusions do NOT apply to the Create_Path.** On the Create_Path, the user is requesting a full provisioning blueprint. ALL resources in the `.tf` files that have ACK equivalents MUST be included — VPCs, subnets, EKS clusters, everything. The goal is a complete self-serve abstraction that provisions the entire stack from zero. Excluding VPCs or EKS clusters from a Create_Path output makes the abstraction useless.

---

## Helper Scripts (Python)

The skill ships a small Python toolkit under `scripts/` that handles the deterministic work the LLM must not do (parsing tfstate/HCL, inventorying live-cluster CRDs, kubectl dry-run grounding, CEL grammar checks, resource-graph HTML rendering, and adoption orchestration). No static registry, no bundled mapping tables — every TF→ACK mapping decision reasons against `cluster.ack_crds` in the migrate-set.

**Prerequisites (operator machine):**
- Python 3.11+ on PATH
- `kubectl` on PATH, `KUBECONFIG` pointing at the target EKS cluster (needed for CRD inventory, dry-run validation, and adoption)
- `terraform` on PATH (adoption only)

**Bootstrap once per checkout:**

```bash
bash scripts/bootstrap.sh
```

That single command creates `scripts/.venv/`, installs Python dependencies, and is idempotent. There is **no `source activate` step** — every helper is invoked through the `scripts/run` wrapper, which delegates to the venv's Python for you.

**Helper contract (always invoke via `scripts/run <subcommand>`):**

| Subcommand | Purpose | Called at |
|---|---|---|
| `scripts/run discover` | Parse tfstate + HCL + live-cluster CRD inventory → `migrate-set.json` | Phase 1 (both paths) |
| `scripts/run crd-inventory` | Standalone CRD listing (called by discover, or ad-hoc if the cluster changed) | Phase 1; Phase 4 pre-validate if needed |
| `scripts/run validate-manifest` | `kubectl apply --dry-run=server` per manifest + adoption-annotation invariants | Phase 4 (both paths) |
| `scripts/run validate-cel` | CEL grammar check on `readyWhen` and `${…}` interpolations in RGDs | Phase 4 (both paths) |
| `scripts/run validate-spec-fields` | Every spec field in the generated CRs **and** the ACK templates inside `rgd.yaml` must exist in the live CRD — catches fields that exist in the AWS SDK/Terraform but not the CRD (e.g. `skipFinalSnapshot`, `applyImmediately`). `--dry-run=server` cannot catch these: the API server silently **prunes** unknown fields | Phase 4 (both paths); also inline right after writing each CR in Phase 2 |
| `scripts/run validate-all` | Parallel validate-spec-fields + validate-manifest + validate-cel across every `<mode>/<rgd>/` subtree; aggregates per-mode findings.json for render-report | Phase 4 (preferred over serial invocations — ~2.3× speedup measured) |
| `scripts/run render-report` | Single-page HTML combining Phase_Checkpoint decisions + Cytoscape resource graph + migration report | Phase 4 (both paths) |
| `scripts/run adopt` | Apply ACK CRs + poll `ACK.ResourceSynced=True` (verify adoption; **never** touches TF state) | Post-Phase 4, Adopt_Path only, opt-in |

**Skill obligations at every phase:**
- The skill authors ACK CRs and KRO RGDs in the conversation. Helper scripts NEVER generate YAML. They only parse, ground, or render.
- After every generation step, the skill runs the relevant validator and treats the returned findings as blocking input to the next Phase_Checkpoint.
- The skill records each Phase_Checkpoint's Decision_Summary and operator response into a `decisions.json` structure. That structure is passed to `render_report.py` at the end of Phase 4.

- **⚠️ Write `decisions.json` INCREMENTALLY — append one `checkpoints[]` entry at each Phase_Checkpoint, never batch all phases at the end.** Write the file to `/tmp/decisions.json` as soon as Phase 1's checkpoint is confirmed, then append at Phases 2, 3 and 4. Rationale: (a) batching duplicates work already done in the chat summaries; (b) a schema mistake discovered at Phase 4 forces a rewrite of ALL phases plus a re-render, whereas an incremental write surfaces it after Phase 1 when the file is ~2 KB. Measured cost of getting this wrong: a full 13 KB rewrite plus a second render pass.

- **⚠️ The `decisions.json` schema is defined by `render_report.py`'s module docstring (the `--decisions` block, lines 7–19). READ IT before authoring the first entry — do NOT infer the shape from this SKILL.md or from the report's rendered output:**

  ```bash
  sed -n '1,25p' scripts/render_report.py
  ```

  The contract that is easiest to get wrong:

  | Key | Required shape | Common wrong guess |
  |---|---|---|
  | `checkpoints` | array — the top-level list of phase records | `phases` |
  | `checkpoints[].decisions` | array of **`{title, detail}` objects** | array of plain strings |
  | `checkpoints[].phase` | string, e.g. `"Phase 1 (Adopt_Path) — Parse State"` | integer `1` |
  | `checkpoints[].needs_attention` | array of plain strings | array of objects |
  | `checkpoints[].operator_response` | `Confirm` \| `Correct` \| `Proceed` | free text |
  | `class_a` / `class_b` | arrays of TF address strings | omitted |
  | `unsupported` | array of `{address, reason}` | array of strings |
  | `resource_groups` | array of `{name, resources: [addr]}` | omitted |
  | `path` | `adopt` \| `create` \| `both` | `mode` |

  A wrong key name does **not** error. `render-report` exits 0 and emits valid HTML with
  the affected section empty (`"No checkpoints recorded."`). **After rendering, always
  grep the output to confirm the decisions actually landed:**

  ```bash
  grep -c 'class="attention"' <output-dir>/report.html   # expect >= 1 per flagged item
  grep -q 'No checkpoints recorded' <output-dir>/report.html \
    && echo "FAIL: decisions.json key mismatch — re-read render_report.py docstring"
  ```

- **Generalize the rule: before authoring ANY input file for a helper script, read that script's docstring/argparse for the expected shape.** These contracts are documented at the top of each file in `scripts/`. The failure mode is consistently silent (exit 0 + missing section), not loud, so a post-render assertion is mandatory rather than optional.

## Parallel Execution

Most of the phase work has independent fan-out points that a serial run leaves on the table. The skill MUST parallelize the three phases below when the input list has more than one element. Everything not listed here stays serial.

Measured baseline on a 44-manifest stack against a live cluster: Phase 4 validation ran 45s serial with three RGDs at 18.6s / 16.9s / 9.0s. Wall-clock in parallel is bounded by the slowest RGD (~18.6s) plus a small overhead — an expected ~2.4× speedup with three RGDs and roughly linear scaling to the number of RGDs beyond that.

### Parallel Phase A — Web verification (Phase 1)

**Fan-out unit: one ACK SERVICE (controller repo), carrying ALL the TF types that map to it** — NOT one subagent per TF type. Derive the set by grouping `migrate-set.json` TF types by their target controller (`aws_iam_role`, `aws_iam_policy`, `aws_iam_role_policy_attachment` → one `iam` agent). Typically 4–8 services for a real stack, versus 15–25 types.

Rationale: verification is per **controller repo** — all `aws_iam_*` types resolve from the same `helm/crds` listing and the same `pkg/resource/**` adoption logic. Per-type fan-out re-fetches the identical repo N times for no new information. Measured: 10 TF types collapsed to 5 service agents, ~157s wall-clock.

Grouping is also **more accurate**, not just cheaper: consolidation relationships are only visible to an agent holding the whole service. An agent given `aws_security_group_rule` alone cannot know there is no standalone rule CRD and that rules fold into the parent `SecurityGroup.spec.{ingressRules,egressRules}` — it needs `aws_security_group` in the same context.

**Each subagent's job (one service, N TF types):**
- Enumerate the controller's Kinds ONCE: `https://github.com/aws-controllers-k8s/<service>-controller/tree/main/helm/crds` (filenames reveal every Kind).
- For each TF type in its group: resolve the Kind, or report it as consolidated into a parent Kind, or report no equivalent.
- **Priority #1 — adoption-fields lookup keys.** These are the highest-value output because they exist ONLY in controller source and are invisible to a live-CRD read. Get them from `pkg/resource/<resource>/resource.go` → `PopulateResourceFromAnnotation` (the `fields["…"]` key), corroborated by `test/e2e/tests/test_<resource>.py` → `ADOPTION_FIELDS`. Report the exact casing.
- **Priority #2 — upstream-vs-cluster version drift.** Flag any spec field that exists upstream but is missing from `cluster.ack_crds`, naming the controller version that added it. This is the other thing a live-CRD read cannot tell you.
- Do **NOT** spend effort enumerating required/immutable/all-spec-field-names — the batched live-CRD read (see `§ Coordination hazards`) is authoritative and cheaper. Report only where upstream **disagrees** with the cluster.
- Return one JSON blob per service: `{service, controller_repo, kinds: [...], mappings: [{tf_type, ack_group, ack_kind, cluster_installed: bool, adoption_fields_key: str, consolidates_into: str|null, notes: str}], version_drift: [{field, kind, added_in_version}]}`.

**Skill obligations:**
- Collect all results into a single "verified mapping table" in-conversation.
- **This is the authoritative mapping** for the rest of the run. Serial phases downstream MUST NOT re-derive mappings.
- If a subagent reports `cluster_installed: false`, mark the TF type as unsupported for this cluster and record it in the Phase 1 Decision_Summary.
- Ties/conflicts (e.g., two subagents give different Kinds for the same TF type) escalate to the operator at the Phase 1 checkpoint.

### Parallel Phase B — Per-RGD authoring (Phase 3)

**Fan-out unit:** one resource group from the Phase 1 grouping decision (typically 2–5 groups; e.g., network-stack, eks-stack, app-stack).

**Each subagent's job (per RGD, per mode):**
- Receive: the verified mapping table (from Phase A), the list of resource addresses in this group, the resolved TF attributes for each, and the mode (adopt or create).
- Author `<rgd-name>/rgd.yaml` + `<rgd-name>/instance.yaml` + `<rgd-name>/resources/*.yaml`.
- Run its own tight grounding loop: `./scripts/run validate-spec-fields <rgd>/` + `./scripts/run validate-manifest --dir <rgd>/resources --mode <mode>` + `./scripts/run validate-cel <rgd>/rgd.yaml`. Fix findings and re-run until clean or 3 attempts.
- Return `{rgd_name, files_written: [...], findings_summary: {errors, warnings}, attempts_needed: int}`.

**Skill obligations:**
- Both modes (adopt + create) run in parallel with each other AND across RGDs — so a 3-RGD × 2-mode run fans out 6 subagents.
- All subagents receive the SAME **authoring contract** at `references/authoring-contract.json` — hand it to each subagent verbatim. It encodes label conventions, annotation shape, adopt vs create spec rules, adoption-fields lookup keys per Kind, known CRD OpenAPI-required minima, consolidation rules, and the capability role trust policy. Update in ONE place; never fork per subagent.
- If any subagent hits 3 failed attempts, surface to the operator at Phase 4 checkpoint — do not silently drop.
- Serial follow-up in the main skill: assemble the per-RGD outputs into `migration-output/{adopt,create}/{rgd}/...`, then run one final aggregate validation over all directories (Phase C below).

### Parallel Phase C — Validation sweep (Phase 4)

**Fan-out unit:** `(mode, rgd)` tuple — 6 tuples for a 3-RGD × 2-mode run.

**Each subagent's job (or delegate the whole sweep to the built-in helper):**
- Run `./scripts/run validate-spec-fields <path>/ --context $CTX --format json`.
- Run `./scripts/run validate-manifest --dir <path> --context $CTX --mode <mode> --format json`.
- Run `./scripts/run validate-cel <path>/rgd.yaml --format json`.
- Return `{mode, rgd, spec_findings: [...], manifest_findings: [...], cel_findings: [...], duration_seconds}`.

**Preferred invocation — one command does the fan-out for you:**

```bash
./scripts/run validate-all \
  --output migration-output/ \
  --context $KUBECTL_CONTEXT \
  --workers 8
```

`validate-all` auto-discovers every `<mode>/<rgd>/` subtree and runs validate-spec-fields + validate-manifest + validate-cel across all `(mode, rgd)` tuples concurrently via a thread pool. Aggregates findings into `<mode>/findings.json` ready for `render-report`. Exit code non-zero iff any tuple has a `severity: error` finding. **Use this instead of orchestrating separate subagents unless you specifically want per-RGD subagent conversation state.**

Measured on a 3-RGD adopt-only workload: **2.31× speedup** (45s serial → 18s parallel). Projected on 3-RGD × 2-mode (6 tuples): ~4-5× speedup, bounded by the slowest tuple.

**Housekeeping (observed on a `--mode adopt` run):** `validate-all` auto-discovers `<mode>/` subtrees and writes a `findings.json` for **every** mode directory it creates, including an empty `create/findings.json` on an adopt-only run. Delete the unused mode directory before rendering so the output tree matches the requested mode (or scope the sweep with `--modes adopt`).

**Skill obligations:**
- Aggregate all findings into `<mode>/findings.json` for report rendering (validate-all does this automatically).
- If ANY subagent (or validate-all tuple) returns `severity: error`, the Phase 4 checkpoint blocks and hands the errors back to Phase B for that specific RGD (targeted re-authoring), not all of them.

### What stays serial

- **Phase 0 bootstrap.** `bash scripts/bootstrap.sh` is idempotent; parallelizing would either race on `.venv` creation or duplicate work.
- **Phase 1 discover.** `./scripts/run discover` is a single call producing one JSON.
- **Phase 1 grouping decision.** Groups are chosen by reasoning across the whole resource graph; can't split.
- **All Phase_Checkpoint pauses.** Human-in-the-loop is the value; parallelizing checkpoints defeats the review contract. Checkpoints fire AFTER their parallel phase completes and consolidates.
- **Adopt verification.** `./scripts/run adopt` applies the ACK CRs and polls each until `ACK.ResourceSynced=True` per resource. This is intentionally sequential to preserve safety semantics; do not parallelize. It **never** modifies Terraform state (Key Principle 7) — `terraform.tfstate` is left intact as a rollback backup.
- **Report rendering.** Single Jinja render per mode.

### Coordination hazards

- **Shared kubeconfig / kubectl.** Read-only server-side calls (`kubectl apply --dry-run=server`, `kubectl get crd`) are safe under high concurrency. Write-side (real `kubectl apply`, `adopt.py`) stays serial.
- **Decision drift between subagents.** Two authoring subagents can pick incompatible label conventions if the shared authoring contract is fuzzy. Encode the contract as a static JSON handed to every subagent, not as prose.
- **⚠️ kubectl startup cost dominates on short calls — BATCH CRD reads. This is a rule, not a future optimization.** Each invocation costs ~1–2s of process startup, so a 7-iteration shell loop over CRDs wastes ~30–60s versus one call. `kubectl get crd` accepts **multiple names in a single invocation**, and one `-o json` pass carries spec properties, `required`, `x-kubernetes-validations` (immutability) and status properties together — so enumerate all of them in ONE call, not three passes:

  ```bash
  kubectl get crd \
    dbinstances.rds.services.k8s.aws \
    dbsubnetgroups.rds.services.k8s.aws \
    securitygroups.ec2.services.k8s.aws \
    roles.iam.services.k8s.aws \
    policies.iam.services.k8s.aws \
    secrets.secretsmanager.services.k8s.aws \
    podidentityassociations.eks.services.k8s.aws \
    -o json > /tmp/crds.json

  python3 - /tmp/crds.json <<'PY'
  import sys, json
  for c in json.load(open(sys.argv[1]))["items"]:
      props = c["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]
      spec = props["spec"]
      status = props.get("status", {})
      p = spec["properties"]
      print("==", c["spec"]["names"]["kind"])
      print("  required :", spec.get("required", []))
      print("  immutable:", [k for k in p if p[k].get("x-kubernetes-validations")])
      print("  spec     :", sorted(p))
      print("  status   :", sorted(status.get("properties", {})))
  PY
  ```

  Writing to `/tmp/crds.json` first means later questions ("is field X present on Kind Y?")
  are answered from the local file instead of another round trip to the API server.

  Anti-pattern to avoid — three loops over the same list, ~21 invocations where 1 suffices:

  ```bash
  for c in <7 crds>; do kubectl get crd $c -o jsonpath='...spec.properties'; done   # ✗
  for c in <7 crds>; do kubectl get crd $c -o json | ... required/immutable; done   # ✗
  for c in <7 crds>; do kubectl get crd $c -o json | ... status; done               # ✗
  ```

  Same rule for `kubectl apply --dry-run=server`: pass repeated `-f` flags (or `-f <dir>`) in one invocation rather than shelling out per file.

## Workflow

### Phase 0: Bootstrap + Load Context (both paths)

**Step 0.1 — Bootstrap Python helpers (idempotent):**

```bash
bash scripts/bootstrap.sh
```

Run once per checkout. This creates `scripts/.venv/` and installs dependencies. The `scripts/run` wrapper picks the venv Python automatically on every call — do NOT run `source .venv/bin/activate`; you never call Python directly.

If bootstrap fails (missing Python ≥3.11, no network for pip), STOP and surface the error to the operator. Do not proceed to Phase 1 without a working `.venv`.

**Step 0.2 — Load references:** Do NOT load all references at once. Read the README index files first, then load only what the current phase needs.

**Always load first (both paths):**
1. `references/ack/README.md` — tells you which ACK file to load per phase
2. `references/kro/README.md` — tells you which KRO file to load per phase

Then load per-phase based on the selected path (tables below).

### Per-phase Reference Loading

**Adopt_Path:**

| Phase | References |
|---|---|
| 1 | `tf-state-schema.md`, `ack/aws-to-ack-mappings.md` **[S]** |
| 2 | `ack/adoption/adoption-patterns.md`, `ack/adoption/examples.md` |
| 3 | `kro/rgd-reference.md` **[S]**, `kro/building-abstractions.md` **[S]**, `kro/adoption/examples.md` |
| 4 | `kro/adoption/instances.md`, `ack/controller-permissions.md` **[S]** |

**Create_Path:**

| Phase | References |
|---|---|
| 1 | `tf-hcl-schema.md`, `ack/aws-to-ack-mappings.md` **[S]** |
| 2 | `ack/creation/creation-patterns.md`, `ack/creation/examples.md` |
| 3 | `kro/rgd-reference.md` **[S]**, `kro/building-abstractions.md` **[S]**, `kro/creation/hcl-to-rgd.md`, `kro/creation/examples.md` |
| 4 | `kro/creation/examples.md`, `ack/controller-permissions.md` **[S]** |

(**[S]** = Shared file, used by both paths)

### Full reference structure (after restructure):
```
references/
├── ack/
│   ├── README.md
│   ├── core-concepts.md
│   ├── controllers-catalog.md
│   ├── aws-to-ack-mappings.md        [S]
│   ├── controller-permissions.md      [S]
│   ├── adoption/
│   │   ├── adoption-patterns.md
│   │   └── examples.md
│   └── creation/
│       ├── creation-patterns.md
│       └── examples.md
├── kro/
│   ├── README.md
│   ├── rgd-reference.md               [S]
│   ├── overview.md
│   ├── building-abstractions.md       [S]
│   ├── adoption/
│   │   ├── instances.md
│   │   └── examples.md
│   └── creation/
│       ├── hcl-to-rgd.md
│       └── examples.md
├── tf-state-schema.md
├── tf-hcl-schema.md
└── known-limitations.md
```

---

## Adopt_Path Workflow

### Phase 1: Parse State & Discover Resources

**References:** `tf-state-schema.md`, `ack/aws-to-ack-mappings.md`

**Helper invocation (MANDATORY, do NOT parse tfstate by hand):**

```bash
./scripts/run discover \
  --tf-path <source-dir> \
  --state <path/to/terraform.tfstate> \
  --kubeconfig $KUBECONFIG \
  --out /tmp/migrate-set.json
```

`discover` produces `migrate-set.json` with: managed resources with attributes and sensitive-field redaction, HCL variables/outputs/locals/data, cross-references from both state and HCL, and a live-cluster CRD inventory under `cluster.ack_crds`. If `--kubeconfig` is omitted, `cluster.ack_crds` is empty and the skill treats mapping availability as unknown — the operator should re-run with a kubeconfig for a real classification.

Then reason over the emitted JSON:

1. Verify version 4 was parsed (`source.state_location` was set and `resources[]` is populated).
2. For each resource in `migrate-set.json`, look up its Terraform type against `cluster.ack_crds`. The mapping is derived at runtime — an ACK Kind exists iff a CRD in `*.services.k8s.aws` with that Kind is installed. `aws-to-ack-mappings.md` is a HINT only; the live CRD list is authoritative.
3. **🌐 Web-verify** each resource's ACK controller status per [Mandatory Web Verification](#mandatory-web-verification-all-phases) — especially for resources whose CRD is not in the current cluster (may be GA on another cluster).
4. Identify resources that ACK consolidates into parent CRs (e.g., `aws_iam_role_policy_attachment` → merged into Role).
5. The cross-reference and dependency graphs are already in `migrate-set.cross_refs`; use them for RGD `readyWhen` ordering.
6. Document any resources without an ACK Kind installed in the target cluster.

**Do NOT re-derive what `discover` already computed.** Before reasoning by hand, check
`migrate-set.json` for the answer:

| Need | Already in `migrate-set.json` | Don't do this instead |
|---|---|---|
| Dependency order for RGD wiring | `cross_refs[]` — `{from, to, source}`, populated from both state and HCL | Re-reading `attributes` to guess which resource references which |
| ACK `plural` for `adopted_resources` (Phase 3) and for `kubectl get crd` | `cluster.ack_crds[].plural` | Pluralizing the Kind by hand (`Policy` → `policys`) |
| Kind / group / scope / served version | `cluster.ack_crds[]` | A second `kubectl get crd` round trip |
| Which fields were redacted | `resources[].sensitive_fields` | Scanning attributes for `***REDACTED***` |
| Module attribution for `rekoncile.io/tf-module` | `resources[].module` | Parsing the TF address string |

Derive the dependency order by filtering `cross_refs` to adoptable-to-adoptable edges and
topologically sorting; do not hand-build the graph. Note that `cross_refs` includes edges to
**excluded** resources too (e.g. `aws_db_instance → random_password`), so filter to the
Class B set first.

**Output:** Classified list of adoptable resources with identifiers, dependencies, resource-group assignments, and Class A/B/C classification.

#### Phase_Checkpoint — Phase 1 (Adopt_Path)

At the end of this phase, report the following Decision_Summary and **pause** for operator validation before starting Phase 2.

**Decisions to report:**
- Resources discovered (full list with TF type, module, and extracted identifiers)
- Per-resource classification: **adoptable** (Class B) vs **Class A retained-in-TF** (excluded) vs **unsupported/no-ACK-equivalent**
- Inferred dependency order (the graph that will drive RGD `readyWhen` ordering)
- Resources identified for parent-CR consolidation (e.g., `aws_iam_role_policy_attachment` → will merge into Role)

**⚠️ Needs Attention — surface explicitly if present:**
- Any resource type with no clear ACK equivalent (no Kind mapping found in `aws-to-ack-mappings.md`)

**Then pause and present the three operator response options** (Confirm / Correct-Override / Proceed without further check-ins). See [Phase Decision Checkpoints](#phase-decision-checkpoints) for the full pattern.

---

### Phase 2: Generate ACK Adoption CRs

**References:** `ack/adoption/adoption-patterns.md`, `ack/adoption/examples.md`

**MANDATORY before authoring any CR — read and apply these `references/authoring-contract.json` sections:**

```bash
cat references/authoring-contract.json
```

Sections that are **binding rules, not suggestions**:

| Section | What it governs |
|---|---|
| `adoption_fields_by_kind` | Exact lookup JSON per ACK Kind — use verbatim, do not derive from mappings table alone |
| `rgd_template_rules.spec_field_values` | All spec field values MUST come from tfstate attributes — never invent |
| `rgd_template_rules.immutable_spec_fields` | These fields MUST use literals in RGD templates, never CEL |
| `naming_conventions.cr_metadata_name` | CR `metadata.name` = `<migration-name>-<kind-kebab-case>` — no exceptions |
| `naming_conventions.resource_filename` | Filename = `<kind-kebab-case>.yaml` — no exceptions |
| `consolidation_rules` | `aws_iam_role_policy_attachment` MUST populate `spec.policies` on the Role — never skip silently |
| `pre_write_crd_field_check` | Every spec field MUST exist in the live CRD — enumerate it, don't infer from AWS docs (unknown fields are silently pruned) |

**Validation gate:** before writing any CR file, confirm adoption-fields for that Kind against `adoption_fields_by_kind`. If the Kind is absent from the section, web-verify the controller source before proceeding. Then run `./scripts/run validate-spec-fields <resources-dir>` to confirm every spec field exists in the live CRD.

For each adoptable resource, generate an ACK CR. The **`adoption-policy`** value determines whether `spec` is empty or populated — the two policies mean fundamentally different things.

**Choose the adoption policy:**

| Policy | `spec` shape | Semantics | When to use |
|---|---|---|---|
| `adopt` (strict import) | **`spec: {}` — MUST be empty** | ACK reads the AWS resource and populates `spec` from live state. The CR describes "what exists". | Adopting existing brownfield resources; you want ACK to observe/manage as-is before making changes. **This is the default for Adopt_Path.** |
| `adopt-or-create` | **`spec:` fully populated** | ACK adopts if the resource exists, or creates it if missing. Reconciles current state toward your spec. Your spec is source of truth. | You want the CR to double as create-if-missing (rare in a strict adoption migration). |

Rules for `adoption-policy: adopt` (the Adopt_Path default):

- **🌐 Web-verify the CRD field schema before generating** — fetch the ACK API reference or CRD YAML to confirm exact field names, required fields, and nesting structure (see [Mandatory Web Verification](#mandatory-web-verification-all-phases)). Do NOT guess field names from Terraform attribute names.
- Use `services.k8s.aws/adoption-policy: adopt`.
- Use `services.k8s.aws/adoption-fields` with the lookup JSON per resource type (see the lookup table in `adoption-patterns.md`).
- **Ideal is `spec: {}`** per the doc — ACK will populate spec from live AWS state after adoption. This is the semantic contract.
- **CRD reality — most ACK CRDs still reject `spec: {}` at OpenAPI validation** even in adopt mode. The doc contract has not been fully implemented at the CRD schema level yet. In this case:
  - Populate ONLY the CRD-required fields (the OpenAPI `required` list on `.spec`), with values pulled from TF state.
  - Add a comment `# adopt: minimum spec to satisfy CRD OpenAPI; ACK will re-populate from live state`.
  - Do NOT populate optional fields — the whole point is to let ACK observe reality, not have your CR fight ACK's reconcile with an incomplete spec.
- Confirmed via live `kubectl apply --dry-run=server` on ACK v1.x controllers that `spec: {}` is rejected for: `iam.services.k8s.aws/Role`, `iam.services.k8s.aws/Policy`, `s3.services.k8s.aws/Bucket`, `rds.services.k8s.aws/DBInstance`. Empty spec has been observed to work on some other CRDs. Treat each Kind as needing verification via dry-run.
- Always set `services.k8s.aws/deletion-policy: retain`.
- Set `services.k8s.aws/region` for regional resources.

Rules for `adoption-policy: adopt-or-create` (used only when justified):

- Populate spec fully — same as if this were a Create_Path CR — because if the resource is missing, ACK creates from your spec.
- Adoption-fields still supplied so ACK looks up before deciding to create.
- deletion-policy: retain STILL mandatory during the migration window.

For resources not covered in local references, fetch examples from:
`https://github.com/aws-controllers-k8s/<service>-controller/tree/main/test/e2e/resources`

To validate field names, check CRD schemas at:
`https://github.com/aws-controllers-k8s/<service>-controller/tree/main/helm/crds`

**Key rules:**
- `aws_iam_role_policy_attachment` is NOT a separate CR — policy ARN goes into Role's `policies` field after adoption
- Tags use array format: `[{key: K, value: V}]`
- Policy documents are JSON strings (use `|` for multiline)

**Guards:**
- IF any required output file cannot be created, THEN prevent ACK adoption CR generation (Req 5.3)
- IF a resource is in a state that prevents clean adoption (conflicting config, missing prerequisites), THEN fail and require manual intervention (Req 12.2)

#### Phase_Checkpoint — Phase 2 (Adopt_Path)

At the end of this phase, report the following Decision_Summary and **pause** for operator validation before starting Phase 3.

**Decisions to report:**
- Per-resource Kind/apiVersion mapping (what each TF resource type mapped to)
- Resources merged into a parent CR (e.g., `aws_iam_role_policy_attachment` → `Role.policies`)
- Adoption-annotation decisions: which lookup field was chosen per resource, `adoption-policy: adopt` applied, `deletion-policy: retain` set
- Required spec fields populated from TF state (per CRD validation requirements)

**⚠️ Needs Attention — surface explicitly if present:**
- Any resource type with no clear ACK equivalent that was deferred from Phase 1 and still unresolved

**Then pause and present the three operator response options** (Confirm / Correct-Override / Proceed without further check-ins). See [Phase Decision Checkpoints](#phase-decision-checkpoints) for the full pattern.

---

### Phase 3: Generate KRO ResourceGraphDefinition

**References:** `kro/rgd-reference.md`, `kro/building-abstractions.md`, `kro/adoption/examples.md`

**MANDATORY before authoring the RGD — apply these `references/authoring-contract.json` sections:**

| Section | What it governs |
|---|---|
| `naming_conventions.rgd_resource_id` | RGD `id:` = Kind → camelCase (lowercase first char) — this becomes the `kro.run/node-id` label; workshop commands depend on it |
| `naming_conventions.rgd_schema_status_field` | Status field names = `<rgd_resource_id>ARN` |
| `naming_conventions.canonical_schema_spec_field_names` | Schema spec field names are fixed — use the canonical names to prevent KRO breaking-change errors on re-generation |
| `rgd_template_rules.namespace` | Every resource template metadata MUST include `namespace:` — use `${schema.metadata.namespace}` |
| `rgd_template_rules.template_top_level_keys` | A template's top-level keys are ONLY `apiVersion`, `kind`, `metadata`, `spec` (+ `data`/`stringData`/`type` for native ConfigMap/Secret, `rules` for RBAC). `annotations`/`labels` nest under `metadata` — a peer-of-`metadata` block passes dry-run and then wedges the RGD at reconciliation |
| `rgd_template_rules.immutable_spec_fields` | Use literals (not CEL) for immutable fields: `Secret.spec.name`, `DBInstance.spec.dbInstanceIdentifier` |
| `rgd_template_rules.readyWhen_scope` | `readyWhen` references ONLY the resource's own `id` — never a sibling or `schema`. Ordering comes from `${sibling.status.*}` interpolation in template fields |
| `rgd_template_rules.adoption_fields_templating` | CEL inside `services.k8s.aws/adoption-fields` is SUPPORTED and intended — parameterize the lookup on `${schema.spec.*}`, never "fix" it to a literal. No sibling `status` references (the lookup resolves before creation) |

Wrap ACK CRs into a KRO RGD for dependency management:
- One TF module → One RGD
- Order resources by dependency graph (no-deps first → dependents last)
- **`readyWhen` references ONLY the resource's own `id`** (see the readyWhen rule below)
- **Ordering between resources is expressed by interpolating a sibling's status in a template field** (`${sibling.status.ackResourceMetadata.arn}`), never in `readyWhen`
- Schema `spec` fields = inputs needed for adoption-fields interpolation (name, region, accountId, clusterName, etc.)
- Schema `status` fields = TF state outputs (ARNs, endpoints)

**⚠️ `readyWhen` scope (MANDATORY — verified against [kro.run readiness docs](https://kro.run/docs/concepts/rgd/resource-definitions/readiness/)):**

A resource's `readyWhen` may reference ONLY that same resource (by its own `id`). It MUST NOT reference another resource or `schema`. kro validates this at RGD creation and rejects cross-resource `readyWhen` with `references unknown identifiers: [<other-id>]`.

Cross-resource ordering is NOT expressed in `readyWhen`. kro infers the dependency graph from CEL references in **template fields**: when resource A's template interpolates `${b.status...}`, kro creates B first, waits for B's own `readyWhen`, then creates A. (See [kro.run graph inference](https://kro.run/docs/concepts/rgd/dependencies-ordering/).)

```yaml
# ✓ CORRECT — readyWhen references only itself; ordering comes from the template interpolation
- id: iamPolicy
  readyWhen:
    - ${iamPolicy.status.?ackResourceMetadata.arn.orValue("") != ""}   # own id only
  template:
    ...

# ✗ WRONG — readyWhen references a sibling (secret). kro rejects: references unknown identifiers: [secret]
- id: iamPolicy
  readyWhen:
    - ${secret.status.ackResourceMetadata.arn != ""}
```

**When a resource has no natural sibling reference in its template** (e.g. an adopted `DBInstance` or `PodIdentityAssociation` whose spec is all tfstate literals), and you still need it ordered after a sibling, create the implicit dependency by interpolating the sibling's status into a traceability annotation on that resource's template — not by touching `readyWhen`:

```yaml
- id: podIdentity
  template:
    metadata:
      annotations:
        rekoncile.io/depends-on-role: ${role.status.ackResourceMetadata.arn}   # forces ordering after role
    ...
```

#### Phase_Checkpoint — Phase 3 (Adopt_Path)

At the end of this phase, report the following Decision_Summary and **pause** for operator validation before starting Phase 4.

**Decisions to report:**
- Schema `spec` field derivation (which inputs feed the RGD spec: name, region, accountId, clusterName, etc.)
- Schema `status` field derivation (which TF state outputs map to RGD status fields)
- Dependency wiring / `readyWhen` ordering (the resource order enforced via `status.ackResourceMetadata.arn`)
- Reconciliation decisions applied from the Authority_Doc:
  - `group:` field usage (defaulted to `kro.run` or explicit)
  - `externalRef` usage for read-only references
  - `forEach` form selected
  - `omit()` vs `includeWhen` choice (with `CELOmitFunction` feature gate note if `omit()` used)

**⚠️ Needs Attention — surface explicitly if present:**
- Any reconciliation decision where the Authority_Doc was ambiguous or no clear precedent exists

**Then pause and present the three operator response options** (Confirm / Correct-Override / Proceed without further check-ins). See [Phase Decision Checkpoints](#phase-decision-checkpoints) for the full pattern.

---

### Phase 4: Generate Instance + Documentation + Ground + Report

**References:** `kro/adoption/instances.md`, `ack/controller-permissions.md`

Generate:
1. **Instance YAML** — populated with values extracted from TF state (names, ARNs, region, etc.)
2. **MIGRATION-NOTES.md** — resources adopted, resources skipped, validation steps, **required IAM permissions**

**⚠️ Author each decision ONCE. `MIGRATION-NOTES.md` and `decisions.json` have different jobs — do not let the notes restate the phase narrative.**

| Artifact | Sole responsibility | MUST NOT contain |
|---|---|---|
| `decisions.json` → `report.html` | The phase-by-phase decision record, per-checkpoint needs-attention items, operator responses | Operator runbook commands |
| `MIGRATION-NOTES.md` | **Operator-actionable content only:** apply order, verification commands, IAM/RBAC requirements, unsupported TF attributes, rollback procedure | A re-narration of Phase 1–4 decisions — link to `report.html` instead |
| Chat Phase_Checkpoint summary | The pause itself, so the operator can respond | — |

Write the notes as if the reader has `report.html` open in another tab. For decision
*rationale*, cross-reference rather than restate:

```markdown
> Decision record and resource graph: see `report.html` § Phase decisions.
```

A ~15 KB MIGRATION-NOTES.md on a 7-resource stack is a smell — it means the phase
narrative was copied in. Target the operator-actionable subset.

**Grounding loop (MANDATORY before the Phase_Checkpoint):**

**⚠️ Use `validate-all` — do NOT also run the three validators serially.** If the inline
per-resource gates already ran during Phases 2–3 (as Phase 2 requires), a serial Phase 4
re-run repeats identical work for no new signal — measured at ~20s of pure duplication on
a 1-RGD stack, and it scales with RGD count. One call covers every `(mode, rgd)` tuple
concurrently and writes the per-mode `findings.json` that `render-report` consumes:

```bash
./scripts/run validate-all \
  --output <output-dir>/ \
  --context $KUBECTL_CONTEXT \
  --workers 8
```

Exit code is non-zero iff any tuple has a `severity: error` finding. Prefer this as the
default. Reach for the three serial commands below **only** when `validate-all` reports an
error and you need to isolate one validator's output, or when debugging a single file.

<details>
<summary>Serial per-validator invocations (isolation/debugging only)</summary>

After writing every YAML to `<output-dir>/`, run:

```bash
./scripts/run validate-spec-fields <output-dir>/ \
  --kubeconfig $KUBECONFIG \
  --format json > /tmp/spec-fields-findings.json

./scripts/run validate-manifest \
  --dir <output-dir>/ \
  --kubeconfig $KUBECONFIG \
  --mode adopt \
  --format json > /tmp/manifest-findings.json

./scripts/run validate-cel <output-dir>/ --format json > /tmp/cel-findings.json
```

</details>

`validate-manifest` runs `kubectl apply --dry-run=server` for every file (real CRD schema check against the target cluster) AND enforces adoption-annotation invariants (adopt CRs MUST carry `services.k8s.aws/adoption-policy` and `services.k8s.aws/deletion-policy: retain`; the check only applies to ACK CRs — RGDs and native objects are skipped). Every `severity: error` finding MUST be fixed before the Phase_Checkpoint; treat them as feedback for another authoring pass, then re-run validation. Do NOT proceed with unresolved errors — the previous CLI's retry-with-feedback loop is replaced by this explicit skill-driven cycle.

`validate-cel` catches CEL grammar errors in `readyWhen` and `${…}` interpolations. Same rule: fix errors and re-run before the Phase_Checkpoint.

**Do NOT dismiss an `instance.yaml` dry-run error as "the CRD does not exist yet."** Distinguish the two cases before deciding:

  - `no matches for kind "<Kind>" in version "kro.run/v1alpha1"` — the generated CRD is genuinely absent because the RGD has not been applied. Expected at this phase; note it and continue.
  - `unknown field "spec.<field>"` — the CRD **exists** and its published schema does not contain that field. This is a real defect in `rgd.yaml`'s `schema.spec` or in `instance.yaml`, and it is frequently the first visible symptom of an RGD that is already wedged in the cluster. Confirm with:

  ```bash
  kubectl get crd <plural>.kro.run \
    -o jsonpath='{.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties}'

  kubectl get resourcegraphdefinition <rgd-name> \
    -o jsonpath='{.status.state}{"\n"}{range .status.conditions[*]}{.type}={.status} {.message}{"\n"}{end}'
  ```

  If `GraphAccepted=False`, the published CRD is stale relative to your `rgd.yaml` and kro will not republish it. Fix the RGD, then `kubectl delete rgd <rgd-name>` before reapplying — an in-place `kubectl apply` does not clear the condition. A `state: Inactive` RGD means no instance can reconcile, so this blocks the Phase_Checkpoint.

**Report rendering (MANDATORY once validation is clean):**

If you used `validate-all` (the default), it has already written `<output-dir>/<mode>/findings.json` — pass that straight to `render-report`, no merge needed:

```bash
cp <output-dir>/adopt/findings.json /tmp/findings.json
```

Only if you ran the serial validators for isolation: merge `/tmp/spec-fields-findings.json`, `/tmp/manifest-findings.json` and `/tmp/cel-findings.json` into a single `/tmp/findings.json` (concatenate the `findings` arrays). Then write the operator report:

```bash
./scripts/run render-report \
  --migrate-set /tmp/migrate-set.json \
  --decisions /tmp/decisions.json \
  --manifests-dir <output-dir>/ \
  --findings /tmp/findings.json \
  --out <output-dir>/report.html
```

`decisions.json` is the running record of Phase_Checkpoint outcomes the skill has kept throughout the run (see [Skill obligations](#helper-scripts-python) above). The generated `report.html` is a self-contained single page containing the decision framework, the migration report, and a Cytoscape resource graph (RGD abstraction → ACK CRs → underlying AWS resources → TF-retained). Open it in a browser to review before adopt/apply.

#### Permissions Analysis (MANDATORY in MIGRATION-NOTES.md)

The MIGRATION-NOTES.md **MUST** include a permissions guidance section:

1. **Required ACK Controllers** — list which controllers are needed (s3, iam, eks, etc.)
2. **Permissions table** — for each service, state the minimum actions needed and whether a common AWS managed policy covers them

4. **Diagnosis command** — show `kubectl describe <kind> <name> | grep -A3 "ACK.Recoverable"` for post-apply troubleshooting

**Key rules:**
- Do NOT generate IAM policy JSON documents — the ACK capability role is managed externally (CDK, Terraform, console)
- Do NOT include permissions for services that are covered by standard managed policies without flagging them
- Only flag services with **known gaps** (actions not in any managed policy)
- The goal is to **alert** the user, not to fix their IAM setup

#### KRO Kubernetes RBAC (Informational Note in MIGRATION-NOTES.md)

KRO needs **Kubernetes RBAC permissions** (not AWS IAM) to create and manage ACK custom resources inside the cluster. This is a separate layer from IAM:

| Layer | Controls | Who needs it |
|-------|----------|--------------|
| AWS IAM | API calls to AWS (s3:GetBucket, etc.) | ACK controller role |
| Kubernetes RBAC | Access to CRDs/CRs inside the cluster | KRO controller role |

**The problem (most likely self-managed deployments):** KRO creates ACK CRs (Buckets, Roles, Policies) as part of reconciling a ResourceGraphDefinition. If the KRO role doesn't have RBAC access to those API groups (`s3.services.k8s.aws`, `iam.services.k8s.aws`, etc.), reconciliation fails with:
```
"buckets" is forbidden: User "...-kro-capability/KRO" cannot get resource "buckets"
in API group "s3.services.k8s.aws" in the namespace "default"
```

**Solution:** The KRO capability role needs an EKS access entry with cluster admin permissions (or a scoped ClusterRole/ClusterRoleBinding granting access to the ACK API groups used in the RGD).


**MIGRATION-NOTES.md must include (as an informational note, not a mandatory action):**
1. A note that KRO needs Kubernetes RBAC to manage ACK resources (relevant for self-managed deployments)
2. The specific ACK API groups involved (based on the resources in the migration)
3. Guidance: either configure an EKS access entry for the KRO role, or apply a ClusterRole/ClusterRoleBinding

#### Phase_Checkpoint — Phase 4 (Adopt_Path)

At the end of this phase, report the following Decision_Summary and **pause** for operator validation before finalizing the output.

**Decisions to report:**
- Instance values populated (names, ARNs, region, account ID — all extracted from TF state)
- Noted permission gaps (services where no AWS managed policy covers required actions — informational for self-managed deployments)
- KRO Kubernetes RBAC note + ACK API groups involved (informational for self-managed deployments; handled automatically by EKS Auto Mode managed capabilities)
- `ownerReferences` flag state: whether `ownerReferences` was included or omitted on adopted resources

**⚠️ Needs Attention — surface explicitly:**
- **`ownerReferences` cascade-delete warning (ALWAYS surface at this checkpoint):** The pending default is to **omit** `ownerReferences` (rollback-safe). If `ownerReferences` is set on adopted resources and the KRO ResourceGraphDefinition instance is later deleted, Kubernetes garbage collection will **cascade-delete the ACK CRs**, which in turn deletes the **live AWS resources** (unless `deletion-policy: retain` catches them). The operator must deliberately opt in or confirm omission before the output is finalized.
- Any permission gaps that require attention before `kubectl apply` (self-managed deployments only)

**Then pause and present the three operator response options** (Confirm / Correct-Override / Proceed without further check-ins). See [Phase Decision Checkpoints](#phase-decision-checkpoints) for the full pattern.

---

### Output layout (both paths, MANDATORY)

Output is grouped **per RGD**, not per file. One Terraform stack may produce N RGDs (one per logical unit — app-tier, data-tier, network-tier, etc.). Each RGD gets its own subdirectory containing the RGD, its instance, and the individual ACK CRs the RGD would render:

```
<output-dir>/
├── report.html                  # skill-level combined report (all RGDs)
├── MIGRATION-NOTES.md           # skill-level migration notes
├── <rgd-name-1>/
│   ├── rgd.yaml                 # KRO ResourceGraphDefinition (parameterized)
│   ├── instance.yaml            # Claim/Instance — RGD input document
│   └── resources/               # Individual ACK CRs (derived, review-only)
│       ├── <resource-1>.yaml
│       ├── <resource-2>.yaml
│       └── ...
└── <rgd-name-2>/
    └── ...
```

**Key rules for the layout:**

1. **RGDs are authored, not the ACK CRs.** `rgd.yaml` is the source of truth. Files under `resources/` are the concrete manifests that WOULD be created when the RGD reconciles this instance — they exist so operators can review each resource before applying, and so `validate-manifest` can `kubectl dry-run` against real CRD schemas. Do NOT edit `resources/*.yaml` directly; edit the RGD template.
2. **One Terraform module may split into multiple RGDs** when the resources form distinct logical units (e.g., `network-stack`, `data-stack`, `app-stack`). Group by ownership per the Phase 1 grouping decisions, not by AWS service.
3. **Adopt and Create produce SEPARATE RGDs**, even for the same Terraform. Their spec schemas differ meaningfully — adopt takes live AWS IDs, create takes configuration parameters — and unifying them via CEL conditionals loses more clarity than it saves. Emit as `<out>/adopt/<rgd-name>/…` and `<out>/create/<rgd-name>/…` when `--mode both` (default).

### Adopt_Path RGD contract (brownfield)

The adopt RGD MUST be **parameterized on the AWS resource IDs**, not carry them hard-coded. The instance file is what supplies the IDs. This makes a single adopt RGD reusable to migrate the same stack shape in a different account or region — just supply a different instance.

**RGD schema (adopt-mode) — required shape:**

```yaml
apiVersion: kro.run/v1alpha1
kind: ResourceGraphDefinition
metadata:
  name: <rgd-name>-adopt
  labels:
    # Provenance — every RGD carries these:
    rekoncile.io/source-type: terraform
    rekoncile.io/source-path: <relative-tf-dir-with-slashes-replaced-by-dashes>
    rekoncile.io/mode: adopt
spec:
  schema:
    apiVersion: v1alpha1
    kind: <PascalCaseName>Adopt
    spec:
      # One field per adopted resource. Field name matches the resource id
      # inside spec.resources[] below. Each field is a string (the AWS ID or
      # ARN that ACK's adoption-fields will look up).
      vpcID: string
      appIrsaRoleName: string
      s3PolicyName: string
      dbPolicyName: string
      bucketName: string
      dbInstanceIdentifier: string
      region: string
    status:
      # Expose ACK-resolved ARNs so downstream RGDs can reference them.
      vpcARN: ${vpc.status.ackResourceMetadata.arn}
      appIrsaARN: ${appIrsa.status.ackResourceMetadata.arn}
      # ...
  resources:
    - id: vpc
      template:
        apiVersion: ec2.services.k8s.aws/v1alpha1
        kind: VPC
        metadata:
          name: <name>-vpc
          labels:
            rekoncile.io/tf-type: aws_vpc
          annotations:
            # adoption-fields wired from instance input — enables per-region/-account reuse
            services.k8s.aws/adoption-policy: adopt
            services.k8s.aws/adoption-fields: '{"vpcID":"${schema.spec.vpcID}"}'
            services.k8s.aws/deletion-policy: retain
            services.k8s.aws/region: ${schema.spec.region}
            rekoncile.io/from-tf-address: module.vpc.aws_vpc.this[0]
        spec: {}    # strict import — ACK populates from live state
```

**Instance file for adopt (`instance.yaml`) — literal AWS IDs from the current stack:**

```yaml
apiVersion: kro.run/v1alpha1
kind: <PascalCaseName>Adopt
metadata:
  name: <name>
  labels:
    rekoncile.io/source-type: terraform
    rekoncile.io/source-path: <relative-tf-dir-with-slashes-replaced-by-dashes>
spec:
  vpcID: vpc-0b49aee86cbef6647             # from module.vpc.aws_vpc.this[0].id
  appIrsaRoleName: rekoncile-demo-dev-app-irsa  # from aws_iam_role.app_irsa.name
  # ... one line per adoptable resource, with a comment naming the TF address
  region: us-west-2
```

Comments citing the TF source address MUST accompany every literal — this is how the operator (and future you) traces where each ID came from. To migrate the same stack shape in a different account or region, only `instance.yaml` needs edits; the RGD does not change.

### Traceability labels (BOTH paths, MANDATORY)

Every emitted YAML — RGD, instance, and each child under `resources/` — MUST carry these labels/annotations so operators can trace every value back to its Terraform origin.

**On the RGD itself (`metadata.labels`):**

| Label | Value | Purpose |
|---|---|---|
| `rekoncile.io/source-type` | `terraform` | What produced this |
| `rekoncile.io/source-path` | e.g. `module-5-rds-tf-baseline` (replace every `/` with `-`) | Where in the repo |
| `rekoncile.io/mode` | `adopt` \| `create` | Which path emitted it |
| `rekoncile.io/tf-module` (optional) | e.g. `module.vpc` | Which TF module this RGD represents |

**On each `spec.resources[N].template` inside the RGD AND on each rendered CR (`metadata.labels`):**

| Label | Value | Purpose |
|---|---|---|
| `rekoncile.io/tf-type` | e.g. `aws_vpc` | Original TF resource type (label-safe) |
| `rekoncile.io/rgd` | e.g. `rekoncile-demo-app-stack-adopt` | Which RGD owns this resource (label-safe) |
| `rekoncile.io/mode` | `adopt` \| `create` | Which path emitted it |

Do NOT put the raw TF address in a label. Kubernetes label values are constrained to `[A-Za-z0-9._-]` and must start/end with an alphanumeric; TF addresses contain `[`, `]`, and dot-heavy paths that fail validation. Live-cluster kubectl dry-run rejects it — verified against `rekoncile-demo`.

**⚠️ `rekoncile.io/source-path` sanitization (MANDATORY):** The `Source:` input argument often contains `/` path separators (e.g. `module-5/rds-tf-baseline`). `/` is illegal in Kubernetes label values (`[A-Za-z0-9._-]` only). Before writing this label on ANY object — RGD, instance, or resource template — replace every `/` with `-`. Example: `module-5/rds-tf-baseline` → `module-5-rds-tf-baseline`. Failure produces a `kubectl apply` validation error on both the RGD and the instance.

**On each rendered ACK CR under `resources/` (`metadata.annotations`):**

Annotations have no regex constraints, so exact TF traceability lives here.

| Annotation | Value | Purpose |
|---|---|---|
| `rekoncile.io/from-tf-address` | e.g. `module.vpc.aws_vpc.this[0]` | Exact TF address this resource maps to |
| `rekoncile.io/tf-attributes` | JSON blob | Map of `{ackField: "tf.attribute.path"}` for every populated field, e.g. `{"spec.cidrBlocks[0]":"attributes.cidr_block"}` |

The `rekoncile.io/tf-attributes` annotation is verbose but load-bearing — it's what makes the report's "Where did this value come from?" tooltip work.

### Adopt_Path Output Files

1. `<output-dir>/adopt/<rgd-name>/rgd.yaml` — parameterized adoption RGD
2. `<output-dir>/adopt/<rgd-name>/instance.yaml` — literal AWS IDs from this stack (reusable per env by editing this file only)
3. `<output-dir>/adopt/<rgd-name>/resources/*.yaml` — rendered adoption CRs for review + kubectl dry-run
4. `<output-dir>/MIGRATION-NOTES.md` — skill-level notes covering all RGDs
5. `<output-dir>/report.html` — self-contained single-page report

### Adopt_Path — Optional Post-Phase 4 Verification

After the operator has reviewed `report.html` and applied the manifests, confirm ACK has taken over each resource with:

```bash
./scripts/run adopt \
  --decisions /tmp/decisions.json \
  --manifests-dir <output-dir>/ \
  --kubeconfig $KUBECONFIG
```

`adopt` applies the ACK CRs (skip with `--skip-apply` if already applied) and polls each CR for `ACK.ResourceSynced=True` (per Key Principle 1: adopt-verify-control), reporting which resources are confirmed adopted. `decisions.json` MUST include an `adopted_resources` array giving `{address, group, version, plural, namespace, name, kind}` for every CR that should be reconciled — the skill populates this during Phase 3 of the Adopt_Path.

**Terraform state is left untouched (Key Principle 7).** This tool never runs `terraform state rm`; `terraform.tfstate` remains a valid rollback backup. Terraform and ACK co-manage the live resources — just do not run `terraform apply`/`terraform destroy` against the adopted resources afterwards.

### Adopt_Path MIGRATION-NOTES Validation Steps

The generated MIGRATION-NOTES.md MUST include:
1. Post-adoption validation: confirm `READY: True`, `status.ackResourceMetadata.arn` populated, `ACK.ResourceSynced: True`
2. Required ACK controllers list
5. Diagnosis command: `kubectl describe <kind> <name> | grep -A3 "ACK.Recoverable"`

---

## Create_Path Workflow

### Phase 1: Parse HCL & Discover Resources

**References:** `tf-hcl-schema.md`, `ack/aws-to-ack-mappings.md`

**Helper invocation (MANDATORY, do NOT parse HCL by hand):**

```bash
./scripts/run discover \
  --tf-path <source-dir> \
  --kubeconfig $KUBECONFIG \
  --out /tmp/migrate-set.json
```

For the Create_Path, `--state` is typically omitted (HCL is the input). If a tfstate is also available, pass `--state` too so the skill can cross-check attribute defaults against a real deployment.

Then reason over the emitted JSON:

1. Verify `hcl.resources[]`, `hcl.variables[]`, `hcl.outputs[]`, `hcl.locals`, and `hcl.data[]` were populated.
2. Map each `hcl.resources[]` type against `cluster.ack_crds` (the live CRD inventory is authoritative; `aws-to-ack-mappings.md` is a hint only).
3. **🌐 Web-verify** each resource's ACK controller status per [Mandatory Web Verification](#mandatory-web-verification-all-phases).
4. Record resource types with no ACK equivalent for the operator summary.
5. Identify resources that ACK consolidates into parent CRs.
6. The HCL-derived dependency graph is already in `migrate-set.cross_refs` (entries with `"source": "hcl"`).

**Output:** Classified list of resources with their ACK Kind mappings (from live CRDs), variables, outputs, and dependency relationships.

#### Phase_Checkpoint — Phase 1 (Create_Path)

At the end of this phase, report the following Decision_Summary and **pause** for operator validation before starting Phase 2.

**Decisions to report:**
- Resources discovered (full list with TF resource type and extracted variable/output/locals/data blocks)
- Per-resource classification: **has ACK equivalent** (Kind/apiVersion found) vs **unsupported/no-ACK-equivalent** (recorded for output summary)
- Resources identified for parent-CR consolidation
- Inferred dependency order (from HCL inter-resource references)

**⚠️ Needs Attention — surface explicitly if present:**
- Any resource type with no clear ACK equivalent (no Kind mapping found in `aws-to-ack-mappings.md`)

**Then pause and present the three operator response options** (Confirm / Correct-Override / Proceed without further check-ins). See [Phase Decision Checkpoints](#phase-decision-checkpoints) for the full pattern.

---

### Phase 2: Generate ACK CRs (no adoption annotations)

**References:** `ack/creation/creation-patterns.md`, `ack/creation/examples.md`

**⚠️ MANDATORY before writing any spec field — enumerate the LIVE CRD fields:**

```bash
kubectl get crd <plural>.<group> \
  -o jsonpath='{.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties}' \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(sorted(d.keys()))"
```

Every spec field you write MUST appear in this output. AWS SDK/API parameters and Terraform lifecycle arguments are **not** the same set as ACK CRD spec fields, so reading the AWS service docs is not evidence that a field exists. If a TF attribute has no matching CRD field, **omit it** and record it in MIGRATION-NOTES.md under "Unsupported TF attributes".

Why this is a hard gate: unknown fields in a CR are **silently pruned** by the API server, so `kubectl apply --dry-run=server` will not flag them (the value is just lost); inside an RGD template kro fails the type check with `schema not found for field <name>`. See `references/authoring-contract.json` → `pre_write_crd_field_check` for the rule and a (non-authoritative) snapshot of known gaps — the live enumeration above always wins.

**⚠️ MANDATORY — write each CR file to disk immediately after authoring it:**

Do NOT accumulate CR content in memory and write everything at Phase 4. Write each `resources/<kind-kebab-case>.yaml` as soon as its content is final, then optionally gate it right away:

```bash
./scripts/run validate-spec-fields <output-dir>/create/<rgd-name>/resources/
```

For each resource with an ACK equivalent, generate a full-spec ACK CR:
- **Write `resources/<kind-kebab-case>.yaml` to disk immediately after authoring each CR**
- **🌐 Web-verify the CRD field schema before generating** — fetch the ACK API reference or CRD YAML to confirm exact field names, required fields, and nesting structure (see [Mandatory Web Verification](#mandatory-web-verification-all-phases)). Do NOT guess field names from Terraform attribute names.
- Populate the full resource spec from Terraform variables (Req 4.4)
- Do NOT include any ACK adoption annotations (`adoption-policy`, `adoption-fields`, `deletion-policy`)
- Set `services.k8s.aws/region` for regional resources

**Guards:**
- IF a Create_Path ACK CR contains an ACK adoption annotation, THEN fail the ACK CR generation immediately (Req 4.5). This is a hard failure — adoption annotations have no place in create-mode CRs.

**Mappings:**
- Terraform `variable` blocks → populate ACK CR spec fields (type/default/description preserved) (Req 4.4)
- Terraform resource attributes → ACK CR spec fields at their corresponding paths

#### Phase_Checkpoint — Phase 2 (Create_Path)

At the end of this phase, report the following Decision_Summary and **pause** for operator validation before starting Phase 3.

**Decisions to report:**
- Per-resource Kind/apiVersion mapping (what each TF resource type mapped to)
- Resources merged into a parent CR (e.g., `aws_iam_role_policy_attachment` → `Role.policies`)
- Full-spec-from-variable decisions: which Terraform variables populated which ACK CR spec fields
- Confirmation that no adoption annotations are present (the fail-on-annotation guard passed)

**⚠️ Needs Attention — surface explicitly if present:**
- Any ambiguous variable→spec mapping (variable type/name doesn't clearly map to a single ACK spec field)

**Then pause and present the three operator response options** (Confirm / Correct-Override / Proceed without further check-ins). See [Phase Decision Checkpoints](#phase-decision-checkpoints) for the full pattern.

---

### Phase 3: Generate Self-Serve KRO ResourceGraphDefinition

**References:** `kro/rgd-reference.md`, `kro/building-abstractions.md`, `kro/creation/hcl-to-rgd.md`, `kro/creation/examples.md`

**⚠️ MANDATORY — write `rgd.yaml` to disk immediately after the RGD spec is complete. Do NOT defer the write to Phase 4.**

Generate an RGD that acts as a self-serve abstraction:
- One TF module → One RGD
- Map Terraform `variable` blocks → RGD `spec` schema fields (preserve type, default, description) (Req 4.2)
- Map Terraform `output` blocks → RGD `status` fields expressed with CEL (Req 4.3)
- Map each Terraform `resource` block → RGD resource template using the ACK Kind/apiVersion (Req 4.6)
- Order resources by dependency graph (no-deps first → dependents last)
- **`readyWhen` references ONLY the resource's own `id`; express ordering by interpolating a sibling's status in a template field** (never reference a sibling or `schema` in `readyWhen` — kro rejects it as `references unknown identifiers`). See the readyWhen scope rule in the Adopt_Path Phase 3 above and [kro.run readiness docs](https://kro.run/docs/concepts/rgd/resource-definitions/readiness/).
- Link to `kro/rgd-reference.md` for schema syntax, CEL, `readyWhen`, `forEach` guidance

#### Phase_Checkpoint — Phase 3 (Create_Path)

At the end of this phase, report the following Decision_Summary and **pause** for operator validation before starting Phase 4.

**Decisions to report:**
- Schema `spec` field derivation (which Terraform `variable` blocks → which RGD spec fields, with type/default/description preserved)
- Schema `status` field derivation (which Terraform `output` blocks → which RGD status fields, expressed with CEL)
- Resource template mapping (each TF `resource` → ACK Kind/apiVersion in the RGD)
- Dependency wiring / `readyWhen` ordering (the resource order enforced via `status.ackResourceMetadata.arn`)
- Reconciliation decisions applied from the Authority_Doc:
  - `group:` field usage (defaulted to `kro.run` or explicit)
  - `externalRef` usage for read-only references
  - `forEach` form selected
  - `omit()` vs `includeWhen` choice (with `CELOmitFunction` feature gate note if `omit()` used)

**⚠️ Needs Attention — surface explicitly if present:**
- Any ambiguous variable→spec mapping where the Terraform variable type/name doesn't clearly map to a single RGD spec field
- Any reconciliation decision where the Authority_Doc was ambiguous or no clear precedent exists

**Then pause and present the three operator response options** (Confirm / Correct-Override / Proceed without further check-ins). See [Phase Decision Checkpoints](#phase-decision-checkpoints) for the full pattern.

---

### Phase 4: Generate Instance Example + Documentation + Ground + Report

**References:** `kro/creation/examples.md`, `ack/controller-permissions.md`

Generate:
1. **Instance YAML** — populated with example values that satisfy all required RGD schema fields (Req 4.8)
2. **MIGRATION-NOTES.md** (or equivalent docs) — resources generated, unsupported resources recorded, permission guidance

**⚠️ Author each decision ONCE. `MIGRATION-NOTES.md` and `decisions.json` have different jobs — do not let the notes restate the phase narrative.**

| Artifact | Sole responsibility | MUST NOT contain |
|---|---|---|
| `decisions.json` → `report.html` | The phase-by-phase decision record, per-checkpoint needs-attention items, operator responses | Developer-facing usage instructions |
| `MIGRATION-NOTES.md` | **Actionable content only:** how a developer consumes the self-serve RGD, required RGD schema inputs, IAM/RBAC requirements, unsupported TF resources and attributes | A re-narration of Phase 1–4 decisions — link to `report.html` instead |
| Chat Phase_Checkpoint summary | The pause itself, so the operator can respond | — |

Write the notes as if the reader has `report.html` open in another tab. For decision
*rationale*, cross-reference rather than restate:

```markdown
> Decision record and resource graph: see `report.html` § Phase decisions.
```

On the Create_Path the notes' job is the **developer contract** for the published
abstraction (what to put in an instance, what each schema field means), not a migration
narrative. If the notes are mostly phase history, the split has been lost.

**Grounding loop (MANDATORY before the Phase_Checkpoint):**

**⚠️ Use `validate-all` — do NOT also run the three validators serially.** If the inline
per-resource gates already ran during Phases 2–3 (as Phase 2 requires), a serial Phase 4
re-run repeats identical work for no new signal — measured at ~20s of pure duplication on
a 1-RGD stack, and it scales with RGD count. One call covers every `(mode, rgd)` tuple
concurrently and writes the per-mode `findings.json` that `render-report` consumes:

```bash
./scripts/run validate-all \
  --output <output-dir>/ \
  --context $KUBECTL_CONTEXT \
  --workers 8
```

Exit code is non-zero iff any tuple has a `severity: error` finding. Prefer this as the
default. Reach for the three serial commands below **only** when `validate-all` reports an
error and you need to isolate one validator's output, or when debugging a single file.

<details>
<summary>Serial per-validator invocations (isolation/debugging only)</summary>

After writing every YAML to `<output-dir>/`, run:

```bash
./scripts/run validate-spec-fields <output-dir>/ \
  --kubeconfig $KUBECONFIG \
  --format json > /tmp/spec-fields-findings.json

./scripts/run validate-manifest \
  --dir <output-dir>/ \
  --kubeconfig $KUBECONFIG \
  --mode create \
  --format json > /tmp/manifest-findings.json

./scripts/run validate-cel <output-dir>/ --format json > /tmp/cel-findings.json
```

</details>

See the Adopt_Path Phase 4 grounding loop for how to tell a genuinely-absent CRD (`no matches for kind`) from a stale published CRD (`unknown field "spec.<field>"` + `GraphAccepted=False`). The second case is a real defect, not expected noise.

`--mode create` enforces the Create_Path invariant that ACK CRs MUST NOT carry any `services.k8s.aws/adoption-*` or `deletion-policy` annotation. Every `severity: error` finding MUST be resolved before the Phase_Checkpoint — treat findings as feedback for another authoring pass and re-run validation.

**Report rendering (MANDATORY once validation is clean):**

If you used `validate-all` (the default), it has already written `<output-dir>/create/findings.json` — pass that straight to `render-report`, no merge needed:

```bash
cp <output-dir>/create/findings.json /tmp/findings.json
```

Only if you ran the serial validators for isolation, merge the three findings files into `/tmp/findings.json` (concatenate the `findings` arrays). Then render:

```bash
./scripts/run render-report \
  --migrate-set /tmp/migrate-set.json \
  --decisions /tmp/decisions.json \
  --manifests-dir <output-dir>/ \
  --findings /tmp/findings.json \
  --out <output-dir>/report.html
```

The Create_Path report shows the RGD abstraction with its child CR templates and (when a tfstate was also provided) the underlying resources those templates parameterize; the Adopt_Path visualization is muted since no runtime AWS resources are being adopted on this path.

#### Phase_Checkpoint — Phase 4 (Create_Path)

At the end of this phase, report the following Decision_Summary and **pause** for operator validation before finalizing the output.

**Decisions to report:**
- Instance example values populated (which values were chosen to satisfy each required RGD schema field)
- Unsupported resources recorded in documentation (resources with no ACK equivalent from Phase 1)
- Flagged permission gaps (services where no AWS managed policy covers required actions)
- KRO Kubernetes RBAC warning + ACK API groups involved

**⚠️ Needs Attention — surface explicitly if present:**
- Any permission gaps that require attention before `kubectl apply`
- Any ambiguous variable→spec mapping that carried through from earlier phases unresolved

**Then pause and present the three operator response options** (Confirm / Correct-Override / Proceed without further check-ins). See [Phase Decision Checkpoints](#phase-decision-checkpoints) for the full pattern.

---

### Create_Path RGD contract (greenfield)

The create RGD is a **self-serve blueprint parameterized on Terraform variables**, not on AWS IDs. Developers set the app name + optional config knobs; the RGD provisions from scratch.

**RGD schema (create-mode) — required shape:**

```yaml
apiVersion: kro.run/v1alpha1
kind: ResourceGraphDefinition
metadata:
  name: <rgd-name>-create
  labels:
    rekoncile.io/source-type: terraform
    rekoncile.io/source-path: <relative-tf-dir-with-slashes-replaced-by-dashes>
    rekoncile.io/mode: create
spec:
  schema:
    apiVersion: v1alpha1
    kind: <PascalCaseName>
    spec:
      # One field per Terraform variable, type + default preserved from HCL.
      appName: string | required=true
      dbInstanceClass: string | default="db.t4g.medium"
      dbAllocatedStorage: integer | default=20
      # ...
    status:
      # Expose ACK-resolved endpoints for downstream apps.
      dbEndpoint: ${appDb.status.endpoint.address}
      s3BucketARN: ${assetsBucket.status.ackResourceMetadata.arn}
  resources:
    - id: appIrsa
      template:
        apiVersion: iam.services.k8s.aws/v1alpha1
        kind: Role
        metadata:
          name: ${schema.spec.appName}-app-irsa
          labels:
            rekoncile.io/from-tf-address: aws_iam_role.app_irsa
        spec:
          # ...
```

**Instance file for create (`instance.yaml`) — example values that satisfy the schema:**

```yaml
apiVersion: kro.run/v1alpha1
kind: <PascalCaseName>
metadata:
  name: <name>
  labels:
    rekoncile.io/source-type: terraform
    rekoncile.io/source-path: <relative-tf-dir-with-slashes-replaced-by-dashes>
spec:
  appName: greenfield
  dbInstanceClass: db.t4g.medium    # from variable "db_instance_class" default
  # ...
```

Same traceability rule as the adopt path: every literal has a comment or label pointing back to the Terraform variable / resource / attribute it came from.

### Create_Path Output Files

1. `<output-dir>/create/<rgd-name>/rgd.yaml` — self-serve create RGD parameterized on TF variables
2. `<output-dir>/create/<rgd-name>/instance.yaml` — example instance
3. `<output-dir>/create/<rgd-name>/resources/*.yaml` — rendered create CRs for review + kubectl dry-run
4. `<output-dir>/MIGRATION-NOTES.md` — skill-level notes
5. `<output-dir>/report.html` — self-contained single-page report

---

## Key Principles

1. **Adopt, never recreate** — zero downtime is non-negotiable
2. **`deletion-policy: retain` always** — safety net
3. **TF state is source of truth** — ARNs, IDs, policy documents come from state
4. **Dependencies from state** — `dependencies` array gives RGD ordering
5. **One TF module → One RGD**
6. **Adopt uses `spec: {}` (strict import)** — under `adoption-policy: adopt`, spec MUST be empty. ACK populates spec from live AWS state. Populate spec ONLY under `adoption-policy: adopt-or-create` (and only when that policy is intentional) or when a specific CRD's OpenAPI schema rejects an empty spec on live dry-run (record and escalate as a finding).
7. **Never modify Terraform state on the AWS/ACK path** — the skill never alters, removes from, or writes to `terraform.tfstate`; Terraform and ACK coexist safely on both the Adopt_Path and the Create_Path
8. **Alert on permission gaps** — MIGRATION-NOTES.md must flag services where no AWS managed policy covers the required actions, so users can update their ACK capability role before applying
9. **`ownerReferences` is an Open_Item** — the default for adopted resources is pending a service-team decision; the rollback-safe pending default is to omit `ownerReferences` (prevents accidental cascade-delete of live AWS resources). See `references/kro/adoption/instances.md` for the cascade-delete warning and opt-in flag.

## Phase Decision Checkpoints

Every phase in both paths ends with a **Phase_Checkpoint** — a mandatory pause where the skill reports the decisions it made and waits for operator validation before starting the next phase. This makes high-stakes decisions visible and correctable before they propagate.

### Checkpoint Pattern (all phases)

At the end of each phase the skill MUST:

1. **Emit a Decision_Summary** — list the decisions made during the phase (phase-specific content defined below in each phase's checkpoint section).
2. **Surface needs-attention items explicitly** — low-confidence or open decisions are called out separately at the top of the summary as "⚠️ Needs Attention", never buried in the main list. Examples:
   - A resource type with no clear ACK equivalent
   - An ambiguous variable→spec mapping (Create_Path)
   - The pending `ownerReferences` default (surfaced with its cascade-delete warning at Phase 4 on the Adopt_Path)
3. **Pause and request operator validation** — the skill holds at the checkpoint and does NOT start the next phase until the operator responds.

### Operator Response Options

| Response | Behavior |
|----------|----------|
| **Confirm** | Accept the decisions as-is; proceed to the next phase |
| **Correct / Override** | Apply the operator's change, re-summarize the affected decisions, and pause again for validation |
| **Proceed without further check-ins** | Accept all remaining phases at once; the skill completes the run autonomously and reports the combined Decision_Summary at the end |

The skill MUST NOT silently proceed past a checkpoint. Absent one of the responses above, the run holds.

---

## Version Adaptability

Every generated RGD carries a header comment naming the targeted KRO version it was produced for, so operators always know which controller release the output expects.

The skill reads the installed KRO controller version to determine which primitives and syntax to use. Detection command:

```bash
kubectl get deployment -n kro kro-controller-manager \
  -o jsonpath='{.spec.template.spec.containers[*].image}'
```

Where the installed KRO version affects a version-dependent primitive (e.g., `forEach` form, feature-gate-dependent functions like `omit()`), the skill adjusts the emitted syntax to match that version. When a primitive whose syntax varies between KRO versions is emitted, the skill adds a verification note as a YAML comment in the generated RGD alerting the operator to check compatibility with their installed controller version.

## Out of Scope — Future Extensions

The following migration paths are **out of scope** for the current skill:

- **Native-Kubernetes adoption** (Terraform `kubernetes`/`helm` providers)
- **Helm chart conversion** (Terraform `helm_release` → FluxCD/ArgoCD HelmRelease)
- **Kustomize conversion** (Terraform-managed K8s manifests → Kustomize overlays)

**Why:** These paths rely on a different ownership mechanism — **Server-Side Apply (SSA)** combined with `terraform state rm` — which requires modifying Terraform state. This conflicts directly with Key Principle 7: "never modify Terraform state on the AWS/ACK path." The AWS/ACK adoption path uses read-only access to TF state and ACK adoption annotations to discover existing resources by ARN/ID; the native-K8s paths require Terraform to *relinquish* state tracking, which is irreversible and breaks the safety model.

These are **potential future additions** (or a separate skill), not removed capabilities — they were never part of this skill's implementation. They represent a logical next step once guardrails for state modification (dry-run validation, rollback plans, state backup) are established.

See `references/known-limitations.md` for detailed documentation of these future-extension paths.

## Mandatory Web Verification (All Phases)

**Before generating ANY output (ACK CRs, RGD templates, or documentation), the agent MUST verify each resource's CRD schema against live documentation.** Local reference files (`controllers-catalog.md`, `aws-to-ack-mappings.md`) provide initial mappings, but they may be outdated. The agent must:

### Division of labour — what the web is FOR (and what it is not)

The live cluster and the web answer **different** questions. Asking both for the same fact
is the single largest source of wasted Phase 1 time.

| Question | Authoritative source | Why |
|---|---|---|
| Does this Kind exist / is it installed? | **live cluster** (`cluster.ack_crds`) | A CRD is installed or it isn't |
| Which spec fields exist? Which are required? Which are immutable? | **live cluster** (one batched `kubectl get crd … -o json`) | The cluster's controller version is what will reconcile your CR |
| Status field names and types (for `readyWhen`) | **live cluster** | Same reason |
| **What are the `adoption-fields` lookup keys, and their exact casing?** | **web — controller source** | NOT derivable from any CRD. Lives in `pkg/resource/<r>/resource.go` → `PopulateResourceFromAnnotation`. Guessing here is the #1 cause of adoption failures |
| **Is a TF resource consolidated into a parent Kind?** | **web — CRD filename listing** | Absence of a Kind is only provable by enumerating all of them |
| **Does a field exist upstream but not on this cluster?** | **web — compare to `cluster.ack_crds`** | Reveals controller-version drift; the cluster alone cannot tell you a field was added later |

**Rule:** web-verify **existence, adoption keys, consolidation, and version drift**.
Read **field names, required sets, immutability, and status shapes from the live CRDs.**
Do not spend web calls re-deriving what one batched `kubectl` call already returned.

Two measured examples of why the web half is non-negotiable — both invisible to a live CRD:
- IAM Policy is adoptable **only by `arn`**; `GetPolicy` has no name lookup, so `{"name":…}` sits in `NotFound` forever.
- `secretsmanager/Secret.spec.recoveryWindowInDays` exists upstream (≥ v1.6.0) but not on older clusters — writing it means silent pruning.

### Phase 1 — Controller & CRD Existence Verification

For **every** Terraform resource type discovered — whether or not it appears in `controllers-catalog.md` or `aws-to-ack-mappings.md`:

1. **Derive the service name** from the TF resource type prefix (e.g., `aws_api_gateway_*` → `apigateway`, `aws_lambda_*` → `lambda`, `aws_dynamodb_*` → `dynamodb`). Note that TF naming and ACK controller naming may differ — try variations.

2. **Search GitHub for the controller repo** — try ALL of the following until you find it or exhaust options:
   - `https://github.com/aws-controllers-k8s/<service>-controller` (direct URL)
   - Web search: `github.com aws-controllers-k8s <service>-controller`
   - Web search: `aws-controllers-k8s <TF resource prefix without aws_> controller`
   - If a service has v1/v2 variants (like API Gateway), search for BOTH: `apigateway-controller` AND `apigatewayv2-controller`

3. **Check the CRD directory** — once you find the controller repo, fetch:
   - `https://github.com/aws-controllers-k8s/<service>-controller/tree/main/helm/crds`
   - The CRD filenames reveal every supported Kind (e.g., `apigateway.services.k8s.aws_restapis.yaml` → Kind = `RestAPI`)

4. **Do NOT trust local references alone:**
   - If `aws-to-ack-mappings.md` says "no ACK equivalent" → **STILL web-search** (the mapping may be outdated)
   - If `controllers-catalog.md` doesn't list a controller → **STILL web-search** (the catalog may be incomplete)
   - If the local reference says "Alpha" or "unsupported" but the web shows GA → **trust the web**

5. **Only after exhausting all search strategies**, mark a resource as genuinely unsupported.

**Common service name derivation pitfalls:**
| TF prefix | ❌ Wrong guess | ✅ Correct controller |
|-----------|---------------|----------------------|
| `aws_api_gateway_*` | "no controller" | `apigateway-controller` (REST API v1) |
| `aws_apigatewayv2_*` | same as above | `apigatewayv2-controller` (HTTP API v2) |
| `aws_db_*` | `db-controller` | `rds-controller` |
| `aws_lb_*` / `aws_alb_*` | `lb-controller` | `elbv2-controller` |
| `aws_cloudwatch_log_*` | `cloudwatch-controller` | `cloudwatchlogs-controller` |
| `aws_sfn_*` | `stepfunctions-controller` | `sfn-controller` |

### Phase 2 — CRD Field Schema Verification

For **every** ACK CR being generated:

1. **Fetch the CRD spec field schema** — search for the resource's API reference or CRD YAML at:
   - `https://aws-controllers-k8s.github.io/community/reference/<service>/v1alpha1/<kind>/`
   - `https://github.com/aws-controllers-k8s/<service>-controller/tree/main/helm/crds`
2. **Verify required fields** — do NOT guess field names from Terraform attribute names. ACK field names follow the AWS SDK Go naming convention (camelCase), which may differ from Terraform's snake_case attribute names.
3. **Verify field nesting** — some Terraform top-level attributes map to nested ACK spec fields (e.g., `source_code_hash` is not an ACK field; Lambda code is under `spec.code.s3Bucket` + `spec.code.s3Key`).
4. **Check for cross-resource references** — ACK CRDs often support `*Ref` fields (e.g., `roleRef` instead of `role`) for referencing other ACK-managed resources within the same namespace.

### Phase 3 — KRO Syntax Verification

1. **Verify KRO version-dependent syntax** — if `forEach`, `omit()`, or other evolving primitives are used, check the latest KRO docs at `https://kro.run/docs/concepts/rgd/`.
2. **Verify CEL expression patterns** — especially for status field access (`?` operator, `.orValue()` patterns).

### Why This Matters — Non-Negotiable

- **NEVER classify a resource as "no ACK equivalent" without exhausting all web search strategies first**
- ACK controllers are independently versioned and graduate from Preview to GA frequently
- New controllers and CRDs are added regularly without local reference files being updated
- CRD schemas evolve between controller versions (fields added, renamed, deprecated)
- The `controllers-catalog.md` and `aws-to-ack-mappings.md` are convenience snapshots that WILL be incomplete
- Generating CRs with incorrect field names results in API server validation errors at apply time
- Incorrectly marking a resource as unsupported means the migration is incomplete
- **The cost of a few web searches is negligible compared to producing an incomplete or invalid migration**

### Verification Failure Handling

- If a web search returns no results for a specific CRD field schema → fall back to the AWS API reference for that service (CreateFunction API → maps to ACK Function spec)
- If verification reveals a field name differs from what the local reference suggests → use the web-verified name and document the discrepancy
- If verification is impossible (network issues, page unavailable) → proceed with local references but add a `# VERIFY: field names not web-verified` comment on affected resources

---

## External References (Fetch On-Demand)

| Need | URL Pattern |
|------|-------------|
| ACK API Reference | `https://aws-controllers-k8s.github.io/community/reference/<service>/v1alpha1/<kind>/` |
| ACK Examples | `https://github.com/aws-controllers-k8s/<service>-controller/tree/main/test/e2e/resources` |
| ACK CRD Schemas | `https://github.com/aws-controllers-k8s/<service>-controller/tree/main/helm/crds` |
| ACK Docs | `https://aws-controllers-k8s.github.io/docs/` |
| KRO Examples (GitHub) | `https://github.com/kubernetes-sigs/kro/tree/main/examples/aws` |
| KRO Docs | `https://kro.run/docs` |
| AWS API Reference | `https://docs.aws.amazon.com/<service>/latest/api/` |
