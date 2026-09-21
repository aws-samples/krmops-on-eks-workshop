#---------------------------------------------------------------
# EKS Cluster with Auto Mode
#---------------------------------------------------------------
module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 21.15"

  name               = local.name
  kubernetes_version = var.cluster_version

  # Auto mode configuration
  compute_config = {
    enabled    = true
    node_pools = ["general-purpose"]
  }
  
  # Give the Terraform identity admin access to the cluster
  enable_cluster_creator_admin_permissions = true

  # VPC configuration
  vpc_id                  = module.vpc.vpc_id
  subnet_ids              = module.vpc.private_subnets
  endpoint_public_access  = true
  endpoint_private_access = true

  tags = local.tags
}

resource "kubectl_manifest" "configmap" {
  yaml_body = templatefile("${path.module}/configmap.yaml", {
    awsAccountID = data.aws_caller_identity.current.account_id
    eksOIDC      = module.eks.oidc_provider
    vpcID        = module.vpc.vpc_id
    subnetIDs    = join(",", module.vpc.private_subnets)
    clusterName  = module.eks.cluster_name
    region       = local.region
  })

  depends_on = [module.eks]
}
