---
name: rgd-authoring-reference
description: Authoritative reference for authoring kro ResourceGraphDefinitions (RGDs). Covers RGD top-level structure, schema markers, CEL expressions and kro extensions, reserved keywords, forEach/includeWhen/readyWhen semantics, status synthesis, optional-read patterns, and version-compatibility notes. Loaded by helm-to-kro-rgd, kustomize-to-kro-rgd, tf-k8s-to-kro-rgd, and tfstate-k8s-to-kro-adoption.
---

# kro Version Compatibility

This reference targets **kro `kro.run/v1alpha1`** as of early 2026. Several primitives are still evolving — call them out as **VERIFY** when emitting generated YAML so operators know to check against their installed kro version.

To check the installed kro version:
```
kubectl get deployment -n kro kro-controller-manager -o jsonpath='{.spec.template.spec.containers[*].image}'
```

The following primitives have changed between alpha releases and should be reviewed against the installed version before assuming they work as documented here:

- `forEach` — iterator and loop-variable syntax
- `externalRef` / read-only resources — naming and semantics
- `includeWhen` — placement and expression scope
- `omit()` — requires the `CELOmitFunction` feature gate, not on by default
- Schema markers — quoting rules for default values

When emitting RGDs, prepend a header comment:
```yaml
# Generated for kro kro.run/v1alpha1 (verify forEach / externalRef / omit() syntax against your installed version)
```

# RGD Top-Level Structure

```yaml
apiVersion: kro.run/v1alpha1
kind: ResourceGraphDefinition
metadata:
  name: web-app             # cluster-scoped, lowercase, kebab-case
spec:
  schema:
    apiVersion: v1alpha1    # version of the GENERATED CRD
    kind: WebApp            # PascalCase; becomes the CRD kind operators apply
    spec:                   # user-facing input fields (typed)
      name: string | required=true
      replicas: integer | default=3 minimum=1
    status:                 # synthesized output fields (CEL)
      readyReplicas: ${deployment.status.?readyReplicas.orValue(0)}
  resources:
    - id: deployment        # lowerCamelCase; unique within the RGD
      readyWhen:
        - ${deployment.status.availableReplicas >= schema.spec.replicas}
      template:
        apiVersion: apps/v1
        kind: Deployment
        ...
```

Key facts:

- The RGD itself is **cluster-scoped**. The CRD it generates is namespaced by default (controlled by the schema kind).
- `spec.schema.apiVersion` is the version of the generated CRD (typically `v1alpha1`), not the kro API version.
- The generated CRD's API group defaults to `kro.run`. **Do not** add a `group:` field at `spec.schema` — kro versions have varied between accepting/ignoring/rejecting it. Let kro derive it.
- Resource `id` values are lowerCamelCase and must be unique. They become the variable name used in CEL refs (`${deployment.status…}`).

# Schema Marker Syntax

Spec fields use a single-line marker grammar:

```
<fieldName>: <type> | <marker1>=<value> <marker2>=<value> ...
```

## Types

| Type                   | Example                                 |
|------------------------|-----------------------------------------|
| `string`               | `name: string \| required=true`         |
| `integer`              | `replicas: integer \| default=3`        |
| `number`               | `cpuFraction: number \| default=0.5`    |
| `boolean`              | `enabled: boolean \| default=false`     |
| `[]string`             | `hosts: "[]string"`                     |
| `[]integer`            | `ports: "[]integer"`                    |
| `[]object`             | `rules: "[]object"` (free-form list)    |
| `map[string]string`    | `labels: "map[string]string"`           |
| nested object          | omit type, indent fields under the key  |

Quote the type when it contains brackets or special characters: `"[]string"`, `"map[string]string"`.

## Markers

