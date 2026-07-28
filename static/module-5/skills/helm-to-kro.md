---
name: helm-to-kro-rgd
description: Convert Helm charts into kro ResourceGraphDefinitions (RGDs). Analyzes Chart.yaml, values.yaml, and templates/ in the repository and generates equivalent kro RGD YAML with correct schema, CEL expressions, resource dependencies, and a matching instance example. Primary input is chart source; rendered-output ingestion is a placeholder.
allowed-tools: Read, Grep, Glob, Write
---

# kro Version Compatibility

This skill targets `kro.run/v1alpha1`. Generated RGDs include a header comment naming the version, and call out primitives whose syntax has varied between alphas (`forEach`, `externalRef`, `omit()`). See [[rgd-authoring-reference]] for current RGD field rules.

# Workflow

When this transformation is invoked:

1. **Discover the chart**:
   - Locate `Chart.yaml` (entry point — required).
   - Read `values.yaml` (default spec inputs).
   - Enumerate `templates/*.yaml` (resources to convert).
   - Note any `charts/` (sub-charts) — flag for manual handling.
   - Note any `_helpers.tpl` or other `_*.tpl` files — flag (named templates not supported).
   - Note any files annotated with `helm.sh/hook` — flag (no kro equivalent).

2. **Pick conversion mode**:
   - **Chart source** (default) — parse templates directly, mapping Go template syntax to CEL.
   - **Rendered output** (placeholder, see section below) — ingest `helm template` output.

3. **Analyze chart source**:
   - Map `values.yaml` keys → RGD `spec` schema fields. Infer types from default values; nested maps become nested objects.
   - Map each `templates/*.yaml` document → an entry in `resources`. Multi-document files split per `---` boundary.
   - Convert `{{ .Values.foo }}` references to `${schema.spec.foo}`.
   - Convert `{{ .Release.Name }}` → `${schema.metadata.name}` and `{{ .Release.Namespace }}` → `${schema.metadata.namespace}`.
   - Convert `{{ if … }}` block guarding an entire resource → resource-level `includeWhen`.
   - Convert `{{ if … }}` guarding a field → CEL ternary with `omit()` for null-out cases.
   - Convert `{{ range … }}` over a `.Values` list → `forEach`.
   - Identify dependencies between resources via name references and emit them as CEL refs (KRO derives ordering automatically).

4. **Generate the RGD**:
   - `kind` = PascalCase form of `Chart.yaml` `name`.
   - `apiVersion: v1alpha1` at `spec.schema.apiVersion`. Do NOT emit `group:` at the schema level — kro derives the API group (`kro.run`) implicitly. See [[rgd-authoring-reference]].
   - Resource `id` = lowerCamelCase derived from the rendered manifest's `kind` (and a suffix when multiple resources share a kind, e.g. `deployment`, `serviceCache`). Avoid reserved IDs — see Reserved IDs section below.
   - Add `readyWhen` for resources with meaningful ready states (Deployment, StatefulSet, Job, DaemonSet, Ingress, PVC; recipes in [[rgd-authoring-reference]]).
   - Use the `?` operator for status fields that may not exist at reconcile time.
   - Prepend a header comment to the generated RGD:
     ```yaml
     # Generated for kro kro.run/v1alpha1 — verify forEach / externalRef / omit() syntax against your installed version
     ```

5. **Generate a matching instance** populated from `values.yaml` defaults.

6. **Output**:
   - RGD → `kro/<chart-name>-rgd.yaml`
   - Instance → `kro/<chart-name>-instance.yaml`
   - `kro/README.md` summarizing mapping decisions, flagged unsupported features, and any manual follow-ups.

# Helm → RGD Mapping Reference

## values.yaml → Spec Schema

```yaml
# values.yaml
replicaCount: 3
image:
  repository: nginx
  tag: "1.25"
  pullPolicy: IfNotPresent
service:
  type: ClusterIP
  port: 80
ingress:
  enabled: false
  hosts: []
```

