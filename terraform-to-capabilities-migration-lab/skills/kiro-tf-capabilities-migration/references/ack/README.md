# ACK References — When to Use What

## Loading Strategy

> **Load this README first**, then load only the files the current phase needs for the selected path.
> Do NOT load the entire `ack/` tree at once.

The agent MUST read this index before loading any per-phase ACK reference file. This allows the
agent to know which files exist, where they live, and which phase requires them — without paying
the context cost of loading content it will not use.

## File Index

| File | What It Contains | Scope |
|------|-----------------|-------|
| `core-concepts.md` | CRD design, reconciliation loop, drift detection, auth model, annotations | Shared [S] — both paths |
| `controllers-catalog.md` | All 67 controllers with versions, CRD lists, apiVersion prefixes | Shared [S] — both paths |
| `aws-to-ack-mappings.md` | TF resource type → ACK Kind, apiVersion, adoption-fields lookup table | Shared [S] — both paths |
| `controller-permissions.md` | IAM permissions required per ACK controller for adoption and full management | Shared [S] — both paths |
| `adoption/adoption-patterns.md` | Adoption policies, adoption-fields, deletion-policy, readonly mode, safety patterns | Adopt_Path only |
| `adoption/examples.md` | Real adoption YAML examples + GitHub URL patterns to find more | Adopt_Path only |
| `creation/creation-patterns.md` | Full-spec CRs without adoption annotations, variable→spec population, fail-on-adoption-annotation guard | Create_Path only |
| `creation/examples.md` | Full-spec ACK CR examples for create-mode (no adoption annotations) | Create_Path only |

Files marked **[S]** are shared — used by both the Adopt_Path and the Create_Path.

---

## Per-Phase Loading — Adopt_Path

| Phase | ACK References to Load | Purpose |
|-------|------------------------|---------|
| 1 — Discovery | `aws-to-ack-mappings.md` [S] | Map TF resource types to ACK Kinds |
| 2 — Generate ACK CRs | `adoption/adoption-patterns.md`, `adoption/examples.md` | Adoption annotation syntax, safety patterns, concrete YAML |
| 3 — Generate RGD | *(no ACK references in this phase)* | — |
| 4 — Validation & Docs | `controller-permissions.md` [S] | IAM permission-gap analysis for MIGRATION-NOTES |

### Adopt_Path phase notes
- **Phase 1:** If a resource type is not in `aws-to-ack-mappings.md`, fall back to `controllers-catalog.md` to check if an ACK controller exists.
- **Phase 2:** Use `adoption/adoption-patterns.md` for annotation syntax (`adoption-policy: adopt`, `adoption-fields`, `deletion-policy: retain`) and `adoption/examples.md` for concrete YAML patterns.
- **Phase 4:** Use `controller-permissions.md` to generate the IAM permissions section in `MIGRATION-NOTES.md` and flag permission gaps.

---

## Per-Phase Loading — Create_Path

| Phase | ACK References to Load | Purpose |
|-------|------------------------|---------|
| 1 — Discovery | `aws-to-ack-mappings.md` [S] | Map TF resource types to ACK Kinds |
| 2 — Generate ACK CRs | `creation/creation-patterns.md`, `creation/examples.md` | Full-spec CR generation patterns, variable→spec population, concrete YAML |
| 3 — Generate RGD | *(no ACK references in this phase)* | — |
| 4 — Validation & Docs | `controller-permissions.md` [S] | IAM permission-gap analysis for documentation |

### Create_Path phase notes
- **Phase 1:** Same mapping step as Adopt_Path — use `aws-to-ack-mappings.md` to resolve TF types to ACK Kinds/apiVersions.
- **Phase 2:** Use `creation/creation-patterns.md` for the full-spec pattern (no adoption annotations, spec populated from TF variables) and `creation/examples.md` for concrete YAML. A create-mode CR containing an adoption annotation MUST fail generation.
- **Phase 4:** Use `controller-permissions.md` to document required IAM permissions per controller.

---

## Shared vs. Mode-Specific Summary

```
references/ack/
├── README.md                       ← YOU ARE HERE (load first)
├── core-concepts.md                [S] shared — both paths
├── controllers-catalog.md          [S] shared — both paths
├── aws-to-ack-mappings.md          [S] shared — Phase 1 both paths
├── controller-permissions.md       [S] shared — Phase 4 both paths
├── adoption/
│   ├── adoption-patterns.md        Adopt_Path Phase 2
│   └── examples.md                 Adopt_Path Phase 2
└── creation/
    ├── creation-patterns.md        Create_Path Phase 2
    └── examples.md                 Create_Path Phase 2
```

---

## External Lookups (On-Demand)

| Need | URL Pattern |
|------|-------------|
| YAML examples for a resource | `https://github.com/aws-controllers-k8s/<service>-controller/tree/main/test/e2e/resources` |
| CRD schema (field validation) | `https://github.com/aws-controllers-k8s/<service>-controller/tree/main/helm/crds` |
| Full docs | `https://aws-controllers-k8s.github.io/docs/` |
