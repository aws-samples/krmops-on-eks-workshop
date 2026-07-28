---
name: tfstate-k8s-to-kro-adoption
description: Adopt native Kubernetes resources currently managed by Terraform into kro RGDs. Reads terraform.tfstate, filters K8s-provider managed resources, generates an RGD plus instance that match the live cluster state, and produces MIGRATION-NOTES.md describing the safe handoff (terraform state rm first, then SSA field-manager handoff, then apply RGD). Zero downtime, no recreation.
allowed-tools: Read, Grep, Glob, Write
---

# kro Version Compatibility

This skill targets `kro.run/v1alpha1`. Generated RGDs include a header comment naming the version, and call out primitives whose syntax has varied between alphas (`forEach`, `externalRef`, `omit()`). See [[rgd-authoring-reference]] for current RGD field rules.

# Workflow

When this transformation is invoked:

1. **Read state**: Open `terraform.tfstate` (or `terraform.tfstate.backup`) JSON. Validate it parses.

2. **Filter to in-scope managed resources**:
   - Keep only `mode: "managed"` (drop `data` sources — those are reads, not adoptions).
   - Keep only K8s providers: `kubernetes`, `kubectl`, `helm`.
   - For each kept instance, capture:
     - `type` (e.g. `kubernetes_deployment_v1`)
     - `name` (TF local name)
     - `module` path (if not root)
     - `instances[].attributes` — this is the source of truth for the live spec
     - `instances[].dependencies` — preserved as ordering hints, but KRO derives ordering from CEL refs

3. **Build the dependency graph** from `dependencies` and from references in `attributes`. Resources with no in-scope upstream dependencies are leaves; root the graph from there.

4. **Detect merge cases**:
   - Multiple `kubernetes_role_binding_v1` referencing the same Role → keep separate (RoleBinding is its own object).
   - `helm_release` → its rendered objects already exist in the cluster as native resources but **are NOT in tfstate** (only the release marker is). Two paths:
     - **Path A (recommended)**: skip the `helm_release` entry, run `helm get manifest <release>` against the cluster, adopt the underlying objects directly via the RGD.
     - **Path B**: leave the chart under Helm's management, document this in MIGRATION-NOTES.md, and don't adopt those objects into KRO.
   - Document the choice for each helm_release.

5. **Generate the RGD**:
   - `kind`: PascalCase from the module/dir name (or root module name).
   - `apiVersion: v1alpha1` at `spec.schema.apiVersion`. Do NOT emit a `group:` field at the schema level — kro derives the API group (`kro.run`) implicitly. See [[rgd-authoring-reference]].
   - Resource `id`: lowerCamelCase from K8s `kind` (suffix on collisions). Avoid reserved IDs (`schema`, `metadata`, `spec`, `status`, `each`, `instance`, `resources`, plus CEL macros: `has`, `all`, `exists`, `map`, `filter`).
   - Each resource template carries enough fields to satisfy CRD validation (required spec fields, populated from `attributes`).
   - **Minimal parameterization** — tfstate doesn't tell you which values came from variables. Default rule: parameterize only `metadata.name`, `metadata.namespace`, and obvious instance-scoped values (image tag, replicas). Everything else stays as a literal pulled from state. The user can refactor the RGD for more parameterization after adoption succeeds.
   - Add `readyWhen` for resources with known ready states (see [[rgd-authoring-reference]] for ready-condition recipes per kind).
   - Prepend a header comment to the generated RGD:
     ```yaml
     # Generated for kro kro.run/v1alpha1 — verify forEach / externalRef / omit() syntax against your installed version
     ```

6. **Generate the instance** with all spec fields populated from state attributes — applying it should produce the same effective desired state as the live cluster.

7. **Generate `MIGRATION-NOTES.md`** with the exact handoff procedure (see template below).

