terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.32"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # For multi-attendee workshops switch to an S3 backend:
  # backend "s3" {
  #   bucket = "krmops-workshop-tf-state"
  #   key    = "workshop-tf-baseline/${var.workshop_id}/terraform.tfstate"
  #   region = "us-west-2"
  # }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Workshop   = "krmops-on-eks"
      Module     = "workshop-tf-baseline"
      WorkshopID = var.workshop_id
      ManagedBy  = "Terraform"
    }
  }
}

data "aws_eks_cluster" "workshop" {
  name = var.cluster_name
}

data "aws_eks_cluster_auth" "workshop" {
  name = var.cluster_name
}

provider "kubernetes" {
  host                   = data.aws_eks_cluster.workshop.endpoint
  cluster_ca_certificate = base64decode(data.aws_eks_cluster.workshop.certificate_authority[0].data)
  token                  = data.aws_eks_cluster_auth.workshop.token
}
