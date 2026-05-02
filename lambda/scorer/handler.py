import html as htmllib
import json
import logging
import os
import re
import time

import boto3
import psycopg2
from openai import OpenAI

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))
for handler in logger.handlers:
    handler.setFormatter(logging.Formatter("%(message)s"))

secretsmanager = boto3.client("secretsmanager")

DB_SECRET_ARN = os.environ["DB_SECRET_ARN"]
OPENAI_SECRET_ARN = os.environ["OPENAI_SECRET_ARN"]
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

_openai_client = None

_db_secret = None
_db_conn = None

MAX_DESCRIPTION_LENGTH = 8000

SYSTEM_PROMPT = """\
You are filtering B2B sales jobs for Jeff.

BACKGROUND:
- Kaseya (Senior Account Manager): 212% of quota in 90 days at $15K-$50K ACV. Managed a 33-account MSP partner book of business, ran monthly QBRs balancing expansion + retention, partnered with SEs on technical scoping. Sold EDR, backup, pen-testing, email security.
- Nsight Health (AE): 150% of quota, $1.8M ARR book, cold outbound to MDs, 10-20 demos/week at 20-30% close rate.
- Spark Membership (GTM Engineer, contract): built production outbound + upsell infra (Python, n8n, HubSpot, Postgres, Claude API). Generated meetings at ~$200 vs. $1K PPC baseline.
- A&R Capital (Account Manager, 3+ years): 150+ HNW investor book, 62% retention rate vs. 45% company average.
- Security+ certified, pursuing AZ-104, Terraform Associate, CRISC.
- Long-term goal: pivot from AE/AM into Sales Engineer, Cloud Security Engineer, or Growth PM via internal mobility.

JEFF'S REAL MOTIONS (important — use these to evaluate JD fit):
- BOOK-OF-BUSINESS AM: Strong. 33-account Kaseya book, 150+ A&R investor book. Expansion + retention motion, QBR-driven, SE partnership.
- FULL-CYCLE AE (cold outbound to close): Strong. Nsight — cold prospect to MD demo to close, $1.8M ARR generated personally.
- SMB DEAL SIZE ($15K-$50K ACV): Proven.
- MID-MARKET DEAL SIZE ($50K-$250K ACV): Stretch but credible. Apply weight based on cycle length and complexity described.
- ENTERPRISE DEAL SIZE ($250K+ ACV, 9+ month cycles, Fortune 1000 buyers): No experience. Penalize.

TARGET ROLES (in order of preference):
1. AE/AM at MSP-channel cybersecurity SaaS (highest leverage)
2. Sales Engineer / Solutions Engineer
3. GTM Engineer / RevOps / Sales Ops
4. AE/AM at cloud, devtools, ML, or sales-tech SaaS
5. AE/AM at healthcare SaaS (Nsight-adjacent)

TIER FRAMEWORK (use this to anchor scores):

TIER 1 — Bullseye fit. Cybersecurity SaaS selling to MSPs, OR sales-tooling companies with known internal AE→SE/PM pivot culture.
Examples: Huntress, ThreatLocker, NinjaOne, Pax8, Blackpoint Cyber, Coro, Arctic Wolf, ConnectWise (non-legacy roles), N-able, Auvik, Cork, Clay, Apollo, Default, Common Room, Pocus.

TIER 2 — Strong fit. Cybersecurity SaaS (non-MSP), cloud security, devtools, AI/ML platforms, fintech, RevOps tooling, healthcare SaaS, strong design/collab SaaS with technical buyers.
Examples: CrowdStrike, SentinelOne, Wiz, Snyk, Datadog, Sumo Logic, Lacework, Orca, Palo Alto (SMB), Cloudflare (SMB), HashiCorp, GitLab, Anthropic, OpenAI, Gong, Outreach, Tebra, Figma, Notion, Linear.

TIER 3 — Possible fit. Generic B2B SaaS, vertical SaaS in tech-adjacent spaces. Monday, Asana, Miro, Airtable, Zapier, ClickUp, Loom, etc.

TIER 4 — Auto-reject. HR software, payroll, CPG, medical devices, pharma, insurance, real estate, furniture, construction, hospitality, advertising, pet products, staffing services, print/office supplies, physical goods, D2D, field sales.

IMPORTANT: If a company isn't on any example list but the product description clearly matches a tier's theme, score it as if it were in that tier. Don't penalize unfamiliar names.

SEGMENT + MOTION ADJUSTMENTS (apply after tier score):
- Role is BOOK-OF-BUSINESS AM / expansion-focused: +1 (matches Kaseya motion)
- Role is FULL-CYCLE AE with cold outbound: +0 (matches Nsight, neutral)
- Role is Mid-Market segment (accounts 500-5000 FTE): -1 (stretch for Jeff, ramp-able)
- Role is Enterprise segment (Fortune 1000, 5000+ FTE, 9+ month cycles): -2 (not qualified)
- Role requires 5+ years AE experience: -2
- Role is SDR/BDR: -3 (going backwards)
- Role mentions MSP/MSSP channel: +1
- JD mentions Python, automation, n8n, API integrations: +1
- Series B-D company (100-800 employees) with visible internal mobility: +0.5

BIG TECH PENALTY: Salesforce, Oracle, SAP, Workday, Microsoft enterprise sales — subtract 2 additional points (Jeff's tenure pattern auto-filters there anyway).

SCORING (1-10) — apply tier score first, then adjustments:
- 10: Tier 1 company + perfect role title + bonus signals
- 9: Tier 1 company + good role, OR Tier 2 company + book-of-business AM role
- 8: Tier 2 company + good AE/AM role (book of business or SMB full-cycle)
- 7: Tier 2 company + Mid-Market AE (stretch segment but credible ramp), OR Tier 1 with mild mismatch
- 6: Tier 3 generic SaaS with something compelling (good logo, clear pivot path)
- 5: Tier 3 generic SaaS, no standout signal
- 4: Tier 3 with concerns (slow mobility, declining company, weak comp)
- 3: Borderline Tier 4 — wrong industry but role is adjacent
- 2: Tier 4 — wrong industry, auto-reject
- 1: Tier 4 AND wrong role type

CALIBRATION EXAMPLES:
- "AE, Mid-Market at Figma" → Tier 2 base (8) − 1 Mid-Market segment = 7
- "Account Manager, SMB at ThreatLocker" → Tier 1 base (10) + 1 book-of-business + 1 MSP = cap at 10
- "Enterprise AE at Salesforce" → Tier 3 (5) − 2 enterprise − 2 Big Tech = 1
- "AE at Monday.com, SMB" → Tier 3 (5) + 0 full-cycle = 5
- "Senior AE at Wiz, Mid-Market" → Tier 2 (8) − 1 Mid-Market − 2 senior (5+ yrs) = 5

Return ONLY valid JSON:
{
  "product_domain": "<1-4 words describing what the company sells>",
  "fit_score": <1-10>
}\
"""