8. **Output**:
   - RGD → `kro/<module-name>-rgd.yaml`
   - Instance → `kro/<module-name>-instance.yaml`
   - `kro/MIGRATION-NOTES.md` — the runbook
   - `kro/README.md` — what was adopted, what was skipped, what was merged

# Adoption Mechanics — How This Differs From ACK Adoption

| Aspect                          | ACK adoption (AWS)                              | Native K8s adoption (this skill)               |
|---------------------------------|-------------------------------------------------|------------------------------------------------|
| Adoption annotation             | `services.k8s.aws/adoption-policy: adopt`       | None — KRO uses Server-Side Apply (SSA)        |
| Lookup-fields annotation        | `services.k8s.aws/adoption-fields`              | None — the RGD template's `metadata.name`/`namespace` IS the lookup |
| Deletion policy                 | `services.k8s.aws/deletion-policy: retain`      | None native; rely on `kro.run/reconcile` semantics + careful instance lifecycle |
| Source of truth                 | tfstate attributes                              | tfstate attributes                             |
| Ownership transfer mechanism    | ACK reads existing AWS resource by ID           | SSA field manager handoff (TF → KRO)           |
| Required cleanup                | `terraform state rm`                            | `terraform state rm` + strip TF labels/annotations |

**Why `terraform state rm` and not `terraform destroy`**: `destroy` deletes the live K8s objects. `state rm` just tells TF to forget about them. The objects keep running.

**Why `state rm` BEFORE applying the RGD (not after)**: While TF still tracks a resource, anyone running `terraform plan/apply` (CI, drift detection, another engineer) will see the kro-applied changes as drift and re-assert TF ownership. That triggers SSA conflicts with the kro field manager. Removing TF state first eliminates the double-ownership window.

**The actual SSA handoff**: TF labels and annotations (`app.kubernetes.io/managed-by: Terraform`, `terraform.io/*`) are *cosmetic* markers — they do NOT control ownership. Ownership lives in `metadata.managedFields[]`, which records every field manager that has touched the object. The TF entry there must be patched out so kro's field manager is the sole owner of the fields the RGD asserts. Stripping labels alone is insufficient.

**Why pre-emptively detach `ownerReferences` before adoption**: When kro applies an instance, by default each created resource gets an `ownerReference` pointing at the instance. Once that link exists, deleting the instance cascades and deletes every adopted resource. For adoption flows, you want to break this link until you've validated everything — otherwise "rollback" = "delete production." See Step 2.5 below.

# State Attribute → RGD Template Mapping

```json
// tfstate fragment
{
  "type": "kubernetes_deployment_v1",
  "name": "web",
  "instances": [
    {
      "attributes": {
        "metadata": [{
          "name": "web",
          "namespace": "production",
          "labels": { "app": "web" }
        }],
        "spec": [{
          "replicas": 3,
          "selector": [{ "match_labels": { "app": "web" } }],
          "template": [{
            "metadata": [{ "labels": { "app": "web" } }],
            "spec": [{
              "container": [{
                "name": "app",
                "image": "nginx:1.25.0",
                "port": [{ "container_port": 80 }]
              }]
            }]
          }]
        }]
      }
    }
  ]
}
```

```yaml
# RGD resource
- id: deployment
  readyWhen:
    - ${deployment.status.availableReplicas >= schema.spec.replicas}
  template:
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: ${schema.spec.appName}
      namespace: ${schema.metadata.namespace}
      labels:
        app: ${schema.spec.appName}
    spec:
      replicas: ${schema.spec.replicas}
      selector:
        matchLabels:
          app: ${schema.spec.appName}
      template:
        metadata:
          labels:
            app: ${schema.spec.appName}
        spec:
          containers:
            - name: app
              image: ${schema.spec.image}
              ports:
                - containerPort: 80
```

```yaml
# Instance — populated from state attributes
apiVersion: kro.run/v1alpha1
kind: WebApp
metadata:
  name: web
  namespace: production
spec:
  appName: web
  replicas: 3
  image: nginx:1.25.0
```

