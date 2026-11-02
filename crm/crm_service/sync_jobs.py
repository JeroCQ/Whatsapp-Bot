"""Only the worker reads sources; API requests persist intent in the CRM DB."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from crm_service.models import SyncRequest, now
from crm_service.sync import Synchronizer


def job_response(job: SyncRequest) -> dict[str, Any]:
    return {
        "id": job.id,
        "business_id": job.business_id,
        "status": job.status,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "result": job.result,
    }


def consume_one(
    worker: Synchronizer, sessions: sessionmaker[Session], business_id: str | None = None
) -> str | None:
    with sessions.begin() as session:
        query = select(SyncRequest).where(SyncRequest.status == "pending")
        if business_id is not None:
            query = query.where(SyncRequest.business_id == business_id)
        job = session.scalar(
            query.order_by(SyncRequest.updated_at, SyncRequest.id).with_for_update(skip_locked=True).limit(1)
        )
        if job is None:
            return None
        if job.business_id not in worker.settings.businesses:
            job.status, job.result, job.updated_at = "failed", {job.business_id: "business_removed"}, now()
            return job.business_id
        # The row lock expires automatically if a worker dies. No stranded 'running' state.
        result = worker.run(job.business_id)
        state = result.get(job.business_id, "failed")
        job.updated_at, job.result = now(), result
        job.status = "done" if state == "ok" else ("failed" if state == "failed" else "pending")
        return job.business_id