```yaml
# RGD spec
spec:
  replicaCount: integer | default=3 minimum=1
  image:
    repository: string | required=true
    tag: string | default="1.25"
    pullPolicy: string | enum="Always,IfNotPresent,Never" default="IfNotPresent"
  service:
    type: string | enum="ClusterIP,NodePort,LoadBalancer" default="ClusterIP"
    port: integer | default=80
  ingress:
    enabled: boolean | default=false
    hosts: "[]string"
```

Type inference rules:
- numeric default → `integer` (or `number` if non-integer)
- boolean default → `boolean`
- string default → `string`
- empty list → `[]object` (NOT `[]string` — most empty lists in chart values hold objects: `hosts: []`, `tolerations: []`, `volumes: []`. Defaulting to `[]string` produces an RGD that fails validation the first time someone passes a real value.) Emit a `# TODO:` comment at the field and surface the field name in `kro/README.md` for explicit user typing.
- empty map → `map[string]string` with same TODO/README caveat — user may need `map[string]<otherType>`.
- nested map → nested object

## Resource Template → RGD Resource

```yaml
# templates/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}
spec:
  replicas: {{ .Values.replicaCount }}
  selector:
    matchLabels:
      app: {{ .Release.Name }}
  template:
    metadata:
      labels:
        app: {{ .Release.Name }}
    spec:
      containers:
        - name: app
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          ports:
            - containerPort: {{ .Values.service.port }}
```

```yaml
# RGD resource
- id: deployment
  readyWhen:
    - ${deployment.status.availableReplicas >= schema.spec.replicaCount}
  template:
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: ${schema.metadata.name}
      namespace: ${schema.metadata.namespace}
    spec:
      replicas: ${schema.spec.replicaCount}
      selector:
        matchLabels:
          app: ${schema.metadata.name}
      template:
        metadata:
          labels:
            app: ${schema.metadata.name}
        spec:
          containers:
            - name: app
              image: "${schema.spec.image.repository}:${schema.spec.image.tag}"
              imagePullPolicy: ${schema.spec.image.pullPolicy}
              ports:
                - containerPort: ${schema.spec.service.port}
```

## `{{ if }}` → `includeWhen` or CEL Ternary

Whole-resource guard:

```yaml
# templates/ingress.yaml
{{- if .Values.ingress.enabled }}
apiVersion: networking.k8s.io/v1
kind: Ingress
…
{{- end }}
```

```yaml
# RGD
- id: ingress
  includeWhen:
    - ${schema.spec.ingress.enabled}
  template: …
```

Field-level guard — use ternary with `omit()`:

```yaml
{{- if .Values.tls.enabled }}
tls:
  - secretName: {{ .Values.tls.secretName }}
{{- end }}
```

```yaml
# Requires kro --feature-gates=CELOmitFunction=true (verify with cluster operator before applying)
tls: ${schema.spec.tls.enabled ? [{"secretName": schema.spec.tls.secretName}] : omit()}
```

⚠️ **`omit()` is gated.** It requires `--feature-gates=CELOmitFunction=true` on the kro controller, which is **not on by default**. If applied to a cluster without the gate, you get a CEL evaluation error at admission time that does NOT obviously point at the gate — operators chase phantom CEL bugs.

When emitting `omit()` in generated RGDs you MUST:
1. Emit the inline `# Requires kro --feature-gates=CELOmitFunction=true ...` comment shown above directly above the use site. Do not rely on README documentation alone — operators see the YAML before the README.
2. List every `omit()` use site in `kro/README.md` under a "Feature Gates Required" section.
3. If gating a whole resource (not a field), prefer `includeWhen` instead — it requires NO feature gate and has no risk of admission-time surprise.

## `{{ range }}` → `forEach`

```yaml
{{- range .Values.ingress.hosts }}
- host: {{ .host }}
  http:
    paths:
      - path: {{ .path }}
{{- end }}
```

For a list of objects iterated within a single resource, prefer CEL `map()` — no version risk:

```yaml
rules: ${schema.spec.ingress.hosts.map(h, {"host": h.host, "http": {"paths": [{"path": h.path}]}})}
```

