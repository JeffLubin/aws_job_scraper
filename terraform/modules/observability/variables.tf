variable "project_name" {
  type    = string
  default = "aws-job-scraper"
}

variable "alert_email" {
  description = "Email address for CloudWatch alarm notifications"
  type        = string
}

variable "dispatcher_function_name" {
  type = string
}

variable "scraper_function_name" {
  type = string
}

variable "enricher_function_name" {
  type = string
}

variable "scorer_function_name" {
  type = string
}

variable "scrape_queue_name" {
  type = string
}

variable "enrich_queue_name" {
  type = string
}

variable "score_queue_name" {
  type = string
}

variable "scrape_dlq_name" {
  type = string
}

variable "enrich_dlq_name" {
  type = string
}

variable "score_dlq_name" {
  type = string
}

variable "db_instance_identifier" {
  type = string
}
