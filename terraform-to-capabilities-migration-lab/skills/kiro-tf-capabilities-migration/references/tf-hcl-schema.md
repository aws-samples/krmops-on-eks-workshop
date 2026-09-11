# Terraform HCL Schema (`.tf` source)

> Source: [Terraform Language Documentation — Syntax & Blocks](https://developer.hashicorp.com/terraform/language)

## Overview

Terraform `.tf` files are HashiCorp Configuration Language (HCL) documents that describe infrastructure as **structure and logic** — resource shapes, input variables, computed locals, outputs, and data lookups — *without* runtime values. Unlike `tfstate`, HCL has no ARNs, no resource IDs, and no realized attributes; it describes what *should* exist, not what *does* exist.

This is the **Create_Path Phase 1** input. The skill reads `.tf` files directly — no `terraform` CLI, `terraform plan`, or provider initialization needed.

**Adopt vs Create — why the input differs:**

| Aspect | `tfstate` (Adopt_Path) | `.tf` HCL (Create_Path) |
|--------|------------------------|--------------------------|
| Contains | Realized runtime values (ARNs, IDs, policy JSON) | Declarations, variables, references |
| Used to | Adopt existing resources (lookup by ID/ARN) | Provision new resources from a full spec |
| ACK output | CRs **with** adoption annotations | CRs **without** adoption annotations |
| RGD output | Dependency-management RGD | Self-serve abstraction RGD (vars→spec, outputs→status) |

Because HCL carries no runtime identifiers to adopt against, both the `create` and `adopt` goals on a `.tf` input route to the Create_Path (see `SKILL.md` Router, Req 2.3).

## Block Types to Extract

A `.tf` file (or a directory of `.tf` files forming a module) is a flat list of top-level blocks. Phase 1 extracts five block types:

| Block | Purpose | Maps to (Create_Path) |
|-------|---------|------------------------|
| `resource` | Declares a managed AWS resource | RGD resource template (ACK Kind + apiVersion) |
| `variable` | Declares a typed input | RGD `spec` schema field |
| `output` | Declares an exported value | RGD `status` field (CEL) |
| `locals` | Declares computed intermediate values | Inlined / resolved during spec population |
| `data` | Declares a read-only lookup | `externalRef` or excluded (see below) |

> A directory of `.tf` files is parsed as **one module** — files are concatenated logically; block order across files does not matter. One module → one RGD (mirrors the `tfstate` module-grouping rule).

## `resource` Blocks

```hcl
resource "aws_s3_bucket" "this" {
  bucket = "${var.name}-${data.aws_caller_identity.current.account_id}"

  tags = {
    Cluster = var.cluster_name
    Owner   = var.team
  }
}
```

**Block header:** `resource "<TYPE>" "<NAME>" { ... }`

| Element | Example | Skill Usage |
|---------|---------|-------------|
| `<TYPE>` | `aws_s3_bucket` | Map to ACK Kind + apiVersion via `ack/aws-to-ack-mappings.md` |
| `<NAME>` | `this` | Local reference name; used for CR naming and dependency wiring |
| arguments | `bucket = ...`, `tags = ...` | Become the ACK CR spec fields (full spec, no adoption lookup) |
| `count` / `for_each` | meta-argument | Multiple instances → KRO `forEach` (see rgd-reference) |

**Filter:** only process AWS provider resource types (`aws_*`). Resource types with no ACK equivalent are recorded as unsupported in the output summary (Req 4.7). On the Create_Path, ALL resources with ACK equivalents are included (VPCs, subnets, EKS clusters — everything). Class A exclusions only apply to the Adopt_Path.

> Unlike `tfstate` (where you read realized `attributes`), here you read the **argument expressions**. Values may be literals, `var.*` references, `local.*` references, `data.*` references, or interpolations — resolve these when populating the ACK spec.

## `variable` Blocks

```hcl
variable "name" {
  type        = string
  description = "Application name prefix"
}

variable "versioning_enabled" {
  type    = bool
  default = true
}

variable "tags" {
  type    = map(string)
  default = {}
}
```

| Field | Type | Purpose | Skill Usage |
|-------|------|---------|-------------|
| `type` | type expression | Variable's HCL type | Map to RGD `spec` schema field type |
| `default` | any (optional) | Default value | Preserve as schema default; **no default → required field** |
| `description` | string (optional) | Human-readable doc | Preserve as schema field description |
| `validation` | block (optional) | Constraint rules | Document; KRO schema has limited validation support |
| `sensitive` | bool (optional) | Marks secret values | Flag — route to a Secret rather than inline spec |

**Use for:** RGD `spec` schema fields, **preserving type, default, and description** (Req 4.2).

### HCL type → KRO schema type mapping

| HCL type | KRO schema type | Notes |
|----------|-----------------|-------|
| `string` | `string` | |
| `number` | `integer` or `float` | Choose by usage |
| `bool` | `boolean` | |
| `list(T)` / `set(T)` | `[]T` | |
| `map(T)` | `map[string]T` | |
| `object({...})` | nested object | Map each attribute recursively |
| `any` | `string` (or document) | Ambiguous — surface at Phase 1 checkpoint |

> A variable with **no `default`** becomes a **required** schema field. A variable with a `default` becomes optional with that default. This distinction drives the generated instance example (Req 4.8).

## `output` Blocks

```hcl
output "bucket_arn" {
  value       = aws_s3_bucket.this.arn
  description = "ARN of the created bucket"
}

output "bucket_name" {
  value = aws_s3_bucket.this.bucket
}
```

| Field | Type | Purpose | Skill Usage |
|-------|------|---------|-------------|
| `value` | expression | The exported expression | Convert reference to a CEL `status` expression |
| `description` | string (optional) | Human-readable doc | Preserve on the `status` field |
| `sensitive` | bool (optional) | Marks secret output | Flag in generated docs |

**Use for:** RGD `status` fields expressed with CEL (Req 4.3).

**Reference → CEL translation:** a Terraform output references a resource attribute (`aws_s3_bucket.this.arn`). In the RGD, the corresponding resource template has an ID; the `status` field is a CEL expression reading that resource's status:

```
output "bucket_arn" { value = aws_s3_bucket.this.arn }
   ↓
status:
  bucketArn: ${bucket.status.ackResourceMetadata.arn}
```

> The exact CEL form depends on the ACK Kind's status schema — consult `kro/rgd-reference.md` (CEL section) and `kro/creation/hcl-to-rgd.md`.

## `locals` Blocks

```hcl
locals {
  name_prefix = "${var.name}-${var.environment}"
  common_tags = {
    ManagedBy = "kro"
    Team      = var.team
  }
}
```

- A single `locals` block declares one or more named local values.
- Locals are **computed intermediate values** — references to `var.*`, `data.*`, other `local.*`, or resource attributes.

**Skill Usage:** locals are **resolved/inlined** when populating ACK CR specs and RGD fields. They are *not* a distinct RGD construct. Resolve `local.name_prefix` to its underlying expression (here, a composition of `var.name` and `var.environment`) and emit the equivalent CEL or schema reference.

> Watch for `local → local` chains: resolve transitively until you reach `var.*`, `data.*`, or literals.

## `data` Blocks

```hcl
data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "bucket_access" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.this.arn}/*"]
  }
}
```

**Block header:** `data "<TYPE>" "<NAME>" { ... }`

| `data` kind | Handling on Create_Path |
|-------------|--------------------------|
| Identity/context (`aws_caller_identity`, `aws_region`, `aws_partition`) | Resolve to cluster/runtime context; often supplied by ACK or instance values, not a CR |
| Read-only resource reference (existing VPC, subnet, role by name/tags) | Map to `externalRef` primitive for read-only references (Req 8.3) |
| Policy document (`aws_iam_policy_document`) | Render the resulting JSON policy inline into the ACK spec field |

**Filter:** `data` sources are **not managed resources** — they are never emitted as create-mode ACK CRs (parallel to skipping `mode: "data"` entries in `tfstate`). They are either resolved as context, rendered inline (policy documents), or mapped to `externalRef`.

## References & Dependencies

HCL has **no explicit `dependencies` array** (that exists only in `tfstate`). Dependencies are **implicit**, inferred from reference expressions inside arguments.

**Reference forms to detect:**

| Reference | Refers to | Dependency edge |
|-----------|-----------|-----------------|
| `aws_iam_policy.this.arn` | another `resource` | This resource depends on `aws_iam_policy.this` |
| `var.name` | a `variable` | Bind to a `spec` field (not an ordering edge) |
| `local.name_prefix` | a `locals` value | Resolve, then follow its references |
| `data.aws_caller_identity.current.account_id` | a `data` source | Resolve as context / `externalRef` |

**How to build KRO ordering from HCL:**

1. For each `resource`, scan all argument expressions for `<type>.<name>.<attr>` references to **other `resource` blocks**.
2. Each such reference is a dependency edge: the referenced resource must be created before this one.
3. Build a DAG from these edges (same model as the `tfstate` dependency DAG, but derived from references instead of a stored array).
4. Resources with no resource-to-resource references → first in the RGD.
5. Resources that reference others → add `readyWhen` referencing the dependency's status.
6. References to `var.*` / `local.*` / `data.*` are **not** ordering edges — they bind to schema fields, resolved values, or external refs respectively.

**Example dependency chain inferred from HCL references:**

```
aws_s3_bucket.this        (refs only var.*)              → FIRST
aws_iam_policy.this        (refs aws_s3_bucket.this.arn)  → SECOND
aws_iam_role.this          (refs only var.*)              → can be FIRST/SECOND
aws_iam_role_policy_attachment.this
                           (refs role + policy)           → THIRD (merged into role)
```

## Module Grouping

```hcl
module "my_app" {
  source = "./modules/app"
  name   = var.name
}
```

- A **directory of `.tf` files** is parsed as a single module → **one KRO RGD**.
- A `module` block invoking a child module passes variables down; the child module's own `.tf` files define its resources/variables/outputs.
- `module.<name>.<output>` references read a child module's outputs — treat like a cross-resource reference when wiring dependencies.

**Rule:** One Terraform module → One KRO RGD (mirrors the `tfstate` module-grouping rule).

## Parsing Algorithm Summary

```
1. Read all .tf files in the module directory (treat as one logical document)
2. Extract top-level blocks by type: resource, variable, output, locals, data
3. For each variable block:
   a. Map HCL type → KRO schema type
   b. Record default (absent default → required field) and description
   c. Add to RGD spec schema
4. For each resource block:
   a. Skip if type is not aws_* (rare / non-AWS provider)
   b. Map type → ACK Kind + apiVersion (using aws-to-ack-mappings.md)
   d. If type has no ACK mapping → record as unsupported in output summary
   e. If type is consolidated (e.g. role_policy_attachment) → merge into parent
   f. Resolve argument expressions (var.* → spec ref, local.* → inline, data.* → context/externalRef)
   g. Populate the FULL ACK spec (NO adoption annotations — Create_Path)
5. For each output block:
   a. Translate value reference → CEL expression over a resource's status
   b. Add to RGD status schema
6. Resolve locals transitively (local → var/data/literal)
7. Handle data blocks: context | inline policy JSON | externalRef (never a managed CR)
8. Build dependency DAG from resource-to-resource references (implicit)
9. Output: RGD spec (from variables), resource templates (ordered), status (from outputs),
   plus an instance example populating required schema fields
```

## Relationship to Other References

| Reference | Role |
|-----------|------|
| `ack/aws-to-ack-mappings.md` | Terraform type → ACK Kind/apiVersion (Phase 1, shared) |
| `ack/creation/creation-patterns.md` | Generate ACK CRs without adoption annotations (Phase 2) |
| `kro/creation/hcl-to-rgd.md` | Full HCL → RGD conversion logic (Phase 3) |
| `kro/rgd-reference.md` | KRO schema, CEL, `readyWhen`, `forEach` (Phase 3, shared) |
| `tf-state-schema.md` | The Adopt_Path Phase 1 counterpart (`tfstate` parsing) |
