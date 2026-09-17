# KRO Overview

> Source: [kro.run/docs/overview](https://kro.run/docs/overview)

## What is KRO?

kro (Kube Resource Orchestrator) lets you turn a set of Kubernetes resources into a reusable API. You define the API schema, describe the resources behind it in YAML, and connect them with CEL expressions. kro turns that definition into a CRD, watches for instances of that API, and reconciles the underlying resources.

**Version:** 0.9.2

## Core Concepts

- **ResourceGraphDefinition (RGD):** The blueprint for a custom API — describes the interface users work with and the resources each instance should produce
- **Schema:** Defines your API surface (fields users fill in) using SimpleSchema syntax
- **Resources:** Templates that reference schema fields with CEL expressions `${...}`
- **Instances:** What users create to deploy resources — one instance = one deployed set of resources

## How It Works

1. You create an RGD with schema + resource templates
2. kro parses CEL expressions, infers the dependency graph, generates a CRD
3. Users create instances of the generated CRD
4. kro reconciles: creates resources in dependency order, wires data between them

## What KRO Handles

- **SimpleSchema** — define API schema inline (types, defaults, constraints)
- **Wires data that doesn't exist yet** — reference status fields from uncreated resources; kro waits
- **Infers ordering from expressions** — no explicit dependency declaration needed
- **Conditional resources** — `includeWhen` to include/exclude subgraphs
- **Collections** — `forEach` expands one template into N resources
- **Non-Turing complete** — CEL always terminates, no side effects, type-checked at apply time

## KRO + ACK Integration

KRO works with any Kubernetes resource — native or CRD. For AWS resources, use ACK CRDs inside KRO resource templates:

```yaml
resources:
  - id: bucket
    template:
      apiVersion: s3.services.k8s.aws/v1alpha1
      kind: Bucket
      metadata:
        name: ${schema.spec.bucketName}
      spec:
        name: ${schema.spec.bucketName}
```

KRO manages the dependency graph and lifecycle; ACK manages the AWS API calls.

## KRO + ArgoCD Integration

> Source: [kro.run/docs/faq](https://kro.run/docs/faq)

To use kro resources with ArgoCD, add tracking annotations and owner references to all templated resources:

```yaml
metadata:
  ownerReferences:
    - apiVersion: kro.run/v1alpha1
      kind: ${schema.kind}
      name: ${schema.metadata.name}
      uid: ${schema.metadata.uid}
      blockOwnerDeletion: true
      controller: false
  annotations:
    argocd.argoproj.io/tracking-id: ${schema.metadata.?annotations["argocd.argoproj.io/tracking-id"]}
```

This allows ArgoCD to properly track and manage resources created by kro instances. Note: owner references have limitations (see instances docs for risks).

## Key URLs

- Docs: https://kro.run/docs/overview
- GitHub: https://github.com/kubernetes-sigs/kro
- Examples: https://kro.run/examples/
- Slack: #kro on Kubernetes Slack
