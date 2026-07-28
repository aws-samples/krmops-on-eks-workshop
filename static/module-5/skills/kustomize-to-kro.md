---
name: kustomize-to-kro-rgd
description: Convert Kustomize configurations into kro ResourceGraphDefinitions (RGDs). Renders the production overlay, parameterizes fields that vary across overlays, and emits a kro RGD plus a matching instance example.
allowed-tools: Read, Grep, Glob, Write
---

# kro Version Compatibility

This skill targets `kro.run/v1alpha1`. Generated RGDs include a header comment naming the version, and call out primitives whose syntax has varied between alphas (`forEach`, `externalRef`, `omit()`). See [[rgd-authoring-reference]] for current RGD field rules.

# Workflow

When this transformation is invoked:

1. **Discover the kustomization**:
   - Locate the root `kustomization.yaml` (or `kustomization.yml` / `Kustomization`).
   - Identify `bases` / `resources` / `components`.
   - Enumerate overlays (typical layout: `base/` + `overlays/<env>/`).
   - If multiple overlays exist, ask the user which is the **production overlay** (defaults to `overlays/prod` if present, else the only overlay, else asks).

2. **Render**:
   - Render the chosen overlay using `kustomize build` semantics — this is the source of truth for what the RGD will produce.
   - Parse the rendered output as a multi-document YAML stream.
   - Each document = one resource entry in the RGD.

3. **Detect parameters**:
   - Render every overlay (not just production) and diff the rendered manifests.
   - Fields that **vary across overlays** become candidate `spec` fields.
   - Always-promote candidates (offer to user for confirmation):
     - `metadata.namespace` on every resource → `${schema.metadata.namespace}`
     - `metadata.name` prefixes/suffixes (from `namePrefix` / `nameSuffix`) → `${schema.metadata.name}`
     - Container `image` values → `${schema.spec.image.<name>}`
     - `Deployment.spec.replicas` → `${schema.spec.replicas}`
     - Resource limits / requests → grouped under `spec.resources`
     - Env-var values that differ across overlays → `spec.env`
     - `commonLabels` and `commonAnnotations` → `${schema.spec.labels}` / `${schema.spec.annotations}`
   - Anything that is identical across overlays stays as a literal.

4. **Generate the RGD**:
   - `kind` = PascalCase form of the kustomization root directory name (or the `commonLabels.app` value if present).
   - `apiVersion: v1alpha1` at `spec.schema.apiVersion`. Do NOT emit `group:` at the schema level — kro derives the API group (`kro.run`) implicitly. See [[rgd-authoring-reference]].
   - Resource `id` = lowerCamelCase derived from manifest `kind` (suffix on collisions). Avoid reserved IDs (`schema`, `metadata`, `spec`, `status`, `each`, `instance`, `resources`, plus CEL macros: `has`, `all`, `exists`, `map`, `filter`).
   - Replace each detected parameter literal with the corresponding CEL expression.
   - Emit `readyWhen` for `Deployment`, `StatefulSet`, `Job`, `DaemonSet`, `Ingress`, and any CRDs with well-known ready conditions (recipes in [[rgd-authoring-reference]]).
   - Use `?` for status field reads.
   - Prepend a header comment to the generated RGD:
     ```yaml
     # Generated for kro kro.run/v1alpha1 — verify forEach / externalRef / omit() syntax against your installed version
     ```

5. **Generate instance(s)**:
   - One instance per overlay. The production-overlay instance becomes the canonical example.
   - Each instance's `spec` populated from that overlay's rendered values.

6. **Output**:
   - RGD → `kro/<root-name>-rgd.yaml`
   - Instance(s) → `kro/<root-name>-instance.yaml` (production) and optionally `kro/<root-name>-instance-<env>.yaml`
   - `kro/README.md` summarizing parameterization decisions, overlay coverage, and unsupported features.

# Kustomize → RGD Mapping Reference

## Overlay Diff → Spec

```
overlays/dev/kustomization.yaml      replicas: 1, image tag: dev
overlays/staging/kustomization.yaml  replicas: 2, image tag: rc
overlays/prod/kustomization.yaml     replicas: 5, image tag: 1.25.0
```

```yaml
# RGD spec
spec:
  replicas: integer | default=5 minimum=1
  image:
    repository: string | required=true
    tag: string | default="1.25.0"
```

```yaml
# Instances
# kro/<root>-instance.yaml — prod
spec:
  replicas: 5
  image:
    repository: nginx
    tag: "1.25.0"

# kro/<root>-instance-dev.yaml
spec:
  replicas: 1
  image:
    tag: "dev"
```

## Rendered Resource → RGD Resource

