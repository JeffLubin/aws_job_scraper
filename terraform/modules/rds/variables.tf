variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "rds_security_group_id" {
  type = string
}

variable "endpoints_security_group_id" {
  type = string
}

variable "vpc_cidr" {
  type = string
}

variable "project_name" {
  type    = string
  default = "aws-job-scraper"
}

variable "db_name" {
  type    = string
  default = "awsjobs"
}

variable "db_username" {
  type    = string
  default = "awsjobs_admin"
}
