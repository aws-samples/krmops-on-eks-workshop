# Create_Path (Class C) — Self-Serve RGD Examples

> **What this is:** Examples of ResourceGraphDefinitions produced by the **Create_Path**.
> These are **self-serve abstraction RGDs** that platform teams publish so developers
> can provision AWS resources from a typed API — without writing ACK CRs directly.
>
> The Create_Path reads Terraform `.tf` HCL source and produces:
> - An RGD whose `spec` schema fields derive from Terraform `variable` blocks
>   (type, default, description preserved).
> - `status` fields derived from Terraform `output` blocks (expressed as CEL).
> - Resource templates using ACK Kind/apiVersion with **full spec populated from
>   variables** — NO adoption annotations.

---

## Shared Authoring Guidance

For schema marker syntax, CEL expressions, `readyWhen`, `forEach`, reserved IDs,
`includeWhen`, `externalRef`, and version-compatibility rules, see:

**[`../rgd-reference.md`](../rgd-reference.md)** (the Shared_KRO_Layer reference)

This file provides mode-specific **examples only** — it does not duplicate authoring
content.

---

## Example 1: S3 Bucket + IAM Write Policy

### Terraform Source (what the Create_Path reads)

```hcl
# variables.tf
variable "bucket_name" {
  type        = string
  description = "Name of the S3 bucket"
}

variable "enable_versioning" {
  type        = bool
  default     = false
  description = "Enable S3 bucket versioning"
}

variable "tags" {
  type        = map(string)
  default     = {}
  description = "Tags applied to all resources"
}

# outputs.tf
output "bucket_arn" {
  value       = aws_s3_bucket.main.arn
  description = "ARN of the created S3 bucket"
}

output "bucket_name" {
  value       = aws_s3_bucket.main.id
  description = "Name of the created S3 bucket"
}

# main.tf
resource "aws_s3_bucket" "main" {
  bucket = var.bucket_name
  tags   = var.tags
}

resource "aws_s3_bucket_versioning" "main" {
  bucket = aws_s3_bucket.main.id
  versioning_configuration {
    status = var.enable_versioning ? "Enabled" : "Suspended"
  }
}

resource "aws_iam_policy" "bucket_write" {
  name   = "${var.bucket_name}-write-policy"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
      Resource = ["${aws_s3_bucket.main.arn}/*"]
    }]
  })
}
```

### Generated RGD

```yaml
# Generated for kro kro.run/v1alpha1 (target controller image: kro 0.9.2)
# VERIFY against your installed kro version: forEach binding form, omit() feature gate.
# Detect with: kubectl get deployment -n kro kro-controller-manager \
#   -o jsonpath='{.spec.template.spec.containers[*].image}'
apiVersion: kro.run/v1alpha1
kind: ResourceGraphDefinition
metadata:
  name: s3-bucket-with-policy
spec:
  schema:
    apiVersion: v1alpha1
    kind: S3BucketWithPolicy
    spec:
      # variable "bucket_name" { type = string }
      bucketName: string | required=true description="Name of the S3 bucket"
      # variable "enable_versioning" { type = bool, default = false }
      enableVersioning: boolean | default=false description="Enable S3 bucket versioning"
      # variable "tags" { type = map(string), default = {} }
      tags: "map[string]string"
    status:
      # output "bucket_arn"
      bucketArn: ${bucket.status.?ackResourceMetadata.arn.orValue("")}
      # output "bucket_name"
      bucketName: ${bucket.spec.name}

  resources:
    # aws_s3_bucket.main → ACK Bucket
    - id: bucket
      readyWhen:
        - ${bucket.status.?ackResourceMetadata.arn != ""}
      template:
        apiVersion: s3.services.k8s.aws/v1alpha1
        kind: Bucket
        metadata:
          name: ${schema.spec.bucketName}
        spec:
          name: ${schema.spec.bucketName}
          versioning:
            status: ${schema.spec.enableVersioning ? "Enabled" : "Suspended"}
          tagging:
            tagSet: ${schema.spec.tags}

    # aws_iam_policy.bucket_write → ACK Policy
    - id: bucketWritePolicy
      readyWhen:
        - ${bucketWritePolicy.status.?ackResourceMetadata.arn != ""}
      template:
        apiVersion: iam.services.k8s.aws/v1alpha1
        kind: Policy
        metadata:
          name: ${schema.spec.bucketName}-write-policy
        spec:
          name: ${schema.spec.bucketName}-write-policy
          policyDocument: |
            {
              "Version": "2012-10-17",
              "Statement": [
                {
                  "Effect": "Allow",
                  "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                  "Resource": ["${bucket.status.ackResourceMetadata.arn}/*"]
                }
              ]
            }
```

### Generated Instance

The instance populates the required `spec` schema fields defined in the RGD above.
Developers apply this to provision resources — they never touch ACK CRs directly.

