module "networking" {
  source = "./modules/networking"

  project_name = "aws-job-scraper"
}

module "rds" {
  source = "./modules/rds"

  vpc_id                      = module.networking.vpc_id
  private_subnet_ids          = module.networking.private_subnet_ids
  rds_security_group_id       = module.networking.rds_security_group_id
  endpoints_security_group_id = module.networking.endpoints_security_group_id
  vpc_cidr                    = module.networking.vpc_cidr
}
