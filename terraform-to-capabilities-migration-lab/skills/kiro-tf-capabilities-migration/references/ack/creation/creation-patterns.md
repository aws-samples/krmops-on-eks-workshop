# ACK Resource Creation Patterns (Create_Path)

> This document describes how the Create_Path (Class C) generates ACK Custom Resources for **new resource provisioning** — no adoption of existing AWS resources.

## Overview

The Create_Path produces ACK CRs that provision AWS resources from scratch. Unlike the Adopt_Path (Class B), which imports existing resources using adoption annotations and an empty or minimal spec, the Create_Path:

- **Omits all adoption annotations** — the resource does not exist yet; there is nothing to adopt.
- **Populates the full `spec`** from Terraform variable values — the CR carries a complete desired-state definition.
- **Does not set `services.k8s.aws/deletion-policy: retain`** — the default `delete` behavior is correct because the resource was never externally managed; if the CR is removed, the resource should be cleaned up.

These CRs are consumed inside a KRO ResourceGraphDefinition that acts as a self-serve blueprint developers instantiate to provision resources on demand.

## Contrast with Adoption Mode

| Aspect | Adopt_Path (Class B) | Create_Path (Class C) |
|--------|---------------------|----------------------|
| Purpose | Import existing AWS resource | Provision a new AWS resource |
| `services.k8s.aws/adoption-policy` | `adopt` or `adopt-or-create` | **NEVER present** |
| `services.k8s.aws/adoption-fields` | JSON lookup fields | **NEVER present** |
| `services.k8s.aws/deletion-policy` | `retain` (safety) | **Not set** (default `delete` is correct) |
| `spec:` | Empty (`{}`) or minimal | **Fully populated** from Terraform variables |
| Data source | `terraform.tfstate` (runtime values) | `.tf` HCL files (variable definitions) |

---

## Generation Rules

### 1. Populate ALL required spec fields from Terraform variable mappings

Every field the ACK CRD marks as required MUST be populated in the generated CR. Values are derived from Terraform variables and resource argument expressions:

- A Terraform `variable` block maps to a spec field value (using its `default` or marking the field as required if no default exists).
- Terraform resource argument expressions (`var.xxx`, `local.xxx`) are resolved to concrete values drawn from the corresponding variable definitions.
- Nested objects in the Terraform resource map to nested spec structures in the ACK CR.

### 2. Use `aws-to-ack-mappings.md` to determine Kind and apiVersion

Every generated CR uses the ACK Kind and apiVersion from the shared mapping table at `../aws-to-ack-mappings.md`:

```yaml
apiVersion: <service>.services.k8s.aws/v1alpha1
kind: <Kind>
```

For example, `aws_s3_bucket` → `apiVersion: s3.services.k8s.aws/v1alpha1`, `kind: Bucket`.

### 3. NEVER include adoption annotations

The following annotations MUST NOT appear on a Create_Path CR:

- `services.k8s.aws/adoption-policy`
- `services.k8s.aws/adoption-fields`
- `services.k8s.aws/deletion-policy: retain`

The `deletion-policy` annotation is intentionally omitted (not set to any value). The default ACK behavior (`delete`) is correct for create-mode: the resource was provisioned by ACK and should be removed when the CR is deleted.

### 4. Derive `metadata.name` from the Terraform resource name

The CR `metadata.name` is derived from the Terraform resource block name, normalized to a valid Kubernetes name (lowercase, alphanumeric, hyphens):

```hcl
resource "aws_s3_bucket" "app_data" { ... }
```

→ `metadata.name: app-data`

Underscores are converted to hyphens. If a naming prefix/suffix is desired for the RGD context, it is applied by the RGD template using CEL expressions.

---

## Fail Guard: Adoption Annotation Rejection (Req 4.5)

**If any generated Create_Path CR contains an ACK adoption annotation, generation MUST fail immediately.**

This is a hard guard. During CR generation, the output is validated:

1. Scan the generated CR for any of the three forbidden annotations:
   - `services.k8s.aws/adoption-policy`
   - `services.k8s.aws/adoption-fields`
   - `services.k8s.aws/deletion-policy: retain`
2. If **any** of these annotations are present → **FAIL** generation.
3. Report the offending resource and annotation to the operator.
4. Do NOT emit partial output — either all CRs pass validation or none are generated.

**Rationale:** A Create_Path CR with adoption annotations is a logic error that could cause ACK to attempt adopting a non-existent resource (failing) or — worse — accidentally take over an unrelated resource that happens to match the lookup fields. The fail-fast guard prevents this class of error from reaching the cluster.

---

## Variable → Spec Mapping Pattern

The core task of Create_Path CR generation is translating Terraform variable definitions and resource arguments into ACK spec fields.

### Terraform variable type → ACK spec field type