| Marker             | Applies to       | Example                                    |
|--------------------|------------------|--------------------------------------------|
| `required=true`    | any              | `name: string \| required=true`            |
| `default=<v>`      | any              | `replicas: integer \| default=3`           |
| `description="…"`  | any              | `name: string \| description="App name"`   |
| `minimum=<n>`      | integer/number   | `replicas: integer \| minimum=1`           |
| `maximum=<n>`      | integer/number   | `replicas: integer \| maximum=100`         |
| `enum="a,b,c"`     | string           | `mode: string \| enum="dev,prod"`          |
| `minLength=<n>`    | string           | `name: string \| minLength=3`              |
| `maxLength=<n>`    | string           | `name: string \| maxLength=63`             |
| `pattern="<re>"`   | string           | `name: string \| pattern="^[a-z]+$"`       |

Default-value quoting:
- Numeric and boolean defaults: unquoted (`default=3`, `default=true`).
- String defaults: quoted (`default="IfNotPresent"`).
- Empty-string defaults: `default=""`.

## Nested Objects

```yaml
spec:
  image:
    repository: string | required=true
    tag: string | default="latest"
    pullPolicy: string | enum="Always,IfNotPresent,Never" default="IfNotPresent"
```

Reference as `${schema.spec.image.repository}` etc.

# CEL Expressions

CEL expressions appear inside `${ ... }` in any template field, status field, `readyWhen`, or `includeWhen`.

## Available Variables

| Variable            | Meaning                                                       |
|---------------------|---------------------------------------------------------------|
| `schema.spec.<f>`   | A field from the user-provided spec                           |
| `schema.metadata.<f>` | The instance's metadata (name, namespace, labels, annotations) |
| `<resourceId>.<f>`  | A field from a previously-defined resource (live cluster state) |
| `each.<f>` (in forEach) | Loop variable for `forEach` iterations (verify name with installed version) |

Resource refs read live cluster state. `${deployment.status.readyReplicas}` waits for the Deployment to report that field, then proceeds.

## Optional Reads

Status fields don't always exist immediately. Use the `?` operator and `.orValue()`:

```yaml
status:
  readyReplicas: ${deployment.status.?readyReplicas.orValue(0)}
  endpoint: ${service.status.?loadBalancer.?ingress[0].?hostname.orValue("")}
```

Without `?`, the RGD blocks reconciliation until the field appears, which can stall instances indefinitely.

## String Construction

Prefer concatenation over mixed text-and-CEL interpolation in synthesized fields:

```yaml
# RECOMMENDED for status.* synthesis:
endpoint: ${service.spec.clusterIP + ":" + string(service.spec.ports[0].port)}

# OK in template.* paths (where the surrounding YAML is a literal string):
image: "${schema.spec.image.repository}:${schema.spec.image.tag}"
```

## Common CEL Functions

| Function           | Purpose                                                        |
|--------------------|----------------------------------------------------------------|
| `string(x)`        | Coerce to string                                               |
| `int(x)`           | Coerce to integer                                              |
| `size(x)`          | Length of string/list/map                                      |
| `has(x.y)`         | Check optional field presence                                  |
| `x.orValue(y)`     | Default for optional read                                      |
| `x.map(i, expr)`   | Transform list                                                 |
| `x.filter(i, expr)`| Filter list                                                    |
| `x.all(i, expr)`   | All elements satisfy expr                                      |
| `x.exists(i, expr)`| Any element satisfies expr                                     |
| `omit()`           | Drop a field from the rendered template (requires feature gate)|

# `omit()` — Feature Gate Required

`omit()` removes a field entirely from the rendered template. It requires `--feature-gates=CELOmitFunction=true` on the kro controller, which is **not on by default**.

When emitting `omit()` in generated RGDs:

1. Add a comment near the use site:
   ```yaml
   # Requires kro --feature-gates=CELOmitFunction=true (verify with cluster operator)
   tls: ${schema.spec.tls.enabled ? [{"secretName": schema.spec.tls.secretName}] : omit()}
   ```
2. Note this requirement in `kro/README.md`.
3. If the field is at the top of a resource (whole-resource gating), prefer `includeWhen` over `omit()` — no feature gate needed.

# `includeWhen` — Whole-Resource Conditional

```yaml
- id: ingress
  includeWhen:
    - ${schema.spec.ingress.enabled}
  template: ...
```

