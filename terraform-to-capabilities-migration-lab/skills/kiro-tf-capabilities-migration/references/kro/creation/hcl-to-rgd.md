# HCL → RGD Conversion Reference (Create_Path Phase 3)

> **Create-mode HCL-to-RGD conversion logic.** This reference documents how to
> transform Terraform `.tf` HCL declarations into a KRO ResourceGraphDefinition.
>
> Consolidated from `kro/skills/transformation_definition.md` and
> `kro/skills/tf-hcl-to-kro.md` (Req 10.5).
>
> **Companion references — do NOT duplicate their content here:**
> - [RGD Authoring Reference (Shared_KRO_Layer)](../rgd-reference.md) — schema syntax,
>   CEL expressions, `readyWhen`, `forEach`, reserved IDs, version compatibility
> - [Terraform HCL Schema (Phase 1 parsing)](../../tf-hcl-schema.md) — `.tf` block
>   extraction, type definitions, reference forms, dependency detection

---

## Scope

This file covers **Phase 3 only** — converting already-parsed HCL structures into an
RGD. It assumes Phase 1 (HCL parsing, documented in `tf-hcl-schema.md`) has already
extracted the five block types: `resource`, `variable`, `output`, `locals`, `data`.

---

## Core Mappings

### 1. `variable` → RGD `spec` Schema Field (Req 4.2)

Each Terraform `variable` block becomes an RGD `spec` field. Preserve **type**,
**default**, and **description**.

```hcl
# Terraform
variable "instance_count" {
  type        = number
  default     = 2
  description = "Number of instances"
  validation {
    condition     = var.instance_count >= 1
    error_message = "Must be at least 1"
  }
}
```

```yaml
# RGD spec
spec:
  instanceCount: integer | default=2 minimum=1 description="Number of instances"
```

#### HCL Type → KRO Schema Type Mapping

| HCL type | KRO schema type | Notes |
|----------|-----------------|-------|
| `string` | `string` | |
| `number` / `int` | `integer` or `number` | Choose `integer` for whole numbers, `number` for floats |
| `bool` | `boolean` | |
| `list(T)` / `set(T)` | `[]T` | Quote in YAML: `"[]string"` |
| `map(T)` | `map[string]T` | Quote in YAML: `"map[string]string"` |
| `object({...})` | nested object | Map each attribute recursively |
| `any` | `string` (or document) | Ambiguous — surface at Phase 1 checkpoint |

#### Required vs Optional

- A variable with **no `default`** → **required** schema field (`required=true`)
- A variable with a `default` → optional with that default value
- A `sensitive = true` variable → flag for Secret routing rather than inline spec

---

### 2. `output` → RGD `status` Field with CEL (Req 4.3)

Each Terraform `output` block becomes an RGD `status` field. The output's `value`
expression (a resource attribute reference) is translated to a CEL expression reading
the corresponding resource's live status.

```hcl
# Terraform
output "bucket_arn" {
  value       = aws_s3_bucket.data_bucket.arn
  description = "ARN of the created bucket"
}
```

```yaml
# RGD status
status:
  bucketArn: ${dataBucket.status.ackResourceMetadata.arn}
```

#### Reference → CEL Translation Rules

| Terraform reference pattern | CEL expression form |
|-----------------------------|---------------------|
| `<type>.<name>.arn` | `${<resourceId>.status.ackResourceMetadata.arn}` |
| `<type>.<name>.id` | `${<resourceId>.status.ackResourceMetadata.arn}` (or `.status.<idField>`) |
| `<type>.<name>.<attribute>` | `${<resourceId>.status.<mappedField>}` |

