# ACK Resource Creation Examples (Create_Path)

> These examples show **full-spec** ACK Custom Resources for create-mode (Class C).
> They contain **NO adoption annotations** — the full resource spec is populated directly
> from Terraform variable values.
>
> For the rules governing these CRs, see [creation-patterns.md](./creation-patterns.md).
> For Kind/apiVersion mappings, see [../aws-to-ack-mappings.md](../aws-to-ack-mappings.md).

## How These Differ from Adoption CRs

| Aspect | Adoption CR (Adopt_Path) | Creation CR (Create_Path) |
|--------|--------------------------|---------------------------|
| `adoption-policy` annotation | Required (`adopt`) | **Absent** |
| `adoption-fields` annotation | Required (JSON lookup) | **Absent** |
| `deletion-policy` annotation | Required (`retain`) | **Absent** |
| `spec:` | Empty (`spec: {}`) or minimal | **Fully populated** from TF variables |
| Purpose | Take over an existing AWS resource | Provision a new AWS resource |

---

## S3 Bucket — Full Spec from Terraform Variables

**Source Terraform:**
```hcl
variable "bucket_name"       { type = string }
variable "enable_versioning" { type = bool, default = true }
variable "sse_algorithm"     { type = string, default = "AES256" }
variable "environment"       { type = string }
```

**Generated ACK CR:**

```yaml
apiVersion: s3.services.k8s.aws/v1alpha1
kind: Bucket
metadata:
  name: app-data-bucket
spec:
  # var.bucket_name → spec.name
  name: app-data-bucket
  # var.enable_versioning → spec.versioning.status
  versioning:
    status: Enabled
  # var.sse_algorithm → spec.encryption.rules[].applyServerSideEncryptionByDefault.sseAlgorithm
  encryption:
    rules:
      - applyServerSideEncryptionByDefault:
          sseAlgorithm: AES256
  # Best practice: block public access on new buckets
  publicAccessBlock:
    blockPublicACLs: true
    blockPublicPolicy: true
    ignorePublicACLs: true
    restrictPublicBuckets: true
  # var.environment → spec.tagging.tagSet[].value
  tagging:
    tagSet:
      - key: Environment
        value: production
      - key: ManagedBy
        value: ACK
```

---

## IAM Role — Full Spec with Assume Role Policy and Attached Policies

**Source Terraform:**
```hcl
variable "role_name"        { type = string }
variable "trusted_service"  { type = string, default = "pods.eks.amazonaws.com" }
variable "policy_arns"      { type = list(string) }
variable "environment"      { type = string }
```

**Generated ACK CR:**

```yaml
apiVersion: iam.services.k8s.aws/v1alpha1
kind: Role
metadata:
  name: app-service-role
spec:
  # var.role_name → spec.name
  name: app-service-role
  description: Service role for application workloads
  maxSessionDuration: 3600
  # var.trusted_service → spec.assumeRolePolicyDocument (Principal.Service)
  assumeRolePolicyDocument: |
    {
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {
          "Service": "pods.eks.amazonaws.com"
        },
        "Action": ["sts:AssumeRole", "sts:TagSession"]
      }]
    }
  # var.policy_arns → spec.policies (list of managed policy ARNs)
  policies:
    - arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess
    - arn:aws:iam::aws:policy/CloudWatchLogsFullAccess
  # var.environment → spec.tags[].value
  tags:
    - key: Environment
      value: production
    - key: ManagedBy
      value: ACK
```

---

## IAM Policy — Full Spec with Policy Document

**Source Terraform:**
```hcl
variable "policy_name"  { type = string }
variable "s3_bucket_arn" { type = string }
variable "environment"   { type = string }
```

**Generated ACK CR:**

```yaml
apiVersion: iam.services.k8s.aws/v1alpha1
kind: Policy
metadata:
  name: app-s3-access-policy
spec:
  # var.policy_name → spec.name
  name: app-s3-access-policy
  description: Grants read/write access to the application S3 bucket
  # Derived from var.s3_bucket_arn → policyDocument Resource field
  policyDocument: |
    {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Sid": "ListBucket",
          "Effect": "Allow",
          "Action": ["s3:ListBucket"],
          "Resource": "arn:aws:s3:::app-data-bucket"
        },
        {
          "Sid": "ReadWriteObjects",
          "Effect": "Allow",
          "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
          "Resource": "arn:aws:s3:::app-data-bucket/*"
        }
      ]
    }
  # var.environment → spec.tags[].value
  tags:
    - key: Environment
      value: production
    - key: ManagedBy
      value: ACK
```

