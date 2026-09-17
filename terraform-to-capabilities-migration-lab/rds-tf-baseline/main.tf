locals {
  name_prefix = "krmops-${var.workshop_id}"
}

# --- Password generation ----------------------------------------------------

resource "random_password" "db" {
  length           = 24
  special          = true
  override_special = "!#%*_-+="
}

# --- Secrets Manager --------------------------------------------------------

resource "aws_secretsmanager_secret" "db" {
  name        = "${local.name_prefix}-rds-credentials"
  description = "RDS master credentials for workshop web app"
  # Workshop-only — allow immediate delete on destroy
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({
    username = var.db_username
    password = random_password.db.result
    engine   = "postgres"
    host     = aws_db_instance.workshop.address
    port     = aws_db_instance.workshop.port
    dbname   = var.db_name
  })
}

# --- RDS networking ---------------------------------------------------------

resource "aws_db_subnet_group" "workshop" {
  name       = "${local.name_prefix}-rds"
  subnet_ids = var.private_subnet_ids
}

resource "aws_security_group" "rds" {
  name        = "${local.name_prefix}-rds-sg"
  description = "Allow PostgreSQL from the workshop EKS pods"
  vpc_id      = var.vpc_id
}

resource "aws_security_group_rule" "rds_ingress_from_cluster" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = data.aws_eks_cluster.workshop.vpc_config[0].cluster_security_group_id
  security_group_id        = aws_security_group.rds.id
  description              = "PostgreSQL from EKS cluster SG"
}

resource "aws_security_group_rule" "rds_egress_all" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.rds.id
}

# --- RDS instance -----------------------------------------------------------

resource "aws_db_instance" "workshop" {
  identifier              = "${local.name_prefix}-rds"
  engine                  = "postgres"
  engine_version          = "16.14"
  instance_class          = "db.t4g.micro"
  allocated_storage       = 20
  storage_type            = "gp3"
  storage_encrypted       = true

  db_name                 = var.db_name
  username                = var.db_username
  password                = random_password.db.result

  db_subnet_group_name    = aws_db_subnet_group.workshop.name
  vpc_security_group_ids  = [aws_security_group.rds.id]
  publicly_accessible     = false

  # Workshop settings — DO NOT use in production
  skip_final_snapshot     = true
  deletion_protection     = false
  backup_retention_period = 0
  apply_immediately       = true

  tags = {
    Name = "${local.name_prefix}-rds"
  }
}

# --- IAM role for the web app pod (via EKS Pod Identity) --------------------

data "aws_iam_policy_document" "pod_trust" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
    actions = [
      "sts:AssumeRole",
      "sts:TagSession",
    ]
  }
}

resource "aws_iam_role" "webapp" {
  name               = "${local.name_prefix}-webapp-role"
  assume_role_policy = data.aws_iam_policy_document.pod_trust.json
}

data "aws_iam_policy_document" "webapp" {
  statement {
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = [aws_secretsmanager_secret.db.arn]
  }
}

resource "aws_iam_policy" "webapp" {
  name   = "${local.name_prefix}-webapp-policy"
  policy = data.aws_iam_policy_document.webapp.json
}

resource "aws_iam_role_policy_attachment" "webapp" {
  role       = aws_iam_role.webapp.name
  policy_arn = aws_iam_policy.webapp.arn
}

# --- EKS Pod Identity Association ------------------------------------------

resource "aws_eks_pod_identity_association" "webapp" {
  cluster_name    = var.cluster_name
  namespace       = kubernetes_namespace.webapp.metadata[0].name
  service_account = kubernetes_service_account.webapp.metadata[0].name
  role_arn        = aws_iam_role.webapp.arn
}
