output "ecr_repo_url" {
  value = aws_ecr_repository.scorer.repository_url
}

output "lambda_function_name" {
  value = aws_lambda_function.scorer.function_name
}
