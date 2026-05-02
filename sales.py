#!/usr/bin/env python3
"""
Sales Job Search Script - Miami (MIA)
Scrapes Sales positions from LinkedIn using JobSpy - Miami metro area only
"""

import sys
import os
import json
import time
import traceback
import logging
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp
import random
import requests
import contextlib
import argparse
import re
import html as htmllib
import uuid

import pandas as pd
import psycopg2
from psycopg2 import sql
from jobspy import scrape_jobs

# =============================================================================
# CONFIGURATION SECTION - Update these for your local environment
# =============================================================================

# Database Configuration - Local PostgreSQL setup
DB_HOST = "127.0.0.1"  # PostgreSQL host
DB_PORT = "5432"  # PostgreSQL port
DB_NAME = "Jlubin_db"  # Your database name
DB_USER = "Jlubin_user"  # Your PostgreSQL username
DB_PASSWORD = "F*Z^D37b&A*^A46"  # Your PostgreSQL password

# Proxy Configuration (from your original setup)
PROXY_USERNAME = "sp8fbkf7om"
PROXY_PASSWORD = "I5ble8Uy4qT8A_dyxc"
PROXY_HOST = "gate.decodo.com"
PROXY_PORT = "7000"
PROXY_SESSION_COUNT = int(os.getenv("PROXY_SESSION_COUNT", "10"))
PROXY_SESSION_DURATION_MINUTES = int(os.getenv("PROXY_SESSION_DURATION_MINUTES", "10"))
USE_PROXY = False  # Set to False to disable proxy usage

# Optimized LinkedIn configuration. Re-run with PRODUCTION_MODE=False to test the ceiling.

PRODUCTION_MODE = True  # Set to False to re-run rate limit testing
TESTED_OPTIMAL_WORKERS = int(os.getenv("LINKEDIN_WORKERS", "10"))
LINKEDIN_FETCH_DESCRIPTION = False  # Avoid one extra LinkedIn request per result; enrich saved title matches later

# Discord Webhook for Notifications (Captain Hook)
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1391155702646444082/mJBcMMLrj1-nrPivYWAcOrd5hckEzHsLcGHodih33XQBHJsulur-YJJFlF5yWzZz2Tiv"

# =============================================================================
# SALES JOB TITLES AND KEYWORDS
# =============================================================================

SALES_JOB_TITLES = [
    "Account Executive",
    "Account Manager",
    "GTM Engineer",
]

# Centralized locations used for search and messaging
LOCATIONS = [
    "Miami, FL",
    "Atlanta, GA",
    "Austin, TX",
    "New York, NY",
    "Seattle, WA",
    "Raleigh, NC",
]

# Sales job filtering keywords - ONLY Account Executive and Account Manager roles
SALES_JOB_KEYWORDS = [
    # Account Executive variations
    "account executive", "account exec", "ae", "acct executive", "acct exec",

    # Account Manager variations
    "account manager", "account mgr", "am", "acct manager", "acct mgr",
    "client account manager", "key account manager", "technical account manager",

    # GTM Engineer
    "gtm engineer", "go-to-market engineer", "go to market engineer",

    # Sales/GTM Technology Product Manager
    "sales technology product manager", "gtm technology product manager",
    "sales tech pm", "gtm tech pm",

    # Revenue Operations / RevOps
    "revenue operations manager", "revops manager", "revops analyst",
    "revenue operations analyst", "rev ops",

    # Sales Engineer / Solutions Engineer
    "sales engineer", "solutions engineer", "se", "technical sales engineer",
]

# Exclusion keywords for leadership roles (excluding "manager" since we want Account Managers)
EXCLUDE_KEYWORDS = [
    "lead", "senior", "principal", "director", "supervisor",
    "chief", "head", "vp", "vice president", "team lead", "team leader", "sr", 
    "level 3", "level iii", "level three", "regional manager", "area manager",
    "sales manager", "district manager", "territory manager"
]

# Account Executive/Manager experience bonus keywords (optional scoring boost)
SALES_CERT_KEYWORDS = [
    "account management", "client management", "relationship management", 
    "b2b sales", "enterprise sales", "saas experience", "software sales",
    "crm experience", "salesforce", "hubspot", "quota carrying", "quota achievement",
    "account planning", "territory management", "client retention", "upselling",
    "cross-selling", "consultative selling", "solution selling", "years account experience"
]

# =============================================================================
# USER AGENT MANAGEMENT
# =============================================================================

UA_TEMPLATES = [
    # Chrome on Windows (most common)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome} Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome} Safari/537.36",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome} Safari/537.36",
    
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{firefox}) Gecko/20100101 Firefox/{firefox}",
    "Mozilla/5.0 (Windows NT 11.0; Win64; x64; rv:{firefox}) Gecko/20100101 Firefox/{firefox}",
    
    # Safari on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_{mac_ver}_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/{safari_major}.0 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_{mac_ver}_2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome} Safari/537.36",
    
    # Chrome on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome} Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:{firefox}) Gecko/20100101 Firefox/{firefox}",
    
    # Edge (Chromium)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome} Safari/537.36 Edg/{edge}",
]

_USED_UAS = set()

def unique_user_agent(max_tries: int = 20) -> str:
    """Generate a unique, realistic user agent string."""
    for _ in range(max_tries):
        tpl = random.choice(UA_TEMPLATES)
        
        # More realistic version ranges
        chrome_major = random.randint(120, 127)
        chrome_minor = random.randint(0, 9)
        chrome_build = random.randint(0, 6000)
        chrome_patch = random.randint(0, 199)
        
        firefox_major = random.randint(115, 121)
        edge_major = chrome_major  # Edge follows Chrome versioning
        
        ua = tpl.format(
            chrome=f"{chrome_major}.{chrome_minor}.{chrome_build}.{chrome_patch}",
            firefox=f"{firefox_major}.0",
            edge=f"{edge_major}.{chrome_minor}.{chrome_build}.{chrome_patch}",
            mac_ver=random.randint(14, 16),  # macOS Sonoma/Sequoia
            safari_major=random.randint(17, 18),
        )
        
        if ua not in _USED_UAS:
            _USED_UAS.add(ua)
            return ua
    return ua

LANG_POOL = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.8,en-US;q=0.6", 
    "en-US,en;q=0.8,es;q=0.6",
    "en-US,en;q=0.8,fr;q=0.6",
    "en-CA,en;q=0.9,fr;q=0.4",
    "en-AU,en;q=0.9",
    "en-US,en;q=0.9,de;q=0.3",
    "en-US,en;q=0.7,zh;q=0.3",
    "en-US,en;q=0.8",
    "en-GB,en;q=0.9,fr;q=0.4",
]