For a list iterated to produce multiple top-level resources, lift it to a `forEach` resource. ⚠️ **`forEach` syntax has varied between kro alphas** — verify Form A vs Form B against your installed version. See [[rgd-authoring-reference]] for both forms.

Form A (older alphas — custom binding name):
```yaml
- id: ingressRules
  forEach:
    - host: ${schema.spec.ingress.hosts}
  template:
    apiVersion: networking.k8s.io/v1
    kind: Ingress
    metadata:
      name: ${schema.metadata.name}-${host.name}
    spec: …
```

Form B (newer alphas — `each` binding):
```yaml
# VERIFY: forEach syntax varies between kro versions — confirm 'each' binding works in your installation
- id: ingressRules
  forEach: ${schema.spec.ingress.hosts}
  template:
    apiVersion: networking.k8s.io/v1
    kind: Ingress
    metadata:
      name: ${schema.metadata.name}-${each.name}
    spec: …
```

When emitting `forEach`, always include a `# VERIFY:` comment in the generated RGD and note the version dependency in `kro/README.md`. Prefer `map()` whenever possible to avoid the version dependency entirely.

## Outputs / `NOTES.txt` → Status

Helm `NOTES.txt` is informational only — it does not map to RGD status. Instead, derive `status` fields from observable resource fields.

⚠️ **For status synthesis, prefer string concatenation over mixed-text interpolation.** Mixed-text-and-CEL (`"${a}:${b}"`) has had inconsistent behavior in `status.*` paths across kro alphas; concat (`a + ":" + b`) is reliable everywhere.

```yaml
# RECOMMENDED — concat
status:
  serviceEndpoint: ${service.spec.clusterIP + ":" + string(service.spec.ports[0].port)}
  readyReplicas: ${deployment.status.?availableReplicas.orValue(0)}
```

```yaml
# AVOID in status.* — mixed-text interpolation has been buggy
status:
  serviceEndpoint: "${service.spec.clusterIP}:${string(service.spec.ports[0].port)}"
```

Mixed-text interpolation is fine in `template.*` paths (where the surrounding YAML is a literal string). The caveat applies specifically to `status.*` synthesis.

# Reserved Resource IDs

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

When auto-deriving an ID from a kind (`deployment`, `service`, ...), check against this list and suffix on collision. Same list documented in [[rgd-authoring-reference]] — the source of truth.

# CRD Lifecycle (charts that ship CRDs)

Helm charts often ship CRDs in `crds/` or as part of templates. kro does NOT install these as part of RGD reconciliation — CRDs must exist in the cluster BEFORE the RGD's CRs can be admitted.

If the chart has a `crds/` directory or templates that emit `kind: CustomResourceDefinition`:

1. List every CRD in the generated `kro/README.md` under a "Pre-Install Required" section.
2. Include the install command:
   ```
   kubectl apply -f https://raw.githubusercontent.com/<chart-repo>/crds/<file>.yaml
   # or, if the chart is local:
   kubectl apply -f charts/<chart>/crds/
   ```
3. Note that without the CRDs, the RGD will fail at instance creation with an admission error referencing the unknown kind. `readyWhen` won't fire because the CRs never get created.
4. If the chart has CRD templates (rendered through Go templating), strip them OUT of the generated RGD and document them as a separate kubectl apply step. Mixing CRD installation with CR creation in the same RGD is unreliable.

For CRDs whose presence is conditional (e.g., only if `crds.install: true` in values), document both paths in `kro/README.md`.

# Helm Feature Support Matrix

