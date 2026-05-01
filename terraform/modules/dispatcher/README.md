# Dispatcher Module

Creates the Phase 3 dispatcher foundation:

- ECR repository for the dispatcher Lambda container image.
- SQS scrape queue and DLQ.
- Image-based dispatcher Lambda.
- Disabled EventBridge schedule running every 4 hours.

## Deployment Order

The Lambda references an image in the ECR repository, so the repository must exist and the image must be pushed before the full module can apply successfully.

Use this order:

```sh
terraform -chdir=terraform apply -target=module.dispatcher.aws_ecr_repository.dispatcher -target=module.dispatcher.aws_ecr_lifecycle_policy.dispatcher
./scripts/build_dispatcher.sh
terraform -chdir=terraform apply
```

The EventBridge rule is created as `DISABLED`; enable it only after manual dispatcher validation.