State quirks worth knowing:
- TF stores nested blocks as **single-element arrays** (`metadata: [{…}]`) — unwrap them.
- TF stores maps as ordered objects — preserve user-meaningful keys (labels, annotations).
- TF attribute names are snake_case; K8s YAML uses camelCase — convert (`match_labels` → `matchLabels`, `container_port` → `containerPort`).
- TF may include computed fields in `attributes` that aren't user-set (e.g., `status`, `metadata[0].generation`). Drop these — they're cluster-managed.

# MIGRATION-NOTES.md Template

Skill output should follow this structure. **Step ordering matters** — running Step 2 (apply RGD) before Step 1 (`terraform state rm`) creates a double-ownership window where TF and kro fight over `managedFields`. Always state-rm first.

```markdown
# Migration Notes — <module-name>

## Summary

- **Adopted**: N resources (list at end)
- **Skipped**: M resources (list at end with reasons)
- **Merged**: K cases (list at end)

## Pre-flight Checks

1. `kubectl` context points at the right cluster: `kubectl config current-context`
2. kro is installed and healthy: `kubectl get pods -n kro`
3. The RGD has been applied and reconciled successfully:
   ```
   kubectl apply -f kro/<module-name>-rgd.yaml
   kubectl get resourcegraphdefinition <name> -o yaml
   ```
4. TF state is backed up: `cp terraform.tfstate terraform.tfstate.pre-adoption-backup`
5. CI / drift-detection jobs that run `terraform plan` are PAUSED for the duration of the migration. A mid-flight `plan` will see in-cluster differences and re-assert TF ownership.
6. Record the original UID and managers for each resource (you'll diff against these later):
   ```
   kubectl get <kind>/<name> -n <ns> -o jsonpath='{.metadata.uid}{"\n"}'
   kubectl get <kind>/<name> -n <ns> -o jsonpath='{.metadata.managedFields[*].manager}{"\n"}'
   ```

## Step 1 — Remove resources from Terraform state FIRST

This must happen BEFORE the RGD instance is applied. While TF still tracks the resource, any concurrent `terraform plan/apply` will reassert TF ownership and conflict with kro's SSA field manager.

For each adopted resource:

```
terraform state rm <module>.<resource_address>
```

Specific commands for this module:

```
terraform state rm kubernetes_deployment_v1.web
terraform state rm kubernetes_service_v1.web
…
```

DO NOT run `terraform destroy`. The live K8s objects must keep running.

Confirm: `terraform plan` should now show the listed resources as "to be created" (because TF no longer knows them). That's expected at this midpoint and proves TF has let go. **Do NOT run `terraform apply`** — that would recreate them.

## Step 2 — SSA field-manager handoff

Labels and annotations are cosmetic. The actual ownership record is `metadata.managedFields[]`. Strip the TF entry from each adopted resource so kro becomes the sole field manager.

Inspect:
```
kubectl get <kind>/<name> -n <ns> -o jsonpath='{range .metadata.managedFields[*]}{.manager}{"\t"}{.operation}{"\n"}{end}'
```

Look for managers like `Terraform`, `terraform-provider-kubernetes`, or `HashiCorp.Terraform`. For each, find its index (0-based) and patch it out:

```
kubectl patch <kind>/<name> -n <ns> --type=json \
  -p='[{"op":"remove","path":"/metadata/managedFields/<index>"}]'
```

(If multiple managers need removing, do them by index from highest to lowest so earlier indices don't shift.)

Optional cosmetic cleanup — does NOT affect ownership but helps observability:
```
kubectl label   <kind>/<name> -n <ns> app.kubernetes.io/managed-by-
kubectl annotate <kind>/<name> -n <ns> terraform.io/external-id-
```

## Step 2.5 — Detach ownerReferences (rollback safety)

If you skip this step, deleting the kro instance later will cascade-delete every adopted resource. For high-stakes adoption, keep `ownerReferences` empty until you've validated kro's reconciliation; then optionally re-attach.

For each adopted resource:
```
kubectl patch <kind>/<name> -n <ns> --type=merge \
  -p='{"metadata":{"ownerReferences":[]}}'
