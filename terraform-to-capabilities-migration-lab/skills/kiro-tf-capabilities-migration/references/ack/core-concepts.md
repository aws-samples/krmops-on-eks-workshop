# ACK Core Concepts

> Source: [ACK Intro](https://aws-controllers-k8s.github.io/docs/intro/), [Core Concepts](https://aws-controllers-k8s.github.io/docs/concepts)

## What ACK Does

ACK lets you define and manage AWS service resources directly from Kubernetes using native CRDs. One controller per AWS service, each independently versioned.

- Manages AWS resources as Kubernetes Custom Resources
- Continuous reconciliation: desired state (spec) → actual state (AWS)
- Drift detection: if someone changes AWS outside K8s, ACK reverts it
- Kubernetes (etcd) becomes the source of truth — not a separate state file

## CRD Design

### One CRD per AWS resource type
- S3 Bucket CRD includes versioning, encryption, lifecycle — all in one spec
- IAM Role CRD includes policy attachments directly — no separate "RoleAttachment"
- Consolidated model: one manifest = complete desired state

### Field mapping
- `spec` fields → AWS API input parameters
- `status` fields → AWS API output/describe responses
- Field names: AWS PascalCase → Kubernetes camelCase

### API Version pattern
```
apiVersion: <service>.services.k8s.aws/v1alpha1
kind: <AWSResourceType>
```

## Status & Conditions

Every ACK resource has:

```yaml
status:
  # AWS-specific state
  tableStatus: ACTIVE
  
  # Standard ACK metadata (always present after creation/adoption)
  ackResourceMetadata:
    arn: arn:aws:dynamodb:us-west-2:123456789012:table/my-table
    ownerAccountID: "123456789012"
    region: us-west-2
  
  # Kubernetes conditions
  conditions:
    - type: Ready              # True = resource is ready to use
    - type: ACK.ResourceSynced # True = in sync with AWS
    - type: ACK.Adopted        # True = successfully adopted
    - type: ACK.Terminal       # Unrecoverable error
    - type: ACK.Recoverable   # Transient error, may self-resolve
```

### Key conditions for migration:
- `Ready: True` → resource is operational
- `ACK.ResourceSynced: True` → spec matches AWS reality
- `ACK.Adopted: True` → adoption was successful

## Reconciliation Loop

- Triggers on: CR create/update/delete, periodic sync (default 10h), controller restart
- Calls AWS APIs to create/update/delete resources
- Detects drift and reverts to declared spec
- Continuous — not one-shot like `terraform apply`

## Authentication Model

Two independent systems:
1. **Kubernetes RBAC** — who can read/write ACK CRs
2. **AWS IAM** — what AWS APIs the controller can call

Controller gets IAM permissions via:
- IRSA (IAM Roles for Service Accounts)
- EKS Pod Identity

The K8s user making `kubectl` calls has NO association with the controller's IAM role.

## Annotations (Behavior Modifiers)

Annotations change how ACK behaves without affecting the AWS resource:

| Annotation | Purpose |
|-----------|---------|
| `services.k8s.aws/region` | Target a specific AWS region |
| `services.k8s.aws/deletion-policy` | `retain` or `delete` |
| `services.k8s.aws/adoption-policy` | `adopt` or `adopt-or-create` |
| `services.k8s.aws/adoption-fields` | JSON with lookup fields |
| `services.k8s.aws/read-only` | Observe without managing |
| `services.k8s.aws/owner-account-id` | Cross-account management |

## Field References

ACK CRDs support referencing other ACK resources:

```yaml
# Reference another ACK resource by name
spec:
  vpcRef:
    from:
      name: my-vpc  # References a VPC CR in same namespace
```

For complex orchestration and dependencies → use KRO (Kube Resource Orchestrator).

## Key Takeaways for Migration

1. **No state file** — Kubernetes etcd IS the state
2. **Continuous reconciliation** — not one-shot apply
3. **Drift auto-correction** — ACK reverts manual changes
4. **One CRD = complete resource** — no fragmented sub-resources
5. **Annotations control behavior** — adoption, deletion, region, read-only
6. **Status.ackResourceMetadata.arn** — always populated after create/adopt
