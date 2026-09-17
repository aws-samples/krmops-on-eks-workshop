# Terraform Resource Type → ACK Mapping

> Source: [ACK API Reference](https://aws-controllers-k8s.github.io/docs/api-reference), [ACK Services](https://aws-controllers-k8s.github.io/docs/services)

## ⚠️ CRITICAL: This Mapping Table Is a Snapshot — ALWAYS Web-Verify

**This file is a convenience reference that may be INCOMPLETE or OUTDATED.** The absence of a Terraform resource type from this table does NOT mean there is no ACK equivalent. ACK controllers and CRDs are added and updated frequently.

**MANDATORY: For EVERY Terraform resource type being migrated, the agent MUST perform a web search to verify the ACK mapping — EVEN IF the resource already appears in this table (field names may have changed) and ESPECIALLY IF it does NOT appear here.**

### Web Verification Steps (REQUIRED for every resource type)

1. **Derive the AWS service name** from the TF resource type prefix:
   - `aws_s3_*` → service = `s3`
   - `aws_iam_*` → service = `iam`
   - `aws_lambda_*` → service = `lambda`
   - `aws_api_gateway_*` → service = `apigateway` (REST API v1)
   - `aws_apigatewayv2_*` → service = `apigatewayv2` (HTTP API v2)
   - `aws_dynamodb_*` → service = `dynamodb`
   - `aws_eks_*` → service = `eks`
   - `aws_rds_*` / `aws_db_*` → service = `rds`
   - `aws_sqs_*` → service = `sqs`
   - `aws_sns_*` → service = `sns`
   - `aws_ecr_*` → service = `ecr`
   - `aws_lb_*` / `aws_alb_*` → service = `elbv2`
   - `aws_cloudfront_*` → service = `cloudfront`
   - `aws_kms_*` → service = `kms`
   - `aws_secretsmanager_*` → service = `secretsmanager`
   - `aws_route53_*` → service = `route53`
   - `aws_elasticache_*` → service = `elasticache`
   - `aws_sfn_*` → service = `sfn`
   - `aws_cloudwatch_*` → service = `cloudwatch` or `cloudwatchlogs`

2. **Search GitHub for the controller and its CRDs:**
   - `https://github.com/aws-controllers-k8s/<service>-controller`
   - `https://github.com/aws-controllers-k8s/<service>-controller/tree/main/helm/crds`
   - Look at the CRD filenames — they reveal every supported Kind

3. **Web-search for the specific resource Kind:**
   - Search: `aws-controllers-k8s <service>-controller <Kind> CRD`
   - Search: `ACK <Kind> apiVersion spec fields`

4. **Verify CRD field names** — ACK uses camelCase (AWS SDK Go naming), which differs from Terraform's snake_case. NEVER guess field names from TF attribute names. Always check the CRD YAML at:
   - `https://github.com/aws-controllers-k8s/<service>-controller/tree/main/helm/crds`

5. **If no controller exists after exhaustive search**, THEN mark as unsupported.

### Common Pitfalls (Why Web Search Is Non-Negotiable)

| Mistake | Reality |
|---------|---------|
| "No API Gateway v1 controller" | `apigateway-controller` exists with 11 CRDs for REST APIs |
| "Lambda is Alpha/unsupported" | `lambda-controller` has been GA since 2023 |
| "CloudFront doesn't have ACK" | `cloudfront-controller` is GA with 7 CRDs |
| "Field name must be `function_name`" | ACK uses `functionName` (camelCase) |
| "No PodIdentityAssociation CRD" | Added in eks-controller v1.4.0+ |

---

## How to Use This Reference

When parsing TF state or HCL, each resource has a `type` field (e.g., `aws_s3_bucket`).
Use this table as a **starting point** to map it to the ACK apiVersion, Kind, and adoption lookup field.
**Then web-verify the CRD schema before generating any YAML.**

## Mapping Table

### S3 (s3.services.k8s.aws/v1alpha1)

| TF Resource Type | ACK Kind | Adoption Field | TF State Attribute for Lookup |
|-----------------|----------|----------------|-------------------------------|
| `aws_s3_bucket` | `Bucket` | `name` | `attributes.bucket` |

**Notes:** S3 Bucket CRD is consolidated — versioning, encryption, lifecycle, policy all go in one spec.

### IAM (iam.services.k8s.aws/v1alpha1)

| TF Resource Type | ACK Kind | Adoption Field | TF State Attribute for Lookup |
|-----------------|----------|----------------|-------------------------------|
| `aws_iam_role` | `Role` | `name` | `attributes.name` |
| `aws_iam_policy` | `Policy` | `arn` | `attributes.arn` |
| `aws_iam_instance_profile` | `InstanceProfile` | `name` | `attributes.name` |
| `aws_iam_group` | `Group` | `name` | `attributes.name` |
| `aws_iam_user` | `User` | `name` | `attributes.name` |
| `aws_iam_role_policy_attachment` | N/A | N/A | Merged into Role `policies` field |
| `aws_iam_policy_attachment` | N/A | N/A | Merged into Role `policies` field |

**Notes:**
- `aws_iam_role_policy_attachment` does NOT have its own ACK CRD — ACK Role has a `policies` field (list of ARNs)
- `aws_iam_role.assume_role_policy` → ACK Role `spec.assumeRolePolicyDocument`
- `aws_iam_policy.policy` → ACK Policy `spec.policyDocument`
- `aws_iam_role_policy` (inline) → ACK Role `spec.inlinePolicies` (map of name→document)

**⚠️ Create_Path: Use `policyRefs` (NOT `policies`) for role-policy binding in KRO RGDs:**
- `spec.policies` accepts a list of literal ARN strings — these must be known at creation time
- `spec.policyRefs` uses ACK cross-resource references (`from.name`) — ACK resolves the Policy CR's ARN asynchronously
- In a KRO RGD, CEL expressions in `spec.policies` may not resolve correctly because KRO renders templates at creation time. Use `policyRefs` instead:
  ```yaml
  policyRefs:
    - from:
        name: "${schema.spec.appName}-my-policy"  # metadata.name of the Policy CR
  ```
- This creates the correct dependency: the Role waits for the Policy to be created, then ACK attaches it

### EC2 (ec2.services.k8s.aws/v1alpha1)

| TF Resource Type | ACK Kind | Adoption Field | TF State Attribute for Lookup |
|-----------------|----------|----------------|-------------------------------|
| `aws_vpc` | `VPC` | `vpcID` | `attributes.id` |
| `aws_subnet` | `Subnet` | `subnetID` | `attributes.id` |
| `aws_security_group` | `SecurityGroup` | `id` | `attributes.id` |
| `aws_internet_gateway` | `InternetGateway` | `internetGatewayID` | `attributes.id` |
| `aws_nat_gateway` | `NATGateway` | `natGatewayID` | `attributes.id` |
| `aws_route_table` | `RouteTable` | `routeTableID` | `attributes.id` |
| `aws_eip` | `ElasticIPAddress` | `allocationID` | `attributes.allocation_id` |
| `aws_instance` | `Instance` | `instanceID` | `attributes.id` |
| `aws_launch_template` | `LaunchTemplate` | `launchTemplateID` | `attributes.id` |
| `aws_vpc_endpoint` | `VPCEndpoint` | `vpcEndpointID` | `attributes.id` |
| `aws_route` | N/A | N/A | Embedded in RouteTable spec |

**Notes:**
- `aws_route` is NOT a separate CRD — routes are part of RouteTable spec
- Security group rules (`aws_security_group_rule`) are part of SecurityGroup spec (`ingressRules`/`egressRules`)

### API Gateway v1 (apigateway.services.k8s.aws/v1alpha1)

| TF Resource Type | ACK Kind | Adoption Field | TF State Attribute for Lookup |
|-----------------|----------|----------------|-------------------------------|
| `aws_api_gateway_rest_api` | `RestAPI` | `id` | `attributes.id` |
| `aws_api_gateway_resource` | `Resource` | `id` | `attributes.id` |
| `aws_api_gateway_method` | `Method` | N/A | Composite: restApiId + resourceId + httpMethod |
| `aws_api_gateway_integration` | `Integration` | N/A | Composite: restApiId + resourceId + httpMethod |
| `aws_api_gateway_deployment` | `Deployment` | `id` | `attributes.id` |
| `aws_api_gateway_stage` | `Stage` | `stageName` + `restApiId` | `attributes.stage_name` |
| `aws_api_gateway_authorizer` | `Authorizer` | `id` | `attributes.id` |
| `aws_api_gateway_api_key` | `ApiKey` | `id` | `attributes.id` |
| `aws_api_gateway_integration_response` | `ApiIntegrationResponse` | N/A | Composite |
| `aws_api_gateway_method_response` | `ApiMethodResponse` | N/A | Composite |
| `aws_api_gateway_vpc_link` | `VpcLink` | `id` | `attributes.id` |
| `aws_lambda_permission` | N/A | N/A | Managed via Lambda resource-based policy, not a separate CRD |

**Notes:**
**Notes:**
- ACK API Gateway v1 supports **cross-resource references** via `restAPIRef` and `resourceRef` fields (using `from: name:` pattern)
- `Method` required fields: `authorizationType`, `httpMethod` + either `restAPIID` or `restAPIRef` + either `resourceID` or `resourceRef`
- `Integration` required fields: `httpMethod`, `type` + either `restAPIID` or `restAPIRef` + either `resourceID` or `resourceRef`
- `RestAPI` required fields: `name`
- `aws_lambda_permission` has no dedicated CRD — **use Integration `credentials` field** with an IAM role trusted by `apigateway.amazonaws.com` (see `ack/creation/creation-patterns.md` for the full pattern)

**⚠️ Field name pitfalls (verified against live CRD):**
- Integration `type` field is `type` — NOT `type_` (the underscore suffix is a Python SDK artifact, not present in the CRD)
- Resource `parentRef` references another **Resource** CR (for nested paths like `/item/{id}`) — for **top-level paths** under the API root, use `parentID: "${restApi.status.rootResourceID}"` instead
- `parentRef` does NOT reference a RestAPI — it only references other Resource CRs

**Status fields (for `readyWhen` and CEL expressions):**

| ACK Kind | Key Status Fields |
|----------|-------------------|
| `RestAPI` | `status.id` (API ID), `status.rootResourceID` (root resource for paths), `status.createdDate` |
| `Resource` | `status.id` (resource ID), `status.path` |
| `Method` | `status.conditions` (use `conditions` existence check) |
| `Integration` | `status.conditions` (use `conditions` existence check) |
| `Deployment` | `status.conditions` (use `conditions` existence check) |
| `Stage` | `status.conditions` |

> **Note:** RestAPI does NOT have `status.restAPIID` — it's `status.id`. Resource does NOT have `status.resourceID` — it's `status.id`. Always verify against the CRD.

### API Gateway v2 (apigatewayv2.services.k8s.aws/v1alpha1)

| TF Resource Type | ACK Kind | Adoption Field | TF State Attribute for Lookup |
|-----------------|----------|----------------|-------------------------------|
| `aws_apigatewayv2_api` | `Api` | `apiID` | `attributes.id` |
| `aws_apigatewayv2_stage` | `Stage` | `stageName` + `apiID` | `attributes.name` |
| `aws_apigatewayv2_route` | `Route` | `routeID` + `apiID` | `attributes.id` |
| `aws_apigatewayv2_integration` | `Integration` | `integrationID` + `apiID` | `attributes.id` |
| `aws_apigatewayv2_vpc_link` | `VpcLink` | `vpcLinkID` | `attributes.id` |
| `aws_apigatewayv2_authorizer` | `Authorizer` | `authorizerID` + `apiID` | `attributes.id` |
| `aws_apigatewayv2_deployment` | `Deployment` | `deploymentID` + `apiID` | `attributes.id` |
| `aws_apigatewayv2_domain_name` | `DomainName` | `domainName` | `attributes.domain_name` |
| `aws_apigatewayv2_api_mapping` | `ApiMapping` | N/A | Composite |

**Notes:**
- API Gateway v2 handles HTTP APIs and WebSocket APIs
- API Gateway v1 handles REST APIs (what `aws_api_gateway_*` resources use)

### EKS (eks.services.k8s.aws/v1alpha1)

| TF Resource Type | ACK Kind | Adoption Field | TF State Attribute for Lookup |
|-----------------|----------|----------------|-------------------------------|
| `aws_eks_cluster` | `Cluster` | `name` | `attributes.name` |
| `aws_eks_node_group` | `Nodegroup` | `nodegroupName` + `clusterName` | `attributes.node_group_name` |
| `aws_eks_addon` | `Addon` | `addonName` + `clusterName` | `attributes.addon_name` |
| `aws_eks_fargate_profile` | `FargateProfile` | `fargateProfileName` | `attributes.fargate_profile_name` |
| `aws_eks_pod_identity_association` | `PodIdentityAssociation` | `associationID` + `clusterName` | `attributes.association_id` |
| `aws_eks_access_entry` | `AccessEntry` | `clusterName` + `principalARN` | `attributes.cluster_name` + `attributes.principal_arn` |

### RDS (rds.services.k8s.aws/v1alpha1)

| TF Resource Type | ACK Kind | Adoption Field | TF State Attribute for Lookup |
|-----------------|----------|----------------|-------------------------------|
| `aws_db_instance` | `DBInstance` | `dbInstanceIdentifier` | `attributes.identifier` |
| `aws_rds_cluster` | `DBCluster` | `dbClusterIdentifier` | `attributes.cluster_identifier` |
| `aws_db_subnet_group` | `DBSubnetGroup` | `name` | `attributes.name` |
| `aws_db_parameter_group` | `DBParameterGroup` | `name` | `attributes.name` |
| `aws_rds_cluster_parameter_group` | `DBClusterParameterGroup` | `name` | `attributes.name` |
| `aws_rds_global_cluster` | `GlobalCluster` | `globalClusterIdentifier` | `attributes.global_cluster_identifier` |

### DynamoDB (dynamodb.services.k8s.aws/v1alpha1)

| TF Resource Type | ACK Kind | Adoption Field | TF State Attribute for Lookup |
|-----------------|----------|----------------|-------------------------------|
| `aws_dynamodb_table` | `Table` | `tableName` | `attributes.name` |
| `aws_dynamodb_global_table` | `GlobalTable` | `globalTableName` | `attributes.name` |

### SQS (sqs.services.k8s.aws/v1alpha1)

| TF Resource Type | ACK Kind | Adoption Field | TF State Attribute for Lookup |
|-----------------|----------|----------------|-------------------------------|
| `aws_sqs_queue` | `Queue` | `queueURL` | `attributes.url` |

### SNS (sns.services.k8s.aws/v1alpha1)

| TF Resource Type | ACK Kind | Adoption Field | TF State Attribute for Lookup |
|-----------------|----------|----------------|-------------------------------|
| `aws_sns_topic` | `Topic` | `arn` | `attributes.arn` |
| `aws_sns_topic_subscription` | `Subscription` | `arn` | `attributes.arn` |

### Lambda (lambda.services.k8s.aws/v1alpha1)

| TF Resource Type | ACK Kind | Adoption Field | TF State Attribute for Lookup |
|-----------------|----------|----------------|-------------------------------|
| `aws_lambda_function` | `Function` | `functionName` | `attributes.function_name` |
| `aws_lambda_alias` | `Alias` | `name` + `functionName` | `attributes.name` |
| `aws_lambda_event_source_mapping` | `EventSourceMapping` | `uuid` | `attributes.uuid` |

**Notes (Lambda):**
- `EventSourceMapping` for SQS sources: use `spec.eventSourceARN` with the SQS queue ARN (NOT `eventSourceRef` or `queueRefs` — those resolve to Kafka/MQ, not SQS). See "Known CRD Field Pitfalls" section.
- `functionRef` works correctly — it resolves against `functions.lambda.services.k8s.aws`.
- `Function` required fields: `code` + `name`. The `code` field needs either `s3Bucket`+`s3Key`, `imageURI`, or `zipFile`.
- `Function.spec.role` accepts a literal ARN string; `Function.spec.roleRef` accepts a cross-resource reference to a Role CR.

### ECR (ecr.services.k8s.aws/v1alpha1)

| TF Resource Type | ACK Kind | Adoption Field | TF State Attribute for Lookup |
|-----------------|----------|----------------|-------------------------------|
| `aws_ecr_repository` | `Repository` | `repositoryName` | `attributes.name` |

### KMS (kms.services.k8s.aws/v1alpha1)

| TF Resource Type | ACK Kind | Adoption Field | TF State Attribute for Lookup |
|-----------------|----------|----------------|-------------------------------|
| `aws_kms_key` | `Key` | `keyID` | `attributes.key_id` |
| `aws_kms_alias` | `Alias` | `aliasName` | `attributes.name` |

### ELBv2 (elbv2.services.k8s.aws/v1alpha1)

| TF Resource Type | ACK Kind | Adoption Field | TF State Attribute for Lookup |
|-----------------|----------|----------------|-------------------------------|
| `aws_lb` / `aws_alb` | `LoadBalancer` | `loadBalancerARN` | `attributes.arn` |
| `aws_lb_target_group` | `TargetGroup` | `targetGroupARN` | `attributes.arn` |
| `aws_lb_listener` | `Listener` | `listenerARN` | `attributes.arn` |

### CloudFront (cloudfront.services.k8s.aws/v1alpha1)

| TF Resource Type | ACK Kind | Adoption Field | TF State Attribute for Lookup |
|-----------------|----------|----------------|-------------------------------|
| `aws_cloudfront_distribution` | `Distribution` | `distributionID` | `attributes.id` |
| `aws_cloudfront_cache_policy` | `CachePolicy` | `cachePolicyID` | `attributes.id` |
| `aws_cloudfront_function` | `Function` | `functionName` | `attributes.name` |

### Secrets Manager (secretsmanager.services.k8s.aws/v1alpha1)

| TF Resource Type | ACK Kind | Adoption Field | TF State Attribute for Lookup |
|-----------------|----------|----------------|-------------------------------|
| `aws_secretsmanager_secret` | `Secret` | `secretID` | `attributes.id` |

### Route53 (route53.services.k8s.aws/v1alpha1)

| TF Resource Type | ACK Kind | Adoption Field | TF State Attribute for Lookup |
|-----------------|----------|----------------|-------------------------------|
| `aws_route53_zone` | `HostedZone` | `hostedZoneID` | `attributes.zone_id` |
| `aws_route53_record` | `RecordSet` | N/A | Complex — zone + name + type |

## Resources WITHOUT ACK Equivalent (Skip in Migration)

> ⚠️ **This list is a snapshot.** Before classifying ANY resource as "no ACK equivalent", the agent MUST web-search to confirm. Controllers and CRDs are added frequently.

| TF Resource Type | Reason | Alternative |
|-----------------|--------|-------------|
| `aws_iam_role_policy_attachment` | Merged into Role `policies` field | Include policy ARN in Role spec |
| `aws_security_group_rule` | Merged into SecurityGroup | Include in SG `ingressRules`/`egressRules` |
| `aws_route` | Merged into RouteTable | Include in RT `routes` |
| `aws_s3_bucket_versioning` | Merged into Bucket | Include in Bucket `versioning` |
| `aws_s3_bucket_encryption` | Merged into Bucket | Include in Bucket `encryption` |
| `aws_s3_bucket_lifecycle_configuration` | Merged into Bucket | Include in Bucket `lifecycle` |
| `aws_s3_bucket_policy` | Merged into Bucket | Include in Bucket `policy` |
| `aws_lambda_permission` | No dedicated CRD | **Use Integration `credentials` field** — create an IAM role trusted by `apigateway.amazonaws.com` with `lambda:InvokeFunction` and set it as `spec.credentials` on the Integration. See `ack/creation/creation-patterns.md` for the full pattern. |
| `random_password` | TF-specific | Pre-create K8s Secret |
| `null_resource` | TF-specific | K8s Job if needed |
| `time_sleep` | TF-specific | Use KRO readyWhen |
| `terraform_data` | TF-specific | Not needed |

## Key Insight: Consolidated CRDs

ACK consolidates what Terraform splits into multiple resources:

```
Terraform (5 resources):          ACK (1 resource):
├── aws_s3_bucket                 └── Bucket (with all config inline)
├── aws_s3_bucket_versioning
├── aws_s3_bucket_encryption
├── aws_s3_bucket_lifecycle
└── aws_s3_bucket_policy

Terraform (3 resources):          ACK (1 resource):
├── aws_iam_role                  └── Role (with policies + inline policies)
├── aws_iam_role_policy_attachment
└── aws_iam_role_policy (inline)
```


---

## ACK Resource-Based Permissions Model

When migrating Terraform resources that use resource-based policies (`aws_lambda_permission`, `aws_s3_bucket_policy`, `aws_sqs_queue_policy`, etc.), ACK handles permissions through three patterns:

### Pattern 1: Inline `policy` field on the resource CRD

These services consolidate the resource-based policy directly into the resource spec:

| TF Resource (separate) | ACK Field (inline) | Service |
|------------------------|--------------------|---------| 
| `aws_s3_bucket_policy` | `Bucket.spec.policy` | S3 |
| `aws_sqs_queue_policy` | `Queue.spec.policy` | SQS |
| `aws_sns_topic_policy` | `Topic.spec.policy` | SNS |
| `aws_kms_key_policy` | `Key.spec.policy` | KMS |
| `aws_ecr_repository_policy` | `Repository.spec.policy` | ECR |
| `aws_efs_file_system_policy` | `FileSystem.spec.policy` | EFS |

**Action:** Merge the separate TF policy resource into the parent resource's `spec.policy` field.

### Pattern 2: Caller-side IAM role (for invoke/trigger permissions)

When a service needs to invoke another (e.g., API Gateway → Lambda), use an IAM role on the caller side:

| TF Resource | ACK Pattern | Field |
|-------------|-------------|-------|
| `aws_lambda_permission` (for APIGW) | IAM Role trusted by `apigateway.amazonaws.com` | Integration `spec.credentials` |
| `aws_lambda_permission` (for EventBridge) | IAM Role trusted by `events.amazonaws.com` | Rule `spec.roleARN` |
| `aws_lambda_permission` (for SQS/SNS) | Handled by EventSourceMapping or inline policy | N/A (Lambda pulls from source) |

**Action:** Create an IAM Role + Policy, set it on the caller's `credentials`/`roleARN` field. See `ack/creation/creation-patterns.md` for the full APIGW pattern.

### Pattern 3: IAM identity-based policies (always available)

All identity-based permissions use the ACK IAM controller:

| TF Resource | ACK Resource | Binding |
|-------------|--------------|---------|
| `aws_iam_role` | `Role` | `spec.policyRefs` (cross-resource ref) |
| `aws_iam_policy` | `Policy` | `spec.policyDocument` |
| `aws_iam_role_policy_attachment` | N/A (merged) | `Role.spec.policyRefs` |
| `aws_iam_role_policy` (inline) | N/A (merged) | `Role.spec.inlinePolicies` |

**Key rule for KRO RGDs:** Always use `policyRefs` (not `policies`) to bind policies to roles — ACK resolves the reference asynchronously and handles ordering correctly.

---

## Known CRD Field Pitfalls (Verified via Live Testing)

> These are field name/type mismatches discovered during live cluster testing.
> They demonstrate why **web-verifying CRD schemas is mandatory** — Terraform
> attribute names do NOT reliably predict ACK CRD field names or types.

### IAM Role: `policies` NOT `managedPolicies`

| What you might guess | Correct CRD field | Type |
|---------------------|-------------------|------|
| `spec.managedPolicies` | `spec.policies` | `[]string` (array of policy ARNs) |
| `spec.managed_policy_arns` | `spec.policies` | `[]string` |

The IAM Role CRD spec fields are:
- `name` (string)
- `assumeRolePolicyDocument` (string — JSON)
- `description` (string)
- `path` (string)
- `maxSessionDuration` (integer)
- `policies` ([]string — list of managed policy ARNs to attach)
- `policyRefs` ([]object — cross-resource refs to Policy CRs by name)
- `inlinePolicies` (object — map of policy-name → JSON document)
- `permissionsBoundary` (string)
- `tags` ([]object — `[{key, value}]` format)

**In KRO RGDs:** Use `policyRefs` (not `policies`) when the Policy is also managed
in the same RGD — ACK resolves the reference asynchronously.

### Tags Format: Varies Per Service (NOT Consistent!)

**⚠️ CRITICAL:** ACK services use DIFFERENT tag formats. Do NOT assume all use the same structure.

| Service | CRD `spec.tags` Type | Format |
|---------|---------------------|--------|
| `iam.services.k8s.aws` (Role, Policy) | `[]object` (array) | `[{key: "K", value: "V"}]` |
| `ec2.services.k8s.aws` (VPC, Subnet, etc.) | `[]object` (array) | `[{key: "K", value: "V"}]` |
| `eks.services.k8s.aws` (Cluster) | `map[string]string` (object) | `{Key: "Value"}` |
| `eks.services.k8s.aws` (PodIdentityAssociation) | `map[string]string` (object) | `{Key: "Value"}` |
| `sqs.services.k8s.aws` (Queue) | `map[string]string` (object) | `{Key: "Value"}` |
| `s3.services.k8s.aws` (Bucket) | `[]object` (array) | `[{key: "K", value: "V"}]` |
| `rds.services.k8s.aws` (DBInstance) | `[]object` (array) | `[{key: "K", value: "V"}]` |
| `lambda.services.k8s.aws` (Function) | `map[string]string` (object) | `{Key: "Value"}` |

**Rule:** ALWAYS check the CRD schema for `spec.tags` type BEFORE generating. The wrong format causes KRO to reject the RGD at apply time with:
- Array when map expected: `"expected object type for path spec.tags, got array"`
- Map when array expected: validation error at apply time

### SimpleSchema Array Defaults in KRO

KRO's SimpleSchema does NOT support inline array defaults like `"[]string" | default=["a","b"]`. This causes YAML parse errors. Workarounds:
- Use plain string fields with hardcoded values in templates
- Or define without default and make them required inputs

### EventSourceMapping: `eventSourceRef` and `queueRefs` DON'T Reference SQS Queues

| What you might guess | What it actually references | Correct field for SQS |
|---------------------|----------------------------|----------------------|
| `spec.eventSourceRef` | `clusters.kafka.services.k8s.aws` (MSK clusters) | ❌ NOT SQS |
| `spec.queueRefs` | `brokers.mq.services.k8s.aws` (Amazon MQ brokers) | ❌ NOT SQS |
| `spec.eventSourceARN` (literal string) | Direct ARN — works for ANY event source | ✅ Correct for SQS |

**The problem:** The `EventSourceMapping` CRD has three potential ways to specify the event source:
1. `eventSourceRef` — resolves via `kafka.services.k8s.aws` → only for MSK (Managed Streaming for Kafka)
2. `queueRefs` — resolves via `mq.services.k8s.aws` → only for Amazon MQ brokers
3. `eventSourceARN` — a literal string field accepting any valid ARN → **the only option for SQS**

**In a KRO RGD:** Use CEL to wire the SQS queue ARN from the Queue CR's status:
```yaml
spec:
  eventSourceARN: "${sqsQueue.status.ackResourceMetadata.arn}"
  functionRef:
    from:
      name: "${schema.spec.functionName}"
```

**Why `functionRef` works but `eventSourceRef` doesn't for SQS:** The Lambda controller knows how to resolve `functionRef` against its own `Function` CRD. But `eventSourceRef` is hardcoded to look up Kafka clusters (a different controller's CRD), and `queueRefs` is hardcoded to look up MQ brokers. Neither resolves against `queues.sqs.services.k8s.aws`.

### EKS Cluster Authentication Mode

Clusters created via ACK default to `CONFIG_MAP` auth mode. To use EKS access entries (API-based auth), the cluster config must be updated to `API_AND_CONFIG_MAP` after creation. Consider:
- Adding `accessConfig.authenticationMode: API_AND_CONFIG_MAP` in the Cluster spec
- Or documenting a post-creation step for access entry setup

**Verified Cluster spec field:** `spec.accessConfig.authenticationMode` — set to `"API_AND_CONFIG_MAP"` to enable access entries at creation time.