| Feature                                  | Supported | Notes                                               |
|------------------------------------------|-----------|-----------------------------------------------------|
| `.Values.*`                              | ✅        | → `${schema.spec.*}`                                |
| `.Release.Name`                          | ✅        | → `${schema.metadata.name}`                         |
| `.Release.Namespace`                     | ✅        | → `${schema.metadata.namespace}`                    |
| `.Chart.Name` / `.Chart.Version`         | ✅        | Hardcode (the RGD itself represents the chart)      |
| `{{ if … }}` / `{{ else if }}`           | ✅        | → `includeWhen` (whole resource) or CEL ternary     |
| `{{ range }}` over `.Values`             | ✅        | → CEL `map()` or top-level `forEach`                |
| `{{ with … }}`                           | ⚠️         | Inline the path manually                            |
| `{{ default }}` / `{{ coalesce }}`       | ✅        | → `?.orValue(...)` or ternary                       |
| `{{ toYaml }}` / `{{ nindent }}`         | ⚠️         | Strip — kro emits structured YAML natively          |
| `{{ include "x" . }}`                    | ❌        | Named templates / `_helpers.tpl` not supported      |
| `{{ tpl … }}`                            | ❌        | Recursive templating — flag for manual conversion   |
| `{{ lookup … }}`                         | ❌        | Use kro `externalRef` instead                       |
| `helm.sh/hook` annotations               | ❌        | No kro equivalent — out of scope                    |
| `.Files.Get`                             | ❌        | No equivalent — inline file content as a string val |
| Sub-charts (`charts/`)                   | ⚠️         | Flag; convert as separate RGDs and reference        |
| CRD installation (`crds/`)               | ❌        | Apply CRDs out-of-band BEFORE the RGD instance — see CRD Lifecycle section |

Legend: ✅ supported, ⚠️ partial / requires user intervention, ❌ not supported.

# Key Rules to Follow

- Resource `id` is lowerCamelCase derived from manifest `kind` (suffix when collisions: `deployment`, `serviceWeb`, `serviceCache`).
- Never use reserved kro keywords / CEL macros as resource IDs — see Reserved Resource IDs section above.
- Preserve `metadata.namespace: ${schema.metadata.namespace}` on every namespaced resource so cluster-scoped RGDs behave correctly.
- Add `readyWhen` for `Deployment`, `StatefulSet`, `Job`, `DaemonSet`, `Ingress`, `PVC`, and any custom resource with a known ready condition (recipes in [[rgd-authoring-reference]]).
- Use `?` on optional status reads: `${deployment.status.?availableReplicas.orValue(0)}`.
- For status synthesis, prefer string concatenation (`a + ":" + b`) over mixed-text interpolation (`"${a}:${b}"`) — mixed-text has had bugs in `status.*` paths.
- For empty list/map values in chart `values.yaml`, default to `[]object` / `map[string]string` AND emit a TODO; don't infer `[]string` (which fails validation when users pass real values).
- When a Helm template's value is constant (no `.Values` ref), inline the literal — don't manufacture a spec field for it.
- When the same `.Values.x` appears in many places, it becomes one spec field.
- Do NOT emit a `group:` field at `spec.schema` — kro derives the API group implicitly.
- For `omit()` use, emit an inline `# Requires --feature-gates=CELOmitFunction=true` comment AND a README entry. Prefer `includeWhen` for whole-resource gating.
- For `forEach`, emit a `# VERIFY:` comment and document the version dependency. Prefer CEL `map()` whenever it fits.
- Charts that ship CRDs: extract them to a separate "Pre-Install Required" section in `kro/README.md`; do not embed them in the RGD.
- If you hit an unsupported feature, emit a `// TODO:` YAML comment and a line in `kro/README.md` describing the manual fix.

# Rendered Output Path (Placeholder)

If the user provides `helm template` output instead of chart source:

1. Each `---`-separated manifest becomes a resource entry.
2. **Parameterization is lost** — every value is now a literal. Either:
   - Skip parameterization (the RGD has no spec, every instance is identical), or
   - Have the user point at fields to expose; lift those into `spec` and replace the literals with CEL refs.
3. Document the chosen approach in `kro/README.md`.

> This path is not the primary workflow. Implementation is deferred. When invoked, ask the user whether they have access to the chart source and prefer that path.

# kro Authoring Reference

For full RGD syntax, schema markers, CEL libraries, reserved keywords, forEach/includeWhen/readyWhen semantics, and field rules, see [[rgd-authoring-reference]] (`./rgd-authoring-reference.md`).
