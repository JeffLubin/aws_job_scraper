variable "project_name" {
  type    = string
  default = "aws-job-scraper"
}

variable "image_tag" {
  type    = string
  default = "latest"
}

variable "schedule_expression" {
  type    = string
  default = "rate(4 hours)"
}
