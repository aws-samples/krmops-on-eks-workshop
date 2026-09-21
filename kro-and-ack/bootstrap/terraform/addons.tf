#---------------------------------------------------------------
# EKS Addons (Upgraded to use EKS Capabilities)
#---------------------------------------------------------------

module "eks_blueprints_addons" {
  source  = "aws-ia/eks-blueprints-addons/aws"
  version = "~> 1.21"

  cluster_name      = module.eks.cluster_name
  cluster_endpoint  = module.eks.cluster_endpoint
  cluster_version   = module.eks.cluster_version
  oidc_provider_arn = module.eks.oidc_provider_arn

  # Note: ArgoCD is now managed via EKS Capability below
  # enable_argocd = false

  enable_metrics_server                            = true
  enable_external_secrets                          = true
  enable_external_dns                              = true
  external_dns_route53_zone_arns                   = ["arn:aws:route53:::hostedzone/Z07589007ZVX1K0A3C82"]
  
  # Required for RDS ResourceGraphDefinition to use SecretProviderClass
  enable_secrets_store_csi_driver              = true
  enable_secrets_store_csi_driver_provider_aws = true
  
  # Use latest version that supports EKS Pod Identity
  secrets_store_csi_driver_provider_aws = {
    chart_version = "0.3.11"
  }

  depends_on = [module.eks.cluster_addons]
}

################################################################################
# ArgoCD - Using EKS Capability
# NOTE: ArgoCD capability requires AWS IAM Identity Center (SSO) configuration
# Uncomment and configure when you have IAM Identity Center set up
################################################################################
# module "argocd_capability" {
#   source = "terraform-aws-modules/eks/aws//modules/capability"
#   version = "~> 21.15"
#
#   name         = "${local.name}-argocd"
#   cluster_name = module.eks.cluster_name
#   type         = "ARGOCD"
#
#   # ArgoCD configuration with AWS IAM Identity Center
#   configuration = {
#     argo_cd = {
#       aws_idc = {
#         idc_instance_arn = "arn:aws:sso:::instance/YOUR-SSO-INSTANCE-ID"
#       }
#       namespace = "argocd"
#     }
#   }
#
#   # IAM Role/Policy for ArgoCD
#   iam_policy_statements = {
#     ECRRead = {
#       actions = [
#         "ecr:GetAuthorizationToken",
#         "ecr:BatchCheckLayerAvailability",
#         "ecr:GetDownloadUrlForLayer",
#         "ecr:BatchGetImage",
#       ]
#       resources = ["*"]
#     }
#   }
#
#   tags = local.tags
#
#   depends_on = [module.eks]
# }

################################################################################
# ACK - Using EKS Capability
################################################################################
module "ack_capability" {
  source = "terraform-aws-modules/eks/aws//modules/capability"
  version = "~> 21.15"

  name         = "${local.name}-ack"
  cluster_name = module.eks.cluster_name
  type         = "ACK"

  # IAM Role/Policy for ACK Controllers
  # For POC/dev: giving admin access to manage all AWS resources
  iam_role_policies = {
    AdminAccess = "arn:aws:iam::aws:policy/AdministratorAccess"
  }

  tags = local.tags

  depends_on = [module.eks]
}

################################################################################
# KRO - Using EKS Capability
################################################################################
module "kro_capability" {
  source = "terraform-aws-modules/eks/aws//modules/capability"
  version = "~> 21.15"

  name         = "${local.name}-kro"
  cluster_name = module.eks.cluster_name
  type         = "KRO"

  # KRO needs permissions to manage Kubernetes resources and potentially AWS resources
  # For POC/dev: giving admin access
  iam_role_policies = {
    AdminAccess = "arn:aws:iam::aws:policy/AdministratorAccess"
  }

  tags = local.tags

  depends_on = [module.eks]
}

# Grant KRO additional Kubernetes permissions to create resources defined in RGDs
# The capability module creates an access entry with AmazonEKSKROPolicy by default,
# but KRO needs additional permissions to manage arbitrary Kubernetes resources
resource "aws_eks_access_policy_association" "kro_cluster_admin" {
  cluster_name  = module.eks.cluster_name
  principal_arn = module.kro_capability.iam_role_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [module.kro_capability]
}

################################################################################
# Outputs for the new capabilities
################################################################################
# output "argocd_capability_arn" {
#   description = "ARN of the ArgoCD capability"
#   value       = module.argocd_capability.arn
# }

# output "argocd_server_url" {
#   description = "URL of the ArgoCD server"
#   value       = module.argocd_capability.argocd_server_url
# }

output "ack_capability_arn" {
  description = "ARN of the ACK capability"
  value       = module.ack_capability.arn
}

output "kro_capability_arn" {
  description = "ARN of the KRO capability"
  value       = module.kro_capability.arn
}