def log(level, event, **fields):
    logger.log(level, json.dumps({"event": event, **fields}, default=str))


# ---------------------------------------------------------------------------
# Secrets Manager + DB connection caching (mirrors enricher handler)
# ---------------------------------------------------------------------------

def get_db_secret():
    global _db_secret
    if _db_secret is None:
        response = secretsmanager.get_secret_value(SecretId=DB_SECRET_ARN)
        _db_secret = json.loads(response["SecretString"])
    return _db_secret


def open_db_connection():
    secret = get_db_secret()
    return psycopg2.connect(
        host=secret["host"],
        port=secret["port"],
        database=secret["dbname"],
        user=secret["username"],
        password=secret["password"],
    )


def get_db_connection():
    global _db_conn
    if _db_conn is None or _db_conn.closed:
        _db_conn = open_db_connection()
        return _db_conn

    try:
        with _db_conn.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        try:
            _db_conn.close()
        except Exception:
            pass
        _db_conn = open_db_connection()
    return _db_conn


# ---------------------------------------------------------------------------
# HTML stripping (matches scraper:68-73)
# ---------------------------------------------------------------------------

def _html_to_text(value):
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(value))
    text = htmllib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# RDS operations
# ---------------------------------------------------------------------------

def lookup_job(job_url):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT title, company, description, ai_analysis_cache "
            "FROM sales_jobs_mia WHERE job_url = %s",
            (job_url,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return {
        "title": row[0],
        "company": row[1],
        "description": row[2],
        "ai_analysis_cache": row[3],
    }


def save_score(job_url, score_result):
    to_save = {k: v for k, v in score_result.items() if not k.startswith("_")}
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE sales_jobs_mia "
                "SET ai_analysis_cache = %s::jsonb, n8n_processing_status = 'scored' "
                "WHERE job_url = %s",
                (json.dumps(to_save), job_url),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# OpenAI client
# ---------------------------------------------------------------------------

def get_openai_client():
    global _openai_client
    if _openai_client is None:
        response = secretsmanager.get_secret_value(SecretId=OPENAI_SECRET_ARN)
        api_key = response["SecretString"]
        _openai_client = OpenAI(api_key=api_key)
    return _openai_client


# ---------------------------------------------------------------------------
# OpenAI invocation
# ---------------------------------------------------------------------------

def invoke_openai(title, company, description, strict=False):
    user_content = f"Job title: {title}\nCompany: {company}\nDescription: {description}"
    if strict:
        user_content = (
            "CRITICAL: Respond with ONLY valid JSON, no preamble.\n\n"
            + user_content
        )

    client = get_openai_client()
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        max_tokens=200,
        temperature=0.0,
    )

    raw_text = response.choices[0].message.content

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError:
        log(logging.WARNING, "openai_json_parse_failed",
            raw_response=raw_text, strict=strict)
        return None

    fit_score = result.get("fit_score")
    product_domain = result.get("product_domain")

    if not isinstance(fit_score, int) or not (1 <= fit_score <= 10):
        log(logging.WARNING, "openai_invalid_fit_score",
            fit_score=fit_score, raw_response=raw_text, strict=strict)
        return None

    if not isinstance(product_domain, str) or not product_domain:
        log(logging.WARNING, "openai_invalid_product_domain",
            product_domain=product_domain, raw_response=raw_text, strict=strict)
        return None

    usage = response.usage
    result["_usage"] = {
        "input_tokens": usage.prompt_tokens if usage else None,
        "output_tokens": usage.completion_tokens if usage else None,
    }
    return result


