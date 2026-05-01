output "enrich_queue_arn" {
  value = aws_sqs_queue.enrich.arn
}

output "enrich_queue_url" {
  value = aws_sqs_queue.enrich.url
}

output "enrich_dlq_arn" {
  value = aws_sqs_queue.enrich_dlq.arn
}

output "ecr_repo_url" {
  value = aws_ecr_repository.scraper.repository_url
}

output "lambda_function_name" {
  value = aws_lambda_function.scraper.function_name
}
