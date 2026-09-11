# Known Limitations

## CRD Validation Requires Spec Fields (Even for Adoption)

**Issue:** The ACK documentation suggests `spec: {}` for strict adoption, but CRD validation enforces required fields regardless of adoption annotations.

**Impact:** Adoption CRs with empty spec will be rejected by the API server with `Invalid value` errors.

**Workaround:** Always populate required spec fields from TF state attributes:
- `Bucket` → `spec.name` (required)
- `Role` → `spec.name` + `spec.assumeRolePolicyDocument` (required)
- `Policy` → `spec.name` + `spec.policyDocument` (required)
- `PodIdentityAssociation` → `spec.clusterName` + `spec.namespace` + `spec.serviceAccount` + `spec.roleARN` (required)

**Source of truth:** TF state `attributes` section contains all values needed.

## EKS Auto Mode — ACK Capability Permissions

**Issue:** When ACK is installed as an EKS Auto Mode capability, the controller role (`<cluster>-ack-capability`) may not include all service-specific permissions by default.

**Impact:** Adoption CRs are accepted by the API server but the controller reports `AccessDeniedException` in conditions, and the resource stays in `Unknown` sync state.

**Workaround:** 
1. The skill generates a scoped IAM policy in MIGRATION-NOTES.md
2. User must attach this policy to the ACK capability role before applying CRs
3. Once permissions are added, the controller automatically retries and adoption succeeds

**Common missing permissions:**
- `eks:DescribePodIdentityAssociation` — needed for PodIdentityAssociation adoption
- `eks:ListPodIdentityAssociations` — needed for listing/discovery
- Service-specific Describe/Get actions for any resource being adopted

## Resources That Cannot Be Adopted (Only Created)

