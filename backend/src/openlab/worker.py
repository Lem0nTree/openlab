"""Durable PostgreSQL worker for OpenLab background jobs."""

import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .db import SessionLocal
from .models import InboxItem, Job
from .providers import ProviderError
from .services import process_inbox_item

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("openlab.worker")


def claim_one() -> Job | None:
    with SessionLocal.begin() as db:
        job = db.scalar(
            select(Job)
            .where(Job.status == "queued")
            .order_by(Job.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.leased_until = datetime.now(UTC) + timedelta(minutes=5)
        return job


def run_job(job_id: str) -> None:
    with SessionLocal.begin() as db:
        job = db.get(Job, job_id)
        if job is None or job.status != "running":
            return
        try:
            if job.kind != "inbox.process":
                raise ProviderError(f"No enabled handler for job kind: {job.kind}")
            inbox_id = str(job.payload.get("inbox_id", ""))
            inbox = db.get(InboxItem, inbox_id)
            if not inbox or inbox.lab_id != job.lab_id:
                raise ProviderError("Inbox item for job is unavailable")
            inbox.status = "processing"
            process_inbox_item(db, inbox)
            job.status = "completed"
            job.last_error = None
        except ProviderError as exc:
            job.status = "dead_letter" if job.attempts >= job.max_attempts else "queued"
            job.last_error = str(exc)
        finally:
            job.leased_until = None


def main() -> None:
    logger.info("OpenLab worker started")
    while True:
        job = claim_one()
        if job is None:
            time.sleep(2)
        else:
            run_job(job.id)


if __name__ == "__main__":
    main()
