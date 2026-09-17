# ACK Controller Permissions — Migration Guidance

## Overview

ACK controllers run with an IAM role (the "ACK capability role") that needs permissions for each AWS service it manages. The skill cannot modify this role — it lives outside the migration scope (typically provisioned via CDK, Terraform, or console).

**The skill's job:** Alert users in MIGRATION-NOTES.md about which permissions the ACK capability role needs for the specific resources being adopted, so they can verify/add them before applying.

## Minimum Permissions by Service (for Adoption)

When adopting existing resources, ACK needs **read** access to describe the resource and sync its state. For ongoing management after adoption, it also needs **write** access.

### S3
- **Adoption (read):** `s3:GetBucket*`, `s3:ListBucket`
- **Management (write):** `s3:CreateBucket`, `s3:DeleteBucket`, `s3:PutBucket*`
- **Common managed policy:** `AmazonS3FullAccess`

### IAM
- **Adoption (read):** `iam:GetRole`, `iam:GetPolicy`, `iam:GetPolicyVersion`, `iam:ListRoleTags`, `iam:ListAttachedRolePolicies`, `iam:ListRolePolicies`, `iam:ListPolicyTags`, `iam:ListPolicyVersions`
- **Management (write):** `iam:CreateRole`, `iam:UpdateRole`, `iam:DeleteRole`, `iam:AttachRolePolicy`, `iam:DetachRolePolicy`, `iam:CreatePolicy`, `iam:DeletePolicy`, `iam:CreatePolicyVersion`, `iam:TagRole`, `iam:TagPolicy`
- **Common managed policy:** `IAMFullAccess`

### EKS (PodIdentityAssociation, Addon, Nodegroup, AccessEntry)
- **Adoption (read):** `eks:DescribePodIdentityAssociation`, `eks:ListPodIdentityAssociations`, `eks:DescribeAddon`, `eks:DescribeNodegroup`, `eks:DescribeAccessEntry`, `eks:ListTagsForResource`
- **Management (write):** `eks:CreatePodIdentityAssociation`, `eks:UpdatePodIdentityAssociation`, `eks:DeletePodIdentityAssociation`, `eks:CreateAddon`, `eks:UpdateAddon`, `eks:DeleteAddon`
- **Common managed policy:** None covers all EKS actions — requires custom policy for self-managed deployments
- **ℹ️ Note:** No AWS managed policy includes `eks:DescribePodIdentityAssociation`. For **self-managed ACK controllers**, likely user will have to add it explicitly to the controller's IAM role.

### EC2 (VPC, Subnet, SecurityGroup, etc.)
- **Adoption (read):** `ec2:DescribeVpcs`, `ec2:DescribeSubnets`, `ec2:DescribeSecurityGroups`, `ec2:DescribeRouteTables`, `ec2:DescribeInternetGateways`, `ec2:DescribeNatGateways`, `ec2:DescribeTags`
- **Management (write):** `ec2:Create*`, `ec2:Delete*`, `ec2:Modify*`, `ec2:AuthorizeSecurityGroup*`, `ec2:RevokeSecurityGroup*`
- **Common managed policy:** `AmazonEC2FullAccess`

### RDS
- **Adoption (read):** `rds:DescribeDBInstances`, `rds:DescribeDBClusters`, `rds:DescribeDBSubnetGroups`, `rds:ListTagsForResource`
- **Management (write):** `rds:CreateDBInstance`, `rds:ModifyDBInstance`, `rds:DeleteDBInstance`, `rds:CreateDBCluster`, `rds:ModifyDBCluster`, `rds:DeleteDBCluster`
- **Common managed policy:** `AmazonRDSFullAccess`

### DynamoDB
- **Adoption (read):** `dynamodb:DescribeTable`, `dynamodb:ListTagsOfResource`, `dynamodb:DescribeContinuousBackups`, `dynamodb:DescribeTimeToLive`
- **Management (write):** `dynamodb:CreateTable`, `dynamodb:UpdateTable`, `dynamodb:DeleteTable`, `dynamodb:TagResource`
- **Common managed policy:** `AmazonDynamoDBFullAccess`

### SQS
- **Adoption (read):** `sqs:GetQueueAttributes`, `sqs:GetQueueUrl`, `sqs:ListQueueTags`
- **Management (write):** `sqs:CreateQueue`, `sqs:DeleteQueue`, `sqs:SetQueueAttributes`, `sqs:TagQueue`
- **Common managed policy:** `AmazonSQSFullAccess`

### SNS
- **Adoption (read):** `sns:GetTopicAttributes`, `sns:ListTagsForResource`
- **Management (write):** `sns:CreateTopic`, `sns:DeleteTopic`, `sns:SetTopicAttributes`, `sns:TagResource`
- **Common managed policy:** `AmazonSNSFullAccess`

### Lambda
- **Adoption (read):** `lambda:GetFunction`, `lambda:ListTags`, `lambda:GetFunctionCodeSigningConfig`
- **Management (write):** `lambda:CreateFunction`, `lambda:UpdateFunctionCode`, `lambda:UpdateFunctionConfiguration`, `lambda:DeleteFunction`, `lambda:TagResource`
- **Common managed policy:** `AWSLambda_FullAccess`

### ECR
- **Adoption (read):** `ecr:DescribeRepositories`, `ecr:ListTagsForResource`
- **Management (write):** `ecr:CreateRepository`, `ecr:DeleteRepository`, `ecr:PutLifecyclePolicy`, `ecr:TagResource`
- **Common managed policy:** `AmazonECRFullAccess`

## For MIGRATION-NOTES.md Generation

The skill should include a section like:

```markdown
## ACK Capability Role — Required Permissions

The ACK capability role needs permissions for the following services
used in this migration:

| Service | Actions Needed | Covered by Managed Policy |
|---------|---------------|--------------------------|
| S3      | GetBucket*, ListBucket | AmazonS3FullAccess ✅ |
| IAM     | GetRole, GetPolicy, ... | IAMFullAccess ✅ |
| EKS     | DescribePodIdentityAssociation | ⚠️ No managed policy (self-managed only) |

> **ℹ️ Note (self-managed deployments only):** If running self-managed ACK controllers,
> ensure the controller IAM role includes the EKS permissions above.


**How to diagnose missing permissions after applying:**
    kubectl describe <kind> <name> | grep -A3 "ACK.Recoverable"
Look for `AccessDeniedException` — it names the exact missing action.
```

**Rules:**
1. List only the services involved in this specific migration
2. Note services where no managed policy covers the needed actions — as informational, relevant specially for self-managed ACK deployments. 
3. Do NOT generate IAM policy JSON — the user manages their role externally
4. Include the diagnosis command for post-apply troubleshooting