---

## DynamoDB Table — Full Spec with Key Schema and Attributes

**Source Terraform:**
```hcl
variable "table_name"    { type = string }
variable "hash_key"      { type = string }
variable "range_key"     { type = string, default = "" }
variable "billing_mode"  { type = string, default = "PAY_PER_REQUEST" }
variable "environment"   { type = string }
```

**Generated ACK CR:**

```yaml
apiVersion: dynamodb.services.k8s.aws/v1alpha1
kind: Table
metadata:
  name: app-events-table
spec:
  # var.table_name → spec.tableName
  tableName: app-events-table
  # var.billing_mode → spec.billingMode
  billingMode: PAY_PER_REQUEST
  # var.hash_key + var.range_key → spec.keySchema
  keySchema:
    - attributeName: pk
      keyType: HASH
    - attributeName: sk
      keyType: RANGE
  # Attribute definitions match the key schema types
  attributeDefinitions:
    - attributeName: pk
      attributeType: S
    - attributeName: sk
      attributeType: S
  # var.environment → spec.tags[].value
  tags:
    - key: Environment
      value: production
    - key: ManagedBy
      value: ACK
```

---

## SQS Queue — Full Spec with Queue Configuration

**Source Terraform:**
```hcl
variable "queue_name"          { type = string }
variable "visibility_timeout"  { type = number, default = 30 }
variable "message_retention"   { type = number, default = 345600 }
variable "max_message_size"    { type = number, default = 262144 }
variable "delay_seconds"       { type = number, default = 0 }
variable "environment"         { type = string }
```

**Generated ACK CR:**

```yaml
apiVersion: sqs.services.k8s.aws/v1alpha1
kind: Queue
metadata:
  name: app-processing-queue
spec:
  # var.queue_name → spec.queueName
  queueName: app-processing-queue
  # var.visibility_timeout → spec.visibilityTimeout (string representation)
  visibilityTimeout: "30"
  # var.message_retention → spec.messageRetentionPeriod
  messageRetentionPeriod: "345600"
  # var.max_message_size → spec.maximumMessageSize
  maximumMessageSize: "262144"
  # var.delay_seconds → spec.delaySeconds
  delaySeconds: "0"
  # var.environment → spec.tags (map format for SQS)
  tags:
    Environment: production
    ManagedBy: ACK
```

---

## Key Observations

1. **No adoption annotations** — Create_Path CRs never include `adoption-policy`, `adoption-fields`, or `deletion-policy: retain`. A CR with any adoption annotation in create-mode fails generation.

2. **Full spec populated** — Every field that Terraform variables define is mapped into the ACK CR spec. The spec is never empty (`spec: {}`); it contains the complete resource configuration.

3. **Variable → spec field mapping** — Each Terraform `variable` block maps to one or more `spec` fields. Comments in the generated YAML trace which variable populated which field.

4. **Consolidated resources** — Terraform resources that ACK consolidates (e.g., `aws_s3_bucket_versioning` → `Bucket.spec.versioning`) are merged into a single CR, matching the ACK CRD structure (see [../aws-to-ack-mappings.md](../aws-to-ack-mappings.md) "Consolidated CRDs" section).

5. **Tags use the ACK format** — Most ACK controllers use `tags: [{key: K, value: V}]` (array of key-value pairs). SQS is an exception with map-format tags (`tags: {Key: Value}`). Always reference the relevant ACK CRD schema for the correct tag format.

6. **Policy documents are JSON strings** — `policyDocument` and `assumeRolePolicyDocument` are multiline JSON strings (use `|` or `>` block scalar), not YAML objects.

7. **SQS attributes are strings** — SQS queue attributes like `visibilityTimeout`, `messageRetentionPeriod`, and `maximumMessageSize` are string values in the ACK CRD, even though their Terraform counterparts are numbers.

8. **metadata.name is the K8s object name** — The CR `metadata.name` is the Kubernetes object identifier. The AWS resource name (bucket name, role name, table name, queue name) goes in the appropriate `spec` field.