The resource is created only when the expression evaluates to `true`. If it transitions to `false` after creation, kro deletes the resource (cascading per `ownerReferences`).

`includeWhen` is **per resource**, not per field. For per-field conditionals, use CEL ternary in the template field, optionally with `omit()`.

# `readyWhen` — Reconciliation Gate

```yaml
- id: deployment
  readyWhen:
    - ${deployment.status.availableReplicas >= schema.spec.replicas}
    - ${deployment.status.observedGeneration == deployment.metadata.generation}
  template: ...
```

All `readyWhen` expressions must be true before kro considers the resource "ready" and proceeds to dependents. Add `readyWhen` for any resource type with a meaningful ready condition:

| Kind           | Ready condition                                                              |
|----------------|------------------------------------------------------------------------------|
| `Deployment`   | `status.availableReplicas >= spec.replicas`                                  |
| `StatefulSet`  | `status.readyReplicas >= spec.replicas`                                      |
| `DaemonSet`    | `status.numberReady >= status.desiredNumberScheduled`                        |
| `Job`          | `status.succeeded >= 1`                                                      |
| `Ingress`      | `size(status.loadBalancer.?ingress.orValue([])) > 0`                         |
| `Service` (LB) | `size(status.loadBalancer.?ingress.orValue([])) > 0`                         |
| `PVC`          | `status.phase == "Bound"`                                                    |

For CRDs with custom ready conditions (Cert-Manager `Certificate`, Argo `Rollout`, etc.), inspect `status.conditions[?(@.type=='Ready')].status == "True"` patterns.

# `forEach` — Resource Iteration

**VERIFY against your installed kro version.** The exact syntax has been in flux. Check the kro release you're targeting; the patterns below show two forms — pick the one your version accepts.

## Form A (older alphas)

```yaml
- id: ingressRules
  forEach:
    - host: ${schema.spec.ingress.hosts}
  template:
    apiVersion: networking.k8s.io/v1
    kind: Ingress
    metadata:
      name: ${schema.metadata.name}-${host.name}
    spec: ...
```

## Form B (newer alphas)

```yaml
- id: ingressRules
  forEach: ${schema.spec.ingress.hosts}
  template:
    apiVersion: networking.k8s.io/v1
    kind: Ingress
    metadata:
      name: ${schema.metadata.name}-${each.name}
    spec: ...
```

When emitting `forEach`, include a comment:
```yaml
# VERIFY: forEach syntax varies between kro versions — confirm 'each' vs custom name binding works in your installation
```

For lists transformed within a single resource, prefer CEL `map()` instead — no version risk:

```yaml
rules: ${schema.spec.ingress.hosts.map(h, {"host": h.host, "http": {"paths": [{"path": h.path}]}})}
```

# Referencing Existing (Externally-Managed) Resources

kro currently lacks a stable `externalRef` primitive. There are two patterns for referencing objects that exist in the cluster but are not owned by the RGD:

## Pattern 1: Read-only via resource template + SSA noop

Template the existing resource's metadata only and rely on Server-Side Apply to no-op when fields match. Use it for read-side references where you need to read fields:

```yaml
- id: existingNamespace
  template:
    apiVersion: v1
    kind: Namespace
    metadata:
      name: ${schema.spec.namespace}
```

This will create the Namespace if absent and adopt it (via SSA) if present. **Caveat**: the RGD becomes a co-manager, which may not be what you want for resources owned by other controllers.

## Pattern 2: Inline literal data, do not reference

If you only need a static value (e.g., a known cluster DNS suffix), inline the literal in the template instead of trying to read it from the cluster.

If your installed kro version exposes a documented `externalRef` keyword, prefer that — but verify the syntax against the version's release notes; do not assume it from older skill output.

# Reserved Resource IDs and CEL Bindings

Do not use these as resource `id` values — they collide with kro bindings or CEL macros:

