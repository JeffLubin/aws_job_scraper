import html as htmllib
import json
import logging
import os
import re
from datetime import datetime, timezone

import boto3
import pandas as pd
import psycopg2
from jobspy import scrape_jobs
from psycopg2.extras import execute_values

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))
for handler in logger.handlers:
    handler.setFormatter(logging.Formatter("%(message)s"))

sqs = boto3.client("sqs")
secretsmanager = boto3.client("secretsmanager")

DB_SECRET_ARN = os.environ["DB_SECRET_ARN"]
ENRICH_QUEUE_URL = os.environ["ENRICH_QUEUE_URL"]

_db_secret = None
_db_conn = None

SALES_JOB_KEYWORDS = [
    "account executive", "account exec", "ae", "acct executive", "acct exec",
    "account manager", "account mgr", "am", "acct manager", "acct mgr",
    "client account manager", "key account manager", "technical account manager",
    "gtm engineer", "go-to-market engineer", "go to market engineer",
    "sales technology product manager", "gtm technology product manager",
    "sales tech pm", "gtm tech pm",
    "revenue operations manager", "revops manager", "revops analyst",
    "revenue operations analyst", "rev ops",
    "sales engineer", "solutions engineer", "se", "technical sales engineer",
]

EXCLUDE_KEYWORDS = [
    "lead", "senior", "principal", "director", "supervisor",
    "chief", "head", "vp", "vice president", "team lead", "team leader", "sr",
    "level 3", "level iii", "level three", "regional manager", "area manager",
    "sales manager", "district manager", "territory manager",
]

SALES_CERT_KEYWORDS = [
    "account management", "client management", "relationship management",
    "b2b sales", "enterprise sales", "saas experience", "software sales",
    "crm experience", "salesforce", "hubspot", "quota carrying", "quota achievement",
    "account planning", "territory management", "client retention", "upselling",
    "cross-selling", "consultative selling", "solution selling", "years account experience",
]

INSERT_COLUMNS = [
    "job_url", "title", "company", "location_str", "description", "date_posted",
    "is_remote", "job_type", "salary_min", "salary_max", "salary_interval",
    "company_url", "company_url_direct", "company_industry",
    "source_site", "scraped_at", "processed_by_n8n_at", "ai_analysis_cache",
    "n8n_processing_status", "relevance_score", "interested",
]


def log(level, event, **fields):
    logger.log(level, json.dumps({"event": event, **fields}, default=str))


def _html_to_text(value):
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", str(value))
    text = htmllib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def filter_and_score_job(job):
    job_title = job.get("title") or ""
    if not job_title:
        return 0

    job_title_lower = job_title.lower()
    job_description_lower = _html_to_text(job.get("description") or "").lower()
    title_words = set(job_title_lower.replace("/", " ").replace("-", " ").split())

    for exclude_word in EXCLUDE_KEYWORDS:
        if all(part in title_words for part in exclude_word.split()):
            return 0

    title_matches = any(include_word in job_title_lower for include_word in SALES_JOB_KEYWORDS)
    if not title_matches:
        for include_word in SALES_JOB_KEYWORDS:
            include_parts = include_word.split()
            if all(part in title_words for part in include_parts) or any([
                include_word == "account executive" and "account" in title_words and any(w in title_words for w in ["executive", "exec"]),
                include_word == "ae" and "ae" in title_words,
                include_word == "account exec" and "account" in title_words and any(w in title_words for w in ["exec", "executive"]),
                include_word == "account manager" and "account" in title_words and any(w in title_words for w in ["manager", "mgr"]),
                include_word == "am" and "am" in title_words,
                include_word == "account mgr" and "account" in title_words and any(w in title_words for w in ["mgr", "manager"]),
                include_word == "key account manager" and "key" in title_words and "account" in title_words and any(w in title_words for w in ["manager", "mgr"]),
                include_word == "client account manager" and "client" in title_words and "account" in title_words and any(w in title_words for w in ["manager", "mgr"]),
            ]):
                title_matches = True
                break

    if not title_matches:
        return 0

    for cert_variant in SALES_CERT_KEYWORDS:
        cert_parts = cert_variant.split()
        if cert_variant in job_description_lower or all(part in job_description_lower for part in cert_parts):
            return 2
    return 1


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


def clean_value(value):
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def normalize_job(job):
    job = {key: clean_value(value) for key, value in job.items()}
    job["job_url"] = str(job.get("job_url") or "").strip()
    job["location_str"] = job.get("location_str") or job.get("location") or job.get("location_raw")
    job["salary_min"] = job.get("salary_min") or job.get("min_amount")
    job["salary_max"] = job.get("salary_max") or job.get("max_amount")
    job["salary_interval"] = job.get("salary_interval") or job.get("interval")
    job["source_site"] = job.get("source_site") or job.get("site") or "linkedin"
    job["scraped_at"] = job.get("scraped_at") or datetime.now(timezone.utc)
    job["processed_by_n8n_at"] = None
    job["ai_analysis_cache"] = None
    job["n8n_processing_status"] = None
    job["interested"] = "pending"
    return job


