---
name: tf-k8s-to-kro-rgd
description: Convert Terraform HCL that provisions native Kubernetes resources (kubernetes provider, kubectl provider, helm_release) into a kro ResourceGraphDefinition (RGD). Variables become the spec, K8s resources become RGD resources, and helm_release blocks are rendered to inline manifests.
allowed-tools: Read, Grep, Glob, Write
---

# kro Version Compatibility

This skill targets `kro.run/v1alpha1`. Generated RGDs include a header comment naming the version, and call out primitives whose syntax has varied between alphas (`forEach`, `externalRef`, `omit()`). See [[rgd-authoring-reference]] for current RGD field rules.

# Workflow

When this transformation is invoked:

1. **Discover Terraform files**: Scan the repository for `.tf` files. Identify variables, outputs, locals, data sources, and resources from the K8s-related providers:
   - `hashicorp/kubernetes` → `kubernetes_*_v1` (typed) and `kubernetes_manifest`
   - `gavinbunney/kubectl` or `alekc/kubectl` → `kubectl_manifest`
   - `hashicorp/helm` → `helm_release`
   - Any other provider — out of scope for this skill (use `terraform-to-kro-rgd` for AWS/ACK).

2. **Classify each resource**:
   - **Typed `kubernetes_*_v1`**: structured HCL → infer K8s GVK from the resource type.
   - **`kubernetes_manifest`**: passthrough — the `manifest` block IS the K8s object.
   - **`kubectl_manifest`**: YAML in a string — parse and emit.
   - **`helm_release`**: render the chart to manifests, then convert each rendered document like a `kubernetes_manifest`.

3. **Map structure**:
   - `variable` → RGD `spec` schema field.
   - `resource` → entry in RGD `resources`.
   - `output` → RGD `status` field via CEL.
   - `locals` → inline CEL or intermediate values.
   - `data` source → see "Referencing Existing Resources" below — kro lacks a stable `externalRef` keyword across alphas, so use one of the documented patterns.
   - `count` / `for_each` → `forEach` (verify syntax against installed kro version — see [[rgd-authoring-reference]]).
   - `depends_on` → drop, replaced by implicit deps via CEL refs.

4. **Generate the RGD**:
   - `kind`: PascalCase from the module/dir name.
   - `apiVersion: v1alpha1` at `spec.schema.apiVersion`. Do NOT emit `group:` at the schema level — kro derives the API group (`kro.run`) implicitly. See [[rgd-authoring-reference]].
   - Resource `id`: lowerCamelCase derived from the K8s `kind` (suffix on collisions: `deployment`, `serviceWeb`, `serviceCache`). Avoid reserved IDs (`schema`, `metadata`, `spec`, `status`, `each`, `instance`, `resources`, plus CEL macros: `has`, `all`, `exists`, `map`, `filter`).
   - Add `readyWhen` for `Deployment`, `StatefulSet`, `DaemonSet`, `Job`, `Ingress`, and CRDs with known ready conditions (recipes in [[rgd-authoring-reference]]).
   - Use `?` for optional status reads.
   - Drop provider-specific fields (`wait_for_rollout`, `wait_for_load_balancer`, `field_manager` block, etc.).
   - Prepend a header comment to the generated RGD:
     ```yaml
     # Generated for kro kro.run/v1alpha1 — verify forEach / externalRef / omit() syntax against your installed version
     ```

5. **Generate a matching instance** populated from variable defaults.

6. **Output**:
   - RGD → `kro/<module-name>-rgd.yaml`
   - Instance → `kro/<module-name>-instance.yaml`
   - `kro/README.md` summarizing decisions, helm_release rendering, and unsupported features.

# TF Kubernetes Provider → RGD Mapping

## Typed `kubernetes_*_v1` Resource → RGD Resource

