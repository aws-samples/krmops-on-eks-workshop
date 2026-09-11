# KRO References — When to Use What

## Loading Strategy

> **Load this README first**, then load only the files the current phase needs for the selected path.
> Do NOT load the entire `kro/` tree at once.

The agent MUST read this index before loading any per-phase KRO reference file. This allows the
agent to know which files exist, where they live, and which phase requires them — without paying
the context cost of loading content it will not use.

## File Index

| File | What It Contains | Scope |
|------|-----------------|-------|
| `rgd-reference.md` | Complete RGD authoring: SimpleSchema, CEL, `readyWhen`, `includeWhen`, `forEach`, `externalRef`, dependency graph, version policy, reserved IDs | Shared [S] — both paths |
| `overview.md` | KRO overview, KRO+ACK integration, ArgoCD tracking annotation | Shared [S] — both paths |
| `building-abstractions.md` | Single Resource RGD, Multi Resource RGD (grouping), RGD Chaining | Shared [S] — both paths |
| `adoption/instances.md` | Instance lifecycle, status interpretation, debugging, `ownerReferences` cascade-delete flag | Adopt_Path only |
| `adoption/examples.md` | Real adoption-mode RGD YAML examples + GitHub URL patterns | Adopt_Path only |
| `creation/hcl-to-rgd.md` | HCL→RGD conversion: `variable`→`spec`, `output`→`status` CEL, `resource`→RGD template | Create_Path only |
| `creation/examples.md` | Self-serve RGD example + matching instance example | Create_Path only |

Files marked **[S]** are shared — used by both the Adopt_Path and the Create_Path.

---

## Per-Phase Loading — Adopt_Path

| Phase | KRO References to Load | Purpose |
|-------|------------------------|---------|
| 1 — Discovery | *(none)* | No KRO references needed for input parsing |
| 2 — Generate ACK CRs | *(none)* | No KRO references needed for ACK CR generation |
| 3 — Generate KRO RGD | `rgd-reference.md` [S], `building-abstractions.md` [S], `adoption/examples.md` | RGD pattern selection, schema syntax, CEL, concrete YAML |
| 4 — Instance + Docs | `adoption/instances.md` | Instance structure, status, `ownerReferences` flag |

### Adopt_Path phase notes
- **Phase 3:** Start with `building-abstractions.md` to pick the RGD pattern (Multi Resource RGD for TF module migration), then `rgd-reference.md` for syntax/CEL/`readyWhen`, then `adoption/examples.md` for concrete patterns.
- **Phase 4:** Load `adoption/instances.md` for instance lifecycle, status interpretation, debugging, and the `ownerReferences` cascade-delete warning.

---

## Per-Phase Loading — Create_Path

| Phase | KRO References to Load | Purpose |
|-------|------------------------|---------|
| 1 — Discovery | *(none)* | No KRO references needed for input parsing |
| 2 — Generate ACK CRs | *(none)* | No KRO references needed for ACK CR generation |
| 3 — Generate self-serve RGD | `rgd-reference.md` [S], `building-abstractions.md` [S], `creation/hcl-to-rgd.md`, `creation/examples.md` | RGD pattern, HCL mapping, syntax, concrete YAML |
| 4 — Instance + Docs | `creation/examples.md` | Self-serve instance example with populated schema fields |

### Create_Path phase notes
- **Phase 3:** Start with `building-abstractions.md` to pick the RGD pattern, then `creation/hcl-to-rgd.md` for HCL→RGD mapping (`variable`→`spec`, `output`→`status`, `resource`→template), then `rgd-reference.md` for full schema syntax/CEL/dependency wiring, then `creation/examples.md` for concrete patterns.
- **Phase 4:** Load `creation/examples.md` for the self-serve instance example showing required field population.

---

## Shared vs. Mode-Specific Summary

```
references/kro/
├── README.md                       ← YOU ARE HERE (load first)
├── rgd-reference.md                [S] shared — the consolidated authoring reference
├── overview.md                     [S] shared — KRO overview, KRO+ACK, ArgoCD
├── building-abstractions.md        [S] shared — Phase 3 both paths
├── adoption/
│   ├── instances.md                Adopt_Path Phase 4
│   └── examples.md                 Adopt_Path Phase 3
└── creation/
    ├── hcl-to-rgd.md               Create_Path Phase 3
    └── examples.md                 Create_Path Phase 3 + 4
```

---

## Key Insight — Multi Resource RGD Pattern

For **both** adoption and creation modes, the **Multi Resource RGD** pattern is the primary
choice when migrating a Terraform module:

- **Adoption:** one RGD groups all adopted resources with dependency wiring via
  `${resource.status.ackResourceMetadata.arn}`.
- **Creation:** one RGD groups all created resources, exposing Terraform variables as `spec`
  fields and wiring outputs as `status` CEL expressions.

Load `building-abstractions.md` early in Phase 3 to confirm this pattern.

---

## Subfolder Linking Rule

The `adoption/` and `creation/` subfolders **reference** the shared root files — they never
duplicate shared content. When mode-specific docs need to refer to schema syntax, CEL patterns,
`readyWhen`, or dependency wiring, they link to `rgd-reference.md` or `building-abstractions.md`
at the root. This prevents drift between modes.

---

## External Lookups (On-Demand)

| Need | URL |
|------|-----|
| Full KRO docs | `https://kro.run/docs/overview` |
| KRO examples (all) | `https://kro.run/examples/` |
| KRO examples (GitHub, AWS) | `https://github.com/kubernetes-sigs/kro/tree/main/examples/aws` |
| KRO examples (GitHub, K8s) | `https://github.com/kubernetes-sigs/kro/tree/main/examples/kubernetes` |
| GitHub | `https://github.com/kubernetes-sigs/kro` |
| CEL libraries reference | `https://kro.run/docs/concepts/rgd/cel-libraries` |
| SimpleSchema spec | `https://kro.run/api/specifications/simple-schema` |