- Always use `?` and `.orValue(...)` for fields that may not exist at first reconcile.
- Preserve `description` from the output block as a YAML comment.
- For CEL syntax details (string interpolation, `?` operator, `.format()`) →
  see [RGD Authoring Reference](../rgd-reference.md#cel-expressions).

---

### 3. `resource` → RGD Resource Template with ACK Kind/apiVersion (Req 4.6)

Each Terraform `resource` block maps to an RGD `resources` entry with the ACK Kind and
apiVersion for that AWS resource type.

```hcl
# Terraform
resource "aws_s3_bucket" "data_bucket" {
  bucket = var.bucket_name
  tags   = var.tags
}
```

```yaml
# RGD resource
- id: dataBucket
  readyWhen:
    - ${dataBucket.status.?ackResourceMetadata.arn.orValue("") != ""}
  template:
    apiVersion: s3.services.k8s.aws/v1alpha1
    kind: Bucket
    metadata:
      name: ${schema.spec.bucketName}
      namespace: ${schema.metadata.namespace}
    spec:
      name: ${schema.spec.bucketName}
      tags: ${schema.spec.tags}
```

#### Mapping Steps

1. **Type → ACK Kind + apiVersion**: Use the ACK API Version Reference table (below).
2. **Name → resource `id`**: Strip provider prefix, convert to lowerCamelCase
   (`aws_s3_bucket.my_bucket` → `myBucket`).
3. **Arguments → ACK spec fields**: Resolve `var.*` references to `${schema.spec.*}`,
   `local.*` to inlined CEL, `data.*` to context/externalRef.
4. **No adoption annotations**: Create_Path CRs carry **no** `services.k8s.aws/adoption-*`
   annotations — the full spec is derived from Terraform variables (Req 4.4).
5. **Add `readyWhen`**: Use ACK-appropriate ready conditions (typically
   `${<id>.status.?ackResourceMetadata.arn.orValue("") != ""}`).
   **CRITICAL:** The `?` operator returns an optional type — you MUST chain
   `.orValue("")` before comparing, otherwise KRO rejects the RGD with a type error.

---

### 4. `data` → ExternalRef or Inline Resolution

```hcl
# Terraform
data "aws_vpc" "main" {
  id = var.vpc_id
}
```

```yaml
# RGD externalRef
- id: mainVpc
  externalRef:
    apiVersion: ec2.services.k8s.aws/v1alpha1
    kind: VPC
    metadata:
      name: ${schema.spec.vpcId}
```

| `data` kind | Handling |
|-------------|----------|
| Identity/context (`aws_caller_identity`, `aws_region`, `aws_partition`) | Resolve to cluster/runtime context |
| Read-only resource reference (VPC, subnet by name/tags) | Map to `externalRef` |
| Policy document (`aws_iam_policy_document`) | Render JSON inline into ACK spec field |

For `externalRef` semantics → see [RGD Authoring Reference](../rgd-reference.md#referencing-existing-resources-with-externalref-decided--supported-primitive).

---

### 5. `count` / `for_each` → `forEach`

```hcl
# Terraform
resource "aws_subnet" "private" {
  count             = length(var.private_subnets)
  cidr_block        = var.private_subnets[count.index]
  availability_zone = var.azs[count.index]
}
```

```yaml
# RGD forEach (v0.9.2+ syntax — named iterator variable)
- id: privateSubnets
  forEach:
    - subnet: ${schema.spec.privateSubnets}
  readyWhen:
    - ${each.status.?subnetID.orValue("") != ""}
  template:
    apiVersion: ec2.services.k8s.aws/v1alpha1
    kind: Subnet
    metadata:
      name: ${schema.metadata.name}-subnet-${subnet}
    spec:
      cidrBlock: ${subnet}
```

For index-based access (matching Terraform `count.index`):

```yaml
- id: privateSubnets
  forEach:
    - idx: ${lists.range(size(schema.spec.privateSubnets))}
  readyWhen:
    - ${each.status.?subnetID.orValue("") != ""}
  template:
    apiVersion: ec2.services.k8s.aws/v1alpha1
    kind: Subnet
    metadata:
      name: ${schema.metadata.name}-subnet-${string(idx)}
    spec:
      cidrBlock: ${schema.spec.privateSubnets[idx]}
      availabilityZone: ${schema.spec.azs[idx]}
```

For `forEach` vs CEL `map()` guidance → see [RGD Authoring Reference](../rgd-reference.md#foreach--resource-iteration-decided--single-documented-form-prefer-map).

---

## Unsupported Resource Types (Req 4.7)

WHERE a Terraform resource type has **no ACK equivalent**, the Migration_Skill:

1. **Does NOT generate** an RGD resource template for that type.
2. **Records** the unsupported resource type in the output summary with:
   - The Terraform resource type (e.g. `helm_release`, `kubernetes_manifest`)
   - The resource name(s) from `.tf` files
   - A note that no ACK controller exists for this type

**Note on `kubernetes_manifest` / `kubernetes_*` resources:** These come from the
Terraform Kubernetes provider and represent native K8s resources (Deployments, Services,
ConfigMaps, CRDs, etc.). They have no ACK equivalent because they don't need one — KRO
can manage **any** Kubernetes resource (native or CRD) directly as resource templates
in the RGD. Therefore, `kubernetes_manifest` resources SHOULD be translated into their
native K8s resource templates within the RGD (not excluded). Only truly unsupported
types (like `helm_release`, which has no K8s-native equivalent) are recorded here.

Example output summary entry:

```markdown
## Unsupported Resources

| Terraform Type | Resource Name(s) | Notes |
|----------------|-------------------|-------|
| `helm_release` | `nginx_ingress` | No K8s-native equivalent — manage via Helm/ArgoCD |
```

---

## ACK API Version Reference

| Terraform Resource Prefix | ACK apiVersion |
|---|---|
| `aws_s3_*` | `s3.services.k8s.aws/v1alpha1` |
| `aws_dynamodb_*` | `dynamodb.services.k8s.aws/v1alpha1` |
| `aws_lambda_*` | `lambda.services.k8s.aws/v1alpha1` |
| `aws_iam_*` | `iam.services.k8s.aws/v1alpha1` |
| `aws_vpc` / `aws_subnet` / `aws_ec2_*` | `ec2.services.k8s.aws/v1alpha1` |
| `aws_rds_*` | `rds.services.k8s.aws/v1alpha1` |
| `aws_eks_*` | `eks.services.k8s.aws/v1alpha1` |
| `aws_sqs_*` | `sqs.services.k8s.aws/v1alpha1` |
| `aws_sns_*` | `sns.services.k8s.aws/v1alpha1` |
| `aws_secretsmanager_*` | `secretsmanager.services.k8s.aws/v1alpha1` |
| `kubernetes_*` | native Kubernetes API — include directly in RGD as native resource templates |
| `helm_release` | no direct equivalent — record as unsupported |

> For the full list of ACK-supported services and kinds, consult
> `references/ack/aws-to-ack-mappings.md`.

---

## Key Rules

1. **Resource `id` must be lowerCamelCase** — strip provider prefix and convert:
   - `aws_s3_bucket.my_bucket` → `myBucket`
   - `aws_iam_role.lambda_exec` → `lambdaExec`

2. **Never use reserved KRO keywords as resource IDs**: `schema`, `each`, `namespace`,
   `metadata`, `spec`, `status`, `instance`, `resources`, `has`, `all`, `exists`,
   `map`, `filter`, `size`, `string`, `int`, `bool`.
   See [RGD Authoring Reference](../rgd-reference.md#reserved-resource-ids-and-cel-bindings).

3. **`depends_on` → implicit CEL reference**: Do not explicitly declare ordering.
   Reference the upstream resource in a field expression and KRO infers the dependency.

4. **`count.index` → `each.index`** (or `idx` iterator via `lists.range()` depending
   on KRO version).

5. **`for_each` key → iterator variable** matching the map key.

6. **Terraform `null` → `omit()`**: Requires `CELOmitFunction` feature gate — add a
   note. Prefer `includeWhen` when the conditional is at the resource level.

7. **`dynamic` blocks → CEL conditional fields** or `forEach`.

8. **Use `?` operator with `.orValue()`** for fields that may not exist at reconcile
   time. The `?` operator returns an optional type — you MUST unwrap it with
   `.orValue(<default>)` before comparing or assigning:
   - `${x.status.?ackResourceMetadata.arn.orValue("") != ""}` ✅
   - `${x.status.?ackResourceMetadata.arn != ""}` ❌ (type error: optional vs string)

9. **Add `readyWhen`** for resources with meaningful ready states (ACK resources:
   `${<id>.status.?ackResourceMetadata.arn.orValue("") != ""}`).

10. **No ACK equivalent → record in output summary**, do not generate a placeholder
    resource template. Exception: `kubernetes_*` resources (native K8s) CAN be included
    directly as native resource templates in the RGD — only types with no K8s-native
    representation at all (e.g. `helm_release`) are truly unsupported.

11. **RGD Kind**: Choose a meaningful PascalCase `kind` derived from the Terraform
    module or directory name. Set `apiVersion: v1alpha1` and `group: kro.run` (or
    user-specified custom group).

---

## Locals Resolution

Terraform `locals` are **not** a distinct RGD construct. Resolve them transitively:

- `local.name_prefix` referencing `"${var.name}-${var.environment}"` →
  CEL: `${schema.spec.name + "-" + schema.spec.environment}`
- Follow `local → local` chains until reaching `var.*`, `data.*`, or literals.

---

## Dependency Wiring (from HCL References)

HCL has no explicit `dependencies` array. Dependencies are inferred from reference
expressions inside resource arguments:

1. Scan each resource's argument expressions for `<type>.<name>.<attr>` references to
   **other `resource` blocks**.
2. Each such reference is a dependency edge.
3. Resources with no resource-to-resource references → first in the RGD.
4. Resources that reference others → add `readyWhen` referencing the dependency's
   status.
5. References to `var.*` / `local.*` / `data.*` are **not** ordering edges.

KRO automatically infers ordering from CEL references — the `readyWhen` and template
expressions create the DAG implicitly.

---

## Module Grouping Rule

One Terraform module (directory of `.tf` files) → **one KRO RGD**.

A `module` block invoking a child module passes variables down; the child module's
`.tf` files define its own resources/variables/outputs and produce a separate RGD.
