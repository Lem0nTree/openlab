"""Durable PostgreSQL worker for OpenLab background jobs."""

import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from .db import SessionLocal
from .intelligence import (
    cleanup_expired_job_results,
    complete_job,
    embed_thing,
    plan_build,
)
from .models import InboxItem, Job
from .providers import ProviderError
from .schematics import propose_schematic
from .services import cleanup_expired_attachments, process_inbox_item

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("openlab.worker")


def claim_one() -> Job | None:
    with SessionLocal.begin() as db:
        job = db.scalar(
            select(Job)
            .where(
                or_(
                    Job.status == "queued",
                    (Job.status == "running") & (Job.leased_until < datetime.now(UTC)),
                )
            )
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
            if job.kind == "inbox.process":
                inbox_id = str(job.payload.get("inbox_id", ""))
                inbox = db.get(InboxItem, inbox_id)
                if not inbox or inbox.lab_id != job.lab_id:
                    raise ProviderError("Inbox item for job is unavailable")
                inbox.status = "processing"
                process_inbox_item(db, inbox)
                complete_job(job, {"inbox_id": inbox.id})
            elif job.kind == "thing.embed":
                result = embed_thing(db, job.lab_id, str(job.payload.get("thing_id", "")))
                complete_job(job, result)
            elif job.kind == "project.plan":
                result = plan_build(
                    db,
                    str(job.payload.get("project_id", "")),
                    job.lab_id,
                    str(job.payload["goal"]) if job.payload.get("goal") else None,
                )
                complete_job(job, result, temporary=True)
            elif job.kind == "project.schematic":
                result = propose_schematic(
                    db,
                    str(job.payload.get("project_id", "")),
                    job.lab_id,
                    str(job.payload["notes"]) if job.payload.get("notes") else None,
                )
                complete_job(job, result, temporary=True)
            else:
                raise ProviderError(f"No enabled handler for job kind: {job.kind}")
        except ProviderError as exc:
            job.status = "dead_letter" if job.attempts >= job.max_attempts else "queued"
            job.last_error = str(exc)
        except Exception as exc:  # pragma: no cover - final worker safety net
            logger.exception("Unexpected job failure for %s", job.id)
            job.status = "dead_letter" if job.attempts >= job.max_attempts else "queued"
            job.last_error = f"Unexpected worker error: {str(exc)[:2000]}"
        finally:
            job.leased_until = None


def cleanup_artifacts() -> None:
    with SessionLocal.begin() as db:
        count = cleanup_expired_attachments(db)
        if count:
            logger.info("Purged %s expired Inbox artifacts", count)
        expired_jobs = cleanup_expired_job_results(db)
        if expired_jobs:
            logger.info("Expired %s temporary job results", expired_jobs)


def main() -> None:
    logger.info("OpenLab worker started")
    next_cleanup = datetime.now(UTC)
    while True:
        if datetime.now(UTC) >= next_cleanup:
            cleanup_artifacts()
            next_cleanup = datetime.now(UTC) + timedelta(hours=1)
        job = claim_one()
        if job is None:
            time.sleep(2)
        else:
            run_job(job.id)


if __name__ == "__main__":
    main()
