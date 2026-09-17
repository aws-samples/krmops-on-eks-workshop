variable "region" {
  type        = string
  description = "AWS region"
  default     = "us-west-2"
}

variable "cluster_name" {
  type        = string
  description = "Workshop EKS cluster name (from Module I bootstrap)"
}

variable "workshop_id" {
  type        = string
  description = "Unique per-attendee identifier used to suffix resource names"
}

variable "vpc_id" {
  type        = string
  description = "VPC that hosts the EKS cluster — RDS goes into the same VPC"
}

variable "private_subnet_ids" {
  type        = list(string)
  description = "Two or more private subnet IDs (different AZs) for the RDS subnet group"
}

variable "db_name" {
  type        = string
  description = "Initial database name"
  default     = "webappdb"
}

variable "db_username" {
  type        = string
  description = "RDS master username"
  default     = "webapp"
}

variable "namespace" {
  type        = string
  description = "Kubernetes namespace for the workload"
  default     = "webapp-rds"
}
