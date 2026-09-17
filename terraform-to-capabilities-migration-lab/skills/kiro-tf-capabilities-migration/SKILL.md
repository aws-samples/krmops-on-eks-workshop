---
name: terraform-to-ack-kro-migration-skill
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
| `scripts/run validate-all` | Parallel validate-manifest + validate-cel across every `<mode>/<rgd>/` subtree; aggregates per-mode findings.json for render-report | Phase 4 (preferred over serial invocations — ~2.3× speedup measured) |
| `scripts/run render-report` | Single-page HTML combining Phase_Checkpoint decisions + Cytoscape resource graph + migration report | Phase 4 (both paths) |
| `scripts/run adopt` | Apply ACK CRs + poll `ACK.ResourceSynced=True` (verify adoption; **never** touches TF state) | Post-Phase 4, Adopt_Path only, opt-in |

**Skill obligations at every phase:**
- The skill authors ACK CRs and KRO RGDs in the conversation. Helper scripts NEVER generate YAML. They only parse, ground, or render.
- After every generation step, the skill runs the relevant validator and treats the returned findings as blocking input to the next Phase_Checkpoint.
- The skill records each Phase_Checkpoint's Decision_Summary and operator response into an in-conversation `decisions.json` structure. That structure is passed to `render_report.py` at the end of Phase 4.

## Parallel Execution

Most of the phase work has independent fan-out points that a serial run leaves on the table. The skill MUST parallelize the three phases below when the input list has more than one element. Everything not listed here stays serial.

Measured baseline on a 44-manifest stack against a live cluster: Phase 4 validation ran 45s serial with three RGDs at 18.6s / 16.9s / 9.0s. Wall-clock in parallel is bounded by the slowest RGD (~18.6s) plus a small overhead — an expected ~2.4× speedup with three RGDs and roughly linear scaling to the number of RGDs beyond that.

### Parallel Phase A — Web verification (Phase 1)

**Fan-out unit:** unique TF resource type in `migrate-set.json` (typically 15–25 for a real stack).

**Each subagent's job:**
- Take one TF type (e.g. `aws_lambda_permission`).
- Web-verify per the `Mandatory Web Verification` section: derive service name, check `github.com/aws-controllers-k8s/<service>-controller`, cross-check against `cluster.ack_crds`, fetch the CRD field schema if the Kind exists.
- Return a small JSON blob: `{tf_type, ack_group, ack_kind, cluster_installed: bool, required_spec_fields: [...], adoption_fields_key: str, notes: str}`.

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
- Run its own tight grounding loop: `./scripts/run validate-manifest --dir <rgd>/resources --mode <mode>` + `./scripts/run validate-cel <rgd>/rgd.yaml`. Fix findings and re-run until clean or 3 attempts.
- Return `{rgd_name, files_written: [...], findings_summary: {errors, warnings}, attempts_needed: int}`.

**Skill obligations:**
- Both modes (adopt + create) run in parallel with each other AND across RGDs — so a 3-RGD × 2-mode run fans out 6 subagents.
- All subagents receive the SAME **authoring contract** at `references/authoring-contract.json` — hand it to each subagent verbatim. It encodes label conventions, annotation shape, adopt vs create spec rules, adoption-fields lookup keys per Kind, known CRD OpenAPI-required minima, consolidation rules, and the capability role trust policy. Update in ONE place; never fork per subagent.
- If any subagent hits 3 failed attempts, surface to the operator at Phase 4 checkpoint — do not silently drop.
- Serial follow-up in the main skill: assemble the per-RGD outputs into `migration-output/{adopt,create}/{rgd}/...`, then run one final aggregate validation over all directories (Phase C below).

### Parallel Phase C — Validation sweep (Phase 4)

**Fan-out unit:** `(mode, rgd)` tuple — 6 tuples for a 3-RGD × 2-mode run.

**Each subagent's job (or delegate the whole sweep to the built-in helper):**
- Run `./scripts/run validate-manifest --dir <path> --context $CTX --mode <mode> --format json`.
- Run `./scripts/run validate-cel <path>/rgd.yaml --format json`.
- Return `{mode, rgd, manifest_findings: [...], cel_findings: [...], duration_seconds}`.

**Preferred invocation — one command does the fan-out for you:**

```bash
./scripts/run validate-all \
  --output migration-output/ \
  --context $KUBECTL_CONTEXT \
  --workers 8
```

`validate-all` auto-discovers every `<mode>/<rgd>/` subtree and runs validate-manifest + validate-cel across all `(mode, rgd)` tuples concurrently via a thread pool. Aggregates findings into `<mode>/findings.json` ready for `render-report`. Exit code non-zero iff any tuple has a `severity: error` finding. **Use this instead of orchestrating separate subagents unless you specifically want per-RGD subagent conversation state.**