# ---------------------------------------------------------------------------
# SQS record processing
# ---------------------------------------------------------------------------

def process_record(record):
    message = json.loads(record["body"])
    job_url = message.get("job_url")
    if not job_url:
        raise ValueError("Missing required message key: job_url")

    log(logging.INFO, "score_started", job_url=job_url)

    job = lookup_job(job_url)
    if job is None:
        log(logging.WARNING, "score_skipped_not_found", job_url=job_url)
        return {"job_url": job_url, "status": "not_found"}

    if job["ai_analysis_cache"] is not None:
        log(logging.INFO, "score_skipped_already_scored", job_url=job_url)
        return {"job_url": job_url, "status": "already_scored"}

    if not job["description"]:
        log(logging.WARNING, "score_skipped_no_description", job_url=job_url)
        return {"job_url": job_url, "status": "no_description"}

    clean_description = _html_to_text(job["description"])[:MAX_DESCRIPTION_LENGTH]

    start = time.time()

    # First attempt
    result = invoke_openai(job["title"], job["company"], clean_description)

    # Retry with stricter JSON instructions
    if result is None:
        result = invoke_openai(
            job["title"], job["company"], clean_description, strict=True
        )

    # Both attempts failed
    if result is None:
        raise ValueError(
            f"OpenAI returned invalid JSON after 2 attempts for {job_url}"
        )

    duration_ms = int((time.time() - start) * 1000)

    save_score(job_url, result)

    usage = result.get("_usage", {})
    log(logging.INFO, "score_completed",
        job_url=job_url,
        fit_score=result["fit_score"],
        product_domain=result["product_domain"],
        duration_ms=duration_ms,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"))

    return {"job_url": job_url, "status": "scored",
            "fit_score": result["fit_score"]}


def lambda_handler(event, context):
    results = [process_record(record) for record in event.get("Records", [])]
    return {"processed": len(results), "results": results}
