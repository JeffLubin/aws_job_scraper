output "score_queue_arn" {
  value = aws_sqs_queue.score.arn
}

output "score_queue_url" {
  value = aws_sqs_queue.score.url
}

output "score_dlq_arn" {
  value = aws_sqs_queue.score_dlq.arn
}

output "ecr_repo_url" {
  value = aws_ecr_repository.enricher.repository_url
}

output "lambda_function_name" {
  value = aws_lambda_function.enricher.function_name
}

output "score_queue_name" {
  value = aws_sqs_queue.score.name
}

output "score_dlq_name" {
  value = aws_sqs_queue.score_dlq.name
}