| Resource Type | Reason | Workaround |
|---------------|--------|------------|
| `aws_eks_pod_identity_association` | EKS ACK controller does not implement adoption for this resource ([no adoption test in e2e](https://github.com/aws-controllers-k8s/eks-controller/tree/main/test/e2e/resources)) | Let ACK **create** it fresh with the same spec (no adoption annotations), and delete the old association from AWS. The result is identical — same cluster, namespace, SA, role. **Do NOT `terraform state rm`** — TF state is left untouched (see the detailed explanation below). |
| `aws_iam_role_policy_attachment` | No ACK CRD — merged into Role | ACK auto-discovers attached policies on Role adoption |
| `aws_security_group_rule` | No ACK CRD — merged into SecurityGroup | Include in SG `ingressRules`/`egressRules` |
| `aws_route` | No ACK CRD — merged into RouteTable | Include in RT `routes` |
| `aws_s3_bucket_versioning` | No ACK CRD — merged into Bucket | ACK reads versioning config on Bucket adoption |
| `aws_s3_bucket_policy` | No ACK CRD — merged into Bucket | ACK reads bucket policy on Bucket adoption |
| `random_password` | TF-specific, no AWS resource | Pre-create K8s Secret |
| `null_resource` | TF-specific | K8s Job if needed |

### EKS PodIdentityAssociation — Detailed Explanation

The EKS ACK controller (v1.14.0) supports adoption **only for Cluster** resources. PodIdentityAssociation, Addon, Nodegroup, AccessEntry, and FargateProfile do NOT support the `adoption-policy` annotation — the controller silently ignores it (no error, no status, no reconciliation).

**Evidence:**
- GitHub `test/e2e/resources/` has `cluster_adoption.yaml` but no `pod_identity_association_adoption.yaml`
- Creating a PodIdentityAssociation with `adoption-policy: adopt` results in zero status and zero events from the controller
- Creating without adoption annotations works immediately

**Migration strategy for PodIdentityAssociation:**
1. In the RGD, do NOT use adoption annotations for this resource — use a plain create spec
2. Delete the existing association in AWS before applying: `aws eks delete-pod-identity-association --cluster-name <cluster> --association-id <id>`
3. ACK creates a new one with the same spec (same cluster, namespace, SA, role = functionally identical)
4. Inform the user that this resource will be recreated (not adopted) — the old TF-managed one should be cleaned up separately

**Important:** Do NOT recommend modifying Terraform state files (`terraform state rm`). Instead, inform the user that:
- The PodIdentityAssociation cannot be adopted and will be created fresh
- The old TF-managed association should be deleted from AWS (via console, CLI, or `terraform destroy` targeting that resource)
- After migration, Terraform will show the resource as "missing" on next plan — this is expected and can be resolved by removing the resource block from `.tf` files

## Adoption-Fields Lookup Variations

**Issue:** Some resources require compound lookup fields (multiple values in the adoption-fields JSON).

**Known compound lookups:**
- `PodIdentityAssociation` → `{"associationID": "...", "clusterName": "..."}`
- `Nodegroup` → `{"nodegroupName": "...", "clusterName": "..."}`
- `Addon` → `{"addonName": "...", "clusterName": "..."}`
- `AccessEntry` → `{"clusterName": "...", "principalARN": "..."}`

**Impact:** If only one field is provided, adoption fails silently or adopts the wrong resource.

## KRO + Adoption Annotations in Templates

**Issue:** When using KRO RGD with CEL expressions inside annotation values (e.g., `adoption-fields`), the JSON must be carefully formatted to avoid CEL parsing conflicts.

**Workaround:** Use block scalar (`|`) for annotation values containing CEL interpolation:
```yaml
annotations:
  services.k8s.aws/adoption-fields: |
    {"name": "${schema.spec.bucketName}"}
```

Do NOT use single-line with nested quotes — it breaks CEL parsing.

## KRO Needs Kubernetes RBAC to Manage ACK Resources

**Issue:** KRO creates ACK CRs (Buckets, Roles, Policies, etc.) as part of reconciling a ResourceGraphDefinition. By default, the KRO capability role has no Kubernetes RBAC permissions to access ACK API groups.

**Impact:** RGD instances go to `ERROR` state with:
```
"buckets" is forbidden: User "...-kro-capability/KRO" cannot get resource "buckets"
in API group "s3.services.k8s.aws" in the namespace "default"
```

**Root cause:** Two separate permission layers exist:
- **AWS IAM** — controls what ACK can do in AWS (s3:GetBucket, etc.)
- **Kubernetes RBAC** — controls what KRO can do inside the cluster (create/get/update CRs)

**Solution (pick one):**

1. **EKS Access Entry (recommended for EKS Auto Mode):**
   Add an access entry for the KRO role with `AmazonEKSClusterAdminPolicy`:
   ```python
   # In CDK
   eks.CfnAccessEntry(self, "KroAccessEntry",
       cluster_name=cluster_name,
       principal_arn=kro_role.role_arn,
       type="STANDARD",
       access_policies=[eks.CfnAccessEntry.AccessPolicyProperty(
           access_scope=eks.CfnAccessEntry.AccessScopeProperty(type="cluster"),
           policy_arn="arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy",
       )],
   )
   ```

2. **ClusterRole + ClusterRoleBinding (manual/GitOps):**
   ```yaml
   apiVersion: rbac.authorization.k8s.io/v1
   kind: ClusterRole
   metadata:
     name: kro-ack-resource-manager
   rules:
     - apiGroups: ["s3.services.k8s.aws", "iam.services.k8s.aws", "eks.services.k8s.aws"]
       resources: ["*"]
       verbs: ["*"]
   ---
   apiVersion: rbac.authorization.k8s.io/v1
   kind: ClusterRoleBinding
   metadata:
     name: kro-ack-resource-manager-binding
   subjects:
     - kind: User
       name: arn:aws:sts::<account>:assumed-role/<cluster>-kro-capability/KRO
       apiGroup: rbac.authorization.k8s.io
   roleRef:
     kind: ClusterRole
     name: kro-ack-resource-manager
     apiGroup: rbac.authorization.k8s.io
   ```

**Note:** The API groups in the ClusterRole must match the ACK services used in the RGD. Add more as needed.

## Future Extension — Out-of-Scope Migration Paths

The following three migration paths are **out of scope** for the current AWS/ACK-path skill. They are documented here as potential future additions, not as removed capabilities.

### Native-Kubernetes Adoption (Terraform `kubernetes`/`helm` providers)

**Issue:** Terraform manages Kubernetes resources (Deployments, Services, ConfigMaps, etc.) via the `kubernetes` and `helm` providers. Migrating ownership of these resources to a Kubernetes-native controller requires Server-Side Apply (SSA) field-level ownership transfer combined with `terraform state rm` to release Terraform's hold on the resource.

### Helm Chart Conversion

**Issue:** Converting Terraform-managed Helm releases (via the `helm_release` resource) into standalone Helm charts or FluxCD/ArgoCD HelmRelease CRs requires extracting the chart reference and values, then removing the resource from Terraform state so Helm can assume full ownership.

### Kustomize Conversion

**Issue:** Converting Terraform-managed Kubernetes manifests into Kustomize overlays requires transferring field ownership via SSA and removing the resources from Terraform state so Kustomize can manage them directly.

---

### Why These Are Excluded

All three paths rely on a fundamentally different ownership mechanism than the AWS/ACK adoption path:

| Mechanism | AWS/ACK Path (current) | Native-K8s / Helm / Kustomize (future) |
|-----------|------------------------|----------------------------------------|
| Ownership transfer | ACK adoption annotations — controller discovers existing AWS resource by ARN/ID | Server-Side Apply (SSA) — field-level ownership transfer inside the cluster |
| Terraform state handling | **Never modified** — TF state is read-only input | Requires `terraform state rm` to release resources |
| Risk model | Zero-downtime; TF and ACK coexist safely during transition | Modifying TF state is irreversible; mistakes orphan resources |

The AWS-path skill's Key Principle is to **never modify Terraform state**. The native-K8s paths violate this principle because SSA ownership transfer only works if Terraform relinquishes its state tracking of the resource via `terraform state rm`. Mixing the two mechanisms in a single skill would undermine the safety guarantees the adopt-path provides.

### Path Forward

These paths are viable future extensions once:
1. A dedicated skill or skill-mode is created with its own safety model for state modification.
2. Guardrails are established for `terraform state rm` (dry-run validation, rollback plan, state backup).
3. SSA conflict-resolution logic is documented for common Terraform-to-K8s resource mappings.

Until then, users needing native-K8s migration should handle it manually or through a separate workflow.