```hcl
# Terraform
resource "kubernetes_deployment_v1" "web" {
  metadata {
    name      = var.app_name
    namespace = var.namespace
    labels = {
      app = var.app_name
    }
  }

  spec {
    replicas = var.replicas

    selector {
      match_labels = {
        app = var.app_name
      }
    }

    template {
      metadata {
        labels = {
          app = var.app_name
        }
      }
      spec {
        container {
          name  = "app"
          image = var.image
          port {
            container_port = 80
          }
        }
      }
    }
  }
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
      namespace: ${schema.spec.namespace}
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

Conversion rules for typed resources:

| HCL nesting (TF)   | YAML nesting (RGD)   |
|--------------------|----------------------|
| `metadata { … }`   | `metadata: { … }`    |
| `spec { … }`       | `spec: { … }`        |
| `container { … }`  | `containers: [{ … }]` (list — multiple `container` blocks become list entries) |
| `port { … }`       | `ports: [{ … }]`     |
| `match_labels`     | `matchLabels`        |
| `container_port`   | `containerPort`      |
| `image_pull_policy`| `imagePullPolicy`    |
| `env { name = …; value = … }` | `env: [{name: …, value: …}]` |

Rule of thumb: snake_case → camelCase, repeated HCL blocks → YAML lists.

Drop provider-only fields:
- `wait_for_rollout`, `wait_for_load_balancer`, `wait_for { … }`
- `field_manager { name = … force_conflicts = … }`
- `timeouts { … }`
- `computed_fields`

## `kubernetes_manifest` → RGD Resource (Passthrough)

```hcl
resource "kubernetes_manifest" "vpa" {
  manifest = {
    apiVersion = "autoscaling.k8s.io/v1"
    kind       = "VerticalPodAutoscaler"
    metadata = {
      name      = "${var.app_name}-vpa"
      namespace = var.namespace
    }
    spec = {
      targetRef = {
        apiVersion = "apps/v1"
        kind       = "Deployment"
        name       = var.app_name
      }
      updatePolicy = {
        updateMode = "Auto"
      }
    }
  }
}
```

```yaml
# RGD resource — direct passthrough
- id: vpa
  template:
    apiVersion: autoscaling.k8s.io/v1
    kind: VerticalPodAutoscaler
    metadata:
      name: ${schema.spec.appName}-vpa
      namespace: ${schema.spec.namespace}
    spec:
      targetRef:
        apiVersion: apps/v1
        kind: Deployment
        name: ${schema.spec.appName}
      updatePolicy:
        updateMode: Auto
```

## `kubectl_manifest` → RGD Resource

The `yaml_body` is already YAML — parse it, replace `${var.foo}` HCL interpolations with `${schema.spec.foo}` CEL, emit as a template.

```hcl
resource "kubectl_manifest" "monitor" {
  yaml_body = <<-YAML
    apiVersion: monitoring.coreos.com/v1
    kind: ServiceMonitor
    metadata:
      name: ${var.app_name}
    spec:
      selector:
        matchLabels:
          app: ${var.app_name}
  YAML
}
```

```yaml
- id: serviceMonitor
  template:
    apiVersion: monitoring.coreos.com/v1
    kind: ServiceMonitor
    metadata:
      name: ${schema.spec.appName}
      namespace: ${schema.metadata.namespace}
    spec:
      selector:
        matchLabels:
          app: ${schema.spec.appName}
```

## `helm_release` → Rendered Inline Manifests

Three handling modes — pick based on what's available:

### Mode A: Render via `helm template` (preferred)

If the chart is reachable (local path or you can `helm repo add` it), run an equivalent of:

```
helm template <release_name> <chart> \
  --namespace <namespace> \
  --version <version> \
  -f values.yaml
