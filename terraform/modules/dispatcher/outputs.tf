output "scrape_queue_arn" {
  value = aws_sqs_queue.scrape.arn
}

output "scrape_queue_url" {
  value = aws_sqs_queue.scrape.url
}

output "dlq_arn" {
  value = aws_sqs_queue.dlq.arn
}

output "ecr_repo_url" {
  value = aws_ecr_repository.dispatcher.repository_url
}

output "lambda_function_name" {
  value = aws_lambda_function.dispatcher.function_name
}
