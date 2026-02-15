# IAM Role Analysis: EKS Capability Module vs AWS Documentation

## Summary

After reviewing the AWS documentation for EKS Capability IAM roles, here's what we found:

## ✅ What the Terraform EKS Capability Module Handles Automatically

The `terraform-aws-modules/eks/aws//modules/capability` module **DOES** handle:

1. **IAM Role Creation** - Creates the capability IAM role
2. **Trust Policy** - Automatically configures the trust relationship with `capabilities.eks.amazonaws.com`
3. **IAM Policy Attachment** - Attaches policies you specify via `iam_role_policies` or `iam_policy_statements`
4. **Access Entry Creation** - Automatically creates EKS access entries for the role
5. **Service Account Setup** - AWS manages the Kubernetes ServiceAccount

## 📋 Required Trust Policy (Handled by Module)

According to AWS docs, the trust policy must be:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "capabilities.eks.amazonaws.com"
      },
      "Action": [
        "sts:AssumeRole",
        "sts:TagSession"
      ]
    }
  ]
}
```

**Status**: ✅ The Terraform module handles this automatically via `aws_service_principal.capabilities_eks` data source.

## ⚠️ What YOU Need to Provide

### 1. IAM Permissions for ACK

Your current code uses AWS managed policies (e.g., `IAMFullAccess`, `EC2FullAccess`). This works but is **NOT recommended for production**.

**Current Code (Dev/Test OK, Production NOT OK)**:
```terraform
iam_role_policies = {
  IAMFullAccess      = "arn:aws:iam::aws:policy/IAMFullAccess"
  EC2FullAccess      = "arn:aws:iam::aws:policy/AmazonEC2FullAccess"
  # ... more *FullAccess policies
}
```

**AWS Recommendation**: Use least-privilege policies scoped to specific resources.

### 2. IAM Permissions for ArgoCD

Your current code only has ECR permissions. According to AWS docs, you may also need:

- **Secrets Manager** (if ArgoCD reads secrets)
- **CodeConnections** (if using AWS CodeConnections for Git repos)

**Current Code**:
```terraform
iam_policy_statements = {
  ECRRead = {
    actions = ["ecr:GetAuthorizationToken", "ecr:BatchGetImage", ...]
    resources = ["*"]
  }
}
```

**Missing** (if you use these features):
- Secrets Manager permissions
- CodeConnections permissions

### 3. IAM Permissions for KRO

Your current code has NO IAM permissions for KRO. This is OK if your KRO ResourceGroups don't interact with AWS APIs.

**Current Code**:
```terraform
# KRO doesn't require additional IAM permissions by default
# Add custom policies if your KRO resources need AWS API access
```

**Add permissions if**: Your KRO resources need to call AWS APIs (e.g., CloudFormation, S3, etc.)

## 🔍 Verification Checklist

Before running `terraform apply`, verify:

- [ ] **Trust Policy**: Module handles this ✅
- [ ] **ACK Permissions**: Are you OK with `*FullAccess` policies? (Dev/Test: Yes, Production: No)
- [ ] **ArgoCD Permissions**: Do you need Secrets Manager or CodeConnections? (Add if yes)
- [ ] **KRO Permissions**: Do your KRO resources call AWS APIs? (Add if yes)
- [ ] **EKS Cluster Version**: >= 1.32 required
- [ ] **Terraform Module Version**: >= 21.15 required

## 🎯 Current Code Status

### For Development/Testing
**Status**: ✅ **READY TO USE**

Your current code in `addons.tf` is ready for dev/test environments. The module will:
1. Create IAM roles with correct trust policies
2. Attach the AWS managed policies you specified
3. Create EKS access entries
4. Deploy the capabilities

### For Production
**Status**: ⚠️ **NEEDS IMPROVEMENT**

You should:
1. Replace `*FullAccess` policies with least-privilege policies
2. Add Secrets Manager/CodeConnections permissions to ArgoCD (if needed)
3. Add AWS API permissions to KRO (if needed)
4. Consider using the `addons-production.tf.example` template

## 📝 Recommended Actions

### Option 1: Use Current Code (Dev/Test)
```bash
# Your current addons.tf is ready
terraform init -upgrade
terraform plan
terraform apply
```

### Option 2: Enhance for Production
```bash
# Use the production template
cp addons-production.tf.example addons-production.tf
# Edit addons-production.tf to customize IAM policies
# Then apply
terraform init -upgrade
terraform plan -var-file=production.tfvars
terraform apply
```

## 🔗 References

- [AWS EKS Capability IAM Role Documentation](https://docs.aws.amazon.com/eks/latest/userguide/capability-role.html)
- [Terraform EKS Module - Capability Submodule](https://registry.terraform.io/modules/terraform-aws-modules/eks/aws/latest/submodules/capability)
- [ACK Permissions Documentation](https://docs.aws.amazon.com/eks/latest/userguide/ack-permissions.html)

## ✅ Final Answer

**YES, your code is ready** for dev/test environments. The Terraform EKS capability module handles all the IAM role creation and trust policy setup automatically. You just need to provide the permissions (which you've done via `iam_role_policies`).

For production, consider using more restrictive IAM policies as shown in `addons-production.tf.example`.