```yaml
apiVersion: kro.run/v1alpha1
kind: S3BucketWithPolicy
metadata:
  name: team-data-bucket
  namespace: team-alpha
spec:
  bucketName: team-alpha-data-prod
  enableVersioning: true
  tags:
    team: alpha
    environment: production
    managed-by: kro
```

---

## Example 2: Multi-Resource RGD with `includeWhen`

A more complex scenario: an S3 bucket with an optional replication configuration and a
conditional read-only policy (only created when `enableReadPolicy` is true).

### Generated RGD (abbreviated — focuses on patterns)

```yaml
# Generated for kro kro.run/v1alpha1 (target controller image: kro 0.9.2)
# VERIFY against your installed kro version: forEach binding form, omit() feature gate.
# Detect with: kubectl get deployment -n kro kro-controller-manager \
#   -o jsonpath='{.spec.template.spec.containers[*].image}'
apiVersion: kro.run/v1alpha1
kind: ResourceGraphDefinition
metadata:
  name: s3-bucket-full
spec:
  schema:
    apiVersion: v1alpha1
    kind: S3BucketFull
    spec:
      bucketName: string | required=true description="Name of the S3 bucket"
      enableVersioning: boolean | default=false description="Enable bucket versioning"
      enableReadPolicy: boolean | default=false description="Create a read-only IAM policy"
      replicationDestinationArn: string | default="" description="Destination bucket ARN for replication (empty = no replication)"
      tags: "map[string]string"
    status:
      bucketArn: ${bucket.status.?ackResourceMetadata.arn.orValue("")}
      readPolicyArn: ${readPolicy.status.?ackResourceMetadata.arn.orValue("")}

  resources:
    - id: bucket
      readyWhen:
        - ${bucket.status.?ackResourceMetadata.arn != ""}
      template:
        apiVersion: s3.services.k8s.aws/v1alpha1
        kind: Bucket
        metadata:
          name: ${schema.spec.bucketName}
        spec:
          name: ${schema.spec.bucketName}
          versioning:
            status: ${schema.spec.enableVersioning ? "Enabled" : "Suspended"}
          tagging:
            tagSet: ${schema.spec.tags}

    # Conditional: only created when enableReadPolicy is true
    - id: readPolicy
      includeWhen:
        - ${schema.spec.enableReadPolicy}
      readyWhen:
        - ${readPolicy.status.?ackResourceMetadata.arn != ""}
      template:
        apiVersion: iam.services.k8s.aws/v1alpha1
        kind: Policy
        metadata:
          name: ${schema.spec.bucketName}-read-policy
        spec:
          name: ${schema.spec.bucketName}-read-policy
          policyDocument: |
            {
              "Version": "2012-10-17",
              "Statement": [
                {
                  "Effect": "Allow",
                  "Action": ["s3:GetObject", "s3:ListBucket"],
                  "Resource": [
                    "${bucket.status.ackResourceMetadata.arn}",
                    "${bucket.status.ackResourceMetadata.arn}/*"
                  ]
                }
              ]
            }
```

### Generated Instance

```yaml
apiVersion: kro.run/v1alpha1
kind: S3BucketFull
metadata:
  name: analytics-bucket
  namespace: data-team
spec:
  bucketName: analytics-data-eu-west-1
  enableVersioning: true
  enableReadPolicy: true
  replicationDestinationArn: ""
  tags:
    team: data-engineering
    cost-center: "12345"
```

---

## Key Patterns — Create_Path vs Adopt_Path

| Aspect | Create_Path (this file) | Adopt_Path (`../adoption/examples.md`) |
|--------|------------------------|----------------------------------------|
| **Purpose** | Self-serve abstraction; developers provision new resources | Zero-downtime takeover of existing AWS resources |
| **Input** | `.tf` HCL source (variables, outputs, resources) | `terraform.tfstate` (runtime ARNs, IDs, attributes) |
| **ACK annotations** | **None** — full spec populated from variables | `services.k8s.aws/adoption-policy: adopt` + `adoption-fields` |
| **Deletion policy** | Not required (resources are new) | `services.k8s.aws/deletion-policy: retain` (safety) |
| **RGD `spec` source** | Terraform `variable` blocks (type/default/description) | Minimal — adopted resources already exist |
| **RGD `status` source** | Terraform `output` blocks → CEL expressions | Resource state post-adoption → CEL expressions |
| **Resource templates** | Full ACK spec derived from variables | Minimal spec + adoption annotations do the heavy lifting |
| **Consumer** | Developers (apply an instance to get resources) | Platform operators (adopt then hand off) |

### Summary

Create_Path RGDs are **blueprints** — they expose a typed API that developers consume to
provision resources from scratch. The ACK CRs inside the RGD have their full spec
populated from the schema (which in turn derives from Terraform variables), and carry
**no adoption annotations**. This is fundamentally different from Adopt_Path RGDs, which
take over resources that already exist in AWS.
