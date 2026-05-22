"""Celery application and rate-limiting configuration.

Concurrency model:
  - Each user becomes an independent task dispatched to a Celery worker.
  - Platform-level rate limits (via Celery semaphores) prevent hammering
    the same site from 50 workers simultaneously.
  - Workers are grouped by type: selenium workers (heavy, low concurrency)
    and api workers (light, high concurrency).

Queue topology:
  scraping.selenium  — LinkedIn, Indeed, Sapo, Expresso (1 at a time per worker)
  scraping.api       — ITJobs, Companies, Landing (up to 4 parallel per worker)
  post.scoring       — scorer + enrichment (CPU-light)
  post.dispatch      — webhook dispatch (I/O-light)
"""

import os
from celery import Celery
from celery.utils.log import get_task_logger
from kombu import Queue, Exchange

REDIS_URL = os.environ.get('REDIS_URL', 'redis://redis:6379/0')

app = Celery('searching_eng', broker=REDIS_URL, backend=REDIS_URL)

app.conf.update(
    # Serialisation
    task_serializer='json',
    result_serializer='json',
    accept_content=['json'],
    timezone='UTC',
    enable_utc=True,

    # Worker behaviour
    worker_prefetch_multiplier=1,   # one task at a time per worker process
    task_acks_late=True,            # ack only after task completes (safe retry on crash)
    task_reject_on_worker_lost=True,

    # Result expiry — keep task results for 24h (used by Flower + status checks)
    result_expires=86400,

    # Queue definitions
    task_queues=(
        # Selenium scrapers: max_concurrency=2 — Chrome is memory-heavy
        Queue('scraping.selenium',
              Exchange('scraping.selenium'),
              routing_key='scraping.selenium'),
        # API scrapers: max_concurrency=8 — cheap HTTP requests
        Queue('scraping.api',
              Exchange('scraping.api'),
              routing_key='scraping.api'),
        # Post-processing (scorer, enrichment, webhook)
        Queue('post.processing',
              Exchange('post.processing'),
              routing_key='post.processing'),
    ),
    task_default_queue='scraping.api',
    task_default_exchange='scraping.api',
    task_default_routing_key='scraping.api',

    # Platform-level rate limits — prevent 50 workers from hitting LinkedIn at once.
    # Format: N/period where period is s(econd), m(inute), h(our).
    # These are per-worker limits; total system rate = limit × number_of_workers.
    task_annotations={
        'automation.tasks.scrape_user_linkedin': {'rate_limit': '3/m'},
        'automation.tasks.scrape_user_indeed':   {'rate_limit': '3/m'},
        'automation.tasks.scrape_user_sapo':     {'rate_limit': '5/m'},
        'automation.tasks.scrape_user_expresso': {'rate_limit': '5/m'},
        'automation.tasks.scrape_user_task':     {'rate_limit': '10/m'},
    },

    # Retry policy for transient failures
    task_autoretry_for=(Exception,),
    task_max_retries=2,
    task_retry_backoff=True,
    task_retry_backoff_max=300,
    task_retry_jitter=True,
)

logger = get_task_logger(__name__)
