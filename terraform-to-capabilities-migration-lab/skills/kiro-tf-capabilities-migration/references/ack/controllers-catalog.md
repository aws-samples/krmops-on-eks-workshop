# ACK Controllers Catalog

> Source: [ACK Services](https://aws-controllers-k8s.github.io/docs/services), [API Reference](https://aws-controllers-k8s.github.io/docs/api-reference)

## ⚠️ CRITICAL: This Catalog Is a Snapshot — ALWAYS Web-Verify

**This file is a convenience reference that may be INCOMPLETE or OUTDATED.** ACK controllers are released independently and new controllers are added frequently. The absence of a controller or CRD from this file does NOT mean it doesn't exist.

**MANDATORY: For EVERY Terraform resource type being migrated, the agent MUST perform a web search to verify ACK controller existence — regardless of whether the resource appears in this catalog or not.**

### Web Verification Steps (REQUIRED for every resource type)

1. **Search GitHub for the controller repo:**
   - `https://github.com/aws-controllers-k8s/<service>-controller`
   - Derive `<service>` from the AWS service name (e.g., `apigateway`, `lambda`, `s3`, `dynamodb`, `eks`, `rds`)
   - If the obvious name doesn't work, try variations: `apigateway` vs `apigatewayv2`, `elasticloadbalancing` vs `elbv2`, `sqs`, `sns`, etc.

2. **Check the CRDs directory on GitHub:**
   - `https://github.com/aws-controllers-k8s/<service>-controller/tree/main/helm/crds`
   - This gives you the definitive list of supported Kinds and their field schemas

3. **Search the ACK documentation site:**
   - `https://aws-controllers-k8s.github.io/community/reference/`
   - `https://aws-controllers-k8s.github.io/docs/services/`

4. **Search for blog posts and tutorials:**
   - Search: `aws-controllers-k8s <service> controller`
   - Search: `ACK <service> Kubernetes CRD`

5. **If all searches return nothing**, THEN mark the resource as unsupported.

### Why This Matters

- Controllers graduate from Preview to GA frequently
- New controllers are added without this file being updated
- CRDs are added to existing controllers (new resource types supported)
- Field names and schemas evolve between versions
- **The cost of a web search is negligible compared to incorrectly classifying a resource as unsupported**

---

## Overview

ACK has 67+ controllers. Each controller manages one AWS service and is independently versioned.

**Release Phases:**
- **General Availability (GA)** — version 1.x.x+, production-ready
- **Preview** — version 0.x.x, under active development

## GA Controllers (Relevant for Migration)

| Service | Controller | Version | CRDs | apiVersion prefix |
|---------|-----------|---------|------|-------------------|
| S3 | s3-controller | 1.5.0 | 1 | `s3.services.k8s.aws/v1alpha1` |
| IAM | iam-controller | 1.6.4 | 7 | `iam.services.k8s.aws/v1alpha1` |
| EC2 | ec2-controller | 1.12.0 | 20 | `ec2.services.k8s.aws/v1alpha1` |
| EKS | eks-controller | 1.13.1 | 8 | `eks.services.k8s.aws/v1alpha1` |
| RDS | rds-controller | 1.7.12 | 10 | `rds.services.k8s.aws/v1alpha1` |
| DynamoDB | dynamodb-controller | 1.8.0 | 3 | `dynamodb.services.k8s.aws/v1alpha1` |
| SQS | sqs-controller | 1.4.3 | 1 | `sqs.services.k8s.aws/v1alpha1` |
| SNS | sns-controller | 1.4.4 | 4 | `sns.services.k8s.aws/v1alpha1` |
| ECR | ecr-controller | 1.5.3 | 3 | `ecr.services.k8s.aws/v1alpha1` |
| KMS | kms-controller | 1.2.3 | 3 | `kms.services.k8s.aws/v1alpha1` |
| Lambda | lambda-controller | 1.12.2 | 7 | `lambda.services.k8s.aws/v1alpha1` |
| CloudFront | cloudfront-controller | 1.4.0 | 7 | `cloudfront.services.k8s.aws/v1alpha1` |
| API Gateway | apigateway-controller | 1.4.3 | 11 | `apigateway.services.k8s.aws/v1alpha1` |
| API Gateway v2 | apigatewayv2-controller | 1.2.3 | 9 | `apigatewayv2.services.k8s.aws/v1alpha1` |
| ElastiCache | elasticache-controller | 1.3.5 | 9 | `elasticache.services.k8s.aws/v1alpha1` |
| ELBv2 | elbv2-controller | 1.3.5 | 4 | `elbv2.services.k8s.aws/v1alpha1` |
| EventBridge | eventbridge-controller | 1.2.3 | 4 | `eventbridge.services.k8s.aws/v1alpha1` |
| Step Functions | sfn-controller | 1.2.3 | 2 | `sfn.services.k8s.aws/v1alpha1` |
| Secrets Manager | secretsmanager-controller | 1.2.4 | 1 | `secretsmanager.services.k8s.aws/v1alpha1` |
| CloudWatch | cloudwatch-controller | 1.4.3 | 3 | `cloudwatch.services.k8s.aws/v1alpha1` |
| CloudWatch Logs | cloudwatchlogs-controller | 1.2.4 | 1 | `cloudwatchlogs.services.k8s.aws/v1alpha1` |
| Route53 | route53-controller | 1.3.3 | 3 | `route53.services.k8s.aws/v1alpha1` |
| ACM | acm-controller | 1.3.7 | 1 | `acm.services.k8s.aws/v1alpha1` |
| EFS | efs-controller | 1.2.3 | 3 | `efs.services.k8s.aws/v1alpha1` |
| MSK (Kafka) | kafka-controller | 1.5.1 | 3 | `kafka.services.k8s.aws/v1alpha1` |
| OpenSearch | opensearchservice-controller | 1.3.0 | 1 | `opensearchservice.services.k8s.aws/v1alpha1` |
| WAFv2 | wafv2-controller | 1.3.2 | 3 | `wafv2.services.k8s.aws/v1alpha1` |
| Cognito | cognitoidentityprovider-controller | 1.2.3 | 1 | `cognitoidentityprovider.services.k8s.aws/v1alpha1` |
| SES | ses-controller | 1.2.3 | 1 | `ses.services.k8s.aws/v1alpha1` |
| Kinesis | kinesis-controller | 1.2.4 | 1 | `kinesis.services.k8s.aws/v1alpha1` |
| MQ | mq-controller | 1.2.3 | 1 | `mq.services.k8s.aws/v1alpha1` |

## Key CRDs per Controller (Most Used)

### S3 (s3.services.k8s.aws/v1alpha1)
- `Bucket`

### IAM (iam.services.k8s.aws/v1alpha1)
- `Role`
- `Policy`
- `Group`
- `User`
- `InstanceProfile`
- `OpenIDConnectProvider`
- `ServiceLinkedRole`

### EC2 (ec2.services.k8s.aws/v1alpha1)
- `VPC`
- `Subnet`
- `SecurityGroup`
- `InternetGateway`
- `NATGateway`
- `RouteTable`
- `ElasticIPAddress`
- `Instance`
- `LaunchTemplate`
- `VPCEndpoint`
- `TransitGateway`
- `FlowLog`
- `DHCPOptions`
- `NetworkInterface`
- `VPCPeeringConnection`

### EKS (eks.services.k8s.aws/v1alpha1)
- `Cluster`
- `Nodegroup`
- `FargateProfile`
- `Addon`
- `PodIdentityAssociation`
- `AccessEntry`
- `IdentityProviderConfig`

### RDS (rds.services.k8s.aws/v1alpha1)
- `DBInstance`
- `DBCluster`
- `DBSubnetGroup`
- `DBParameterGroup`
- `DBClusterParameterGroup`
- `GlobalCluster`
- `DBProxy`

### DynamoDB (dynamodb.services.k8s.aws/v1alpha1)
- `Table`
- `Backup`
- `GlobalTable`

### Lambda (lambda.services.k8s.aws/v1alpha1)
- `Function`
- `Alias`
- `CodeSigningConfig`
- `EventSourceMapping`
- `FunctionURLConfig`
- `LayerVersion`
- `Version`

## Helm Installation Pattern

```bash
# Install controller
helm install ack-<service>-controller \
  oci://public.ecr.aws/aws-controllers-k8s/<service>-chart \
  --version=<version> \
  --namespace ack-system \
  --create-namespace
```

## Important Notes

- **All controllers are now GA** for the major services (S3, IAM, EC2, EKS, RDS, Lambda, CloudFront, API Gateway)
- Previous skill versions incorrectly listed Lambda, CloudFront, API Gateway as Alpha/unsupported — they are now GA
- Each controller is independently released and versioned
- One controller per AWS service — install only what you need
- CRD naming pattern: `<kind-plural>.<service>.services.k8s.aws`
