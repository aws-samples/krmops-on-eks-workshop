# Terraform State File Schema (v4)

> Source: [Terraform Internals — JSON Output Format](https://developer.hashicorp.com/terraform/internals/json-format)

## Overview

Terraform state files (`terraform.tfstate`) are JSON documents that track the current state of managed infrastructure. Version 4 is the current format (Terraform 0.12+).

The skill reads this file directly — no `terraform` CLI needed.

## Top-Level Structure

```json
{
  "version": 4,
  "terraform_version": "1.15.3",
  "serial": 7,
  "lineage": "ab6f8dbe-...",
  "outputs": { ... },
  "resources": [ ... ]
}
```

| Field | Type | Purpose |
|-------|------|---------|
| `version` | integer | State format version (always 4 for modern TF) |
| `terraform_version` | string | TF version that wrote this state |
| `serial` | integer | Increments on each state change |
| `lineage` | string | UUID identifying this state's lineage |
| `outputs` | object | Root module outputs (name → value/type) |
| `resources` | array | All tracked resources |

## Outputs Section

```json
"outputs": {
  "bucket_arn": {
    "value": "arn:aws:s3:::my-app-123456789012",
    "type": "string"
  },
  "iam_role_arn": {
    "value": "arn:aws:iam::123456789012:role/my-app-123456789012-role",
    "type": "string"
  }
}
```

**Use for:** Mapping to KRO RGD `status` fields.

## Resources Array

Each entry in `resources` represents one tracked resource or data source.

```json
{
  "module": "module.my_app",
  "mode": "managed",
  "type": "aws_s3_bucket",
  "name": "this",
  "provider": "provider[\"registry.terraform.io/hashicorp/aws\"]",
  "instances": [
    {
      "schema_version": 0,
      "attributes": { ... },
      "dependencies": [ ... ]
    }
  ]
}
```

| Field | Type | Purpose | Skill Usage |
|-------|------|---------|-------------|
| `module` | string (optional) | Module path (e.g., `module.my_app`) | Grouping — one module = one RGD |
| `mode` | string | `"managed"` or `"data"` | **Filter: only process `managed`** |
| `type` | string | TF resource type (e.g., `aws_s3_bucket`) | Map to ACK Kind via `ack/aws-to-ack-mappings.md` |
| `name` | string | Resource name in TF config | Used for CR naming |
| `provider` | string | Provider reference | Confirms AWS provider |
| `instances` | array | Resource instances (usually 1 unless count/for_each) | Contains attributes and deps |

## Instance Object

```json
{
  "schema_version": 0,
  "attributes": {
    "arn": "arn:aws:s3:::my-app-123456789012",
    "bucket": "my-app-123456789012",
    "region": "us-west-2",
    "tags": {
      "Cluster": "my-cluster",
      "Owner": "my-team"
    },
    ...
  },
  "dependencies": [
    "data.aws_caller_identity.current",
    "module.my_app.aws_iam_role.this"
  ]
}
```

| Field | Purpose | Skill Usage |
|-------|---------|-------------|
| `attributes` | All resource attributes as key-value | Extract ARN, ID, name, region, tags, policy docs |
| `dependencies` | Array of resource addresses this depends on | Build KRO RGD dependency ordering |

## Key Attributes by Resource Type

### aws_s3_bucket
- `attributes.bucket` → bucket name (adoption lookup field)
- `attributes.arn` → bucket ARN
- `attributes.region` → region for ACK annotation
- `attributes.tags` → preserve in CR

### aws_iam_role
- `attributes.name` → role name (adoption lookup field)
- `attributes.arn` → role ARN
- `attributes.assume_role_policy` → JSON string of trust policy document
- `attributes.tags` → preserve in CR

### aws_iam_policy
- `attributes.name` → policy name
- `attributes.arn` → policy ARN (adoption lookup field)
- `attributes.policy` → JSON string of policy document
- `attributes.tags` → preserve in CR

### aws_iam_role_policy_attachment
- `attributes.role` → role name
- `attributes.policy_arn` → policy ARN to attach
- **Not a separate ACK CR** — merge into Role's `policies` field

### aws_eks_pod_identity_association
- `attributes.association_id` → association ID (adoption lookup field)
- `attributes.cluster_name` → EKS cluster name
- `attributes.namespace` → K8s namespace
- `attributes.service_account` → K8s service account
- `attributes.role_arn` → IAM role ARN
- `attributes.region` → region for ACK annotation

### aws_vpc
- `attributes.id` → VPC ID (adoption lookup: `vpcID`)
- `attributes.cidr_block` → CIDR
- `attributes.tags` → preserve

### aws_subnet
- `attributes.id` → subnet ID (adoption lookup: `subnetID`)
- `attributes.vpc_id` → parent VPC
- `attributes.availability_zone` → AZ

### aws_security_group
- `attributes.id` → SG ID (adoption lookup: `id`)
- `attributes.vpc_id` → parent VPC
- `attributes.ingress` / `attributes.egress` → rules

### aws_eks_cluster
- `attributes.name` → cluster name (adoption lookup: `name`)
- `attributes.arn` → cluster ARN
- `attributes.endpoint` → API endpoint

### aws_db_instance
- `attributes.identifier` → DB identifier (adoption lookup: `dbInstanceIdentifier`)
- `attributes.arn` → DB ARN
- `attributes.endpoint` → connection endpoint

## Dependencies Array

```json
"dependencies": [
  "data.aws_caller_identity.current",
  "module.my_app.aws_iam_policy.this",
  "module.my_app.aws_iam_role.this",
  "module.my_app.aws_iam_role_policy_attachment.this",
  "module.my_app.aws_s3_bucket.this",
  "module.my_app.data.aws_iam_policy_document.bucket_access",
  "module.my_app.data.aws_iam_policy_document.trust"
]
```

**Format:** `[module_path.]<type>.<name>`

**How to use for KRO ordering:**
1. Filter to only `managed` resources (skip `data.*` dependencies)
2. Build a DAG: if resource A lists resource B in dependencies, B must be created/adopted before A
3. Resources with no managed dependencies → first in RGD (no `readyWhen` needed from others)
4. Resources with dependencies → add `readyWhen` referencing the dependency's status

**Example dependency chain from real state:**
```
aws_s3_bucket (no managed deps) → FIRST
aws_iam_policy (depends on s3_bucket) → SECOND
aws_iam_role (no managed deps) → can be FIRST/SECOND
aws_iam_role_policy_attachment (depends on role + policy) → THIRD (merged into role)
aws_eks_pod_identity_association (depends on all above) → LAST
```

## Module Grouping

Resources prefixed with the same `module` value belong together:
- `module.my_app.aws_s3_bucket.this`
- `module.my_app.aws_iam_role.this`

**Rule:** One TF module → One KRO RGD.

Resources without a `module` field are in the root module.

## Parsing Algorithm Summary

```
1. Read JSON file
2. For each resource in resources[]:
   a. Skip if mode != "managed"
   b. Skip if type starts with non-AWS prefix (rare)
   c. Group by module field
   d. Extract: type, name, attributes (ARN/ID/name/region/tags/policies), dependencies
   e. Map type → ACK Kind (using aws-to-ack-mappings.md)
   f. If type has no ACK mapping → document as unsupported
   g. If type is consolidated (e.g., role_policy_attachment) → merge into parent
3. Build dependency DAG per module group
4. Output: ordered list of adoptable resources with all needed attributes
```
