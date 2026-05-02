variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "alert_email" {
  description = "Email address for CloudWatch alarm notifications"
  type        = string
}

provider "aws" {
  region = var.aws_region
}
