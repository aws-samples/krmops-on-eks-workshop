# ACK Resource Adoption Patterns

> Source: [ACK Adoption Guide](https://aws-controllers-k8s.github.io/docs/guides/adoption), [Deletion Policy](https://aws-controllers-k8s.github.io/docs/guides/deletion-policy), [ReadOnly Resources](https://aws-controllers-k8s.github.io/docs/guides/readonly)

## What is Resource Adoption?

Resource adoption imports existing AWS resources into ACK management without recreating them. ACK looks up the resource in AWS, takes ownership, and begins reconciling it.

**Beta feature** — enabled by default in ACK runtime (`featureGates.ResourceAdoption=true`).

## Adoption Policies

### `adopt-or-create` (Recommended for migration)

Adopts the resource if it exists in AWS, or creates it if it doesn't.

- Provide a fully populated spec with desired configuration
- Use `services.k8s.aws/adoption-fields` to specify how to find the resource
- If resource exists: ACK adopts it and updates it to match your spec
- If resource doesn't exist: ACK creates it from your spec
- Your manifest becomes the source of truth

```yaml
apiVersion: ec2.services.k8s.aws/v1alpha1
kind: VPC
metadata:
  name: my-vpc
  annotations:
    services.k8s.aws/adoption-policy: "adopt-or-create"
    services.k8s.aws/adoption-fields: |
      {"vpcID": "vpc-0123456789abcdef0"}
spec:
  cidrBlocks:
    - "10.0.0.0/16"
  enableDNSSupport: true
  enableDNSHostnames: true
```

### `adopt` (Strict Import — read current state)

Imports the resource exactly as it exists in AWS. ACK populates the spec from the current AWS configuration.

- Provide an empty or minimal spec
- ACK fills in the spec from AWS
- Useful when you want to inspect before modifying

```yaml
apiVersion: s3.services.k8s.aws/v1alpha1
kind: Bucket
metadata:
  name: my-bucket
  annotations:
    services.k8s.aws/adoption-policy: adopt
    services.k8s.aws/adoption-fields: |
      {"name": "my-actual-bucket-name"}
spec: {}
```

## Adoption Fields (per resource type)

The `services.k8s.aws/adoption-fields` annotation specifies which fields ACK uses to look up the resource:

| Resource Type | Lookup Field | Example |
|---------------|-------------|---------|
| S3 Bucket | name | `{"name": "my-bucket-name"}` |
| EKS Cluster | name | `{"name": "my-cluster"}` |
| VPC | vpcID | `{"vpcID": "vpc-123456789012"}` |
| Subnet | subnetID | `{"subnetID": "subnet-abc123"}` |
| Security Group | id | `{"id": "sg-abc123"}` |
| IAM Role | name | `{"name": "my-role-name"}` |
| IAM Policy | arn | `{"arn": "arn:aws:iam::123:policy/name"}` |
| DynamoDB Table | tableName | `{"tableName": "my-table"}` |
| SQS Queue | queueURL | `{"queueURL": "https://sqs..."}` |
| SNS Topic | arn | `{"arn": "arn:aws:sns:..."}` |
| RDS DBInstance | dbInstanceIdentifier | `{"dbInstanceIdentifier": "mydb"}` |
| RDS DBCluster | dbClusterIdentifier | `{"dbClusterIdentifier": "mycluster"}` |

Refer to the [API Reference](https://aws-controllers-k8s.github.io/docs/api-reference) for the specific fields required for each resource type.

## Deletion Policy

Controls what happens to the AWS resource when the Kubernetes CR is deleted.

### Values
- `delete` (default) — Deletes the AWS resource when CR is deleted
- `retain` — Keeps the AWS resource, only removes the Kubernetes CR

### Configuration (precedence order)
1. Resource annotation: `services.k8s.aws/deletion-policy: retain`
2. Namespace annotation: `services.k8s.aws/deletion-policy: retain` (or `s3.services.k8s.aws/deletion-policy: retain` for service-specific)
3. Controller default (Helm values)

### Example — Retain on adopted resource
```yaml
apiVersion: s3.services.k8s.aws/v1alpha1
kind: Bucket
metadata:
  name: my-bucket
  annotations:
    services.k8s.aws/adoption-policy: "adopt-or-create"
    services.k8s.aws/adoption-fields: |
      {"name": "my-production-data"}
    services.k8s.aws/deletion-policy: retain
spec:
  name: my-production-data
```

**CRITICAL for migration:** Always use `retain` when adopting existing resources. This prevents accidental deletion if the CR is removed.

## ReadOnly Mode

Observe AWS resources without managing them. ACK syncs status but never creates, updates, or deletes.

```yaml
metadata:
  annotations:
    services.k8s.aws/read-only: "true"
```

### ReadOnly + Adoption (observe-only import)
```yaml
metadata:
  annotations:
    services.k8s.aws/adoption-policy: adopt
    services.k8s.aws/adoption-fields: |
      {"name": "my-actual-bucket"}
    services.k8s.aws/read-only: "true"
spec: {}
```

This adopts for observation only — ACK syncs status but never modifies the resource.

### Switching from ReadOnly to Managed
```bash
kubectl annotate bucket my-bucket services.k8s.aws/read-only-
```

## Verifying Adoption

```bash
# Check if resource is ready
kubectl get bucket my-bucket
# NAME        READY
# my-bucket   True

# Check conditions
kubectl describe bucket my-bucket | grep -A5 Conditions
# Should show ACK.ResourceSynced: True

# Check ARN populated
kubectl get bucket my-bucket -o jsonpath='{.status.ackResourceMetadata.arn}'
```

## After Adoption

- Adoption annotations are no longer needed (can be removed)
- ACK manages the resource like any other ACK resource
- Changes to spec will be applied to AWS (unless read-only)
- Deleting the CR will delete the AWS resource (unless deletion-policy: retain)

## Drift Detection & Reconciliation

- ACK continuously reconciles (default sync interval: 10 hours, configurable)
- If someone modifies the resource outside Kubernetes (Console, CLI, TF), ACK detects drift and reverts to the spec defined in Kubernetes
- Reconciliation triggers: CR create/update/delete, periodic sync, controller restart

## Multi-Region

Use annotation to target a specific region:
```yaml
metadata:
  annotations:
    services.k8s.aws/region: us-west-2
```

## Migration Safety Pattern (Recommended)

1. **Start with ReadOnly + Adopt** — observe without risk
2. **Validate** — confirm status matches expectations
3. **Remove read-only** — enable ACK management
4. **Set deletion-policy: retain** — safety net
5. **Do NOT delete Terraform state** — keep as rollback reference