| Terraform Type | ACK Spec Field Type | Notes |
|---------------|--------------------|-|
| `string` | `string` | Direct mapping |
| `number` | `integer` or `number` | Check CRD schema for exact type |
| `bool` | `boolean` | Direct mapping |
| `list(string)` | `[]string` (array) | Maps to YAML list |
| `map(string)` | `map[string]string` | Maps to YAML object / inline JSON string (depends on CRD) |
| `object({...})` | nested object | Map each attribute to the corresponding spec sub-field |

### Terraform variable default → field value

- If the variable has a `default`, use it as the field value in the generated CR.
- If the variable has **no default** (it is required), the field becomes a parameter in the RGD schema `spec` — the developer provides the value at instance creation time.

### Resolving resource argument expressions

Terraform resource blocks reference variables, locals, and other expressions:

```hcl
variable "bucket_name" {
  type    = string
  default = "my-app-data"
}

resource "aws_s3_bucket" "data" {
  bucket = var.bucket_name
  tags   = { Name = var.bucket_name }
}
```

Resolution:
1. `var.bucket_name` → resolve to the variable's default (`"my-app-data"`) or mark as RGD schema input.
2. Expressions like `"${var.prefix}-data"` → resolve by substituting known defaults or parameterize in the RGD.
3. `local.*` references → resolve from the corresponding `locals` block value.
4. References to other resources (e.g., `aws_iam_role.my_role.arn`) → wire as a dependency in the RGD using CEL expressions to inject the value from the upstream resource's status.

### Example: variable-to-spec mapping

**Terraform source:**

```hcl
variable "role_name" {
  type        = string
  description = "Name of the IAM role"
}

variable "max_session" {
  type    = number
  default = 3600
}

variable "assume_role_policy" {
  type        = string
  description = "JSON trust policy document"
}

resource "aws_iam_role" "app" {
  name                 = var.role_name
  max_session_duration = var.max_session
  assume_role_policy   = var.assume_role_policy
}
```

**Generated ACK CR (Create_Path):**

```yaml
apiVersion: iam.services.k8s.aws/v1alpha1
kind: Role
metadata:
  name: app
spec:
  name: ${schema.spec.roleName}          # from var.role_name (no default → RGD param)
  maxSessionDuration: 3600               # from var.max_session default
  assumeRolePolicyDocument: |            # from var.assume_role_policy (no default → RGD param)
    ${schema.spec.assumeRolePolicy}
```

> Note: `${schema.spec.*}` placeholders indicate fields that become RGD schema inputs because the source variable has no default. In the final RGD template, these use CEL expressions (e.g., `schema.spec.roleName`).

---

## Class A Handling on Create_Path

**Class A exclusions do NOT apply to the Create_Path.** When a user provides `.tf` files and asks for a provisioning blueprint, the goal is a complete self-serve abstraction that provisions the entire stack from zero.