```

Each rendered document becomes one RGD resource. Values from the `helm_release.set { name = …; value = … }` blocks plus the `values = [...]` arg become a synthetic `values.yaml` for the render.

**If the chart is currently installed in a target cluster** (i.e., you're not just doing a code-conversion exercise, you're migrating live infrastructure), use the [[tfstate-to-kro]] Path A handoff procedure to detach Helm cleanly:

1. `terraform state rm <helm_release>` (so TF doesn't fight kro)
2. `helm uninstall <release> -n <ns> --keep-resources` (Helm 3.13+) — removes the release marker but keeps the live objects
3. Strip `app.kubernetes.io/managed-by: Helm` labels and `meta.helm.sh/*` annotations
4. Patch out `Helm` and `Terraform` entries from `metadata.managedFields[]` on every adopted object
5. Apply the kro RGD instance

This is the same handoff documented in [[tfstate-to-kro]] — keep the two skills aligned. Do NOT skip step 4 (managedFields) — labels alone don't transfer SSA ownership.

### Mode B: Lift values into spec, document chart binding

If you can't render at conversion time, emit a single RGD resource that carries the chart coordinates and values, plus a clear note that the user must apply the chart out-of-band (or via a Helm controller like Flux's HelmRelease CRD) and that KRO won't manage the underlying objects.

### Mode C: Delegate to `helm-to-kro-rgd`

If the chart is in-repo (path-style `chart = "./charts/foo"`), invoke the `helm-to-kro-rgd` skill on that chart and merge its output into this RGD as an inline group of resources. Note this in `kro/README.md`.

### `helm_release` HCL → RGD mapping

```hcl
resource "helm_release" "ingress_nginx" {
  name       = "ingress-nginx"
  namespace  = "ingress-nginx"
  repository = "https://kubernetes.github.io/ingress-nginx"
  chart      = "ingress-nginx"
  version    = "4.10.0"
  create_namespace = true

  values = [
    yamlencode({
      controller = {
        replicaCount = var.ingress_replicas
        service = {
          type = "LoadBalancer"
        }
      }
    })
  ]

  set {
    name  = "controller.metrics.enabled"
    value = "true"
  }
}
```

In Mode A this gets rendered to a Deployment, Service, ServiceAccount, ConfigMap, etc., each becoming an RGD resource with values like `replicaCount` parameterized to `${schema.spec.ingressReplicas}` based on the `var` references the user supplied.

In Mode B the HCL collapses to a single placeholder note in `kro/README.md`:

> The original module installed the `ingress-nginx` chart (v4.10.0). The RGD does not manage these resources. Install the chart out-of-band before applying the RGD instance.

# Variable / Output / Data Mapping

These follow the same rules as `terraform-to-kro-rgd`:

- `variable "x" { type = … default = … description = … }` → spec field with type, default, and description preserved.
- `output "y" { value = kubernetes_deployment_v1.web.status[0].available_replicas }` → `status: y: ${deployment.status.?availableReplicas.orValue(0)}`.
- `data "kubernetes_namespace_v1" "ns" { metadata { name = var.namespace } }` → see "Referencing Existing Resources" below. kro does not have a stable `externalRef` keyword across all alphas, so this conversion is version-dependent.
- `count` / `for_each` → `forEach` — VERIFY syntax against installed kro version. See [[rgd-authoring-reference]] for Form A vs Form B variants.

# Referencing Existing Resources (Data Sources)

When TF uses `data` sources to read existing K8s objects, you have three options depending on what your installed kro version supports:

**Option 1: SSA noop template** (works on all kro alphas)

Template the existing resource's metadata only. SSA will adopt it if present; the resource's user fields aren't asserted by the RGD, so the original owner keeps managing them.

```yaml
- id: existingNamespace
  template:
    apiVersion: v1
    kind: Namespace
    metadata:
      name: ${schema.spec.namespace}
```

Caveat: kro becomes a co-manager. Use this only when you're comfortable with shared ownership.

**Option 2: Inline literal** (works on all kro alphas)

If the data source reads a static value (cluster DNS suffix, well-known service name, etc.), inline the literal in the consuming template. Don't try to read from the cluster.

**Option 3: Native `externalRef` keyword** (verify against your kro version)

If your installed kro version exposes a documented `externalRef` / read-only resource keyword, prefer that. The exact syntax has varied between alphas — check release notes; do NOT assume it from older skill output.

Document the choice per data source in `kro/README.md`.

# GVK Lookup for Common `kubernetes_*_v1` Types

| Terraform resource type                       | apiVersion              | kind                         |
|-----------------------------------------------|-------------------------|------------------------------|
| `kubernetes_namespace_v1`                     | `v1`                    | `Namespace`                  |
| `kubernetes_config_map_v1`                    | `v1`                    | `ConfigMap`                  |
| `kubernetes_secret_v1`                        | `v1`                    | `Secret`                     |
| `kubernetes_service_v1`                       | `v1`                    | `Service`                    |
| `kubernetes_service_account_v1`               | `v1`                    | `ServiceAccount`             |
| `kubernetes_persistent_volume_v1`             | `v1`                    | `PersistentVolume`           |
| `kubernetes_persistent_volume_claim_v1`       | `v1`                    | `PersistentVolumeClaim`      |
| `kubernetes_pod_v1`                           | `v1`                    | `Pod`                        |
| `kubernetes_deployment_v1`                    | `apps/v1`               | `Deployment`                 |
| `kubernetes_stateful_set_v1`                  | `apps/v1`               | `StatefulSet`                |
| `kubernetes_daemon_set_v1`                    | `apps/v1`               | `DaemonSet`                  |
| `kubernetes_replica_set_v1`                   | `apps/v1`               | `ReplicaSet`                 |
| `kubernetes_job_v1`                           | `batch/v1`              | `Job`                        |
| `kubernetes_cron_job_v1`                      | `batch/v1`              | `CronJob`                    |
| `kubernetes_ingress_v1`                       | `networking.k8s.io/v1`  | `Ingress`                    |
| `kubernetes_network_policy_v1`                | `networking.k8s.io/v1`  | `NetworkPolicy`              |
| `kubernetes_role_v1`                          | `rbac.authorization.k8s.io/v1` | `Role`                |
| `kubernetes_role_binding_v1`                  | `rbac.authorization.k8s.io/v1` | `RoleBinding`         |
| `kubernetes_cluster_role_v1`                  | `rbac.authorization.k8s.io/v1` | `ClusterRole`         |
| `kubernetes_cluster_role_binding_v1`          | `rbac.authorization.k8s.io/v1` | `ClusterRoleBinding`  |
| `kubernetes_horizontal_pod_autoscaler_v2`     | `autoscaling/v2`        | `HorizontalPodAutoscaler`    |
| `kubernetes_pod_disruption_budget_v1`         | `policy/v1`             | `PodDisruptionBudget`        |
| `kubernetes_storage_class_v1`                 | `storage.k8s.io/v1`     | `StorageClass`               |
| `kubernetes_custom_resource_definition_v1`    | `apiextensions.k8s.io/v1` | `CustomResourceDefinition` |
| `kubernetes_validating_webhook_configuration_v1` | `admissionregistration.k8s.io/v1` | `ValidatingWebhookConfiguration` |
| `kubernetes_mutating_webhook_configuration_v1`   | `admissionregistration.k8s.io/v1` | `MutatingWebhookConfiguration`   |

For any `kubernetes_<x>_v1` not in this table, drop the `kubernetes_` prefix and `_v1` suffix, convert remaining underscores to PascalCase to derive the K8s `kind`, and consult upstream K8s API docs for the correct `apiVersion`.

# Key Rules to Follow

- Resource `id` is lowerCamelCase from K8s `kind`; suffix on collisions. Avoid reserved IDs — see [[rgd-authoring-reference]].
- Strip the `_v1`/`_v2` suffix from the TF type when deriving the kind.
- snake_case HCL fields → camelCase YAML fields.
- Repeated HCL blocks (`container { … } container { … }`) → YAML list entries.
- Drop provider-control fields: `wait_for_rollout`, `wait_for_load_balancer`, `field_manager`, `timeouts`, `computed_fields`.
- For `helm_release`, prefer Mode A (render). If the chart is currently deployed, use the live-cluster handoff procedure documented in [[tfstate-to-kro]] (state rm + `helm uninstall --keep-resources` + managedFields strip + apply).
- Use `${schema.metadata.namespace}` when the original used `var.namespace` AND the namespace is the deployment-target namespace; use `${schema.spec.namespace}` when the namespace is meaningfully a parameter (e.g., a target other than the instance's own namespace).
- `depends_on` becomes implicit via CEL refs — drop it.
- Add `readyWhen` for resources with established ready states (recipes in [[rgd-authoring-reference]]).
- Do NOT emit a `group:` field at `spec.schema` — kro derives the API group implicitly.
- For `forEach` and `externalRef`, emit a VERIFY comment in the generated RGD and call out the version dependency in `kro/README.md`.

# kro Authoring Reference

For full RGD syntax, schema markers, CEL libraries, reserved keywords, forEach/includeWhen/readyWhen semantics, and field rules, see [[rgd-authoring-reference]] (`./rgd-authoring-reference.md`).
