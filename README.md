# AWS Job Scraper

Event-driven AWS pipeline that scrapes LinkedIn for B2B sales jobs, filters through a three-gate cascade, and scores survivors with Bedrock AI. Built as a cloud migration of a local Python + n8n workflow.

**Region:** us-east-1
**Budget:** $75/month cap, ~$50/month estimated
**Account:** 548911563197

## Architecture

**Three-gate filter cascade:**
- **Gate 1** (free, scraper Lambda): title-based filter — ~97% rejection
- **Gate 2** (free, enricher Lambda): description red flags + company exclusions — ~50% rejection
- **Gate 3** (paid, scorer Lambda): Bedrock AI scoring on survivors only — ~1.5% of raw jobs reach this stage

```
EventBridge (4hr cron)
    │
    ▼
Dispatcher Lambda ──► scrape-queue (72 messages)
                          │
                          ▼
                    Scraper Lambda (×10 concurrency)
                    ├── Gate 1: title filter
                    ├── Write to RDS
                    └──► enrich-queue (survivors only)
                              │
                              ▼
                        Enricher Lambda (×5 concurrency)
                        ├── Fetch LinkedIn description
                        ├── Write description to RDS
                        ├── Gate 2: description red flags + company exclusions
                        ├── Write rejection reason to n8n_processing_status
                        └──► score-queue (survivors only)
                                  │
                                  ▼
                            Scorer Lambda (×5 concurrency)
                            ├── Idempotency check (skip if already scored)
                            ├── Bedrock AI scoring (Nova Lite)
                            └── Write {product_domain, fit_score} to ai_analysis_cache
```

## AWS Service Inventory

| Category | Services |
|---|---|
| Compute | Lambda (×4), EventBridge, ECR (×4) |
| Messaging | SQS (×3 + ×3 DLQs) |
| Data | RDS Postgres (db.t4g.micro), Secrets Manager |
| Networking | VPC, subnets (×4), security groups, NAT Gateway, VPC endpoints |
| AI | Bedrock (Amazon Nova Lite) |
| Security | IAM roles + policies (least privilege per Lambda) |
| Tooling | S3 (TF state), DynamoDB (TF lock) |

## Project Structure

```
terraform/
  main.tf                    # Root module wiring 5 child modules
  versions.tf                # TF >= 1.14, AWS ~> 6.0
  providers.tf               # us-east-1
  backend.tf                 # S3 remote state
  environments/dev/backend.hcl
  modules/
    networking/              # VPC, NAT, subnets, security groups, VPC endpoints
    rds/                     # Postgres, Secrets Manager, rotation Lambda
    dispatcher/              # EventBridge → Lambda → scrape-queue
    scraper/                 # SQS → Lambda (Gate 1), enrich-queue
    enricher/                # SQS → Lambda (Gate 2), score-queue
    scorer/                  # SQS → Lambda (Gate 3, Bedrock)
lambda/
  dispatcher/handler.py      # Fan-out: 3 titles × 6 locations → SQS
  scraper/handler.py         # JobSpy scrape, title filter, RDS write
  enricher/handler.py        # LinkedIn description fetch, red flag filter
  scorer/handler.py          # Bedrock AI scoring, idempotent
scripts/
  build_dispatcher.sh        # Docker buildx → ECR
  build_scraper.sh
  build_enricher.sh
  build_scorer.sh
sales.py                     # Legacy local scraper (reference only)
roadmap.md                   # Phase tracker with exit criteria
```

## Deployment

Each Lambda uses container images pushed to ECR. Build scripts handle the `docker buildx --platform linux/amd64 --provenance=false --push` pattern required for Lambda compatibility.

```sh
# Example: deploy the enricher
terraform apply -target=module.enricher.aws_ecr_repository.enricher
./scripts/build_enricher.sh
terraform apply
```

## Terraform

```sh
cd terraform
terraform init -backend-config=environments/dev/backend.hcl
terraform validate
terraform plan
```

## Manual State Bootstrap

State resources are created outside Terraform to avoid the backend chicken-and-egg problem.

```sh
aws s3api create-bucket \
  --bucket aws-job-scraper-tfstate-548911563197-us-east-1 \
  --region us-east-1

aws s3api put-bucket-versioning \
  --bucket aws-job-scraper-tfstate-548911563197-us-east-1 \
  --versioning-configuration Status=Enabled

aws s3api put-bucket-encryption \
  --bucket aws-job-scraper-tfstate-548911563197-us-east-1 \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

aws dynamodb create-table \
  --table-name aws-job-scraper-terraform-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
```

## Key Design Decisions

**Cost optimization:**
- VPC endpoints for SQS, Secrets Manager, ECR bypass NAT data transfer charges
- Bedrock traffic routes through NAT (~$0.05/month) — VPC endpoint ($14.40/month) not justified at portfolio scale
- Gate cascade ensures only ~1.5% of jobs reach paid Bedrock scoring
- Single NAT Gateway (no HA pair) — acceptable for non-production
- RDS db.t4g.micro, single-AZ, no Multi-AZ

**Rate limiting:**
- Scraper: reserved concurrency 10 (LinkedIn's tolerance is the real constraint)
- Enricher: reserved concurrency 5 (description fetching hits LinkedIn harder per request)
- Scorer: reserved concurrency 5 (Bedrock has account quotas)
- No `requests.Session` caching in enricher — prevents LinkedIn cookie fingerprinting

**Failure handling:**
- Every queue has a DLQ with maxReceiveCount of 3
- SQS visibility timeout (900s) provides natural backoff on retries
- Scorer has JSON retry with stricter prompt before falling to DLQ (saves Bedrock cost)
- Idempotent scoring: skip if `ai_analysis_cache IS NOT NULL`

## Lessons Learned

1. **Docker manifest format** — Apple Silicon Macs produce OCI image indexes with provenance attestations by default. Lambda only accepts plain Docker v2 manifests. Fix: `docker buildx build --platform linux/amd64 --provenance=false --push`.

2. **Lambda concurrency quota** — New AWS accounts default to 10 concurrent Lambda executions, not 1000. Reserving 10 fails because at least 10 must remain unreserved. Requested quota increase (approved in ~45 min).

3. **Lambda init timeout** — Lambda's init phase has its own 10-second timeout separate from function timeout. Heavy imports (JobSpy + pandas) cause init timeouts on cold start, then succeed on retry. Harmless but noisy.

4. **Inference profiles required** — Claude Haiku 4.5 (and most newer Anthropic models on Bedrock) cannot be invoked by foundation model ID directly. Must use `us.anthropic.claude-...` inference profile ID. IAM policy must allow both the inference profile ARN and foundation model ARNs across 3 US regions (us-east-1, us-east-2, us-west-2).

5. **Anthropic use case form** — AWS retired the Bedrock Model Access page. Anthropic models require a one-time use case form submission; first invocation fails with `ResourceNotFoundException` until approved.

6. **Bedrock daily token quotas** — New accounts ship with 0 tokens/day for most models. Cross-region inference quotas show `Adjustable: false`. This is an account-aging / trust issue, not a code issue. Switching from Haiku 4.5 to Nova Lite didn't help because the throttle is account-wide.

7. **Nova vs Anthropic API formats** — Nova uses `messages: [{role, content: [{text}]}]` and `inferenceConfig: {maxTokens, temperature}` instead of Anthropic's `messages: [{role, content}]` and top-level `max_tokens`. Response shape: `output.message.content[0].text` not `content[0].text`. Token usage uses camelCase (`inputTokens`, `outputTokens`).
