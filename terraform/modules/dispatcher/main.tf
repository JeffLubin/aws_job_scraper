locals {
  dispatcher_name = "${var.project_name}-dispatcher"
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

resource "aws_ecr_repository" "dispatcher" {
  name                 = local.dispatcher_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "dispatcher" {
  repository = aws_ecr_repository.dispatcher.name

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

resource "aws_sqs_queue" "dlq" {
  name                      = "${var.project_name}-scrape-dlq"
  message_retention_seconds = 345600
}

resource "aws_sqs_queue" "scrape" {
  name                       = "${var.project_name}-scrape-queue"
  visibility_timeout_seconds = 900
  message_retention_seconds  = 345600

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })
}

resource "aws_iam_role" "lambda" {
  name               = "${local.dispatcher_name}-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "lambda" {
  statement {
    actions = [
      "sqs:SendMessage",
      "sqs:SendMessageBatch",
    ]
    resources = [aws_sqs_queue.scrape.arn]
  }
}

resource "aws_iam_role_policy" "lambda" {
  name   = "${local.dispatcher_name}-lambda-policy"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.lambda.json
}

resource "aws_cloudwatch_log_group" "dispatcher" {
  name              = "/aws/lambda/${local.dispatcher_name}"
  retention_in_days = 14
}

resource "aws_lambda_function" "dispatcher" {
  function_name = local.dispatcher_name
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.dispatcher.repository_url}:${var.image_tag}"
  role          = aws_iam_role.lambda.arn
  timeout       = 60
  memory_size   = 128

  environment {
    variables = {
      SCRAPE_QUEUE_URL = aws_sqs_queue.scrape.url
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.dispatcher,
    aws_iam_role_policy.lambda,
    aws_iam_role_policy_attachment.lambda_basic,
  ]
}

resource "aws_cloudwatch_event_rule" "dispatcher" {
  name                = "${local.dispatcher_name}-schedule"
  description         = "Runs the dispatcher Lambda"
  schedule_expression = var.schedule_expression
  state               = "DISABLED"
}

resource "aws_cloudwatch_event_target" "dispatcher" {
  rule = aws_cloudwatch_event_rule.dispatcher.name
  arn  = aws_lambda_function.dispatcher.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dispatcher.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.dispatcher.arn
}