```

Document this choice in `kro/README.md`. To later opt back into cascading deletion (after validation), let kro re-apply the instance with default behavior — it will repopulate `ownerReferences`.

## Step 3 — Apply the RGD instance

```
kubectl apply -f kro/<module-name>-instance.yaml
```

Watch reconciliation:
```
kubectl get <instance-kind> <instance-name> -n <namespace> -o yaml -w
```

Because TF state is gone (Step 1) and the TF field-manager entry is gone (Step 2), kro will pick up ownership via SSA without recreating anything. Verify each adopted object's UID is unchanged:

```
kubectl get <kind> <name> -n <namespace> -o jsonpath='{.metadata.uid}'
```

The UID MUST match the value recorded in pre-flight. A new UID means kro recreated the object — that's a regression, stop and investigate.

## Step 4 — Verify

```
kubectl get <kind>/<name> -n <ns> -o jsonpath='{range .metadata.managedFields[*]}{.manager}{"\n"}{end}'
```
You should see `kro-controller` (or your kro install's field manager name) and NO `Terraform` entry.

```
terraform plan
```
The plan must show ZERO in-scope resources to add/change/destroy. If it shows the K8s resources you just adopted, state cleanup is incomplete — return to Step 1.

Resume any paused CI / drift-detection jobs.

## Rollback

⚠️ Read this BEFORE deleting any kro resource. If `ownerReferences` were not stripped in Step 2.5, deleting the instance will delete all adopted resources.

To roll back safely:

1. **First**, confirm `ownerReferences` are empty on every adopted resource:
   ```
   kubectl get <kind>/<name> -n <ns> -o jsonpath='{.metadata.ownerReferences}'
   ```
   If non-empty, patch them out FIRST (Step 2.5 commands) before doing anything else.

2. Delete the kro instance only:
   ```
   kubectl delete <instance-kind> <instance-name> -n <namespace>
   ```
   (With ownerReferences empty, the underlying K8s resources keep running.)

3. Re-import each resource into TF:
   ```
   terraform import kubernetes_deployment_v1.web production/web
   ```

4. Run `terraform plan` to confirm parity with state.

## Adopted Resources

| Resource | Kind | Namespace | Pre-flight UID | Notes |
|----------|------|-----------|----------------|-------|
| …        | …    | …         | …              | …     |

## Skipped Resources

| Resource | Reason |
|----------|--------|
| `helm_release.ingress_nginx` | Left under Helm management — see Path B in skill docs |

## Merged Resources

| Source resources | Merged into | Why |
|------------------|-------------|-----|
| (typically none for native K8s) | | |
```

# Permissions Required

Two layers — both must be in place before adoption:

1. **Cluster RBAC for the KRO controller**: KRO must have read+write access to every Kind in the RGD. Common gaps: CRDs from third-party operators (Cert-Manager, Prometheus, Argo), cluster-scoped Roles, NetworkPolicies. Generate or update the KRO ClusterRole accordingly.
2. **kubectl context permissions for the operator**: must be able to run `kubectl annotate`, `kubectl label`, and `kubectl apply` in the target namespaces.

Document both in `kro/MIGRATION-NOTES.md` if any custom RBAC was needed.

# Helm Release Special Handling

When `helm_release` resources appear in tfstate, two ownership layers must be unwound — Terraform AND Helm. Both lay claim to the underlying K8s objects.