```yaml
# Rendered Deployment from `kustomize build overlays/prod`
apiVersion: apps/v1
kind: Deployment
metadata:
  name: prod-web
  namespace: prod
  labels:
    app: web
spec:
  replicas: 5
  template:
    spec:
      containers:
        - name: web
          image: nginx:1.25.0
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
      name: ${schema.metadata.name}
      namespace: ${schema.metadata.namespace}
      labels:
        app: ${schema.metadata.name}
    spec:
      replicas: ${schema.spec.replicas}
      template:
        spec:
          containers:
            - name: web
              image: "${schema.spec.image.repository}:${schema.spec.image.tag}"
```

## Patches → Already-Rendered Output

`patchesStrategicMerge`, `patchesJson6902`, and the unified `patches:` field are evaluated by `kustomize build`. The resulting manifest is what you parameterize. Patches are **not** preserved as a structural concept in the RGD.

If you need conditional resources at runtime (only present in some environments), use `includeWhen`:

```yaml
- id: monitoring
  includeWhen:
    - ${schema.spec.monitoring.enabled}
  template: …
```

## ConfigMap / Secret Generators → Inline Resources (with rolling-restart preservation)

`configMapGenerator` and `secretGenerator` produce real `ConfigMap` / `Secret` resources after rendering. Convert them like any other resource — but the Kustomize hash suffix (`-h7c4d2g`) is **NOT cosmetic**: it's the mechanism that triggers Pod rolling restarts when the ConfigMap content changes. Drop it naively and you regress runtime behavior.

The hash-rename pattern works because:
- Each content change → new ConfigMap name (`-h7c4d2g` → `-x9p3q1f`)
- Consuming Deployment's `volumeMounts`/`envFrom` reference the new name
- PodTemplateSpec changes → rolling restart picks up new content

