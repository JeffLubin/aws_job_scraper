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

module "dispatcher" {
  source = "./modules/dispatcher"
}

module "scraper" {
  source = "./modules/scraper"

  vpc_subnet_ids           = module.networking.private_subnet_ids
  lambda_security_group_id = module.networking.lambda_security_group_id
  scrape_queue_arn         = module.dispatcher.scrape_queue_arn
  db_secret_arn            = module.rds.secret_arn
}

module "enricher" {
  source = "./modules/enricher"

  vpc_subnet_ids           = module.networking.private_subnet_ids
  lambda_security_group_id = module.networking.lambda_security_group_id
  enrich_queue_arn         = module.scraper.enrich_queue_arn
  db_secret_arn            = module.rds.secret_arn
}

module "scorer" {
  source = "./modules/scorer"

  vpc_subnet_ids           = module.networking.private_subnet_ids
  lambda_security_group_id = module.networking.lambda_security_group_id
  score_queue_arn          = module.enricher.score_queue_arn
  db_secret_arn            = module.rds.secret_arn
}