| Reserved        | Reason                                       |
|-----------------|----------------------------------------------|
| `schema`        | Bound to the instance's spec/metadata        |
| `metadata`      | Bound inside `schema`                        |
| `spec`          | Bound inside `schema`                        |
| `status`        | Bound inside `schema`                        |
| `each`          | forEach loop variable (in some versions)     |
| `instance`      | Reserved in some versions                    |
| `resources`     | Top-level RGD field                          |
| `has`           | CEL macro                                    |
| `all`           | CEL macro                                    |
| `exists`        | CEL macro                                    |
| `map`           | CEL macro                                    |
| `filter`        | CEL macro                                    |
| `size`          | CEL function                                 |
| `string` / `int` / `bool` | CEL functions                      |

When auto-generating IDs from kinds (`deployment`, `service`, ...), check against this list and suffix on collision.

# Status Synthesis

Status fields are populated from observed resource fields via CEL:

```yaml
status:
  readyReplicas: ${deployment.status.?readyReplicas.orValue(0)}
  endpoint: ${service.spec.clusterIP + ":" + string(service.spec.ports[0].port)}
  conditions: ${deployment.status.?conditions.orValue([])}
```

Rules:
- Always use `?` and `.orValue(...)` for fields that may not exist at first reconcile.
- Prefer string concatenation (`a + b`) over mixed-text interpolation (`"${a}${b}"`) for synthesized status — concat is consistent across kro versions; mixed-text has had bugs in `status.*` paths.
- Use `string(x)` to coerce numeric ports/IDs before concatenation.
- Don't reference `schema.spec.*` in status — it's a no-op (status would just echo input). Reference resource state.

# Type Inference Rules (for converters)

When converting from typed source (Helm `values.yaml`, Terraform variables, Kustomize fields):

| Source pattern                  | RGD type           | Notes                                              |
|---------------------------------|--------------------|----------------------------------------------------|
| Numeric default                 | `integer` or `number` | `int` if integer literal, `number` if float    |
| Boolean default                 | `boolean`          |                                                    |
| String default                  | `string`           | Quote default value                                |
| Empty list                      | `[]object`         | **Not** `[]string` — empty lists in chart values almost always want object items. Add a TODO comment so the user types it explicitly. |
| Non-empty homogeneous list      | `[]<element-type>` |                                                    |
| Empty map                       | `map[string]string`| Add TODO; user may need `map[string]<otherType>`   |
| Nested map (homogeneous)        | nested object      |                                                    |

Always emit a `# TODO:` YAML comment for any empty list/map encountered, plus a `kro/README.md` line listing the field for user review.

# Common Pitfalls and Anti-Patterns

1. **Forgetting `?` on optional reads** → instances stuck "not ready" forever waiting for a status field that doesn't exist yet.
2. **Using `omit()` without the feature gate** → CEL evaluation errors at apply time with no clear pointer at the gate.
3. **Manufacturing spec fields for constants** → if a value never changes across instances, inline it. Don't pollute the spec.
4. **Using reserved IDs** → silent expression-resolution failures.
5. **`metadata.namespace` left literal** → cluster-scoped surprise. Always parameterize via `${schema.metadata.namespace}` for namespaced resources.
6. **Owner-reference cascade on rollback** → deleting the instance deletes everything kro created. For adopted resources, strip `ownerReferences` before any rollback can be safely attempted.
7. **Dropping ConfigMap content-hash naming** → consumers no longer rolling-restart on config change. Replace with a content-hash annotation on the consuming PodTemplate.

# Quick Validation Checklist

Before declaring a generated RGD ready:

- [ ] `kubectl apply --dry-run=server -f rgd.yaml` succeeds
- [ ] Generated CRD appears: `kubectl get crd <kind>.kro.run`
- [ ] Apply an instance with all defaults — reconciles to Ready
- [ ] All `readyWhen` expressions evaluate (not stuck on optional fields)
- [ ] All resource IDs are unique and not reserved
- [ ] Cluster-scoped resources don't reference `${schema.metadata.namespace}`
- [ ] Namespaced resources DO set `metadata.namespace`
- [ ] No `omit()` use without feature-gate documentation
- [ ] Header comment names the kro version targeted