**The chart's rendered K8s objects are NOT in tfstate** — only the Helm release marker (a Secret in the release namespace) is. Helm itself owns those objects via:
- `metadata.labels."app.kubernetes.io/managed-by": Helm`
- `metadata.annotations."meta.helm.sh/release-name"`
- `metadata.annotations."meta.helm.sh/release-namespace"`
- A `Terraform` entry in `metadata.managedFields[]` (because TF wrote them via the Helm provider)

## Path A (recommended) — Adopt rendered objects into kro

1. Render the chart from cluster: `helm get manifest <release> -n <ns>`
2. Run those manifests through the same adoption logic as native K8s objects.
3. Run the standard MIGRATION-NOTES Step 1 (`terraform state rm <helm_release>`).
4. Detach Helm so it stops considering the release alive (Helm 3.13+ supports keep-resources):
   ```
   helm uninstall <release> -n <ns> --keep-resources
   ```
   This deletes ONLY the release marker Secret and label/annotation. The objects keep running.
5. Strip Helm and TF metadata from each rendered object — both labels AND `managedFields`:
   ```
   kubectl label    <kind>/<name> -n <ns> app.kubernetes.io/managed-by-
   kubectl annotate <kind>/<name> -n <ns> meta.helm.sh/release-name- meta.helm.sh/release-namespace-

   # Patch out BOTH 'Helm' and 'Terraform' (or 'helm-provider') entries from managedFields.
   # Inspect first:
   kubectl get <kind>/<name> -n <ns> -o jsonpath='{range .metadata.managedFields[*]}{.manager}{"\n"}{end}'
   # Then remove by index (highest first):
   kubectl patch <kind>/<name> -n <ns> --type=json -p='[{"op":"remove","path":"/metadata/managedFields/<index>"}]'
   ```
6. Continue with Step 2.5 (ownerReferences) and Step 3 (apply RGD) of the standard runbook.

The order is the same shape as native adoption — state-rm first, then ownership cleanup, then apply — but with Helm uninstall slotted in between TF state removal and SSA cleanup.

## Path B — Leave Helm in charge

Skip the chart's objects entirely. Only the helm_release HCL block is removed from TF state; Helm continues to own the release. Document the boundary in `MIGRATION-NOTES.md` so future operators know not to try to adopt these objects later.

## Path Selection

Document the choice per release in `MIGRATION-NOTES.md`. Path A is preferred when the team wants kro as the single source of truth; Path B is preferred when the chart is community-maintained and operators want `helm upgrade` workflows to keep working.

The same Helm handoff procedure is referenced from [[tf-to-kro]] (Mode A — chart rendering). Keep the two skills consistent.

# Key Rules to Follow

- `mode: "managed"` only — never adopt data sources (they're not yours to own).
- Source of truth for spec values: `instances[].attributes` from state. Don't invent values.
- Drop computed fields from attributes (`status`, `metadata[0].generation`, `metadata[0].resource_version`, `metadata[0].uid`).
- snake_case → camelCase when emitting K8s YAML.
- TF nested-block single-element arrays: unwrap when emitting.
- **`terraform state rm` BEFORE `kubectl apply` of the RGD instance.** Reversed order causes SSA conflicts.
- **Strip `metadata.managedFields[]` entries for Terraform/Helm** — labels alone do not transfer ownership.
- **Detach `ownerReferences` before adoption** unless you're certain instance deletion should cascade.
- DO NOT run `terraform destroy` as part of the migration. Always `terraform state rm`.
- DO NOT delete the live K8s objects between any of the migration steps.
- Verify UIDs match before and after — if they changed, kro recreated something and that's a regression.
- Add `readyWhen` for resources with established ready states.
- Two permission layers: kro ClusterRole + operator kubectl context.
- For helm_releases: pause CI / drift-detection on BOTH `terraform plan` and `helm diff` jobs during migration.

# kro Authoring Reference

For full RGD syntax, schema markers, CEL libraries, reserved keywords, forEach/includeWhen/readyWhen semantics, and field rules, see [[rgd-authoring-reference]] (`./rgd-authoring-reference.md`).
