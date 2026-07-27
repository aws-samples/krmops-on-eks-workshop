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

# Secrets Store CSI Driver + AWS provider are assumed installed as part of
# Module I bootstrap. This SecretProviderClass mounts the RDS credentials.
resource "kubernetes_manifest" "secretproviderclass" {
  manifest = {
    apiVersion = "secrets-store.csi.x-k8s.io/v1"
    kind       = "SecretProviderClass"
    metadata = {
      name      = "webapp-rds-creds"
      namespace = kubernetes_namespace.webapp.metadata[0].name
    }
    spec = {
      provider = "aws"
      parameters = {
        objects = jsonencode([{
          objectName = aws_secretsmanager_secret.db.arn
          objectType = "secretsmanager"
          jmesPath = [
            { path = "username", objectAlias = "DB_USER" },
            { path = "password", objectAlias = "DB_PASS" },
            { path = "host",     objectAlias = "DB_HOST" },
            { path = "port",     objectAlias = "DB_PORT" },
            { path = "dbname",   objectAlias = "DB_NAME" },
          ]
        }])
      }
    }
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
          image = "public.ecr.aws/data-on-eks/spark:4.0.1-scala2.13-java21-python3-r-ubuntu"
          command = ["/bin/sh", "-c", "while true; do echo \"DB_HOST=$DB_HOST DB_NAME=$DB_NAME\"; sleep 30; done"]

          env {
            name = "DB_HOST"
            value_from {
              secret_key_ref {
                name = "webapp-rds-creds"
                key  = "DB_HOST"
              }
            }
          }
          env {
            name = "DB_USER"
            value_from {
              secret_key_ref {
                name = "webapp-rds-creds"
                key  = "DB_USER"
              }
            }
          }
          env {
            name = "DB_PASS"
            value_from {
              secret_key_ref {
                name = "webapp-rds-creds"
                key  = "DB_PASS"
              }
            }
          }
          env {
            name  = "DB_PORT"
            value = "5432"
          }
          env {
            name  = "DB_NAME"
            value = var.db_name
          }

          volume_mount {
            name       = "secrets-store"
            mount_path = "/mnt/secrets-store"
            read_only  = true
          }
        }
        volume {
          name = "secrets-store"
          csi {
            driver    = "secrets-store.csi.k8s.io"
            read_only = true
            volume_attributes = {
              secretProviderClass = "webapp-rds-creds"
            }
          }
        }
      }
    }
  }
  depends_on = [
    kubernetes_manifest.secretproviderclass,
    aws_eks_pod_identity_association.webapp,
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
      port        = 80
      target_port = 8080
    }
    type = "ClusterIP"
  }
}