def batch_save_sales_jobs_mia_to_db(jobs):
    valid_jobs = []
    filtered_count = 0

    for job in jobs:
        job = normalize_job(job)
        if not job["job_url"]:
            filtered_count += 1
            continue

        relevance_score = filter_and_score_job(job)
        if relevance_score < 1:
            filtered_count += 1
            continue

        job["relevance_score"] = relevance_score
        for column in INSERT_COLUMNS:
            job.setdefault(column, None)
        valid_jobs.append(job)

    if valid_jobs:
        seen_urls = set()
        seen_title_company = set()
        deduped = []
        for job in valid_jobs:
            key_url = job["job_url"]
            key_title_company = (job.get("title"), job.get("company"))
            if key_url in seen_urls or key_title_company in seen_title_company:
                filtered_count += 1
                continue
            seen_urls.add(key_url)
            seen_title_company.add(key_title_company)
            deduped.append(job)
        valid_jobs = deduped

    if not valid_jobs:
        return [], filtered_count

    insert_q = """
        INSERT INTO sales_jobs_mia (
            job_url, title, company, location_str, description, date_posted,
            is_remote, job_type, salary_min, salary_max, salary_interval,
            company_url, company_url_direct, company_industry,
            source_site, scraped_at, processed_by_n8n_at, ai_analysis_cache,
            n8n_processing_status, relevance_score, interested
        ) VALUES %s ON CONFLICT DO NOTHING RETURNING job_url;
    """
    values = [tuple(job[column] for column in INSERT_COLUMNS) for job in valid_jobs]
    conn = get_db_connection()

    try:
        with conn.cursor() as cursor:
            rows = execute_values(cursor, insert_q, values, page_size=1000, fetch=True)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    inserted_urls = {row[0] for row in rows}
    return [job for job in valid_jobs if job["job_url"] in inserted_urls], filtered_count


def scrape_for_message(message):
    search_term = message["search_term"]
    location = message["location"]
    hours_old = int(message["hours_old"])

    jobs_df = scrape_jobs(
        site_name=["linkedin"],
        search_term=search_term,
        location=location,
        results_wanted=150,
        hours_old=hours_old,
        country_indeed="USA",
        is_remote=(str(location).strip().lower() == "remote"),
        linkedin_fetch_description=False,
        description_format="html",
    )

    if jobs_df is None or len(jobs_df) == 0:
        return []

    jobs_df["scraped_at"] = datetime.now(timezone.utc)
    jobs_df["source_site"] = "linkedin"
    return [row.to_dict() for _, row in jobs_df.iterrows()]


def send_to_enrich_queue(jobs):
    if not jobs:
        return 0

    sent = 0
    for start in range(0, len(jobs), 10):
        chunk = jobs[start:start + 10]
        response = sqs.send_message_batch(
            QueueUrl=ENRICH_QUEUE_URL,
            Entries=[
                {
                    "Id": str(index),
                    "MessageBody": json.dumps({
                        "job_url": job["job_url"],
                        "title": job.get("title"),
                        "company": job.get("company"),
                    }),
                }
                for index, job in enumerate(chunk)
            ],
        )
        failures = response.get("Failed", [])
        if failures:
            raise RuntimeError(f"Failed to enqueue {len(failures)} enrich messages")
        sent += len(chunk)
    return sent


def process_record(record):
    message = json.loads(record["body"])
    for key in ("search_term", "location", "hours_old"):
        if key not in message:
            raise ValueError(f"Missing required message key: {key}")

    log(logging.INFO, "scrape_started", **message)
    jobs = scrape_for_message(message)
    inserted_jobs, filtered_count = batch_save_sales_jobs_mia_to_db(jobs)
    forwarded_count = send_to_enrich_queue(inserted_jobs)
    log(
        logging.INFO,
        "scrape_completed",
        scraped_count=len(jobs),
        inserted_count=len(inserted_jobs),
        filtered_count=filtered_count,
        forwarded_count=forwarded_count,
        **message,
    )
    return {
        "scraped": len(jobs),
        "inserted": len(inserted_jobs),
        "filtered": filtered_count,
        "forwarded": forwarded_count,
    }


def lambda_handler(event, context):
    results = [process_record(record) for record in event.get("Records", [])]
    return {"processed": len(results), "results": results}
