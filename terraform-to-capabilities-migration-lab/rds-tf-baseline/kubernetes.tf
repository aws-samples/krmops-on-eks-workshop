resource "kubernetes_namespace" "webapp" {
  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/part-of" = "krmops-workshop"
    }
  }
}

resource "kubernetes_service_account" "webapp" {
  metadata {
    name      = "webapp-sa"
    namespace = kubernetes_namespace.webapp.metadata[0].name
  }
}

# --- RDS credentials as a native Kubernetes Secret --------------------------
# The RDS credentials are delivered to the workload as a standard Kubernetes
# Secret sourced directly from the Terraform-managed RDS instance and generated
# password. The same material is also stored in AWS Secrets Manager (main.tf),
# so both the in-cluster consumer and the AWS-native secret exist as migration
# targets. This avoids the Secrets Store CSI driver + credential-vending path,
# which is not available on EKS Auto Mode nodes (the pod-identity-agent
# DaemonSet does not run on `compute-type=auto` nodes).
resource "kubernetes_secret" "webapp_rds_creds" {
  metadata {
    name      = "webapp-rds-creds"
    namespace = kubernetes_namespace.webapp.metadata[0].name
  }
  type = "Opaque"
  data = {
    DB_HOST = aws_db_instance.workshop.address
    DB_PORT = tostring(aws_db_instance.workshop.port)
    DB_NAME = var.db_name
    DB_USER = var.db_username
    DB_PASS = random_password.db.result
  }
}

resource "kubernetes_deployment" "webapp" {
  metadata {
    name      = "webapp"
    namespace = kubernetes_namespace.webapp.metadata[0].name
    labels    = { app = "webapp" }
  }
  spec {
    replicas = 1
    selector { match_labels = { app = "webapp" } }
    template {
      metadata { labels = { app = "webapp" } }
      spec {
        service_account_name = kubernetes_service_account.webapp.metadata[0].name
        container {
          name  = "webapp"
          image = "public.ecr.aws/docker/library/postgres:16-alpine"

          # Demo workload: continuously connect to the Terraform-managed RDS
          # instance using the credentials injected from the Kubernetes Secret,
          # proving the estate is live and correctly wired.
          command = ["/bin/sh", "-c"]
          args = [
            "echo 'webapp starting - target $DB_HOST:$DB_PORT/$DB_NAME'; while true; do if PGPASSWORD=\"$DB_PASS\" psql -h \"$DB_HOST\" -p \"$DB_PORT\" -U \"$DB_USER\" -d \"$DB_NAME\" -tAc 'SELECT 1' >/dev/null 2>&1; then echo \"$(date -u) OK: connected to RDS $DB_HOST:$DB_PORT/$DB_NAME\"; else echo \"$(date -u) WAITING: RDS $DB_HOST:$DB_PORT not reachable yet\"; fi; sleep 30; done"
          ]

          env {
            name = "DB_HOST"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.webapp_rds_creds.metadata[0].name
                key  = "DB_HOST"
              }
            }
          }
          env {
            name = "DB_PORT"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.webapp_rds_creds.metadata[0].name
                key  = "DB_PORT"
              }
            }
          }
          env {
            name = "DB_NAME"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.webapp_rds_creds.metadata[0].name
                key  = "DB_NAME"
              }
            }
          }
          env {
            name = "DB_USER"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.webapp_rds_creds.metadata[0].name
                key  = "DB_USER"
              }
            }
          }
          env {
            name = "DB_PASS"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.webapp_rds_creds.metadata[0].name
                key  = "DB_PASS"
              }
            }
          }

          # Pod is Ready only once it can actually reach the RDS database, so a
          # successful `terraform apply` rollout means the estate is fully live.
          readiness_probe {
            exec {
              command = ["/bin/sh", "-c", "PGPASSWORD=\"$DB_PASS\" psql -h \"$DB_HOST\" -p \"$DB_PORT\" -U \"$DB_USER\" -d \"$DB_NAME\" -tAc 'SELECT 1'"]
            }
            initial_delay_seconds = 15
            period_seconds        = 15
            timeout_seconds       = 10
            failure_threshold     = 20
          }
        }
      }
    }
  }

  depends_on = [
    kubernetes_secret.webapp_rds_creds,
  ]
}

resource "kubernetes_service" "webapp" {
  metadata {
    name      = "webapp"
    namespace = kubernetes_namespace.webapp.metadata[0].name
  }
  spec {
    selector = { app = "webapp" }
    port {
      port        = 5432
      target_port = 5432
    }
    type = "ClusterIP"
  }
}
