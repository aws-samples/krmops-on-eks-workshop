output "rds_arn" {
  value       = aws_db_instance.workshop.arn
  description = "ARN of the workshop RDS instance"
}

output "rds_endpoint" {
  value       = aws_db_instance.workshop.address
  description = "RDS endpoint hostname"
}

output "rds_port" {
  value       = aws_db_instance.workshop.port
  description = "RDS port"
}

output "secret_arn" {
  value       = aws_secretsmanager_secret.db.arn
  description = "Secrets Manager ARN holding RDS credentials"
}

output "webapp_role_arn" {
  value       = aws_iam_role.webapp.arn
  description = "IAM role assumed by the web app pod via Pod Identity"
}

output "pod_identity_association_id" {
  value       = aws_eks_pod_identity_association.webapp.association_id
  description = "EKS Pod Identity association ID"
}

output "namespace" {
  value       = kubernetes_namespace.webapp.metadata[0].name
  description = "Kubernetes namespace hosting the workload"
}