Without the hash, the ConfigMap name stays constant across content changes, the Deployment's PodTemplateSpec doesn't change, and Pods keep running with stale mounted content (kubelet's projected-volume refresh is async/eventual for `env`-style refs and never refreshes `subPath` mounts).

### Recommended replacement: content-hash annotation on the consuming PodTemplate

When the consuming workload is in the same RGD, add a content-hash annotation to its PodTemplateSpec. The PodTemplate then changes whenever the ConfigMap content changes, which triggers a rolling restart:

```yaml
spec:
  appConfig: string | description="Contents of app.conf"

resources:
  - id: appConfigMap
    template:
      apiVersion: v1
      kind: ConfigMap
      metadata:
        name: ${schema.metadata.name}-config
        namespace: ${schema.metadata.namespace}
      data:
        app.conf: ${schema.spec.appConfig}

  - id: deployment
    template:
      apiVersion: apps/v1
      kind: Deployment
      metadata:
        name: ${schema.metadata.name}
        namespace: ${schema.metadata.namespace}
      spec:
        template:
          metadata:
            annotations:
              # Triggers rolling restart on ConfigMap content change.
              # If your kro version doesn't expose a hash() CEL helper, use the raw content
              # (it's in the spec already) or compute the hash client-side before applying.
              config-checksum: ${schema.spec.appConfig}    # VERIFY: replace with hash() if available
          spec: ...
            volumeMounts: ...
        volumes:
          - name: config
            configMap:
              name: ${schema.metadata.name}-config
```

### When the consumer is NOT in the same RGD

If the workload that consumes the ConfigMap is owned by a different RGD or by Helm/raw manifests, kro cannot drive its restart. Document this loss-of-behavior loudly in `kro/README.md`:

> The Kustomize source used `configMapGenerator` with hash-suffix naming, which triggered automatic Pod restarts on content change. The consuming workload is not in this RGD, so config updates will NOT trigger restarts. Operators must `kubectl rollout restart deployment/<name>` after applying spec changes that modify ConfigMap content.

Always emit one of these two patterns explicitly. Never silently drop the hash without a replacement mechanism.

## commonLabels / commonAnnotations → Spec Maps

```yaml
# kustomization.yaml
commonLabels:
  app: web
  team: platform
```

If labels vary across overlays:

```yaml
spec:
  labels: "map[string]string"

# Each resource template
metadata:
  labels: ${schema.spec.labels}
```

If labels are constant across overlays, inline them.

## namespace, namePrefix, nameSuffix → metadata refs

```yaml
# kustomization.yaml
namespace: prod
namePrefix: prod-
```

```yaml
metadata:
  name: ${schema.metadata.name}        # the prefix is now baked into the instance name
  namespace: ${schema.metadata.namespace}
```

Document in `kro/README.md` that names previously generated by `namePrefix`/`nameSuffix` are now the responsibility of whoever creates the instance.

# Kustomize Feature Support Matrix

| Feature                              | Supported | Notes                                                    |
|--------------------------------------|-----------|----------------------------------------------------------|
| `resources:` / `bases:`              | ✅        | All rendered resources become RGD entries                |
| `namespace`                          | ✅        | → `${schema.metadata.namespace}`                         |
| `namePrefix` / `nameSuffix`          | ✅        | Folded into `${schema.metadata.name}`                    |
| `commonLabels` / `commonAnnotations` | ✅        | Spec map if varied; inline if constant                   |
| `images:` (image overrides)          | ✅        | → `spec.image.<n>` per replaced image                    |
| `replicas:`                          | ✅        | → `spec.replicas`                                        |
| `configMapGenerator`                 | ✅        | Inlined; emit content-hash annotation on consumer (see C3 section) — never silently drop hash naming |
| `secretGenerator`                    | ⚠️         | DO NOT inline secret material into the RGD instance. Use external Secret management — see Secret Handling section below |
| `patchesStrategicMerge`              | ✅        | Pre-rendered                                             |
| `patches` (unified)                  | ✅        | Pre-rendered                                             |
| `patchesJson6902`                    | ✅        | Pre-rendered                                             |
| `components`                         | ⚠️         | Pre-rendered; may need `includeWhen` for env-conditional |
| `vars` (deprecated)                  | ❌        | Replace with explicit values in source manifests         |
| `replacements`                       | ✅        | Pre-rendered                                             |
| `helmCharts`                         | ⚠️         | Renders to manifests; CRD installation still out-of-band |
| `openapi.path` (custom OpenAPI)      | ❌        | No kro equivalent                                        |
| Plugins / `kustomize.config.k8s.io`  | ❌        | Unsupported                                              |

Legend: ✅ supported, ⚠️ partial / requires user intervention, ❌ not supported.

# Secret Handling — Do Not Inline

`secretGenerator` produces real `Secret` resources containing actual secret material. Naive conversion to a kro RGD parameterizes the secret value into `spec.<name>` — which means **the instance YAML now contains the secret in plaintext**. Operators paste this into PRs, Git repos, ticket comments. This is the most common foot-gun in this conversion path.

Two acceptable patterns:

**Pattern 1: External Secret Operator (ESO) / Sealed Secrets / SOPS** (recommended)

Reference an externally-managed Secret by name in the RGD; do not template its contents:

```yaml
spec:
  secretName: string | required=true description="Name of pre-existing Secret"

resources:
  - id: deployment
    template:
      ...
      spec:
        template:
          spec:
            containers:
              - name: app
                envFrom:
                  - secretRef:
                      name: ${schema.spec.secretName}
```

The Secret is created out-of-band by ESO/Sealed Secrets/SOPS pulling from a real secrets store (AWS Secrets Manager, Vault, etc.). The RGD only references it.

**Pattern 2: Acknowledge the trade-off explicitly**

If the team has no secret management and accepts the risk, document it loudly:

```yaml
spec:
  # WARNING: instance YAML for this RGD contains secret material in plaintext.
  # Do NOT commit instance files to source control. Use a private channel.
  apiKey: string | required=true
```

Plus a prominent line in `kro/README.md`:
> ⚠️ Instance YAML for this RGD must NOT be committed to source control — `spec.apiKey` is plaintext secret material.

The skill should default to Pattern 1 and prompt the user before falling back to Pattern 2.

# Key Rules to Follow

- Always render from the **production overlay** for the canonical RGD; render others only to detect parameters.
- Resource `id` is lowerCamelCase from manifest `kind`; suffix on collisions (`deployment`, `serviceWeb`, `serviceCache`).
- Avoid reserved IDs — see [[rgd-authoring-reference]] for the full list.
- **Never silently drop ConfigMap hash-suffix naming.** Replace with a content-hash annotation on the consuming PodTemplate (same RGD) or document the loss-of-restart in `kro/README.md` (consumer outside RGD).
- **Never inline secret material into the RGD instance.** Reference externally-managed Secrets by name. See "Secret Handling — Do Not Inline" above.
- Drop kustomize-injected labels (`app.kubernetes.io/managed-by: Kustomize` if present) — kro becomes the manager.
- Keep `metadata.namespace: ${schema.metadata.namespace}` on every namespaced resource.
- Add `readyWhen` for resources with established ready states (recipes in [[rgd-authoring-reference]]).
- Use `?` on optional status reads.
- When a field appears once and is constant, inline it; don't manufacture a spec field.
- Do NOT emit a `group:` field at `spec.schema` — kro derives the API group implicitly.
- If you hit an unsupported feature, emit a `// TODO:` comment in the YAML and a follow-up note in `kro/README.md`.

# kro Authoring Reference

For full RGD syntax, schema markers, CEL libraries, reserved keywords, forEach/includeWhen/readyWhen semantics, and field rules, see [[rgd-authoring-reference]] (`./rgd-authoring-reference.md`).
