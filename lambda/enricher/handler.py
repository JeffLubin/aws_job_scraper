import json
import logging
import os
import re

import boto3
import psycopg2
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))
for handler in logger.handlers:
    handler.setFormatter(logging.Formatter("%(message)s"))

sqs = boto3.client("sqs")
secretsmanager = boto3.client("secretsmanager")

DB_SECRET_ARN = os.environ["DB_SECRET_ARN"]
SCORE_QUEUE_URL = os.environ["SCORE_QUEUE_URL"]

_db_secret = None
_db_conn = None

LINKEDIN_HEADERS = {
    "authority": "www.linkedin.com",
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "cache-control": "max-age=0",
    "upgrade-insecure-requests": "1",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

DESCRIPTION_RED_FLAGS = [
    "door to door", "door-to-door", "d2d",
    "box truck", "route", "delivery route",
    "in-person networking and cold calling strategically",
    "50-60 drop-ins a week",
    "face to face sales to new customers",
    "overnight travel",
    "uncapped commissions averaging between",
]

COMPANY_EXCLUSIONS = [
    "kaseya", "spark membership", "allied universal", "securitas",
    "gardaworld", "g4s", "dsi security", "prosegur", "spothopper",
    "rent-a-center", "ashley furniture", "farmers home furniture",
    "hilton", "embassy suites", "competere", "3mp",
    "centimark", "questmark", "floodgate medical", "jobot",
    "inaba", "odp", "truliant", "antech", "essity",
]


def log(level, event, **fields):
    logger.log(level, json.dumps({"event": event, **fields}, default=str))


# ---------------------------------------------------------------------------
# Secrets Manager + DB connection caching (mirrors scraper handler)
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
# LinkedIn description fetching
# ---------------------------------------------------------------------------

def linkedin_job_id(url):
    match = re.search(r"/jobs/view/(\d+)", url or "") or re.search(r"(\d{8,})", url or "")
    return match.group(1) if match else None


def _strip_attributes(tag):
    for el in tag.find_all(True):
        el.attrs = {}
    return tag


def fetch_description(job_url):
    job_id = linkedin_job_id(job_url)
    if not job_id:
        return None, None

    response = requests.get(
        f"https://www.linkedin.com/jobs/view/{job_id}",
        headers=LINKEDIN_HEADERS,
        timeout=10,
    )

    if response.status_code == 429:
        raise RuntimeError("rate_limited_429")
    response.raise_for_status()

    if "linkedin.com/signup" in response.url:
        return None, None

    soup = BeautifulSoup(response.text, "html.parser")
    div = soup.find("div", class_=lambda x: x and "show-more-less-html__markup" in x)
    if not div:
        return None, None

    html_desc = _strip_attributes(div).prettify(formatter="html")
    text_desc = div.get_text(separator=" ", strip=True)
    return html_desc, text_desc


# ---------------------------------------------------------------------------
# RDS updates
# ---------------------------------------------------------------------------

def update_description_in_db(job_url, html_description):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE sales_jobs_mia SET description = %s WHERE job_url = %s",
                (html_description, job_url),
            )
            if cursor.rowcount == 0:
                log(logging.WARNING, "update_no_match", job_url=job_url)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def update_processing_status(job_url, status):
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE sales_jobs_mia SET n8n_processing_status = %s WHERE job_url = %s",
                (status, job_url),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# Score queue forwarding
# ---------------------------------------------------------------------------

def send_to_score_queue(job_url):
    sqs.send_message(
        QueueUrl=SCORE_QUEUE_URL,
        MessageBody=json.dumps({"job_url": job_url}),
    )


# ---------------------------------------------------------------------------
# SQS record processing
# ---------------------------------------------------------------------------

def process_record(record):
    message = json.loads(record["body"])
    job_url = message.get("job_url")
    if not job_url:
        raise ValueError("Missing required message key: job_url")

    title = message.get("title")
    company = message.get("company")

    log(logging.INFO, "enrich_started", job_url=job_url, title=title, company=company)

    # Gate 2: company exclusion (cheap check before LinkedIn fetch)
    company_lower = (company or "").lower()
    for excluded in COMPANY_EXCLUSIONS:
        if excluded in company_lower:
            update_processing_status(job_url, f"rejected:company_excluded:{excluded}")
            log(logging.INFO, "enrich_completed",
                job_url=job_url, gate_2_passed=False,
                filter_reason=f"company_excluded:{excluded}")
            return {"job_url": job_url, "gate_2_passed": False,
                    "filter_reason": f"company_excluded:{excluded}"}

    # Fetch description from LinkedIn (raises on 429/HTTP errors for SQS retry)
    html_desc, text_desc = fetch_description(job_url)

    # Always persist description to RDS
    if html_desc:
        update_description_in_db(job_url, html_desc)

    # No description found
    if not text_desc:
        update_processing_status(job_url, "rejected:no_description")
        log(logging.WARNING, "enrich_completed",
            job_url=job_url, gate_2_passed=False,
            filter_reason="no_description", description_length=0)
        return {"job_url": job_url, "gate_2_passed": False,
                "filter_reason": "no_description"}

    # Gate 2: description red flag check
    desc_lower = text_desc.lower()
    for flag in DESCRIPTION_RED_FLAGS:
        if flag in desc_lower:
            update_processing_status(job_url, f"rejected:description_red_flag:{flag}")
            log(logging.INFO, "enrich_completed",
                job_url=job_url, gate_2_passed=False,
                filter_reason=f"description_red_flag:{flag}",
                description_length=len(text_desc))
            return {"job_url": job_url, "gate_2_passed": False,
                    "filter_reason": f"description_red_flag:{flag}"}

    # Passed Gate 2 — forward to score queue
    update_processing_status(job_url, "enriched")
    send_to_score_queue(job_url)
    log(logging.INFO, "enrich_completed",
        job_url=job_url, gate_2_passed=True,
        description_length=len(text_desc))
    return {"job_url": job_url, "gate_2_passed": True}


def lambda_handler(event, context):
    results = [process_record(record) for record in event.get("Records", [])]
    return {"processed": len(results), "results": results}