# Additional realistic headers for LinkedIn requests
LINKEDIN_HEADERS_POOL = [
    {"sec-ch-ua": '"Google Chrome";v="123", "Not:A-Brand";v="8", "Chromium";v="123"'},
    {"sec-ch-ua": '"Microsoft Edge";v="123", "Not:A-Brand";v="8", "Chromium";v="123"'},  
    {"sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"'},
    {"upgrade-insecure-requests": "1"},
]

# Disable extra header mutation for stability; only rotate UA and language
ENABLE_EXTRA_HEADERS = False

# =============================================================================
# DISCORD NOTIFICATIONS
# =============================================================================

def send_discord_notification(message, title="Sales Job Search", color=3447003):
    """Send a notification to Discord webhook."""
    if not DISCORD_WEBHOOK_URL:
        print("⚠️  Discord webhook URL not configured")
        return
    
    embed = {
        "title": title,
        "description": message,
        "color": color,
        "timestamp": datetime.utcnow().isoformat(),
        "footer": {"text": "JobSpy Sales Jobs"}
    }
    
    payload = {"embeds": [embed]}
    
    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        if response.status_code == 204:
            print("SUCCESS: Discord notification sent")
        else:
            print(f"⚠️  Discord notification failed: {response.status_code}")
    except Exception as e:
        print(f"ERROR: Discord notification error: {e}")

# =============================================================================
# JOB FILTERING AND SCORING
# =============================================================================

def _html_to_text(value: str) -> str:
    """Convert simple HTML to plain text for keyword matching."""
    if not value:
        return ""
    # Remove tags
    text = re.sub(r"<[^>]+>", " ", str(value))
    # Unescape entities
    text = htmllib.unescape(text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text

def filter_and_score_job(job_title, job_description):
    """
    Filter and score jobs based on relevance with improved partial matching.
    Returns: (should_save: bool, score: int)
    
    Score 0: Doesn't match inclusion keywords OR contains exclusion keywords
    Score 1: Matches inclusion keywords + no exclusion keywords + no sales experience mentioned
    Score 2: Score 1 + has relevant sales experience/qualifications mentioned in description
    """
    if not job_title:
        return False, 0
    
    job_title_lower = job_title.lower()
    # Sanitize HTML descriptions before keyword checks
    job_description_lower = _html_to_text(job_description or "").lower()
    
    # Tokenize the job title into words for partial matching
    title_words = set(job_title_lower.replace('/', ' ').replace('-', ' ').split())
    
    # Check for exclusion keywords using word-based matching to avoid substring false positives (e.g., 'sr')
    for exclude_word in EXCLUDE_KEYWORDS:
        exclude_parts = exclude_word.split()
        if all(part in title_words for part in exclude_parts):
            return False, 0
    
    # Check for inclusion keywords with partial matching
    title_matches = False
    
    # First try exact matches
    for include_word in SALES_JOB_KEYWORDS:
        if include_word in job_title_lower:
            title_matches = True
            break
    
    # If no exact match, try partial matching
    if not title_matches:
        for include_word in SALES_JOB_KEYWORDS:
            include_parts = include_word.split()
            # Match if all parts of the keyword are present in the title
            if all(part in title_words for part in include_parts):
                title_matches = True
                break
            
            # Special case for Account Executive and Account Manager abbreviations and variations
            if any([
                # Account Executive abbreviations and variations
                (include_word == "account executive" and "account" in title_words and any(w in title_words for w in ["executive", "exec"])),
                (include_word == "ae" and "ae" in title_words),
                (include_word == "account exec" and "account" in title_words and any(w in title_words for w in ["exec", "executive"])),
                
                # Account Manager abbreviations and variations  
                (include_word == "account manager" and "account" in title_words and any(w in title_words for w in ["manager", "mgr"])),
                (include_word == "am" and "am" in title_words),
                (include_word == "account mgr" and "account" in title_words and any(w in title_words for w in ["mgr", "manager"])),
                
                # Key Account Manager variations
                (include_word == "key account manager" and "key" in title_words and "account" in title_words and any(w in title_words for w in ["manager", "mgr"])),
                (include_word == "client account manager" and "client" in title_words and "account" in title_words and any(w in title_words for w in ["manager", "mgr"]))
            ]):
                title_matches = True
                break
    
    if not title_matches:
        return False, 0
    
    # Base score is 1 if we get here
    score = 1
    
    # Check for sales experience/qualification bonus (both exact and partial matches)
    for cert_variant in SALES_CERT_KEYWORDS:
        cert_parts = cert_variant.split()
        # Check both exact match and if all parts are present
        if cert_variant in job_description_lower or all(part in job_description_lower for part in cert_parts):
            score = 2
            break
    
    return True, score

# =============================================================================
# DATABASE FUNCTIONS
# =============================================================================

def init_sales_jobs_mia_db():
    """Create the sales_jobs_mia table if it does not exist."""
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sales_jobs_mia (
                job_url TEXT PRIMARY KEY,
                title TEXT,
                company TEXT,
                location_str TEXT,
                description TEXT,
                date_posted TEXT,
                is_remote BOOLEAN,
                job_type TEXT,
                salary_min NUMERIC,
                salary_max NUMERIC,
                salary_interval TEXT,
                company_url TEXT,
                company_url_direct TEXT,
                company_industry TEXT,
                source_site TEXT,
                scraped_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                processed_by_n8n_at TIMESTAMPTZ NULL,
                ai_analysis_cache JSONB,
                search_term TEXT,
                search_location TEXT,
                n8n_processing_status TEXT,
                relevance_score INTEGER DEFAULT 1,
                interested TEXT DEFAULT 'pending' CHECK (interested IN ('pending', 'interested', 'uninterested'))
            );
            
            -- Ensure columns exist for older tables
            ALTER TABLE sales_jobs_mia
            ADD COLUMN IF NOT EXISTS processed_by_n8n_at TIMESTAMPTZ NULL;

            ALTER TABLE sales_jobs_mia
            ADD COLUMN IF NOT EXISTS ai_analysis_cache JSONB;

            ALTER TABLE sales_jobs_mia
            ADD COLUMN IF NOT EXISTS n8n_processing_status TEXT;
            
            ALTER TABLE sales_jobs_mia
            ADD COLUMN IF NOT EXISTS relevance_score INTEGER DEFAULT 1;
            
            ALTER TABLE sales_jobs_mia
            ADD COLUMN IF NOT EXISTS interested TEXT DEFAULT 'pending' CHECK (interested IN ('pending', 'interested', 'uninterested'));

            ALTER TABLE sales_jobs_mia
            ADD COLUMN IF NOT EXISTS company_url TEXT;

            ALTER TABLE sales_jobs_mia
            ADD COLUMN IF NOT EXISTS company_url_direct TEXT;

            ALTER TABLE sales_jobs_mia
            ADD COLUMN IF NOT EXISTS company_industry TEXT;
            """
        )
        conn.commit()
        print("SUCCESS: Database table 'sales_jobs_mia' initialized successfully")
    except Exception as e:
        print(f"ERROR: Error initializing database: {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

def get_sales_jobs_mia_count():
    """Get current count of jobs in sales_jobs_mia table."""
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM sales_jobs_mia")
        return cursor.fetchone()[0]
    finally:
        cursor.close()
        conn.close()

def batch_save_sales_jobs_mia_to_db(jobs_list):
    """Insert multiple jobs in a single batch operation for better performance."""
    if not jobs_list:
        return 0, 0
    
    # Filter and score all jobs first
    valid_jobs = []
    filtered_count = 0
    
    for job_data in jobs_list:
        # Guard: require non-empty job_url for PK insert
        url = job_data.get('job_url')
        if not url or str(url).strip() == "":
            filtered_count += 1
            continue
        job_data['job_url'] = str(url).strip()
        should_save, score = filter_and_score_job(
            job_data.get('title', ''),
            job_data.get('description', '')
        )
        
        if should_save:
            job_data['relevance_score'] = score
            # Ensure all required columns exist
            required_cols = [
                "job_url", "title", "company", "location_str", "description", "date_posted",
                "is_remote", "job_type", "salary_min", "salary_max", "salary_interval",
                "company_url", "company_url_direct", "company_industry",
                "source_site", "scraped_at", "processed_by_n8n_at", "ai_analysis_cache",
                "n8n_processing_status", "relevance_score", "interested"
            ]
            for col in required_cols:
                job_data.setdefault(col, None)
            if "location_str" not in job_data:
                job_data["location_str"] = job_data.get("location") or job_data.get("location_raw") or None
            # ALWAYS force interested to 'pending' for new jobs
            job_data["interested"] = "pending"
            valid_jobs.append(job_data)
        else:
            filtered_count += 1
    
    # Dedupe within batch to reduce DB conflicts and round-trips
    if valid_jobs:
        seen_urls = set()
        seen_title_company = set()
        deduped = []
        for job in valid_jobs:
            key_url = job.get('job_url')
            key_tc = (job.get('title'), job.get('company'))
            if key_url in seen_urls or key_tc in seen_title_company:
                filtered_count += 1
                continue
            seen_urls.add(key_url)
            seen_title_company.add(key_tc)
            deduped.append(job)
        valid_jobs = deduped

    if not valid_jobs:
        return 0, filtered_count
    
    # Batch insert valid jobs
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, database=DB_NAME, user=DB_USER, password=DB_PASSWORD)
    cursor = conn.cursor()
    
    insert_q = """
        INSERT INTO sales_jobs_mia (
            job_url, title, company, location_str, description, date_posted,
            is_remote, job_type, salary_min, salary_max, salary_interval,
            company_url, company_url_direct, company_industry,
            source_site, scraped_at, processed_by_n8n_at, ai_analysis_cache,
            n8n_processing_status, relevance_score, interested
        ) VALUES %s ON CONFLICT DO NOTHING;
    """
    
    try:
        from psycopg2.extras import execute_values
        
        # Prepare values tuple for batch insert
        values = [
            (
                job['job_url'], job['title'], job['company'], job['location_str'],
                job['description'], job['date_posted'], job['is_remote'], job['job_type'],
                job['salary_min'], job['salary_max'], job['salary_interval'],
                job['company_url'], job['company_url_direct'], job['company_industry'],
                job['source_site'],
                job['scraped_at'], job['processed_by_n8n_at'], job['ai_analysis_cache'],
                job['n8n_processing_status'], job['relevance_score'], job['interested']
            ) for job in valid_jobs
        ]
        
        execute_values(cursor, insert_q, values, page_size=1000)
        conn.commit()
        return len(valid_jobs), filtered_count
        
    except Exception as e:
        conn.rollback()
        print(f"ERROR: Batch insert failed: {e}")
        raise
    finally:
        cursor.close()
        conn.close()

# =============================================================================
# PROXY AND NETWORKING FUNCTIONS
# =============================================================================

try:
    _HOST_IP = requests.get("https://api.ipify.org?format=json", timeout=5).json().get("ip", "unknown")
except Exception:
    _HOST_IP = "unknown"

# Global variables for multiprocessing
PORT_QUEUE = None
BAD_PROXIES = None
PROXY_COOLDOWN = int(os.getenv("PROXY_COOLDOWN", "900"))  # 15 min default

# Cache proxy IP lookups per worker to reduce calls to external services
_SESSION_IP_CACHE = {}
_IP_CACHE_TTL = int(os.getenv("PROXY_IP_CACHE_TTL", "180"))  # seconds

# Rate limiting and adaptive concurrency globals
RATE_LIMIT_STATS = None  # Tracks 429 errors and request success rates
REQUEST_THROTTLE = None  # Global request throttle across all workers
ACTIVE_WORKERS = None    # Dynamic worker count based on rate limiting
SWEET_SPOT_FINDER = None # Sweet spot detection for optimal worker count

def _is_proxy_healthy(session_id: str, ip: str = None) -> bool:
    """Return True if sticky session/IP not marked bad within cooldown window."""
    if BAD_PROXIES is None:
        return True

    key = f"{session_id}:{ip}" if ip else session_id
    return (time.time() - BAD_PROXIES.get(key, 0)) > PROXY_COOLDOWN

def _mark_proxy_bad(session_id: str, ip: str = None):
    """Mark sticky proxy session as bad."""
    if BAD_PROXIES is None:
        return

    key = f"{session_id}:{ip}" if ip else session_id
    BAD_PROXIES[key] = time.time()
    print(f"🚫 Marked proxy session {session_id} (IP: {ip}) as bad ({PROXY_COOLDOWN}s cooldown)")

def _init_port_queue(q, bad_dict, rate_stats, throttle, workers, sweet_spot):
    """Pool initializer: set global shared objects in each worker."""
    global PORT_QUEUE, BAD_PROXIES, RATE_LIMIT_STATS, REQUEST_THROTTLE, ACTIVE_WORKERS, SWEET_SPOT_FINDER
    PORT_QUEUE = q
    BAD_PROXIES = bad_dict
    RATE_LIMIT_STATS = rate_stats
    REQUEST_THROTTLE = throttle
    ACTIVE_WORKERS = workers
    SWEET_SPOT_FINDER = sweet_spot

def _record_rate_limit():
    """Record a 429 rate limit event."""
    if RATE_LIMIT_STATS is not None:
        current_time = time.time()
        RATE_LIMIT_STATS['total_429s'] = RATE_LIMIT_STATS.get('total_429s', 0) + 1
        RATE_LIMIT_STATS['last_429'] = current_time
        
        # Track 429s in last 5 minutes for adaptive response
        recent_429s = RATE_LIMIT_STATS.get('recent_429s', [])
        recent_429s.append(current_time)
        # Keep only last 5 minutes
        RATE_LIMIT_STATS['recent_429s'] = [t for t in recent_429s if current_time - t < 300]

def _record_success():
    """Record a successful request."""
    if RATE_LIMIT_STATS is not None:
        RATE_LIMIT_STATS['total_success'] = RATE_LIMIT_STATS.get('total_success', 0) + 1
        RATE_LIMIT_STATS['last_success'] = time.time()

def _should_throttle_request():
    """Check if we should throttle this request based on rate limiting."""
    if REQUEST_THROTTLE is None or RATE_LIMIT_STATS is None:
        return False
    
    current_time = time.time()
    
    # Get recent 429 count
    recent_429s = RATE_LIMIT_STATS.get('recent_429s', [])
    recent_429_count = len([t for t in recent_429s if current_time - t < 300])  # Last 5 min
    
    # If too many 429s recently, implement aggressive throttling
    if recent_429_count > 50:  # More than 50 429s in 5 minutes
        throttle_delay = min(10.0, recent_429_count * 0.1)  # Up to 10 second delay
        time.sleep(throttle_delay)
        return True
    elif recent_429_count > 20:  # Moderate throttling
        time.sleep(random.uniform(2.0, 5.0))
        return True
    elif recent_429_count > 10:  # Light throttling  
        time.sleep(random.uniform(1.0, 3.0))
        return True
    
    return False

def _get_adaptive_worker_count():
    """Get recommended worker count based on recent rate limiting."""
    if RATE_LIMIT_STATS is None:
        return 40
    
    current_time = time.time()
    recent_429s = RATE_LIMIT_STATS.get('recent_429s', [])
    recent_429_count = len([t for t in recent_429s if current_time - t < 300])
    
    # Adaptive worker scaling based on 429 rate
    if recent_429_count > 100:  # Severe rate limiting
        return max(5, int(40 * 0.1))   # Reduce to 10% capacity
    elif recent_429_count > 50:   # Heavy rate limiting
        return max(10, int(40 * 0.25)) # Reduce to 25% capacity  
    elif recent_429_count > 20:   # Moderate rate limiting
        return max(15, int(40 * 0.5))  # Reduce to 50% capacity
    elif recent_429_count > 10:   # Light rate limiting
        return max(25, int(40 * 0.75)) # Reduce to 75% capacity
    else:                         # Low rate limiting
        return 40                   # Full capacity

def _get_initial_worker_count():
    """Get conservative initial worker count for progressive testing."""
    return 5  # Start with 5 workers for testing, scale up based on success rate

class SweetSpotFinder:
    """Progressive rate limit testing to find optimal worker count."""
    
    def __init__(self):
        self.test_phases = [
            {"workers": 5, "duration": 60, "target_success_rate": 90},
            {"workers": 10, "duration": 60, "target_success_rate": 85}, 
            {"workers": 15, "duration": 60, "target_success_rate": 80},
            {"workers": 20, "duration": 60, "target_success_rate": 75},
            {"workers": 25, "duration": 60, "target_success_rate": 70},
            {"workers": 30, "duration": 60, "target_success_rate": 65},
            {"workers": 35, "duration": 60, "target_success_rate": 60},
            {"workers": 40, "duration": 60, "target_success_rate": 55},
        ]
        self.current_phase = 0
        self.phase_start_time = None
        self.optimal_workers = 5
        self.test_complete = False
        self.phase_stats = []
        
    def should_advance_phase(self, rate_stats):
        """Check if we should move to next testing phase."""
        if self.test_complete or self.phase_start_time is None:
            return False
            
        elapsed = time.time() - self.phase_start_time
        current_phase = self.test_phases[self.current_phase]
        
        # Phase duration reached
        if elapsed >= current_phase["duration"]:
            return True
            
        # Early advancement if success rate too low
        recent_429s = len([t for t in rate_stats.get('recent_429s', []) 
                          if time.time() - t < 60])  # Last minute
        recent_successes = rate_stats.get('total_success', 0)
        recent_total = recent_429s + recent_successes
        
        if recent_total > 10:  # Have enough data
            success_rate = (recent_successes / recent_total) * 100
            if success_rate < (current_phase["target_success_rate"] - 10):
                print(f"🚨 Early phase termination: Success rate {success_rate:.1f}% < target {current_phase['target_success_rate']}% ")
                return True
                
        return False
        
    def advance_phase(self, rate_stats):
        """Move to next testing phase."""
        if self.test_complete:
            return self.optimal_workers
            
        # Record current phase results
        if self.phase_start_time:
            phase_duration = time.time() - self.phase_start_time
            recent_429s = len([t for t in rate_stats.get('recent_429s', []) 
                              if time.time() - t < phase_duration])
            recent_successes = rate_stats.get('total_success', 0)
            recent_total = recent_429s + recent_successes
            
            success_rate = (recent_successes / recent_total * 100) if recent_total > 0 else 0
            throughput = recent_successes / (phase_duration / 60)  # requests per minute
            
            current_phase = self.test_phases[self.current_phase]
            phase_result = {
                "workers": current_phase["workers"],
                "success_rate": success_rate,
                "throughput": throughput,
                "duration": phase_duration,
                "total_requests": recent_total
            }
            self.phase_stats.append(phase_result)
            
            print(f"\nSUMMARY PHASE {self.current_phase + 1} RESULTS:")
            print(f"   WORKERS: {current_phase['workers']}")
            print(f"   SUCCESS RATE: {success_rate:.1f}%")
            print(f"   THROUGHPUT: {throughput:.1f} req/min")
            print(f"   TOTAL REQUESTS: {recent_total}")
            
            # Update optimal if this phase performed well
            if success_rate >= current_phase["target_success_rate"]:
                self.optimal_workers = current_phase["workers"]
                print(f"   TARGET New optimal: {self.optimal_workers} workers")
            else:
                print(f"   WARNING Below target {current_phase['target_success_rate']}% - stopping progression")
                self.test_complete = True
                return self.optimal_workers
        
        # Move to next phase
        self.current_phase += 1
        if self.current_phase >= len(self.test_phases):
            print(f"\nFINISH SWEET SPOT TESTING COMPLETE!")
            print(f"   TARGET Optimal Workers: {self.optimal_workers}")
            self._print_summary()
            self.test_complete = True
            return self.optimal_workers
            
        # Start next phase
        next_phase = self.test_phases[self.current_phase]
        self.phase_start_time = time.time()
        print(f"\nSTARTING PHASE {self.current_phase + 1}/{len(self.test_phases)}")
        print(f"   TESTING {next_phase['workers']} workers")
        print(f"   TARGET success rate: {next_phase['target_success_rate']}%")
        print(f"   DURATION: {next_phase['duration']}s")
        
        return next_phase["workers"]
        
    def get_current_target_workers(self):
        """Get current target worker count for testing."""
        if self.test_complete:
            return self.optimal_workers
        if self.current_phase < len(self.test_phases):
            return self.test_phases[self.current_phase]["workers"]
        return self.optimal_workers
        
    def start_first_phase(self):
        """Initialize first testing phase."""
        self.phase_start_time = time.time()
        first_phase = self.test_phases[0]
        print(f"\nTESTING STARTING RATE LIMIT TESTING")
        print(f"   PHASE 1/{len(self.test_phases)}: {first_phase['workers']} workers")
        print(f"   TARGET: {first_phase['target_success_rate']}% success rate")
        print(f"   DURATION: {first_phase['duration']}s per phase\n")
        return first_phase["workers"]
        
    def _print_summary(self):
        """Print complete testing summary."""
        print(f"\nCHART TESTING SUMMARY:")
        for i, stat in enumerate(self.phase_stats):
            status = "SUCCESS" if stat["success_rate"] >= self.test_phases[i]["target_success_rate"] else "FAILED"
            print(f"   {status} Phase {i+1}: {stat['workers']} workers → {stat['success_rate']:.1f}% success, {stat['throughput']:.1f} req/min")
        print(f"\nTROPHY FINAL RECOMMENDATION: {self.optimal_workers} workers")

def _init_sweet_spot_finder():
    """Initialize the sweet spot finder for testing."""
    global SWEET_SPOT_FINDER
    if SWEET_SPOT_FINDER is None:
        SWEET_SPOT_FINDER = SweetSpotFinder()
    return SWEET_SPOT_FINDER

def new_proxy_session_id(worker_id: int = None) -> str:
    suffix = f"_{worker_id}" if worker_id is not None else ""
    return f"{uuid.uuid4().hex[:8]}{suffix}"

def build_session_proxy(session_id: str):
    user = f"user-{PROXY_USERNAME}-session-{session_id}-sessionduration-{PROXY_SESSION_DURATION_MINUTES}"
    return {
        "http": f"http://{user}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}",
        "https": f"http://{user}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}",
    }

def random_proxy_config():
    return build_session_proxy(new_proxy_session_id())

def get_verified_proxy(max_attempts: int = 6):
    for attempt in range(max_attempts):
        cfg = random_proxy_config()
        try:
            proxy_ip = requests.get("https://api.ipify.org?format=json", proxies=cfg, timeout=10).json().get("ip")
            if proxy_ip and proxy_ip != _HOST_IP:
                return cfg, proxy_ip
        except Exception:
            pass
    raise RuntimeError("Unable to acquire a working proxy distinct from host IP")

# =============================================================================
# JOB SEARCH WORKER FUNCTION
# =============================================================================

def search_sales_job_process_safe(args):
    """Worker process: scrape a single (title, location) combo and persist to DB."""
    job_title, location, search_id, total_searches, worker_id = args

    print(f"SEARCH [{search_id}/{total_searches}] {job_title} in {location}")

    max_retries = 4
    base_delay = 1.5
    
    for attempt in range(max_retries):
        try:
            # Obtain exclusive sticky proxy session from queue
            attempts_session = 0
            session_id = None
            session_burned = False
            proxy_ip = None
            proxy_cfg = None

            if USE_PROXY:
                while True:
                    candidate_session = PORT_QUEUE.get()
                    
                    # Get current proxy IP for health check
                    temp_proxy_cfg = build_session_proxy(candidate_session)
                    
                    # For sticky sessions, get the current IP to check health with cache
                    current_ip = None
                    cached = _SESSION_IP_CACHE.get(candidate_session)
                    if cached and (time.time() - cached[1] < _IP_CACHE_TTL):
                        current_ip = cached[0]
                    else:
                        try:
                            current_ip = requests.get("https://api.ipify.org?format=json", proxies=temp_proxy_cfg, timeout=5).json().get("ip")
                            if current_ip:
                                _SESSION_IP_CACHE[candidate_session] = (current_ip, time.time())
                        except Exception:
                            current_ip = None
                    
                    if _is_proxy_healthy(candidate_session, current_ip):
                        session_id = candidate_session
                        proxy_ip = current_ip
                        break
                    
                    PORT_QUEUE.put(new_proxy_session_id(worker_id))
                    attempts_session += 1
                    # Avoid unreliable qsize(); sleep after a few failed attempts
                    if attempts_session >= 5:
                        time.sleep(random.uniform(1, 2))
                        attempts_session = 0
                        
                proxy_cfg = build_session_proxy(session_id)

                # Verify external IP distinct from host (use cached health check when available)
                if not proxy_ip:
                    cached = _SESSION_IP_CACHE.get(session_id)
                    if cached and (time.time() - cached[1] < _IP_CACHE_TTL):
                        proxy_ip = cached[0]
                    else:
                        try:
                            proxy_ip = requests.get("https://api.ipify.org?format=json", proxies=proxy_cfg, timeout=5).json().get("ip")
                            if proxy_ip:
                                _SESSION_IP_CACHE[session_id] = (proxy_ip, time.time())
                        except Exception:
                            proxy_ip = None
                        
                if not proxy_ip or proxy_ip == _HOST_IP:
                    print(f"WARNING Worker {worker_id}: proxy session {session_id} ineffective (IP={proxy_ip}). Retrying.")
                    _mark_proxy_bad(session_id, proxy_ip)
                    PORT_QUEUE.put(new_proxy_session_id(worker_id))
                    session_burned = True
                    if attempt == max_retries - 1:
                        return (pd.DataFrame(), f"Proxy issues after {max_retries} attempts")
                    continue

                print(f"REFRESH Worker {worker_id}: proxy session {session_id} → {proxy_ip}")

            # Randomize LinkedIn request headers for better fingerprinting
            try:
                import jobspy.linkedin.constant as li_const
                
                # Rotate user agent and language
                li_const.headers["user-agent"] = unique_user_agent()
                li_const.headers["accept-language"] = random.choice(LANG_POOL)
                
                if ENABLE_EXTRA_HEADERS:
                    # Add realistic additional headers
                    additional_headers = random.choice(LINKEDIN_HEADERS_POOL)
                    for k, v in additional_headers.items():
                        li_const.headers[k] = v

                    # Dynamically set sec-fetch headers
                    li_const.headers["sec-fetch-dest"] = random.choice(["document", "empty"])
                    li_const.headers["sec-fetch-mode"] = random.choice(["navigate", "cors"])
                    li_const.headers["sec-fetch-site"] = random.choice(["none", "same-origin"])
                        
                    # Randomize other common headers
                    if random.random() < 0.7:  # 70% chance
                        li_const.headers["dnt"] = random.choice(["1", "0"])
                    if random.random() < 0.5:  # 50% chance  
                        li_const.headers["cache-control"] = random.choice(["no-cache", "max-age=0"])
                    
            except Exception as e:
                print(f"WARNING Header randomization failed: {e}")
                pass

            # Worker coordination - stagger requests
            worker_offset = (worker_id % 10) * 0.05  # Smaller offset per worker
            initial_jitter = random.uniform(0.1, 0.5) + worker_offset
            time.sleep(initial_jitter)
            
            # Check if we should throttle this request based on recent rate limiting
            _should_throttle_request()

            # LinkedIn-only scraping for pure rate limit testing
            jobs_df = scrape_jobs(
                site_name=["linkedin"],
                search_term=job_title,
                location=location,
                results_wanted=150,
                hours_old=48,  # past 48 hours
                country_indeed="USA",
                is_remote=(str(location).strip().lower() == "remote"),
                linkedin_fetch_description=LINKEDIN_FETCH_DESCRIPTION,
                description_format="html",
                proxies=proxy_cfg if USE_PROXY else None,
            )
            time.sleep(random.uniform(0.1, 0.3))

            if len(jobs_df) == 0:
                _mark_proxy_bad(session_id, proxy_ip)
                PORT_QUEUE.put(new_proxy_session_id(worker_id))
                session_burned = True
                print(f"⚪ [{search_id}] No jobs found for {job_title} in {location}")
                return (pd.DataFrame(), f"No results for {job_title} in {location}")

            # Record successful request
            _record_success()

            # Enrich and persist with batch operation
            jobs_df["scraped_at"] = datetime.now()
            jobs_df["source_site"] = "linkedin"

            # Convert to list of dicts and clean NaN values
            jobs_list = []
            for _, row in jobs_df.iterrows():
                jd = row.to_dict()
                jd = {k: (None if pd.isna(v) else v) for k, v in jd.items()}
                jobs_list.append(jd)
            
            # Use batch save for better performance
            saved_count, filtered_count = batch_save_sales_jobs_mia_to_db(jobs_list)

            print(f"SUCCESS [{search_id}] Saved {saved_count}/{len(jobs_df)} jobs for {job_title} in {location} (filtered: {filtered_count})")
            return (jobs_df, f"Saved {saved_count}/{len(jobs_df)} jobs (filtered: {filtered_count})")

        except Exception as e:
            error_str = str(e).lower()
            print(f"FAILED [{search_id}] Attempt {attempt+1} failed: {e}")
            
            # Different handling for different error types
            if 'duplicate key value violates unique constraint' in error_str or 'unique constraint' in error_str:
                # Dupes already exist in DB; don't retry the whole attempt
                return (pd.DataFrame(), "Skipped: duplicates already existed in DB")
            if '429' in error_str or 'too many requests' in error_str:
                _record_rate_limit()  # Track rate limit for adaptive scaling
                _mark_proxy_bad(session_id, proxy_ip)
                PORT_QUEUE.put(new_proxy_session_id(worker_id))
                session_burned = True
                print(f"🚫 Proxy session {session_id} (IP: {proxy_ip}) hit 429 – replaced")
                
                # Enhanced exponential backoff for rate limits
                recent_429s = len(RATE_LIMIT_STATS.get('recent_429s', [])) if RATE_LIMIT_STATS else 0
                backoff_multiplier = min(8, 2 + (recent_429s // 10))  # Increase backoff based on recent 429s
                delay = base_delay * (backoff_multiplier ** attempt) + random.uniform(2, 8)
            elif 'name resolution' in error_str or 'dns' in error_str:
                print(f"🌐 DNS resolution error on attempt {attempt+1}")
                delay = base_delay * (2 ** attempt) + random.uniform(0.5, 2.0)
            elif 'timeout' in error_str or 'connection' in error_str:
                print(f"TIMEOUT Network timeout on attempt {attempt+1}")
                delay = base_delay * (1.5 ** attempt) + random.uniform(0.2, 1.0)
            else:
                delay = base_delay * (2 ** attempt) + random.uniform(0.1, 0.8)
            
            if attempt < max_retries - 1:
                print(f"WAITING {delay:.1f}s before retry {attempt+2}/{max_retries}")
                time.sleep(delay)
            else:
                return (pd.DataFrame(), f"Failed after {max_retries} attempts: {e}")

        finally:
            if 'session_id' in locals() and session_id and not session_burned:
                with contextlib.suppress(Exception):
                    PORT_QUEUE.put(session_id)
    
    return (pd.DataFrame(), f"All {max_retries} attempts failed for {job_title} in {location}")

# =============================================================================
# MAIN ORCHESTRATOR FUNCTION
# =============================================================================

def search_sales_jobs_mia_multiprocess():
    """Main function to run all searches in parallel using multiprocessing."""
    locations = LOCATIONS

    total_combos = len(SALES_JOB_TITLES) * len(locations)
    if PRODUCTION_MODE:
        print("ROCKET Starting Sales PRODUCTION job search...")
        print(f"TARGET {len(SALES_JOB_TITLES)} job titles × {len(locations)} locations = {total_combos} searches")
        print(f"LIGHTNING Using tested optimal configuration: {TESTED_OPTIMAL_WORKERS} workers")
    else:
        print("TESTING Starting Sales RATE LIMIT TESTING...")
        print(f"TARGET {len(SALES_JOB_TITLES)} job titles × {len(locations)} locations = {total_combos} searches")
        print(f"MICROSCOPE Will test 5-40 workers to find optimal configuration")

    combos = []
    sid = 0
    for title in SALES_JOB_TITLES:
        for loc in locations:
            sid += 1
            combos.append((title, loc, sid, total_combos, sid % 40))  # Distribute across 40 workers

    start = time.time()
    all_jobs = pd.DataFrame()

    # Build shared resources for progressive testing
    manager = mp.Manager()
    port_queue = manager.Queue()
    rate_limit_stats = manager.dict()
    request_throttle = manager.dict()
    active_workers = manager.Value('i', 5)  # Start with testing baseline
    
    # Initialize rate limiting stats
    rate_limit_stats['total_429s'] = 0
    rate_limit_stats['total_success'] = 0
    rate_limit_stats['recent_429s'] = []
    
    if PRODUCTION_MODE:
        # Use proven optimal configuration from testing
        workers = TESTED_OPTIMAL_WORKERS
        sweet_spot_finder = None
        
        for _ in range(max(PROXY_SESSION_COUNT, workers)):  # Fewer proxy copies for smaller worker count
            port_queue.put(new_proxy_session_id())
            
        print(f"ROCKET LINKEDIN PRODUCTION MODE")
        proxy_mode = f"{max(PROXY_SESSION_COUNT, workers)} Decodo sticky sessions" if USE_PROXY else "direct connections"
        print(f"LIGHTNING Using {workers} workers with {proxy_mode}")
        print(f"CHART Configuration based on rate limit testing results")
    else:
        # Progressive testing mode
        sweet_spot_finder = _init_sweet_spot_finder()
        workers = sweet_spot_finder.start_first_phase()
        
        for _ in range(max(PROXY_SESSION_COUNT, 60)):  # More proxy copies for testing
            port_queue.put(new_proxy_session_id())

        print(f"TESTING LINKEDIN RATE LIMIT TESTING MODE")
        print(f"LIGHTNING Starting Phase 1 with {workers} workers using Decodo sticky sessions")
        print(f"CHART Will progressively test up to 40 workers to find LinkedIn's sweet spot")

    # Use appropriate max workers based on mode
    max_workers = workers if PRODUCTION_MODE else 40
    with ProcessPoolExecutor(max_workers=max_workers, initializer=_init_port_queue, 
                            initargs=(port_queue, manager.dict(), rate_limit_stats, request_throttle, active_workers, sweet_spot_finder)) as ex:
        futures = {}
        
        if PRODUCTION_MODE:
            # Production mode: Submit all jobs with optimal worker count
            for combo in combos:
                futures[ex.submit(search_sales_job_process_safe, combo)] = combo
            
            # Track completion for production
            completed = 0
            last_status_report = time.time()
            
        else:
            # Testing mode: Progressive job submission
            completed = 0
            last_status_report = time.time()
            active_job_count = 0
            submitted_jobs = 0
            
            # Submit initial batch based on current test phase
            current_target_workers = sweet_spot_finder.get_current_target_workers()
            
            for combo in combos[:current_target_workers]:
                futures[ex.submit(search_sales_job_process_safe, combo)] = combo
                submitted_jobs += 1
                active_job_count += 1
            
            remaining_combos = combos[current_target_workers:]
        
        for fut in as_completed(futures):
            df, _ = fut.result()
            if len(df):
                all_jobs = pd.concat([all_jobs, df], ignore_index=True)
            
            completed += 1
            
            if PRODUCTION_MODE:
                # Production mode: Simple progress reporting
                if (completed % 50 == 0) or (time.time() - last_status_report > 120):
                    success_count = rate_limit_stats.get('total_success', 0)
                    total_429s = rate_limit_stats.get('total_429s', 0)
                    total_requests = success_count + total_429s
                    success_rate = (success_count / total_requests * 100) if total_requests > 0 else 0
                    throughput = success_count / ((time.time() - start) / 60) if success_count > 0 else 0
                    
                    print(f"\nROCKET PRODUCTION STATUS [{completed}/{total_combos}]:")
                    print(f"   SUCCESS RATE: {success_rate:.1f}%")
                    print(f"   THROUGHPUT: {throughput:.1f} req/min")
                    print(f"   DISK Jobs Found: {len(all_jobs)}")
                    print(f"   TIMER Elapsed: {(time.time() - start)/60:.1f} min\n")
                    
                    last_status_report = time.time()
            
            else:
                # Testing mode: Progressive testing logic
                active_job_count -= 1
                
                # Check if we should advance to next testing phase
                if sweet_spot_finder.should_advance_phase(rate_limit_stats):
                    new_target_workers = sweet_spot_finder.advance_phase(rate_limit_stats)
                    
                    # Submit more jobs if phase increased worker count
                    if new_target_workers > current_target_workers and remaining_combos:
                        additional_jobs = new_target_workers - current_target_workers
                        new_jobs_to_submit = remaining_combos[:additional_jobs]
                        remaining_combos = remaining_combos[additional_jobs:]
                        
                        for combo in new_jobs_to_submit:
                            futures[ex.submit(search_sales_job_process_safe, combo)] = combo
                            submitted_jobs += 1
                            active_job_count += 1
                        
                        print(f"CHART Scaling up: Added {len(new_jobs_to_submit)} more jobs (now {active_job_count} active)")
                    
                    current_target_workers = new_target_workers
                
                # Submit one more job to maintain target worker count (if not testing complete)
                elif remaining_combos and active_job_count < current_target_workers and not sweet_spot_finder.test_complete:
                    combo = remaining_combos.pop(0)
                    futures[ex.submit(search_sales_job_process_safe, combo)] = combo
                    submitted_jobs += 1
                    active_job_count += 1
                
                # Report status every 15 searches or 1 minute during testing
                if (completed % 15 == 0) or (time.time() - last_status_report > 60):
                    recent_429s = len([t for t in rate_limit_stats.get('recent_429s', []) 
                                     if time.time() - t < 60])  # Last minute for testing
                    success_count = rate_limit_stats.get('total_success', 0)
                    total_429s = rate_limit_stats.get('total_429s', 0)
                    total_requests = success_count + total_429s
                    
                    success_rate = (success_count / total_requests * 100) if total_requests > 0 else 0
                    throughput = success_count / ((time.time() - start) / 60) if success_count > 0 else 0
                    
                    print(f"\nTESTING STATUS [{completed}/{total_combos}] - Phase {sweet_spot_finder.current_phase + 1}")
                    print(f"   WORKERS Current Workers: {current_target_workers}")
                    print(f"   REFRESH Active Jobs: {active_job_count}")
                    print(f"   REFRESH Recent 429s (1min): {recent_429s}")
                    print(f"   SUCCESS Total Success: {success_count}")  
                    print(f"   BLOCKED Total 429s: {total_429s}")
                    print(f"   CHART Success Rate: {success_rate:.1f}%")
                    print(f"   THROUGHPUT: {throughput:.1f} req/min")
                    print(f"   DISK Jobs Found: {len(all_jobs)}")
                    
                    # Show phase progress
                    if sweet_spot_finder.phase_start_time:
                        phase_elapsed = time.time() - sweet_spot_finder.phase_start_time
                        phase_duration = sweet_spot_finder.test_phases[sweet_spot_finder.current_phase]["duration"]
                        phase_progress = min(100, (phase_elapsed / phase_duration) * 100)
                        print(f"   TIMER Phase Progress: {phase_progress:.1f}% ({phase_elapsed:.0f}s/{phase_duration}s)")
                    
                    print()
                    last_status_report = time.time()

    duration = time.time() - start
    
    final_success = rate_limit_stats.get('total_success', 0)
    final_429s = rate_limit_stats.get('total_429s', 0) 
    final_total = final_success + final_429s
    final_success_rate = (final_success / final_total * 100) if final_total > 0 else 0
    
    if PRODUCTION_MODE:
        # Production mode summary
        print(f"\nROCKET LINKEDIN PRODUCTION RUN COMPLETE!")
        print(f"TIMER Total Time: {duration/60:.1f} minutes")
        print(f"CHART PRODUCTION RESULTS:")
        print(f"   WORKERS Used: {TESTED_OPTIMAL_WORKERS} (tested optimal)")
        print(f"   SUCCESS Total Successful Requests: {final_success}")
        print(f"   BLOCKED Total Rate Limited (429s): {final_429s}")
        print(f"   CHART Overall Success Rate: {final_success_rate:.1f}%")
        print(f"   DISK Total Jobs Found: {len(all_jobs)}")
    else:
        # Testing mode summary
        print(f"\nFINISH LINKEDIN RATE LIMIT TESTING COMPLETE!")
        print(f"TIMER Total Testing Time: {duration/60:.1f} minutes")
        print(f"CHART LINKEDIN TESTING RESULTS:")
        print(f"   SUCCESS Total Successful Requests: {final_success}")
        print(f"   BLOCKED Total Rate Limited (429s): {final_429s}")
        print(f"   CHART Overall Success Rate: {final_success_rate:.1f}%")
        print(f"   TARGET Optimal LinkedIn Workers: {sweet_spot_finder.optimal_workers}")
        print(f"   DISK Total Jobs Found: {len(all_jobs)}")
    
    if len(all_jobs) == 0:
        print("\nFAILED No jobs found across all searches")
        return all_jobs

    # Deduplicate and sort
    all_jobs = (
        all_jobs.drop_duplicates(subset=["job_url"], keep="first")
        .sort_values("date_posted", ascending=False)
    )

    if PRODUCTION_MODE:
        print(f"\nCELEBRATION LinkedIn production job search complete!")
        print(f"CHART Unique jobs: {len(all_jobs)}")
        print(f"\nSUCCESS Used optimal configuration: {TESTED_OPTIMAL_WORKERS} workers")
    else:
        print(f"\nCELEBRATION LinkedIn rate limit testing and job search complete!")
        print(f"CHART Unique jobs: {len(all_jobs)}")
        print(f"\nLIGHTBULB RECOMMENDATION: Use {sweet_spot_finder.optimal_workers} workers for future LinkedIn scraping")
        print(f"   To use this setting: Set TESTED_OPTIMAL_WORKERS = {sweet_spot_finder.optimal_workers} and PRODUCTION_MODE = True")
    return all_jobs

# =============================================================================
# MAIN EXECUTION
# =============================================================================

if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)

    logging.basicConfig(stream=sys.stdout, level=logging.INFO, 
                       format="%(asctime)s - %(levelname)s - %(message)s")
    logging.info("Sales Job Search Script Started")

    # Initialize database
    try:
        init_sales_jobs_mia_db()
    except Exception as e:
        print(f"ERROR Database initialization failed: {e}")
        print("Please ensure PostgreSQL is running and credentials are correct")
        sys.exit(1)

    # Get initial job count and send start notification
    start_time = datetime.now()
    try:
        initial_job_count = get_sales_jobs_mia_count()
    except Exception as e:
        print(f"ERROR Cannot connect to database: {e}")
        sys.exit(1)
    
    start_msg = f"ROCKET **Sales Job Search Started**\n\n" \
                f"TIME **Start Time:** {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n" \
                f"CHART **Current Jobs in DB:** {initial_job_count:,}\n" \
                f"SEARCH **Searching:** {len(SALES_JOB_TITLES)} job titles across {len(LOCATIONS)} locations"
    
    send_discord_notification(start_msg, "SEARCH Sales Job Search Started", color=3447003)

    try:
        # Run search
        results = search_sales_jobs_mia_multiprocess()
        
        # Get final job count and calculate stats
        end_time = datetime.now()
        final_job_count = get_sales_jobs_mia_count()
        jobs_added = final_job_count - initial_job_count
        duration = end_time - start_time
        
        # Send completion notification
        completion_msg = f"SUCCESS **Sales Job Search Completed**\n\n" \
                        f"TIME **Start Time:** {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n" \
                        f"FINISH **End Time:** {end_time.strftime('%Y-%m-%d %H:%M:%S')}\n" \
                        f"TIMER **Duration:** {duration.total_seconds()/60:.1f} minutes\n" \
                        f"CHART **Jobs Added:** {jobs_added:,} new jobs\n" \
                        f"CHART **Total Jobs in DB:** {final_job_count:,}\n" \
                        f"SEARCH **Unique Results Found:** {len(results):,}"
        
        send_discord_notification(completion_msg, "SUCCESS Sales Job Search Complete", color=65280)
        
        print(json.dumps({
            "status": "success",
            "jobs_found": len(results),
            "jobs_added_to_db": jobs_added,
            "total_jobs_in_db": final_job_count,
            "duration_minutes": round(duration.total_seconds()/60, 1)
        }))
        
    except Exception as e:
        # Send error notification
        end_time = datetime.now()
        duration = end_time - start_time
        
        error_msg = f"FAILED **Sales Job Search Failed**\n\n" \
                   f"TIME **Start Time:** {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n" \
                   f"EXPLOSION **Failed At:** {end_time.strftime('%Y-%m-%d %H:%M:%S')}\n" \
                   f"TIMER **Duration:** {duration.total_seconds()/60:.1f} minutes\n" \
                   f"ALARM **Error:** {str(e)}"
        
        send_discord_notification(error_msg, "FAILED Sales Job Search Failed", color=16711680)
        
        print(json.dumps({
            "status": "error",
            "error": str(e),
            "duration_minutes": round(duration.total_seconds()/60, 1)
        }))
        raise 