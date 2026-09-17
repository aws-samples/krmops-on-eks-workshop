# KRO RGD Reference (Shared_KRO_Layer)

> **Single authoritative KRO RGD reference.** Consolidated, reconciled guidance for
> building kro `ResourceGraphDefinition`s. Used by both Adopt_Path and Create_Path
> (Shared_KRO_Layer). Supersedes the older `schema.md`, `cel-expressions.md`, and
> `resource-definitions.md` (now stubs redirecting here).
>
> Decided positions are marked **[DECIDED]**. Mode-specific guidance lives in
> `kro/adoption/` and `kro/creation/` (they link here, never copy).
>
> Authority_Doc: `.kiro/steering/history/krmops-kro-open-questions.md`

---

## KRO Version Compatibility [DECIDED — version-adaptable]

**Decision (Authority_Doc #7):** be **version-adaptable**. Read the installed kro
version, adjust the version-dependent primitives the tool emits, and document the
version each output targets.

This reference targets **kro `kro.run/v1alpha1`** (docs pinned at **0.9.2** as of
early 2026). Several primitives are still evolving and have changed between alpha
releases; treat them as version-dependent and emit a **VERIFY** note when generating
YAML that uses them:

- `forEach` — iterator and loop-variable binding syntax
- `omit()` — requires the `CELOmitFunction` feature gate (off by default)
- Schema markers — quoting rules for default values

### Read the installed kro version

The documented detection command (run before generating version-dependent output):

```bash
kubectl get deployment -n kro kro-controller-manager \
  -o jsonpath='{.spec.template.spec.containers[*].image}'
```

Use the result to pick the right `forEach` form for the target cluster instead of
guessing, and to decide whether feature-gate-dependent functions (`omit()`) are safe
to emit.

### Required header comment on every generated RGD [DECIDED — Req 11.1]

Every generated RGD MUST carry a header comment naming the targeted kro version, and
MUST add a verification note where the emitted primitive's syntax varies between
versions (Req 11.4):

```yaml
# Generated for kro kro.run/v1alpha1 (target controller image: kro 0.9.2)
# VERIFY against your installed kro version: forEach binding form, omit() feature gate.
# Detect with: kubectl get deployment -n kro kro-controller-manager \
#   -o jsonpath='{.spec.template.spec.containers[*].image}'
```

---

## RGD Top-Level Structure

> ⚠️ **CRITICAL STRUCTURE RULE:** The RGD `spec` has exactly **two** top-level fields: `schema` and `resources`. Nothing else.
> - Status output expressions go inside `spec.schema.status` — NOT at `spec.status` (which does not exist).
> - Schema spec fields use **SimpleSchema string syntax** (see below) — NOT nested objects with `type`/`required`/`description` properties.

```yaml
apiVersion: kro.run/v1alpha1
kind: ResourceGraphDefinition
metadata:
  name: web-app             # cluster-scoped, lowercase, kebab-case
spec:
  schema:
    apiVersion: v1alpha1    # version of the GENERATED CRD (not the kro API version)
    kind: WebApp            # PascalCase; becomes the CRD kind operators apply
    # group: mycompany.io   # OPTIONAL — defaults to kro.run when omitted (see below)
    # scope: Namespaced     # or Cluster (default: Namespaced)
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
        # ...
```

Key facts:

- The RGD itself is **cluster-scoped**. The CRD it generates is **namespaced by
  default** (controlled by `schema.scope`).
- `spec.schema.apiVersion` is the version of the **generated CRD** (typically
  `v1alpha1`), not the kro API version.
- Resource `id` values are lowerCamelCase and must be unique. The `id` becomes the
  variable name used in CEL refs (`${deployment.status…}`).

### `group:` on the schema [DECIDED — allow it; defaults to `kro.run`]

**Decision (Authority_Doc #2, Req 8.2):** `group:` is an **optional** schema field.
When it is **not** provided, kro defaults the generated CRD's API group to
**`kro.run`**. Platform teams MAY set it to brand their generated APIs under their own
group (e.g. `mycompany.io`).

```yaml
spec:
  schema:
    apiVersion: v1alpha1
    kind: WebApp
    group: platform.mycompany.io   # optional; omit to default to kro.run
```

> Reconciliation note: an older draft of this guidance said "do not add a `group:`
> field." That is **superseded**. The official schema docs list `group:` as a
> supported optional field, and the project decision is to allow it.
> Reference: https://kro.run/docs/concepts/rgd/schema/

---

## Schema Marker Syntax (SimpleSchema)

> ⚠️ **MANDATORY FORMAT:** Schema spec fields MUST use SimpleSchema **string syntax**. Do NOT use nested objects with `type`, `required`, `description` properties — that format is rejected by the KRO CRD.
>
> ```yaml
> # ✅ CORRECT — SimpleSchema strings
> spec:
>   appName: "string | required=true"
>   replicas: "integer | default=3"
>   namespace: "string | default=default"
>
> # ❌ WRONG — nested objects (not valid KRO schema)
> spec:
>   appName:
>     type: string
>     required: true
>     description: "Application name"
> ```

Spec fields use a single-line marker grammar:

```
<fieldName>: <type> | <marker1>=<value> <marker2>=<value> ...
```

### Types

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
| custom type            | defined under `schema.types:` (below)   |

Quote the type when it contains brackets or special characters: `"[]string"`,
`"map[string]string"`.

### Markers

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

### Nested Objects

```yaml
spec:
  image:
    repository: string | required=true
    tag: string | default="latest"
    pullPolicy: string | enum="Always,IfNotPresent,Never" default="IfNotPresent"
```

Reference as `${schema.spec.image.repository}`, etc.

### Custom Types

```yaml
schema:
  types:
    ContainerConfig:
      image: string | required=true
      tag: string | default="latest"
      env: "map[string]string"
  spec:
    primary: ContainerConfig
    sidecars: "[]ContainerConfig"
```

### Scope

- `Namespaced` (default) — instances exist within a namespace.
- `Cluster` — instances are cluster-wide; every namespaced resource must explicitly
  set `metadata.namespace`.

### Additional Printer Columns

```yaml
schema:
  additionalPrinterColumns:
    - name: Replicas
      type: integer
      jsonPath: .spec.replicas
    - name: Available
      type: integer
      jsonPath: .status.availableReplicas
```

---

## CEL Expressions

CEL expressions appear inside `${ ... }` in any template field, status field,
`readyWhen`, or `includeWhen`.

### Available variables

| Variable               | Meaning                                                          |
|------------------------|------------------------------------------------------------------|
| `schema.spec.<f>`      | A field from the user-provided spec                              |
| `schema.metadata.<f>`  | The instance's metadata (name, namespace, labels, annotations)   |
| `<resourceId>.<f>`     | A field from a previously-defined resource (live cluster state)  |
| `each.<f>` (in forEach)| Loop variable for `forEach` iterations (binding varies by version) |

Referencing a resource by its `id` reads live cluster state **and automatically
creates a dependency** — you never declare ordering by hand.

### Two forms of expression

**Standalone** — the entire field value is one expression; the result type must match
the field's expected type:

```yaml
replicas: ${schema.spec.count}          # integer
image: ${schema.spec.containerImage}    # string
env: ${configmap.data}                  # object
```

**String templates** — expressions embedded in surrounding literal text.

### String interpolation [DECIDED — mixed-text allowed; wrap non-strings in `string()`]

**Decision (Authority_Doc #4, Req 8.4):** use **mixed-text CEL string interpolation**.
Every expression embedded in a string MUST return a string — wrap non-string values in
`string()`. Prefer the `.format()` helper for ARNs and other structured strings.

```yaml
# mixed-text interpolation — non-string values wrapped in string()
connectionString: "host=${database.status.endpoint}:${string(database.status.port)}"
image: "${schema.spec.image.repository}:${schema.spec.image.tag}"

# ARNs — prefer .format()
roleArn: ${"arn:aws:iam::%s:role/%s".format([schema.spec.accountId, schema.spec.roleName])}
```

This applies in **both** `template.*` and `status.*` paths.

> Reconciliation note: an older draft recommended preferring string concatenation
> (`a + ":" + b`) over mixed-text interpolation, claiming mixed-text had bugs in
> `status.*`. That caution is **superseded**. The official CEL docs use mixed-text
> interpolation throughout (including status aggregation), and the single rule that
> matters is: every embedded expression must return a string. Concatenation still
> works and is fine, but mixed-text is the documented, decided default.
> Reference: https://kro.run/docs/concepts/rgd/cel-expressions/

### Optional reads (`?` and `.orValue()`)

Status fields don't always exist immediately. Use the `?` operator with `.orValue()`:

```yaml
status:
  readyReplicas: ${deployment.status.?readyReplicas.orValue(0)}
  endpoint: ${service.status.?loadBalancer.?ingress[0].?hostname.orValue("")}
  logLevel: ${config.data.?LOG_LEVEL.orValue("info")}
```

Without `?`, the RGD blocks reconciliation until the field appears, which can stall an
instance indefinitely. Caveat: `?` prevents build-time validation of field existence.

### Common CEL functions

| Function            | Purpose                                                         |
|---------------------|-----------------------------------------------------------------|
| `string(x)`         | Coerce to string                                                |
| `int(x)`            | Coerce to integer                                               |
| `size(x)`           | Length of string/list/map                                       |
| `has(x.y)`          | Check optional field presence                                   |
| `x.orValue(y)`      | Default for an optional read                                    |
| `x.map(i, expr)`    | Transform a list                                                |
| `x.filter(i, expr)` | Filter a list                                                   |
| `x.all(i, expr)`    | All elements satisfy expr                                       |
| `x.exists(i, expr)` | Any element satisfies expr                                      |
| `x.format([...])`   | printf-style string formatting (preferred for ARNs)            |
| `omit()`            | Drop a field from the rendered template (requires feature gate) |

### Other common operations

```yaml
# Conditionals (ternary)
image: ${schema.spec.env == "prod" ? "nginx:stable" : "nginx:latest"}

# Object construction
labels: ${{"app": schema.spec.name, "env": schema.spec.environment}}

# List operations
names: ${deployment.spec.template.spec.containers.map(c, c.name)}
ready: ${items.filter(c, c.status == "True")}
allOn: ${schema.spec.services.all(s, s.enabled)}

# Type conversion
replicaStr: ${string(schema.spec.replicas)}
countInt: ${int(schema.spec.count)}
```

To emit a literal `${VAR}` (e.g. for a shell command), escape with `${"${VAR}"}`.

---

## `readyWhen` — Reconciliation Gate

> ⚠️ **CRITICAL FORMAT RULE:** `readyWhen` is an **array of CEL expression strings** — NOT objects with `key`/`value` fields.
> ```yaml
> # ✅ CORRECT — array of CEL strings
> readyWhen:
>   - "${myResource.status.?ackResourceMetadata.arn.orValue(\"\") != \"\"}"
>
> # ❌ WRONG — do NOT use object format
> readyWhen:
>   - key: status.ackResourceMetadata.arn
>     value: "*"
> ```

All `readyWhen` expressions must evaluate `true` before kro considers a resource
"ready" and proceeds to its dependents.

```yaml
- id: deployment
  readyWhen:
    - ${deployment.status.availableReplicas >= schema.spec.replicas}
    - ${deployment.status.observedGeneration == deployment.metadata.generation}
  template: # ...
```

Rules:

- A `readyWhen` expression can only reference the resource itself (by its `id`).
- It must return a boolean.
- Without `readyWhen`, a resource is "ready" as soon as it is created and its CEL refs
  resolve.
- For collections, use the `each` keyword for per-item readiness.

Common ready conditions:

| Kind           | Ready condition                                                  |
|----------------|------------------------------------------------------------------|
| `Deployment`   | `status.availableReplicas >= spec.replicas`                      |
| `StatefulSet`  | `status.readyReplicas >= spec.replicas`                          |
| `DaemonSet`    | `status.numberReady >= status.desiredNumberScheduled`            |
| `Job`          | `status.succeeded >= 1`                                          |
| `Ingress`      | `size(status.loadBalancer.?ingress.orValue([])) > 0`            |
| `Service` (LB) | `size(status.loadBalancer.?ingress.orValue([])) > 0`            |
| `PVC`          | `status.phase == "Bound"`                                        |
| ACK resources  | `${myResource.status.?ackResourceMetadata.arn != ""}`           |

For CRDs with custom ready conditions (Cert-Manager `Certificate`, Argo `Rollout`),
inspect a `status.conditions.exists(c, c.type == "Ready" && c.status == "True")`
pattern.

---

## Conditional Creation [DECIDED — prefer `includeWhen` over `omit()`]

**Decision (Authority_Doc #6, Req 8.6):** prefer `includeWhen` for whole-resource
conditionals (no feature gate needed). Avoid `omit()` by default. Only emit `omit()`
when a **field-level** conditional truly requires it — and when you do, emit an inline
comment plus a note that the `CELOmitFunction` feature gate must be enabled.

### `includeWhen` — whole-resource conditional (preferred)

```yaml
- id: ingress
  includeWhen:
    - ${schema.spec.ingress.enabled}
  template: # ...
```

Rules:

- The resource is created only when **all** `includeWhen` expressions are `true` (AND).
  For OR, combine in a single expression: `${a || b}`.
- It is per **resource**, not per field.
- Re-evaluated on reconciliation: if the condition flips to `false` after creation, kro
  prunes the resource (cascading per `ownerReferences`). If a resource is skipped, all
  of its dependents are skipped too.

> ⚠️ Avoid referencing volatile upstream `.status` fields in `includeWhen` (the
> resource will flip-flop create/delete). Gate on a user-controlled `schema.spec.*`
> toggle instead, and use `readyWhen` on the upstream resource for sequencing.

```yaml
# RISKY — status fields are volatile
includeWhen:
  - ${deployment.status.availableReplicas > 0}

# SAFE — user-controlled toggle
includeWhen:
  - ${schema.spec.monitoring.enabled}
```

### `omit()` — field-level conditional (feature-gated, last resort)

`omit()` removes a single field entirely from the rendered template. It requires
`--feature-gates=CELOmitFunction=true` on the kro controller, which is **not on by
default**. Without the gate it fails at admission with an error that does not point at
the cause.

When `omit()` is genuinely required, emit the inline gate note:

```yaml
# Requires kro --feature-gates=CELOmitFunction=true (verify with cluster operator)
tls: ${schema.spec.tls.enabled ? [{"secretName": schema.spec.tls.secretName}] : omit()}
```

Also record the feature-gate requirement in `kro/README.md`. If the conditional is at
the level of a whole resource, use `includeWhen` instead — no feature gate needed.

---

## `forEach` — Collections (Resource Iteration) [DECIDED — stable in v0.9.2]

**Decision (Authority_Doc #5, Req 8.5):** Use `forEach` with **named iterator
variables** to create multiple resources from a single definition. For in-place list
transforms **within a single resource**, prefer CEL `map()` — it avoids creating
extra top-level resources. Reserve `forEach` for cases that genuinely produce multiple
top-level resources.

> **Stable since kro 0.9.x:** the `forEach` syntax uses an array of single-entry maps
> binding a named variable to a CEL expression that evaluates to an array. The older
> `each` binding approach is superseded.

### Prefer CEL `map()` for in-place list transforms

No version risk — use this whenever the result stays inside one resource:

```yaml
rules: ${schema.spec.ingress.hosts.map(h, {"host": h.host, "http": {"paths": [{"path": h.path}]}})}
```

### `forEach` syntax (v0.9.2+)

```yaml
- id: workerPods
  forEach:
    - worker: ${schema.spec.workers}   # named iterator variable
  readyWhen:
    - ${each.status.phase == 'Running'}  # 'each' for per-item readyWhen
  template:
    apiVersion: v1
    kind: Pod
    metadata:
      name: ${schema.metadata.name + '-' + worker}  # use iterator var in name
    spec:
      containers:
        - name: app
          image: ${schema.spec.image}
```

**Key rules:**

1. **Each entry is a single-entry map**: `- varName: ${expression}` where expression
   evaluates to an array.
2. **Iterator variable** is available in `template` expressions (e.g., `worker` above).
3. **`each`** is used ONLY inside `readyWhen` for per-item checks.
4. **Resource names must include the iterator variable** for uniqueness (and include
   `schema.metadata.name` to avoid cross-instance collisions).
5. **Multiple iterators = cartesian product**: `regions × tiers` creates N×M resources.
6. **Index access**: use `lists.range(size(array))` as the iterator to get positional:
   ```yaml
   forEach:
     - idx: ${lists.range(size(schema.spec.subnets))}
   template:
     spec:
       cidrBlock: ${schema.spec.subnets[idx]}
   ```

### Collection limits

- **Max 1000 resources** per collection (configurable via `--rgd-max-collection-size`).
- **Max 10 forEach dimensions** per resource.
- **Empty collections are valid** and considered ready (0 items = ready).

### `includeWhen` is collection-wide

`includeWhen` applies to the **entire collection** — if it evaluates to false, zero
resources are created. To filter individual items, use `filter()` in the forEach
expression:

```yaml
# Per-item filtering — only databases with backups enabled
- id: backupJobs
  forEach:
    - dbSpec: ${schema.spec.databases.filter(d, d.backupEnabled)}
  template: ...
```

### Referencing a collection from other resources

A collection exposes all created resources as an array. Other resources can reference
it using CEL list functions:

```yaml
- id: summary
  template:
    kind: ConfigMap
    data:
      podNames: ${workerPods.map(p, p.metadata.name).join(',')}
      count: ${string(size(workerPods))}
```

### One collection iterating over another

```yaml
- id: databases
  forEach:
    - dbSpec: ${schema.spec.databases}
  template: ...

- id: backupJobs
  forEach:
    - db: ${databases}  # iterate over the databases collection
  template:
    metadata:
      name: ${schema.metadata.name + '-backup-' + db.metadata.name}
    spec:
      env:
        - name: DB_HOST
          value: ${db.status.endpoint}
```

kro waits for ALL items in the referenced collection to be ready before proceeding.

---

## Referencing Existing Resources with `externalRef` [DECIDED — supported primitive]

**Decision (Authority_Doc #3, Req 8.3):** treat `externalRef` as a **supported
primitive** for read-only references to resources that exist in the cluster but are not
owned by the RGD — including the **label-selector (collection)** form. This is the
clean way to wire Class A (Terraform-managed) resources into KRO-managed graphs without
taking ownership of them.

### Single external reference (scalar)

```yaml
- id: sharedConfig
  externalRef:
    apiVersion: v1
    kind: ConfigMap
    metadata:
      name: platform-config
      namespace: platform-system
```

Key behaviors:

- kro **reads but never creates/updates/deletes** the external resource.
- The resource must exist for reconciliation to succeed (kro waits for it).
- It participates in the dependency graph like a managed resource.
- Use the `?` operator with `.orValue()` when accessing fields whose structure is
  uncertain (ConfigMap `data`, Secret `data`, CRD status fields).
- **Reactive watches**: kro watches external resources via informers and re-reconciles
  automatically when they change — no polling needed.
- **Namespace defaults**: if namespace is omitted on a scalar ref, kro looks for the
  resource in the instance's namespace.

### Collection external refs (by label selector)

```yaml
- id: teamConfigs
  externalRef:
    apiVersion: v1
    kind: ConfigMap
    metadata:
      selector:
        matchLabels:
          team: platform
```

- Exposed as an **array** — use CEL list functions (`map`, `filter`, `size`, `sortBy`).
- **Namespace defaults**: if namespace is omitted on a collection ref, kro lists
  resources **across all namespaces**.
- `name` and `selector` are mutually exclusive.
- **Reactive**: watches detect new/removed/changed resources matching the selector.

### CEL expressions in selectors (per-instance filtering)

`matchExpressions` values can contain CEL `${...}` syntax for dynamic filtering:

```yaml
- id: teamConfigs
  externalRef:
    apiVersion: v1
    kind: ConfigMap
    metadata:
      selector:
        matchExpressions:
          - key: team
            operator: In
            values: ["${schema.spec.teamName}"]
```

Each instance resolves the CEL expression with its own spec values.

### Working with collections

```yaml
# Count
configCount: ${string(size(teamConfigs))}

# Extract names
names: ${teamConfigs.map(c, c.metadata.name).join(",")}

# Filter
critical: ${teamConfigs.filter(c, c.data.?priority.orValue("") == "critical")}

# Sort by a field (deterministic ordering)
sorted: ${teamConfigs.sortBy(c, c.metadata.name)}
```

### Constraints

- A resource **cannot use both** `forEach` and `externalRef` — they are mutually
  exclusive. However, external refs with `selector` act as collections on their own.
- An empty selector (`selector: {}`) matches ALL resources of that kind — use
  specific labels to keep results bounded.

> Reference: https://kro.run/docs/concepts/rgd/resource-definitions/external-references/

---

## Dependency Graph

kro automatically infers dependencies from CEL expressions — you never declare order.

- `${configmap.data.KEY}` referenced in a Deployment → the Deployment depends on the
  ConfigMap.
- kro builds a DAG and computes topological order. Creation follows topological order;
  deletion follows reverse order.
- Circular dependencies are rejected at RGD creation time.

View the computed order:

```bash
kubectl get rgd my-app -o jsonpath='{.status.topologicalOrder}'
```

Common shapes: linear chain (`configmap → deployment → service`), diamond
(`config → (deployment + database) → gateway`), and independent parallel branches.

---

## Reserved Resource IDs and CEL Bindings

Do not use these as resource `id` values — they collide with kro bindings or CEL
macros. When auto-generating IDs from kinds (`deployment`, `service`, …), check against
this list and suffix on collision.

| Reserved                  | Reason                                   |
|---------------------------|------------------------------------------|
| `schema`                  | Bound to the instance's spec/metadata    |
| `metadata`                | Bound inside `schema`                    |
| `spec`                    | Bound inside `schema`                    |
| `status`                  | Bound inside `schema`                    |
| `each`                    | forEach loop variable (in some versions) |
| `instance`                | Reserved in some versions                |
| `resources`               | Top-level RGD field                      |
| `has`                     | CEL macro                                |
| `all`                     | CEL macro                                |
| `exists`                  | CEL macro                                |
| `map`                     | CEL macro                                |
| `filter`                  | CEL macro                                |
| `size`                    | CEL function                             |
| `string` / `int` / `bool` | CEL functions                            |

---

## Status Synthesis

Status fields are populated from observed resource fields via CEL:

```yaml
status:
  readyReplicas: ${deployment.status.?readyReplicas.orValue(0)}
  endpoint: ${service.status.?loadBalancer.?ingress[0].?hostname.orValue("")}
  bucketArn: ${bucket.status.?ackResourceMetadata.arn.orValue("")}
  conditions: ${deployment.status.?conditions.orValue([])}
```

Rules:

- Always use `?` and `.orValue(...)` for fields that may not exist at first reconcile.
- Use mixed-text interpolation and wrap non-string values in `string()` (see the CEL
  string-interpolation decision above).
- Do **not** reference `schema.spec.*` in status — it is a no-op (status would just echo
  the input). Reference **resource** state instead.
- kro infers status field types from the CEL expressions automatically; they are
  validated at RGD creation time, not runtime.

**Built-in status fields (auto-added — do not define):**

- `conditions` — array tracking instance state.
- `state` — `ACTIVE | IN_PROGRESS | FAILED | DELETING | ERROR`.

---

## Type Inference Rules (for converters)

When converting from typed source (Terraform variables, Helm `values.yaml`, Kustomize
fields):

| Source pattern              | RGD type              | Notes                                            |
|-----------------------------|-----------------------|--------------------------------------------------|
| Numeric default             | `integer` or `number` | `integer` for an integer literal, `number` for a float |
| Boolean default             | `boolean`             |                                                  |
| String default              | `string`              | Quote the default value                          |
| Empty list                  | `[]object`            | **Not** `[]string` — empty lists usually want object items. Add a `# TODO:` for the user to type it explicitly. |
| Non-empty homogeneous list  | `[]<element-type>`    |                                                  |
| Empty map                   | `map[string]string`   | Add `# TODO:` — the user may need `map[string]<otherType>` |
| Nested map (homogeneous)    | nested object         |                                                  |

Always emit a `# TODO:` YAML comment for any empty list/map encountered, and list the
field for user review in the relevant README/output summary.

---

## Common Pitfalls and Anti-Patterns

1. **Forgetting `?` on optional reads** → instances stuck "not ready" forever waiting on
   a status field that doesn't exist yet.
2. **Using `omit()` without the feature gate** → CEL evaluation errors at apply time
   with no clear pointer at the gate. Prefer `includeWhen`.
3. **Manufacturing spec fields for constants** → if a value never changes across
   instances, inline it. Don't pollute the spec.
4. **Using reserved IDs** → silent expression-resolution failures.
5. **`metadata.namespace` left literal** → cluster-scoped surprise. Parameterize via
   `${schema.metadata.namespace}` for namespaced resources.
6. **Owner-reference cascade on rollback** → deleting the instance deletes everything
   kro created. For **adopted** resources this can cascade-delete live AWS resources.
   The `ownerReferences` default for adopted resources is an **open item** (pending a
   service-team decision) and is handled as a configurable, flagged option — see
   `kro/adoption/instances.md`. Do not hardcode it here.
7. **Forgetting the version header** → operators can't tell which kro version the RGD
   targets. Always emit the header comment (see Version Compatibility).
8. **Unquoted CEL expressions or SimpleSchema markers** → `kubectl apply` fails with
   `mapping values are not allowed in this context` or similar YAML parse errors. All
   `${...}` values and `string | marker` fields MUST be double-quoted. See the YAML
   Quoting Rules section below.

---

## YAML Quoting Rules for RGD Generation

KRO's CRD stores CEL expressions and SimpleSchema markers as **opaque strings**, but
`kubectl apply` runs the YAML through a standard parser first. Any value containing
`${}`, `|`, or unbalanced quotes **must** be double-quoted in the YAML output, or the
apply will fail with cryptic parsing errors.

### Rule 1: SimpleSchema markers — always quote

The `|` (pipe) character is a YAML block-scalar indicator. Any schema field that uses
SimpleSchema marker syntax MUST be wrapped in double quotes:

```yaml
# ❌ BREAKS — YAML sees `|` as block scalar start
spec:
  name: string | required=true

# ✅ WORKS
spec:
  name: "string | required=true"
  replicas: "integer | default=3 minimum=1"
  namespace: "string | default=default"
```

### Rule 2: CEL expressions — always quote

Any value containing `${...}` must be double-quoted. This applies everywhere: `status`,
`readyWhen`, `includeWhen`, and all `template` fields:

```yaml
# ❌ BREAKS
status:
  bucketArn: ${bucket.status.?ackResourceMetadata.arn.orValue("")}

# ✅ WORKS — inner quotes escaped
status:
  bucketArn: "${bucket.status.?ackResourceMetadata.arn.orValue(\"\")}"

# ❌ BREAKS
readyWhen:
  - ${bucket.status.?ackResourceMetadata.arn.orValue("") != ""}

# ✅ WORKS
readyWhen:
  - "${bucket.status.?ackResourceMetadata.arn.orValue(\"\") != \"\"}"

# ❌ BREAKS
metadata:
  name: ${schema.spec.name}-bucket

# ✅ WORKS
metadata:
  name: "${schema.spec.name}-bucket"
```

### Rule 3: Nested JSON in CEL — escape inner quotes

When CEL builds a JSON string (common for `adoption-fields` annotations), inner
double-quotes must be backslash-escaped because the outer value is already in YAML
double-quotes:

```yaml
# ✅ Building adoption-fields JSON via CEL string concatenation
services.k8s.aws/adoption-fields: "${\"{\\\"name\\\": \\\"\" + schema.spec.name + \"-\" + schema.spec.accountId + \"\\\"}\"}"
```

The escaping layers:
1. YAML double-quoted string → `\"` produces a literal `"`
2. CEL string literal inside → `\\\"` produces `\"` which is a quote inside the CEL string
3. Result at runtime: `{"name": "krmops-app-476114149042"}`

### Rule 4: Block scalars (`|`) are safe for multiline content

YAML block scalars work fine for fields where the **content** is a literal multiline
string (e.g., policy documents). The content is passed as-is — CEL expressions inside
the block scalar are resolved by KRO at reconcile time, not by the YAML parser:

```yaml
# ✅ SAFE — block scalar for multiline JSON with embedded CEL
policyDocument: |
  {
    "Statement": [{
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": ["arn:aws:s3:::${schema.spec.name}-${schema.spec.accountId}/*"]
    }],
    "Version": "2012-10-17"
  }
```

### Rule 5: Plain strings without special characters — no quoting needed

Values that are plain identifiers or don't contain `${}`, `|`, `:`, `#`, or quotes can
remain unquoted:

```yaml
# ✅ No quoting needed
services.k8s.aws/adoption-policy: adopt
services.k8s.aws/deletion-policy: retain
kind: Bucket
apiVersion: s3.services.k8s.aws/v1alpha1
```

### Quick Reference Table

| Content type | Needs quoting? | Example |
|---|---|---|
| SimpleSchema markers (`\|`) | ✅ Always | `"string \| required=true"` |
| CEL expressions (`${...}`) | ✅ Always | `"${schema.spec.name}-app"` |
| CEL with inner quotes | ✅ + escape inner `"` | `"${...orValue(\"\")}"` |
| CEL building JSON strings | ✅ + double-escape | `"${\"{\\\"key\\\": ...\"}"` |
| Block scalar multiline | ❌ No (use `\|`) | `policyDocument: \|` |
| Plain strings | ❌ No | `adopt`, `retain` |
| Annotations with colons | ❌ No (YAML handles) | `services.k8s.aws/region: eu-west-1` |

### Common Mistake: Forgetting to quote `readyWhen`

This is the most frequent error when generating RGDs. The `readyWhen` array items are
CEL expressions that nearly always contain special characters:

```yaml
# ❌ Every one of these will break kubectl apply
readyWhen:
  - ${bucket.status.?ackResourceMetadata.arn.orValue("") != ""}

# ✅ Quote + escape
readyWhen:
  - "${bucket.status.?ackResourceMetadata.arn.orValue(\"\") != \"\"}"
```

---

## CORE 3 Reconciliation Summary

Quick map of the decided positions baked into this file (Authority_Doc → Req):

| Topic | Decided position | Section |
|---|---|---|
| `group:` on schema | Optional; defaults to `kro.run` when absent | RGD Top-Level Structure → `group:` |
| `externalRef` | Supported primitive for read-only refs, incl. label-selector form | Referencing Existing Resources |
| CEL string interpolation | Mixed-text; wrap non-strings in `string()`; prefer `.format()` for ARNs | CEL → String interpolation |
| `forEach` | Single documented form; prefer CEL `map()` for in-place transforms | `forEach` |
| `omit()` | Prefer `includeWhen`; if emitted, note `CELOmitFunction` gate required | Conditional Creation |
| KRO version policy | Version-adaptable; document target version + run detection command | Version Compatibility |
| Terraform state during adoption | Never modify TF state (AWS/ACK path) | `SKILL.md` Key Principles + `kro/adoption/instances.md` |
| `ownerReferences` (adopted) | **OPEN** — configurable/flagged, pending default omit | `kro/adoption/instances.md` |

> External lookups: full docs https://kro.run/docs/overview · examples
> https://kro.run/examples/ · SimpleSchema spec
> https://kro.run/api/specifications/simple-schema · CEL libraries
> https://kro.run/docs/concepts/rgd/cel-libraries
