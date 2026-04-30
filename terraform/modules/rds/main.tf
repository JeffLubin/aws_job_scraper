data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_rds_engine_version" "postgres" {
  engine  = "postgres"
  version = "16"
  latest  = true
}

resource "random_password" "db" {
  length           = 32
  special          = true
  override_special = "!#$%*()-_+[]{}"
}

resource "aws_db_subnet_group" "this" {
  name       = "${var.project_name}-db-subnet-group"
  subnet_ids = var.private_subnet_ids

  tags = {
    Name = "${var.project_name}-db-subnet-group"
  }
}

resource "aws_db_instance" "this" {
  identifier                 = "${var.project_name}-postgres"
  db_name                    = var.db_name
  username                   = var.db_username
  password                   = random_password.db.result
  engine                     = "postgres"
  engine_version             = data.aws_rds_engine_version.postgres.version
  instance_class             = "db.t4g.micro"
  allocated_storage          = 20
  storage_type               = "gp3"
  storage_encrypted          = true
  multi_az                   = false
  publicly_accessible        = false
  backup_retention_period    = 7
  skip_final_snapshot        = true
  auto_minor_version_upgrade = true
  deletion_protection        = false
  db_subnet_group_name       = aws_db_subnet_group.this.name
  vpc_security_group_ids     = [var.rds_security_group_id]

  tags = {
    Name = "${var.project_name}-postgres"
  }
}

resource "aws_secretsmanager_secret" "db" {
  name                    = "${var.project_name}/rds/postgres"
  recovery_window_in_days = 0

  tags = {
    Name = "${var.project_name}-postgres-secret"
  }
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id
  secret_string = jsonencode({
    username = var.db_username
    password = random_password.db.result
    host     = aws_db_instance.this.address
    port     = aws_db_instance.this.port
    dbname   = var.db_name
    engine   = "postgres"
  })
}

resource "aws_security_group" "rotation_lambda" {
  name        = "${var.project_name}-rds-rotation-lambda-sg"
  description = "RDS password rotation Lambda networking"
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.project_name}-rds-rotation-lambda-sg"
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group_rule" "rds_from_rotation_lambda" {
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  security_group_id        = var.rds_security_group_id
  source_security_group_id = aws_security_group.rotation_lambda.id
}

resource "aws_security_group_rule" "endpoints_from_rotation_lambda" {
  type                     = "ingress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  security_group_id        = var.endpoints_security_group_id
  source_security_group_id = aws_security_group.rotation_lambda.id
}

data "aws_iam_policy_document" "rotation_lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "rotation_lambda" {
  name               = "${var.project_name}-rds-rotation-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.rotation_lambda_assume_role.json
}

resource "aws_iam_role_policy_attachment" "rotation_lambda_basic" {
  role       = aws_iam_role.rotation_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "rotation_lambda_vpc" {
  role       = aws_iam_role.rotation_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_iam_policy_document" "rotation_lambda" {
  statement {
    actions = [
      "secretsmanager:DescribeSecret",
      "secretsmanager:GetSecretValue",
      "secretsmanager:PutSecretValue",
      "secretsmanager:UpdateSecretVersionStage",
    ]
    resources = [aws_secretsmanager_secret.db.arn]
  }

  statement {
    actions   = ["secretsmanager:GetRandomPassword"]
    resources = ["*"]
  }

  statement {
    actions = [
      "rds:DescribeDBInstances",
      "rds:DescribeDBClusters",
    ]
    resources = ["*"]
  }

  statement {
    actions   = ["kms:Decrypt"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${data.aws_region.current.region}.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "kms:CallerAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role_policy" "rotation_lambda" {
  name   = "${var.project_name}-rds-rotation-lambda-policy"
  role   = aws_iam_role.rotation_lambda.id
  policy = data.aws_iam_policy_document.rotation_lambda.json
}

resource "aws_serverlessapplicationrepository_cloudformation_stack" "rotation_lambda" {
  name             = "${var.project_name}-rds-rotation-lambda"
  application_id   = "arn:aws:serverlessrepo:${data.aws_region.current.region}:297356227824:applications/SecretsManagerRDSPostgreSQLRotationSingleUser"
  semantic_version = "1.1.654"
  capabilities     = ["CAPABILITY_IAM", "CAPABILITY_RESOURCE_POLICY"]

  parameters = {
    endpoint            = "https://secretsmanager.${data.aws_region.current.region}.amazonaws.com"
    excludeCharacters   = "/@\"'"
    functionName        = "${var.project_name}-rds-rotation"
    roleArn             = aws_iam_role.rotation_lambda.arn
    vpcSecurityGroupIds = aws_security_group.rotation_lambda.id
    vpcSubnetIds        = join(",", var.private_subnet_ids)
  }

  depends_on = [
    aws_iam_role_policy.rotation_lambda,
    aws_iam_role_policy_attachment.rotation_lambda_basic,
    aws_iam_role_policy_attachment.rotation_lambda_vpc,
  ]
}

resource "aws_secretsmanager_secret_rotation" "db" {
  secret_id           = aws_secretsmanager_secret.db.id
  rotation_lambda_arn = aws_serverlessapplicationrepository_cloudformation_stack.rotation_lambda.outputs.RotationLambdaARN
  rotate_immediately  = false

  rotation_rules {
    automatically_after_days = 30
  }

  depends_on = [
    aws_db_instance.this,
    aws_secretsmanager_secret_version.db,
    aws_security_group_rule.rds_from_rotation_lambda,
  ]
}
