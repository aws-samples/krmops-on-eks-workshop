# KRO Building Abstractions

> Sources:
> - [Single Resource RGD](https://kro.run/docs/building-abstractions/single-resource-rgd)
> - [Multi Resource RGD](https://kro.run/docs/building-abstractions/multi-resource-rgd)
> - [RGD Chaining](https://kro.run/docs/building-abstractions/rgd-chaining)

## Three Abstraction Patterns

| Pattern | What | When to Use |
|---------|------|-------------|
| Single Resource RGD | Better API in front of one resource | Hide provider details, enforce defaults, stabilize API |
| Multi Resource RGD | Package several resources as one unit | Resources are incomplete alone, need shared lifecycle |
| RGD Chaining | Compose RGDs from other RGDs | Build progressively higher abstractions from building blocks |

---

## Single Resource RGD

Wraps one underlying resource (native K8s or CRD like ACK) behind a simpler API.

**Use when:**
- Hide provider-specific implementation details
- Expose only the inputs users should control
- Apply platform defaults (labels, tags, naming, security)
- Change implementation later without changing user API
- Define clear readiness semantics

**Example: Wrap ACK DBInstance as AppDatabase**
```yaml
apiVersion: kro.run/v1alpha1
kind: ResourceGraphDefinition
metadata:
  name: appdatabase.platform
spec:
  schema:
    apiVersion: v1alpha1
    kind: AppDatabase
    spec:
      name: string
      storageGB: integer | default=20
      instanceClass: string | default="db.t3.micro"
    status:
      ready: ${database.status.?endpoint.?address.orValue("") != ""}
      endpoint: ${database.status.?endpoint.?address.orValue("")}

  resources:
    - id: database
      readyWhen:
        - ${database.status.?endpoint.?address.orValue("") != ""}
      template:
        apiVersion: rds.services.k8s.aws/v1alpha1
        kind: DBInstance
        metadata:
          name: ${schema.spec.name}
        spec:
          engine: postgres
          dbInstanceIdentifier: ${schema.spec.name}
          allocatedStorage: ${schema.spec.storageGB}
          dbInstanceClass: ${schema.spec.instanceClass}
```

**When NOT to wrap:** If the underlying resource already matches the platform contract, or users need direct access to most fields.

---

## Multi Resource RGD (Grouping)

Packages several tightly-coupled resources that should always be created/updated/deleted together.

**Use when:**
- Resources are incomplete or unsafe on their own
- One resource's config depends on another's status/ARN
- Security and access control should ship with the primary resource
- You want one lifecycle for a whole capability

**Typical bundles:**
- `Table + IAM Policy + IAM Role`
- `Bucket + bucket policy + access role`
- `Queue + DLQ + redrive policy + consumer policy`
- `Deployment + Service + HPA + PDB`

**Example: AppTable (DynamoDB + IAM Policy + IAM Role)**
```yaml
apiVersion: kro.run/v1alpha1
kind: ResourceGraphDefinition
metadata:
  name: apptable.platform
spec:
  schema:
    apiVersion: v1alpha1
    kind: AppTable
    spec:
      name: string
      servicePrincipal: string | default="lambda.amazonaws.com"
    status:
      tableName: ${table.spec.tableName}
      tableArn: ${table.status.ackResourceMetadata.arn}
      roleArn: ${role.status.ackResourceMetadata.arn}

  resources:
    - id: table
      template:
        apiVersion: dynamodb.services.k8s.aws/v1alpha1
        kind: Table
        metadata:
          name: ${schema.spec.name}
        spec:
          tableName: ${schema.spec.name}
          billingMode: PAY_PER_REQUEST
          keySchema:
            - attributeName: id
              keyType: HASH
          attributeDefinitions:
            - attributeName: id
              attributeType: S

    - id: accessPolicy
      template:
        apiVersion: iam.services.k8s.aws/v1alpha1
        kind: Policy
        metadata:
          name: ${schema.spec.name}-table-access
        spec:
          name: ${schema.spec.name}-table-access
          policyDocument: |
            {
              "Version": "2012-10-17",
              "Statement": [{
                "Effect": "Allow",
                "Action": ["dynamodb:GetItem","dynamodb:PutItem","dynamodb:UpdateItem","dynamodb:DeleteItem","dynamodb:Query","dynamodb:Scan"],
                "Resource": ["${table.status.ackResourceMetadata.arn}","${table.status.ackResourceMetadata.arn}/index/*"]
              }]
            }

    - id: role
      template:
        apiVersion: iam.services.k8s.aws/v1alpha1
        kind: Role
        metadata:
          name: ${schema.spec.name}-table-role
        spec:
          name: ${schema.spec.name}-table-role
          policies:
            - ${accessPolicy.status.ackResourceMetadata.arn}
          assumeRolePolicyDocument: >
            {
              "Version": "2012-10-17",
              "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "${schema.spec.servicePrincipal}"},
                "Action": "sts:AssumeRole"
              }]
            }
```

**Key insight:** This is the pattern for migrating TF modules that bundle an AWS resource with IAM access — one RGD groups all resources with dependency wiring.

---

## RGD Chaining

Compose complex applications by using instances of one RGD within another RGD's resource graph.

**How it works:** When you create an RGD, kro registers a CRD. That CRD can be used as a resource in other RGDs.

```yaml
resources:
  # Use an instance of the Database RGD
  - id: database
    template:
      apiVersion: kro.run/v1alpha1
      kind: Database
      metadata:
        name: ${schema.spec.name}-db
      spec:
        size: ${schema.spec.dbSize}
    readyWhen:
      - ${database.status.ready == true}

  # Use an instance of the WebApplication RGD
  - id: webapp
    template:
      apiVersion: kro.run/v1alpha1
      kind: WebApplication
      metadata:
        name: ${schema.spec.name}-web
      spec:
        databaseUrl: ${database.status.connectionString}
```

**Best practices:**
- Keep RGDs small and focused with clear inputs/outputs
- Expose necessary status fields for outer RGDs to consume
- Name inner instances based on outer instance name for traceability
- Avoid deep nesting (makes debugging harder)

---

## Relevance to Migration

**For Class B (adoption):** Use Multi Resource RGD pattern — one RGD groups all resources from a TF module with adoption annotations.

**For Class C (creation):** Same Multi Resource RGD pattern but without adoption annotations — becomes a self-service blueprint.

**For platform evolution:** RGD Chaining lets you compose higher-level abstractions (e.g., a `FullStackApp` that chains `Database` + `WebApp` RGDs).
