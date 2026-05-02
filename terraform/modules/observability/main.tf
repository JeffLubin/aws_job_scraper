locals {
  name_prefix = var.project_name

  lambda_functions = {
    dispatcher = var.dispatcher_function_name
    scraper    = var.scraper_function_name
    enricher   = var.enricher_function_name
    scorer     = var.scorer_function_name
  }

  queues = {
    scrape = var.scrape_queue_name
    enrich = var.enrich_queue_name
    score  = var.score_queue_name
  }

  dlqs = {
    scrape = var.scrape_dlq_name
    enrich = var.enrich_dlq_name
    score  = var.score_dlq_name
  }
}

data "aws_region" "current" {}

# ─────────────────────────────────────────────
# SNS Topic + Email Subscription
# ─────────────────────────────────────────────

resource "aws_sns_topic" "alerts" {
  name = "${local.name_prefix}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# ─────────────────────────────────────────────
# DLQ Depth Alarms (3 DLQs)
# ─────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "dlq_depth" {
  for_each = local.dlqs

  alarm_name          = "${local.name_prefix}-${each.key}-dlq-depth"
  alarm_description   = "Messages appeared in ${each.value} dead-letter queue"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateNumberOfMessagesVisible"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = each.value
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# ─────────────────────────────────────────────
# Lambda Error Rate Alarms (4 functions)
# Math: Errors / Invocations * 100 > 5%
# ─────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "lambda_error_rate" {
  for_each = local.lambda_functions

  alarm_name          = "${local.name_prefix}-${each.key}-error-rate"
  alarm_description   = "Error rate exceeds 5% for ${each.value}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 5
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "error_rate"
    expression  = "IF(invocations > 0, errors / invocations * 100, 0)"
    label       = "Error Rate (%)"
    return_data = true
  }

  metric_query {
    id = "errors"

    metric {
      metric_name = "Errors"
      namespace   = "AWS/Lambda"
      period      = 300
      stat        = "Sum"

      dimensions = {
        FunctionName = each.value
      }
    }
  }

  metric_query {
    id = "invocations"

    metric {
      metric_name = "Invocations"
      namespace   = "AWS/Lambda"
      period      = 300
      stat        = "Sum"

      dimensions = {
        FunctionName = each.value
      }
    }
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# ─────────────────────────────────────────────
# Lambda Throttle Alarms (4 functions)
# Sustained throttles > 5 min (5 x 60s periods)
# ─────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  for_each = local.lambda_functions

  alarm_name          = "${local.name_prefix}-${each.key}-throttles"
  alarm_description   = "Sustained throttling (>5 min) for ${each.value}"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 5
  metric_name         = "Throttles"
  namespace           = "AWS/Lambda"
  period              = 60
  statistic           = "Sum"
  threshold           = 0
  treat_missing_data  = "notBreaching"

  dimensions = {
    FunctionName = each.value
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# ─────────────────────────────────────────────
# SQS Message Age Alarms (3 queues)
# Threshold: 1800 seconds (30 min)
# ─────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "sqs_message_age" {
  for_each = local.queues

  alarm_name          = "${local.name_prefix}-${each.key}-queue-age"
  alarm_description   = "Oldest message in ${each.value} exceeds 30 minutes"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "ApproximateAgeOfOldestMessage"
  namespace           = "AWS/SQS"
  period              = 300
  statistic           = "Maximum"
  threshold           = 1800
  treat_missing_data  = "notBreaching"

  dimensions = {
    QueueName = each.value
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# ─────────────────────────────────────────────
# RDS CPU Utilization Alarm (> 80%)
# ─────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${local.name_prefix}-rds-cpu-utilization"
  alarm_description   = "RDS CPU utilization exceeds 80%"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  metric_name         = "CPUUtilization"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Average"
  threshold           = 80
  treat_missing_data  = "missing"

  dimensions = {
    DBInstanceIdentifier = var.db_instance_identifier
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# ─────────────────────────────────────────────
# RDS Free Storage Alarm (< 1 GB)
# ─────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "rds_storage" {
  alarm_name          = "${local.name_prefix}-rds-free-storage"
  alarm_description   = "RDS free storage below 1 GB"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "FreeStorageSpace"
  namespace           = "AWS/RDS"
  period              = 300
  statistic           = "Minimum"
  threshold           = 1073741824
  treat_missing_data  = "missing"

  dimensions = {
    DBInstanceIdentifier = var.db_instance_identifier
  }

  alarm_actions = [aws_sns_topic.alerts.arn]
  ok_actions    = [aws_sns_topic.alerts.arn]
}

# ─────────────────────────────────────────────
# CloudWatch Dashboard
# ─────────────────────────────────────────────

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = "${local.name_prefix}-pipeline"

  dashboard_body = jsonencode({
    widgets = concat(
      # Row 1: Lambda Invocations & Errors
      [
        for i, pair in [
          for k, v in local.lambda_functions : { key = k, name = v }
          ] : {
          type   = "metric"
          x      = i * 6
          y      = 0
          width  = 6
          height = 6
          properties = {
            title  = "${pair.key} invocations/errors"
            region = data.aws_region.current.region
            period = 300
            stat   = "Sum"
            metrics = [
              ["AWS/Lambda", "Invocations", "FunctionName", pair.name, { color = "#2ca02c" }],
              ["AWS/Lambda", "Errors", "FunctionName", pair.name, { color = "#d62728" }],
            ]
          }
        }
      ],

      # Row 2: Lambda Duration
      [
        for i, pair in [
          for k, v in local.lambda_functions : { key = k, name = v }
          ] : {
          type   = "metric"
          x      = i * 6
          y      = 6
          width  = 6
          height = 6
          properties = {
            title  = "${pair.key} duration"
            region = data.aws_region.current.region
            period = 300
            metrics = [
              ["AWS/Lambda", "Duration", "FunctionName", pair.name, { stat = "Average", color = "#1f77b4" }],
              ["AWS/Lambda", "Duration", "FunctionName", pair.name, { stat = "p99", color = "#ff7f0e" }],
            ]
          }
        }
      ],

      # Row 3: SQS Queue Depths + Message Age
      [
        {
          type   = "metric"
          x      = 0
          y      = 12
          width  = 12
          height = 6
          properties = {
            title  = "SQS Queue Depths"
            region = data.aws_region.current.region
            period = 300
            stat   = "Maximum"
            metrics = [
              for k, v in local.queues :
              ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", v]
            ]
          }
        },
        {
          type   = "metric"
          x      = 12
          y      = 12
          width  = 12
          height = 6
          properties = {
            title  = "SQS Message Age (seconds)"
            region = data.aws_region.current.region
            period = 300
            stat   = "Maximum"
            metrics = [
              for k, v in local.queues :
              ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", v]
            ]
          }
        },
      ],

      # Row 4: DLQ Depths
      [
        {
          type   = "metric"
          x      = 0
          y      = 18
          width  = 12
          height = 6
          properties = {
            title  = "Dead-Letter Queue Depths"
            region = data.aws_region.current.region
            period = 300
            stat   = "Maximum"
            metrics = [
              for k, v in local.dlqs :
              ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", v]
            ]
          }
        },
      ],

      # Row 5: RDS Metrics
      [
        {
          type   = "metric"
          x      = 0
          y      = 24
          width  = 8
          height = 6
          properties = {
            title  = "RDS CPU Utilization"
            region = data.aws_region.current.region
            period = 300
            stat   = "Average"
            yAxis  = { left = { min = 0, max = 100 } }
            metrics = [
              ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", var.db_instance_identifier, { color = "#d62728" }],
            ]
            annotations = {
              horizontal = [
                { label = "Alarm threshold", value = 80, color = "#d62728" }
              ]
            }
          }
        },
        {
          type   = "metric"
          x      = 8
          y      = 24
          width  = 8
          height = 6
          properties = {
            title  = "RDS Database Connections"
            region = data.aws_region.current.region
            period = 300
            stat   = "Average"
            metrics = [
              ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", var.db_instance_identifier, { color = "#1f77b4" }],
            ]
          }
        },
        {
          type   = "metric"
          x      = 16
          y      = 24
          width  = 8
          height = 6
          properties = {
            title  = "RDS Free Storage (GB)"
            region = data.aws_region.current.region
            period = 300
            stat   = "Minimum"
            metrics = [
              [{ expression = "m1 / 1073741824", label = "Free Storage (GB)", id = "e1" }],
              ["AWS/RDS", "FreeStorageSpace", "DBInstanceIdentifier", var.db_instance_identifier, { id = "m1", visible = false }],
            ]
            annotations = {
              horizontal = [
                { label = "Alarm threshold", value = 1, color = "#d62728" }
              ]
            }
          }
        },
      ]
    )
  })
}
