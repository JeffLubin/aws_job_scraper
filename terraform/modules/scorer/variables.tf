variable "project_name" {
  type    = string
  default = "aws-job-scraper"
}

variable "vpc_subnet_ids" {
  type = list(string)
}

variable "lambda_security_group_id" {
  type = string
}

variable "score_queue_arn" {
  type = string
}

variable "db_secret_arn" {
  type = string
}

variable "openai_secret_arn" {
  type = string
}

variable "image_tag" {
  type    = string
  default = "latest"
}
