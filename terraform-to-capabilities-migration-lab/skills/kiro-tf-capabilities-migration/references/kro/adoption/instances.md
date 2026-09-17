# KRO Instances

> Source: [kro.run/docs/concepts/instances](https://kro.run/docs/concepts/instances)

## What is an Instance?

An instance is what users create to deploy resources. One instance = one deployed set of resources managed as a single unit.

```yaml
apiVersion: kro.run/v1alpha1
kind: WebApplication
metadata:
  name: my-app
spec:
  name: web-app
  image: nginx:latest
  ingress:
    enabled: true
```

## Lifecycle

When you create an instance, kro:
1. Creates all required resources in dependency order
2. Configures them according to spec
3. Manages them as a single unit
4. Keeps status up to date
5. Self-heals if resources are modified or deleted externally

## Reconciliation

- Reactive: watches all managed resources, triggers on any change
- Drift detection: if resource is manually modified/deleted, kro restores it
- Dependency propagation: changes propagate through the graph

**Suspend reconciliation (for debugging):**
```yaml
metadata:
  annotations:
    kro.run/reconcile: suspended
```

## Status

```yaml
status:
  state: ACTIVE              # ACTIVE | IN_PROGRESS | FAILED | DELETING | ERROR
  conditions:
    - type: Ready            # Top-level — true when all sub-conditions are true
    - type: InstanceManaged  # Finalizers and labels set
    - type: GraphResolved    # Resource graph parsed and resolved
    - type: ResourcesReady   # All resources created and ready
```

**States:**
- `ACTIVE` — running successfully
- `IN_PROGRESS` — being processed
- `FAILED` — reconciliation failed
- `DELETING` — being deleted
- `ERROR` — error occurred

## Ownership

- kro uses labels + ApplySet specification (not owner references by default)
- Deletion in reverse topological order (respects dependencies)
- Labels: `kro.run/owned`, `kro.run/resource-graph-definition-name`, etc.

## Debugging

```bash
# Check overall status
kubectl get <kind> <name>

# Check conditions
kubectl get <kind> <name> -o jsonpath='{.status.conditions[?(@.type=="Ready")]}'

# Detailed info
kubectl describe <kind> <name>
```

**Common issues:**
- `GraphResolved: False` → check RGD for syntax/CEL errors
- `ResourcesReady: False` → a managed resource failed to become ready
- `observedGeneration < metadata.generation` → controller hasn't processed latest changes yet
- Missing spec fields → all referenced `schema.spec` fields must have values (use `?` + `.orValue()` for truly optional)

## Instance for Adoption

When using KRO with ACK adoption, the instance provides the values needed for adoption-fields:

```yaml
apiVersion: kro.run/v1alpha1
kind: EksPodIdentityS3
metadata:
  name: my-app
  namespace: default
spec:
  name: my-app-123456789012
  region: us-west-2
  clusterName: my-cluster
  serviceAccount: my-app-sa
  accountId: "123456789012"
```

kro then creates the ACK CRs with adoption annotations, using these values to populate the `adoption-fields` JSON.

---

## ownerReferences — Open Item

### Status

This is an **Open_Item**. The default behavior for `ownerReferences` on adopted resources is **not yet decided** and is pending a service-team decision.

### Configurable + Flagged

The `ownerReferences` handling is exposed as a **configurable, flagged option** — it is not hardcoded into the generated output. Operators may opt in to setting `ownerReferences` via the flag; it is never applied silently.

### Pending Default (Rollback-Safe)

Until the service team decides, the rollback-safe pending default is to **omit `ownerReferences`** on adopted resources.

**Rationale:** Omitting `ownerReferences` prevents accidental cascade-deletion of live AWS resources if the RGD instance is deleted. This is the safest posture for a production migration where the primary goal is zero-downtime takeover without risking resource loss.

### Cascade-Delete Warning

> ⚠️ **Critical risk:** If `ownerReferences` are set on adopted ACK CRs pointing to the RGD instance as owner, then **deleting the instance will cascade-delete the ACK CRs**. If the ACK CR's `deletion-policy` is not set to `retain` (or is accidentally removed), the ACK controller will **delete the live AWS resources**.

This is the worst-case scenario for a production migration: an operator deletes the KRO instance (e.g., during cleanup or debugging), the ACK CRs are garbage-collected via `ownerReferences`, and the underlying AWS resources (S3 buckets, IAM roles, databases) are destroyed.

### Opt-In Behavior

Operators who want full GitOps-tracked lifecycle — where deleting the instance intentionally cleans up all managed ACK CRs — can **enable `ownerReferences` via the flag**. This is appropriate when:

- The operator fully understands the cascade-delete behavior
- `deletion-policy: retain` is confirmed on all CRs as a safety layer
- The team has decided that instance deletion should trigger CR cleanup

**Always pair `ownerReferences` with `deletion-policy: retain` as a defense-in-depth measure.** Even with `ownerReferences` enabled, retain ensures the AWS resource survives CR deletion.
