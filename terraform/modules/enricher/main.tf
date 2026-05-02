locals {
  enricher_name = "${var.project_name}-enricher"
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

resource "aws_ecr_repository" "enricher" {
  name                 = local.enricher_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "enricher" {
  repository = aws_ecr_repository.enricher.name

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

resource "aws_sqs_queue" "score_dlq" {
  name                      = "${var.project_name}-score-dlq"
  message_retention_seconds = 345600
}

resource "aws_sqs_queue" "score" {
  name                       = "${var.project_name}-score-queue"
  visibility_timeout_seconds = 900
  message_retention_seconds  = 345600

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.score_dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_iam_role" "lambda" {
  name               = "${local.enricher_name}-lambda-role"
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
    resources = [var.enrich_queue_arn]
  }

  statement {
    actions = [
      "sqs:SendMessage",
    ]
    resources = [aws_sqs_queue.score.arn]
  }

  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.db_secret_arn]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${local.enricher_name}-lambda-policy"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

resource "aws_cloudwatch_log_group" "enricher" {
  name              = "/aws/lambda/${local.enricher_name}"
  retention_in_days = 14
}

resource "aws_lambda_function" "enricher" {
  function_name                  = local.enricher_name
  package_type                   = "Image"
  image_uri                      = "${aws_ecr_repository.enricher.repository_url}:${var.image_tag}"
  role                           = aws_iam_role.lambda.arn
  timeout                        = 300
  memory_size                    = 512
  reserved_concurrent_executions = 5
  architectures                  = ["x86_64"]

  environment {
    variables = {
      DB_SECRET_ARN   = var.db_secret_arn
      SCORE_QUEUE_URL = aws_sqs_queue.score.url
      LOG_LEVEL       = "INFO"
    }
  }

  vpc_config {
    subnet_ids         = var.vpc_subnet_ids
    security_group_ids = [var.lambda_security_group_id]
  }

  depends_on = [
    aws_cloudwatch_log_group.enricher,
    aws_iam_role_policy.lambda,
    aws_iam_role_policy_attachment.lambda_basic,
    aws_iam_role_policy_attachment.lambda_vpc,
  ]
}

resource "aws_lambda_event_source_mapping" "enrich_queue" {
  event_source_arn = var.enrich_queue_arn
  function_name    = aws_lambda_function.enricher.arn
  batch_size       = 1
}