Measured on a 3-RGD adopt-only workload: **2.31× speedup** (45s serial → 18s parallel). Projected on 3-RGD × 2-mode (6 tuples): ~4-5× speedup, bounded by the slowest tuple.

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
- **kubectl startup cost dominates on very short calls.** For future optimization: batch multiple manifests per `kubectl apply --dry-run` call within a subagent, don't shell out per-file.

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

Wrap ACK CRs into a KRO RGD for dependency management:
- One TF module → One RGD
- Order resources by dependency graph (no-deps first → dependents last)
- Use `readyWhen` with `status.ackResourceMetadata.arn` to enforce ordering
- Schema `spec` fields = inputs needed for adoption-fields interpolation (name, region, accountId, clusterName, etc.)
- Schema `status` fields = TF state outputs (ARNs, endpoints)

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

**Grounding loop (MANDATORY before the Phase_Checkpoint):**

After writing every YAML to `<output-dir>/`, run:

```bash
./scripts/run validate-manifest \
  --dir <output-dir>/ \
  --kubeconfig $KUBECONFIG \
  --mode adopt \
  --format json > /tmp/manifest-findings.json

./scripts/run validate-cel <output-dir>/ --format json > /tmp/cel-findings.json
```

`validate-manifest` runs `kubectl apply --dry-run=server` for every file (real CRD schema check against the target cluster) AND enforces adoption-annotation invariants (adopt CRs MUST carry `services.k8s.aws/adoption-policy` and `services.k8s.aws/deletion-policy: retain`; the check only applies to ACK CRs — RGDs and native objects are skipped). Every `severity: error` finding MUST be fixed before the Phase_Checkpoint; treat them as feedback for another authoring pass, then re-run validation. Do NOT proceed with unresolved errors — the previous CLI's retry-with-feedback loop is replaced by this explicit skill-driven cycle.

`validate-cel` catches CEL grammar errors in `readyWhen` and `${…}` interpolations. Same rule: fix errors and re-run before the Phase_Checkpoint.

**Report rendering (MANDATORY once validation is clean):**

Merge `/tmp/manifest-findings.json` and `/tmp/cel-findings.json` into a single `/tmp/findings.json` (concatenate the `findings` arrays), then write the operator report:

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
    rekoncile.io/source-path: <relative-tf-dir>
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
    rekoncile.io/source-path: <relative-tf-dir>
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
| `rekoncile.io/source-path` | e.g. `Terraform/examples/rekoncile-demo` | Where in the repo |
| `rekoncile.io/mode` | `adopt` \| `create` | Which path emitted it |
| `rekoncile.io/tf-module` (optional) | e.g. `module.vpc` | Which TF module this RGD represents |

**On each `spec.resources[N].template` inside the RGD AND on each rendered CR (`metadata.labels`):**

| Label | Value | Purpose |
|---|---|---|
| `rekoncile.io/tf-type` | e.g. `aws_vpc` | Original TF resource type (label-safe) |
| `rekoncile.io/rgd` | e.g. `rekoncile-demo-app-stack-adopt` | Which RGD owns this resource (label-safe) |
| `rekoncile.io/mode` | `adopt` \| `create` | Which path emitted it |

Do NOT put the raw TF address in a label. Kubernetes label values are constrained to `[A-Za-z0-9._-]` and must start/end with an alphanumeric; TF addresses contain `[`, `]`, and dot-heavy paths that fail validation. Live-cluster kubectl dry-run rejects it — verified against `rekoncile-demo`.

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

For each resource with an ACK equivalent, generate a full-spec ACK CR:
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

Generate an RGD that acts as a self-serve abstraction:
- One TF module → One RGD
- Map Terraform `variable` blocks → RGD `spec` schema fields (preserve type, default, description) (Req 4.2)
- Map Terraform `output` blocks → RGD `status` fields expressed with CEL (Req 4.3)
- Map each Terraform `resource` block → RGD resource template using the ACK Kind/apiVersion (Req 4.6)
- Order resources by dependency graph (no-deps first → dependents last)
- Use `readyWhen` with `status.ackResourceMetadata.arn` to enforce ordering
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

**Grounding loop (MANDATORY before the Phase_Checkpoint):**

After writing every YAML to `<output-dir>/`, run:

```bash
./scripts/run validate-manifest \
  --dir <output-dir>/ \
  --kubeconfig $KUBECONFIG \
  --mode create \
  --format json > /tmp/manifest-findings.json

./scripts/run validate-cel <output-dir>/ --format json > /tmp/cel-findings.json
```

`--mode create` enforces the Create_Path invariant that ACK CRs MUST NOT carry any `services.k8s.aws/adoption-*` or `deletion-policy` annotation. Every `severity: error` finding MUST be resolved before the Phase_Checkpoint — treat findings as feedback for another authoring pass and re-run validation.

**Report rendering (MANDATORY once validation is clean):**

Merge findings into `/tmp/findings.json` and render:

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
    rekoncile.io/source-path: <relative-tf-dir>
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
    rekoncile.io/source-path: <relative-tf-dir>
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
