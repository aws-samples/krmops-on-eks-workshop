# KRO Examples

> Source: [github.com/kubernetes-sigs/kro/tree/main/examples](https://github.com/kubernetes-sigs/kro/tree/main/examples)

## How to Find Examples

URL pattern: `https://github.com/kubernetes-sigs/kro/tree/main/examples/<provider>/<use-case>`

Each example folder contains:
- `rg.yaml` — the ResourceGraphDefinition
- `instance.yaml` — a sample instance

## Available AWS Examples (ACK-based)

| Example | Path | What It Shows |
|---------|------|---------------|
| S3 Bucket + IAM Policy | `examples/aws/s3bucket/` | Single bucket with conditional write policy |
| Pod Identity (IAM Role + PodIdentityAssociation + SA) | `examples/aws/podidenity/` | Multi-resource RGD with ACK + native K8s |
| EKS Cluster Management | `examples/aws/eks-cluster-mgmt/` | Full EKS cluster with nodegroups |
| Network Stack | `examples/aws/networkstack/` | VPC + subnets + routing |
| RDS Postgres | `examples/aws/rds-postgres/` | Database with subnet group |
| ElastiCache Serverless | `examples/aws/elasticache-serverless/` | Cache cluster |
| ALB + Listener + Target Group | `examples/aws/alb-listener-tg/` | Load balancer setup |
| Data Processor (SQS + Lambda) | `examples/aws/data-processor/` | Event-driven pipeline |
| Serverless Microservice | `examples/aws/serverless-microservice/` | API GW + Lambda + DynamoDB |
| Web Stack | `examples/aws/webstack/` | Full web application stack |
| AWS Accounts Factory | `examples/aws/aws-accounts-factory/` | Multi-account provisioning |
| ACK Controller setup | `examples/aws/ack-controller/` | Installing ACK controllers via KRO |
| Ingress Triangle | `examples/aws/ingress-triangle/` | Ingress patterns |
| LLM | `examples/aws/llm/` | ML workload |
| VPC Lattice | `examples/apigateway/vpc-lattice/` | Service mesh |

## Other Providers
- `examples/azure/` — Azure Service Operator (ASO) examples
- `examples/gcp/` — Config Connector examples
- `examples/kubernetes/` — Native K8s resource examples

---

## Key Example: Pod Identity

> Path: `examples/aws/podidenity/rg.yaml`

This example shows a multi-resource RGD combining ACK resources (IAM Role, PodIdentityAssociation) with native K8s resources (ServiceAccount).

**RGD:**
```yaml
apiVersion: kro.run/v1alpha1
kind: ResourceGraphDefinition
metadata:
  name: podidentity.kro.run
spec:
  schema:
    apiVersion: v1alpha1
    kind: PodIdentity
    spec:
      name: string
      clusterName: string | default="kro"
      policyARN: string | default=""
    status:
      serviceAccount: ${serviceaccount.metadata.name}

  resources:
  - id: role
    template:
      apiVersion: iam.services.k8s.aws/v1alpha1
      kind: Role
      metadata:
        name: ${schema.spec.name}-role
      spec:
        name: ${schema.spec.name}-role
        policies:
        - ${schema.spec.policyARN}
        assumeRolePolicyDocument: |
          {
            "Version": "2012-10-17",
            "Statement": [
              {
                "Effect": "Allow",
                "Principal": {
                  "Service": "pods.eks.amazonaws.com"
                },
                "Action": [
                  "sts:AssumeRole",
                  "sts:TagSession"
                ]
              }
            ]
          }

  - id: podidentityassociation
    template:
      apiVersion: eks.services.k8s.aws/v1alpha1
      kind: PodIdentityAssociation
      metadata:
        name: ${schema.spec.name}
      spec:
        clusterName: ${schema.spec.clusterName}
        roleARN: ${role.status.ackResourceMetadata.arn}
        serviceAccount: ${serviceaccount.metadata.name}
        namespace: ${schema.metadata.namespace}

  - id: serviceaccount
    template:
      apiVersion: v1
      kind: ServiceAccount
      metadata:
        name: ${schema.spec.name}
```

**Instance:**
```yaml
apiVersion: kro.run/v1alpha1
kind: PodIdentity
metadata:
  name: my-pod-identity
  namespace: default
spec:
  name: my-app
  clusterName: my-cluster
  policyARN: arn:aws:iam::123456789012:policy/my-policy
```

---

## Key Example: S3 Bucket with Conditional Policy

> Path: `examples/aws/s3bucket/rg.yaml`

Shows `includeWhen` for conditional resources and `${resource.status.ackResourceMetadata.arn}` for cross-resource references.

**RGD:**
```yaml
apiVersion: kro.run/v1alpha1
kind: ResourceGraphDefinition
metadata:
  name: s3bucket.kro.run
spec:
  schema:
    apiVersion: v1alpha1
    kind: S3Bucket
    spec:
      name: string
      access: string | default="write"
    status:
      s3ARN: ${s3bucket.status.ackResourceMetadata.arn}
      s3PolicyARN: ${s3PolicyWrite.status.ackResourceMetadata.arn}

  resources:
  - id: s3bucket
    template:
      apiVersion: s3.services.k8s.aws/v1alpha1
      kind: Bucket
      metadata:
        name: ${schema.spec.name}
      spec:
        name: ${schema.spec.name}

  - id: s3PolicyWrite
    includeWhen:
    - ${schema.spec.access == "write"}
    template:
      apiVersion: iam.services.k8s.aws/v1alpha1
      kind: Policy
      metadata:
        name: ${schema.spec.name}-s3-write-policy
      spec:
        name: ${schema.spec.name}-s3-write-policy
        policyDocument: |
          {
            "Version": "2012-10-17",
            "Statement": [
              {
                "Effect": "Allow",
                "Action": ["s3:GetObject","s3:PutObject","s3:PutObjectAcl","s3:DeleteObject"],
                "Resource": ["${s3bucket.status.ackResourceMetadata.arn}/*"]
              },
              {
                "Effect": "Allow",
                "Action": ["s3:ListBucket","s3:GetBucketLocation"],
                "Resource": ["${s3bucket.status.ackResourceMetadata.arn}"]
              }
            ]
          }
```

**Instance:**
```yaml
apiVersion: kro.run/v1alpha1
kind: S3Bucket
metadata:
  name: s3demo
  namespace: default
spec:
  name: s3demo-11223344
```

---

## Key Patterns Observed

1. **`${role.status.ackResourceMetadata.arn}`** — standard way to reference ACK resource ARNs across resources
2. **`${schema.metadata.namespace}`** — pass instance namespace to child resources
3. **`includeWhen`** — conditional resources based on spec fields
4. **Policy documents use `|` block scalar** — JSON as multiline string with CEL interpolation inside
5. **Naming: `${schema.spec.name}-suffix`** — consistent naming derived from spec
6. **Instance is minimal** — just the spec fields, kro handles everything else
