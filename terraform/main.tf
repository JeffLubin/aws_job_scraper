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
  openai_secret_arn        = "arn:aws:secretsmanager:us-east-1:548911563197:secret:jobspy/openai-api-key-d3435i"
}

module "observability" {
  source = "./modules/observability"

  alert_email = var.alert_email

  dispatcher_function_name = module.dispatcher.lambda_function_name
  scraper_function_name    = module.scraper.lambda_function_name
  enricher_function_name   = module.enricher.lambda_function_name
  scorer_function_name     = module.scorer.lambda_function_name

  scrape_queue_name = module.dispatcher.scrape_queue_name
  enrich_queue_name = module.scraper.enrich_queue_name
  score_queue_name  = module.enricher.score_queue_name

  scrape_dlq_name = module.dispatcher.dlq_name
  enrich_dlq_name = module.scraper.enrich_dlq_name
  score_dlq_name  = module.enricher.score_dlq_name

  db_instance_identifier = module.rds.db_instance_identifier
}
