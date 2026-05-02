locals {
  scorer_name = "${var.project_name}-scorer"
}

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_ecr_repository" "scorer" {
  name                 = local.scorer_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "scorer" {
  repository = aws_ecr_repository.scorer.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

resource "aws_iam_role" "lambda" {
  name               = "${local.scorer_name}-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "lambda_vpc" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

data "aws_iam_policy_document" "lambda" {
  statement {
    actions = [
      "sqs:ReceiveMessage",
      "sqs:DeleteMessage",
      "sqs:GetQueueAttributes",
    ]
    resources = [var.score_queue_arn]
  }

  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.db_secret_arn, var.openai_secret_arn]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${local.scorer_name}-lambda-policy"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

resource "aws_cloudwatch_log_group" "scorer" {
  name              = "/aws/lambda/${local.scorer_name}"
  retention_in_days = 14
}

resource "aws_lambda_function" "scorer" {
  function_name                  = local.scorer_name
  package_type                   = "Image"
  image_uri                      = "${aws_ecr_repository.scorer.repository_url}:${var.image_tag}"
  role                           = aws_iam_role.lambda.arn
  timeout                        = 60
  memory_size                    = 512
  reserved_concurrent_executions = 5
  architectures                  = ["x86_64"]

  environment {
    variables = {
      DB_SECRET_ARN     = var.db_secret_arn
      OPENAI_SECRET_ARN = var.openai_secret_arn
      OPENAI_MODEL      = "gpt-4.1-nano"
      LOG_LEVEL         = "INFO"
    }
  }

  vpc_config {
    subnet_ids         = var.vpc_subnet_ids
    security_group_ids = [var.lambda_security_group_id]
  }

  depends_on = [
    aws_cloudwatch_log_group.scorer,
    aws_iam_role_policy.lambda,
    aws_iam_role_policy_attachment.lambda_basic,
    aws_iam_role_policy_attachment.lambda_vpc,
  ]
}

resource "aws_lambda_event_source_mapping" "score_queue" {
  event_source_arn = var.score_queue_arn
  function_name    = aws_lambda_function.scorer.arn
  batch_size       = 1
}
