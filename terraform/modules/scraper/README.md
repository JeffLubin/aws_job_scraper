# Scraper Module

Creates the Phase 4 scraper foundation:

- ECR repository for the scraper Lambda container image.
- SQS enrich queue and DLQ.
- Image-based scraper Lambda attached to private subnets.
- SQS event source mapping from scrape queue to scraper Lambda.

## Deployment Order

The Lambda references an image in the ECR repository, so the repository must exist and the image must be pushed before the full module can apply successfully.

Use this order:

```sh
terraform -chdir=terraform apply -target=module.scraper.aws_ecr_repository.scraper -target=module.scraper.aws_ecr_lifecycle_policy.scraper
./scripts/build_scraper.sh
terraform -chdir=terraform apply
```
