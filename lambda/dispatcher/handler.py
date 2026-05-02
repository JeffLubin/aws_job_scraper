import json
import logging
import os
from itertools import product

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sqs = boto3.client("sqs")

SEARCH_TERMS = [
    "Account Executive",
    "Account Manager",
    "GTM Engineer",
]
LOCATIONS = [
    "Miami, FL",
    "Atlanta, GA",
    "Austin, TX",
    "New York, NY",
    "Seattle, WA",
    "Raleigh, NC",
]
HOURS_OLD_BUCKETS = [168]


def _chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _build_messages():
    # Tunable matrix: 6 sales search terms x 4 locations x 3 recency buckets = 72 jobs.
    return [
        {
            "search_term": search_term,
            "location": location,
            "hours_old": hours_old,
        }
        for search_term, location, hours_old in product(
            SEARCH_TERMS,
            LOCATIONS,
            HOURS_OLD_BUCKETS,
        )
    ]


def lambda_handler(event, context):
    queue_url = os.environ["SCRAPE_QUEUE_URL"]
    messages = _build_messages()
    failures = []

    for chunk in _chunks(messages, 10):
        response = sqs.send_message_batch(
            QueueUrl=queue_url,
            Entries=[
                {
                    "Id": str(index),
                    "MessageBody": json.dumps(message),
                }
                for index, message in enumerate(chunk)
            ],
        )
        failures.extend(response.get("Failed", []))

    if failures:
        logger.error("SQS batch dispatch failures: %s", json.dumps(failures))
        raise RuntimeError(f"Failed to dispatch {len(failures)} messages")

    logger.info(f"Dispatched {len(messages)} messages")
    return {"dispatched": len(messages)}