ALL resources in the `.tf` files that have ACK equivalents are included — VPCs, subnets, EKS clusters, everything. The Create_Path never excludes resources just because they are "foundation infrastructure." That classification only matters for the Adopt_Path (where you don't want to adopt your existing VPC into ACK).

**Adopt_Path only:** Resources classified as Class A (VPC, subnet, EKS cluster) are excluded from Adopt_Path generation. They remain managed by Terraform during adoption scenarios.

---

## Naming Patterns

| Source | CR Field | Rule |
|--------|----------|------|
| Terraform resource name (e.g., `"app_data"`) | `metadata.name` | Lowercase, underscores → hyphens |
| Terraform `name` argument or variable | `spec.name` (where applicable) | Use the resolved value or parameterize |
| Terraform resource type | Kind + apiVersion | Look up in `../aws-to-ack-mappings.md` |

---

## Reference: Shared Mapping Table

For the complete Terraform resource type → ACK Kind/apiVersion mapping, see:

**[`../aws-to-ack-mappings.md`](../aws-to-ack-mappings.md)**

That table provides:
- The ACK `apiVersion` and `Kind` for each supported Terraform resource type
- The adoption field (used only by the Adopt_Path — irrelevant for Create_Path)
- Notes on consolidated CRDs (multiple TF resources → one ACK CR)

When generating Create_Path CRs, the "Adoption Field" and "TF State Attribute for Lookup" columns are ignored — only `Kind` and `apiVersion` are consumed.

---

## Summary

1. Create_Path CRs carry a **full spec** derived from Terraform variables — never an empty spec.
2. Adoption annotations are **forbidden** — their presence fails generation immediately.
3. The `deletion-policy` annotation is **not set** — default `delete` is correct for newly provisioned resources.
4. Variable types map to spec field types; defaults become values; required variables become RGD schema inputs.
5. Use `../aws-to-ack-mappings.md` for Kind/apiVersion; ignore adoption-specific columns.
6. Class A resources are excluded — they stay in Terraform.


---

## API Gateway → Lambda: Solving `aws_lambda_permission` with the `credentials` Pattern

### The Problem

Terraform uses `aws_lambda_permission` to grant API Gateway the right to invoke a Lambda function via a resource-based policy. There is **no ACK CRD** for `aws_lambda_permission` — the ACK Lambda controller does not expose `AddPermission` as a declarative resource.

Without this permission, API Gateway returns HTTP 403 ("Forbidden") when trying to invoke Lambda through an integration.

### The Solution: Integration `credentials` Field

The ACK API Gateway Integration CRD has a `credentials` field (type: `string`). When set to an IAM role ARN, API Gateway assumes that role to invoke the backend (Lambda). This **completely bypasses** the need for a Lambda resource-based policy.

**How it works:**
1. Create an IAM Role with a trust policy for `apigateway.amazonaws.com`
2. Attach a policy granting `lambda:InvokeFunction` to the role
3. Set `spec.credentials` on the Integration to the role's ARN

API Gateway assumes the role → the role allows `lambda:InvokeFunction` → invocation succeeds. No resource-based policy on Lambda needed.

### Pattern (within a KRO RGD)

```yaml
# 1. IAM Policy granting Lambda invoke permission
- id: apigwInvokePolicy
  readyWhen:
    - "${apigwInvokePolicy.status.?ackResourceMetadata.arn.orValue(\"\") != \"\"}"
  template:
    apiVersion: iam.services.k8s.aws/v1alpha1
    kind: Policy
    metadata:
      name: "${schema.spec.appName}-apigw-invoke-policy"
    spec:
      name: "${schema.spec.appName}-apigw-invoke-policy"
      description: "Allows API Gateway to invoke Lambda functions"
      policyDocument: |
        {
          "Version": "2012-10-17",
          "Statement": [{
            "Effect": "Allow",
            "Action": "lambda:InvokeFunction",
            "Resource": "*"
          }]
        }

# 2. IAM Role trusted by API Gateway, with the invoke policy attached
- id: apigwInvokeRole
  readyWhen:
    - "${apigwInvokeRole.status.?ackResourceMetadata.arn.orValue(\"\") != \"\"}"
  template:
    apiVersion: iam.services.k8s.aws/v1alpha1
    kind: Role
    metadata:
      name: "${schema.spec.appName}-apigw-invoke-role"
    spec:
      name: "${schema.spec.appName}-apigw-invoke-role"
      assumeRolePolicyDocument: |
        {
          "Version": "2012-10-17",
          "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "apigateway.amazonaws.com"},
            "Action": "sts:AssumeRole"
          }]
        }
      policyRefs:
        - from:
            name: "${schema.spec.appName}-apigw-invoke-policy"

# 3. Integration with credentials set to the invoke role ARN
- id: myIntegration
  template:
    apiVersion: apigateway.services.k8s.aws/v1alpha1
    kind: Integration
    metadata:
      name: "${schema.spec.appName}-my-integration"
    spec:
      restAPIRef:
        from:
          name: "${schema.spec.appName}-my-api"
      resourceRef:
        from:
          name: "${schema.spec.appName}-my-resource"
      httpMethod: POST
      type: AWS_PROXY
      integrationHTTPMethod: POST
      credentials: "${apigwInvokeRole.status.ackResourceMetadata.arn}"
      uri: "${arn:aws:apigateway:... + function ARN + /invocations}"
```

### When to Use This Pattern

- **Always** when the source Terraform has `aws_lambda_permission` resources granting API Gateway invoke access
- **Always** for Create_Path API Gateway → Lambda integrations (since there's no other declarative way to grant the permission)
- The pattern also works for the Adopt_Path when re-creating the permission structure

### Comparison with `aws_lambda_permission`

| Aspect | `aws_lambda_permission` (TF) | `credentials` (ACK) |
|--------|------------------------------|---------------------|
| Mechanism | Resource-based policy on Lambda | IAM role assumed by API Gateway |
| Declarative in K8s? | ❌ No ACK CRD | ✅ Yes (Role + Policy + Integration.credentials) |
| Scope | Per-function permission | One role covers all functions (or scoped by Resource ARN) |
| Drift detection | ❌ Only on TF plan | ✅ ACK reconciles continuously |
| Cleanup on delete | Manual | Automatic (role deleted with RGD instance) |

### Security Considerations

- Scope the Policy's `Resource` field to limit which Lambda functions the role can invoke (use `arn:aws:lambda:<region>:<account>:function:<prefix>-*` instead of `*` for production)
- The trust policy should only allow `apigateway.amazonaws.com` as the principal
- One invoke role can be shared across all integrations in the same RGD (reduces IAM resource count)
