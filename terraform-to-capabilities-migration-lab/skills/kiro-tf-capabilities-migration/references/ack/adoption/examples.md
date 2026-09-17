# ACK Resource Examples

> Sources: Official ACK controller e2e test resources from GitHub
> - [s3-controller/test/e2e/resources](https://github.com/aws-controllers-k8s/s3-controller/tree/main/test/e2e/resources)
> - [iam-controller/test/e2e/resources](https://github.com/aws-controllers-k8s/iam-controller/tree/main/test/e2e/resources)
> - [eks-controller/test/e2e/resources](https://github.com/aws-controllers-k8s/eks-controller/tree/main/test/e2e/resources)

## How to Find Examples for Any ACK Resource

If an example is not listed here, use this URL pattern to find official e2e test YAMLs:

```
https://github.com/aws-controllers-k8s/<service>-controller/tree/main/test/e2e/resources
```

Replace `<service>` with the ACK service name (lowercase):
- S3 → `s3`
- IAM → `iam`
- EC2 → `ec2`
- EKS → `eks`
- RDS → `rds`
- DynamoDB → `dynamodb`
- SQS → `sqs`
- SNS → `sns`
- Lambda → `lambda`
- CloudFront → `cloudfront`
- ELBv2 → `elbv2`
- ECR → `ecr`
- KMS → `kms`
- Secrets Manager → `secretsmanager`
- EventBridge → `eventbridge`
- Step Functions → `sfn`
- Route53 → `route53`
- CloudWatch → `cloudwatch`

**Example URLs:**
- `https://github.com/aws-controllers-k8s/rds-controller/tree/main/test/e2e/resources`
- `https://github.com/aws-controllers-k8s/dynamodb-controller/tree/main/test/e2e/resources`
- `https://github.com/aws-controllers-k8s/lambda-controller/tree/main/test/e2e/resources`

Look for files named `<resource>_adopt.yaml` or `<resource>_adoption.yaml` for adoption-specific examples.

## How to Validate ACK Resource Fields (CRD Schemas)

To validate that a generated ACK CR has correct field names, types, and structure, reference the CRD definitions:

```
https://github.com/aws-controllers-k8s/<service>-controller/tree/main/helm/crds
```

These CRD YAML files contain the full OpenAPI v3 schema for each resource — every spec field, status field, type, required/optional markers, and validation rules.

**Example URLs:**
- `https://github.com/aws-controllers-k8s/s3-controller/tree/main/helm/crds`
- `https://github.com/aws-controllers-k8s/iam-controller/tree/main/helm/crds`
- `https://github.com/aws-controllers-k8s/eks-controller/tree/main/helm/crds`

**File naming pattern:** `<service>.services.k8s.aws_<resource-plural>.yaml`
- S3 Bucket → `s3.services.k8s.aws_buckets.yaml`
- IAM Role → `iam.services.k8s.aws_roles.yaml`
- IAM Policy → `iam.services.k8s.aws_policies.yaml`
- EKS Cluster → `eks.services.k8s.aws_clusters.yaml`
- EKS PodIdentityAssociation → `eks.services.k8s.aws_podidentityassociations.yaml`

**When to use CRD schemas:**
- Validating field names before generating a CR (e.g., is it `roleARN` or `roleArn`?)
- Checking which fields are required vs optional
- Discovering available spec fields for a resource you haven't seen before
- Confirming status field paths for readyWhen conditions in KRO

## S3 Bucket — Create

```yaml
apiVersion: s3.services.k8s.aws/v1alpha1
kind: Bucket
metadata:
  name: my-app-bucket
spec:
  name: my-app-bucket
```

## S3 Bucket — Adopt (strict import)

```yaml
apiVersion: s3.services.k8s.aws/v1alpha1
kind: Bucket
metadata:
  name: my-existing-bucket
  annotations:
    services.k8s.aws/adoption-policy: adopt
    services.k8s.aws/adoption-fields: '{"name": "my-existing-bucket-name"}'
    services.k8s.aws/deletion-policy: retain
spec: {}
```

## S3 Bucket — Adopt-or-Create

```yaml
apiVersion: s3.services.k8s.aws/v1alpha1
kind: Bucket
metadata:
  name: my-bucket
  annotations:
    services.k8s.aws/adoption-policy: adopt-or-create
    services.k8s.aws/adoption-fields: '{"name": "my-actual-bucket-name"}'
    services.k8s.aws/deletion-policy: retain
spec:
  name: my-actual-bucket-name
  tagging:
    tagSet:
      - key: Environment
        value: production
```

## S3 Bucket — Full spec (versioning + encryption + public access block)

```yaml
apiVersion: s3.services.k8s.aws/v1alpha1
kind: Bucket
metadata:
  name: my-production-bucket
spec:
  name: my-production-data
  versioning:
    status: Enabled
  encryption:
    rules:
      - applyServerSideEncryptionByDefault:
          sseAlgorithm: AES256
  publicAccessBlock:
    blockPublicACLs: true
    blockPublicPolicy: true
    ignorePublicACLs: true
    restrictPublicBuckets: true
  tagging:
    tagSet:
      - key: Name
        value: my-production-data
```

---

## IAM Role — Create

```yaml
apiVersion: iam.services.k8s.aws/v1alpha1
kind: Role
metadata:
  name: my-app-role
spec:
  name: my-app-role
  description: Role for my application
  maxSessionDuration: 3600
  assumeRolePolicyDocument: >
    {
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {
          "Service": ["ec2.amazonaws.com"]
        },
        "Action": ["sts:AssumeRole"]
      }]
    }
  tags:
    - key: Environment
      value: production
```

## IAM Role — With policies attached

```yaml
apiVersion: iam.services.k8s.aws/v1alpha1
kind: Role
metadata:
  name: my-app-role
spec:
  name: MyApplicationRole
  assumeRolePolicyDocument: |
    {
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "pods.eks.amazonaws.com"},
        "Action": ["sts:AssumeRole", "sts:TagSession"]
      }]
    }
  policies:
    - arn:aws:iam::aws:policy/CloudWatchLogsFullAccess
  inlinePolicies:
    s3-read-access: |
      {
        "Version": "2012-10-17",
        "Statement": [{
          "Effect": "Allow",
          "Action": ["s3:GetObject", "s3:ListBucket"],
          "Resource": "*"
        }]
      }
```

## IAM Role — Adopt

```yaml
apiVersion: iam.services.k8s.aws/v1alpha1
kind: Role
metadata:
  name: my-existing-role
  annotations:
    services.k8s.aws/adoption-policy: adopt
    services.k8s.aws/adoption-fields: '{"name": "my-existing-role-name"}'
    services.k8s.aws/deletion-policy: retain
spec: {}
```

---

## IAM Policy — Create

```yaml
apiVersion: iam.services.k8s.aws/v1alpha1
kind: Policy
metadata:
  name: my-app-policy
spec:
  name: my-app-policy
  description: S3 access policy
  policyDocument: |
    {
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Action": ["s3:ListAllMyBuckets"],
        "Resource": "arn:aws:s3:::*"
      }, {
        "Effect": "Allow",
        "Action": ["s3:List*"],
        "Resource": ["*"]
      }]
    }
  tags:
    - key: Environment
      value: production
```

## IAM Policy — Adopt

```yaml
apiVersion: iam.services.k8s.aws/v1alpha1
kind: Policy
metadata:
  name: my-existing-policy
  annotations:
    services.k8s.aws/adoption-policy: adopt
    services.k8s.aws/adoption-fields: '{"arn": "arn:aws:iam::123456789012:policy/my-existing-policy"}'
    services.k8s.aws/deletion-policy: retain
spec: {}
```

---

## EKS PodIdentityAssociation — Create

```yaml
apiVersion: eks.services.k8s.aws/v1alpha1
kind: PodIdentityAssociation
metadata:
  name: my-app-pod-identity
spec:
  clusterName: my-cluster
  namespace: default
  roleARN: arn:aws:iam::123456789012:role/my-app-role
  serviceAccount: my-app-sa
```

## EKS Cluster — Adopt

```yaml
apiVersion: eks.services.k8s.aws/v1alpha1
kind: Cluster
metadata:
  name: my-cluster
  annotations:
    services.k8s.aws/adoption-policy: adopt
    services.k8s.aws/adoption-fields: '{"name": "my-cluster-name"}'
    services.k8s.aws/deletion-policy: retain
spec: {}
```

---

## Adoption Pattern Summary (All Resources)

The adoption pattern is consistent across all ACK resources:

```yaml
apiVersion: <service>.services.k8s.aws/v1alpha1
kind: <Kind>
metadata:
  name: <cr-name>
  annotations:
    services.k8s.aws/adoption-policy: adopt        # or adopt-or-create
    services.k8s.aws/adoption-fields: '<json>'     # lookup fields
    services.k8s.aws/deletion-policy: retain       # safety
spec: {}                                           # empty for strict adopt
```

The `adoption-fields` JSON varies per resource type (see aws-to-ack-mappings.md for the lookup field per resource).

---

## Key Observations from Official Examples

1. **Adoption CRs have empty spec** — ACK fills it from AWS state
2. **deletion-policy: retain is always set** on adoption resources
3. **adoption-fields is a JSON string** (not a YAML object) — must be quoted
4. **CR name ≠ AWS resource name** — the CR metadata.name is the K8s object name; the AWS resource name goes in adoption-fields or spec
5. **Tags use array format** — `tags: [{key: K, value: V}]` not map format
6. **policyDocument is a string** (JSON as string), not a YAML object
7. **assumeRolePolicyDocument uses `>` or `|`** for multiline JSON strings
