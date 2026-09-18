import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from celery import Celery
from sqlalchemy import or_, select, update

from backend.config import settings
from backend.db import now, session_scope
from backend.models import Document, EvaluationReport, GraderCalibration, Job, Run, RunEvent

logger = logging.getLogger(__name__)
celery_app = Celery("workspace", broker=settings().redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "dispatch-outbox": {"task": "workspace.dispatch", "schedule": 3.0},
        "purge-expired-payloads": {"task": "workspace.purge", "schedule": 86400.0},
    },
)


def enqueue(db, kind, target_id):
    job = Job(kind=kind, target_id=target_id)
    db.add(job)
    return job


def pending_jobs():
    with session_scope() as db:
        return list(
            db.scalars(
                select(Job.id)
                .where(or_(Job.status == "queued", (Job.status == "running") & (Job.lease_until < now())))
                .order_by(Job.created_at)
                .limit(20)
            )
        )


def process_job(job_id):
    with session_scope() as db:
        claimed = db.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.attempts < settings().max_job_attempts,
                or_(Job.status == "queued", (Job.status == "running") & (Job.lease_until < now())),
            )
            .values(status="running", lease_until=now() + timedelta(seconds=90), attempts=Job.attempts + 1)
        )
        if not claimed.rowcount:
            job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            eligible = job and (
                job.status == "queued"
                or (job.status == "running" and job.lease_until and job.lease_until < now())
            )
            if eligible and job.attempts >= settings().max_job_attempts:
                job.status, job.error = "failed", "Worker recovery attempt limit exceeded"
                target = db.get(
                    {
                        "run": Run,
                        "ingestion": Document,
                        "evaluation": EvaluationReport,
                        "calibration": GraderCalibration,
                    }[job.kind],
                    job.target_id,
                )
                if target and target.status not in {"completed", "ready", "failed", "cancelled", "timed_out"}:
                    target.status, target.error = "failed", job.error
                    if isinstance(target, (Run, EvaluationReport, GraderCalibration)):
                        target.finished_at = now()
                    if isinstance(target, Run):
                        db.add(RunEvent(run_id=target.id, event="status", data={"status": "failed"}))
            return
        job = db.get(Job, job_id)
        kind, target_id, attempt = job.kind, job.target_id, job.attempts
    stop = threading.Event()

    def heartbeat():
        while not stop.wait(20):
            try:
                with session_scope() as db:
                    db.execute(
                        update(Job)
                        .where(Job.id == job_id, Job.status == "running", Job.attempts == attempt)
                        .values(lease_until=now() + timedelta(seconds=90))
                    )
            except Exception:
                logger.warning("Job lease renewal failed", extra={"job_id": job_id})

    pulse = threading.Thread(target=heartbeat, daemon=True)
    pulse.start()
    try:
        if kind == "ingestion":
            from backend.knowledge import ingest_document

            ingest_document(target_id)
        elif kind == "run":
            from backend.execution import execute_run

            execute_run(target_id)
        elif kind == "evaluation":
            from backend.evaluations import run_evaluation

            run_evaluation(target_id)
        elif kind == "calibration":
            from backend.calibration_workspace import run_calibration

            run_calibration(target_id)
        else:
            raise ValueError("Unknown job kind")
        with session_scope() as db:
            db.execute(
                update(Job).where(Job.id == job_id, Job.attempts == attempt).values(status="completed")
            )
    except Exception as exc:
        logger.error("Job failed: %s", type(exc).__name__, extra={"job_id": job_id})
        with session_scope() as db:
            db.execute(
                update(Job)
                .where(Job.id == job_id, Job.attempts == attempt)
                .values(status="failed", error=f"{type(exc).__name__}: job could not complete")
            )
    finally:
        stop.set()
        pulse.join(timeout=1)


@celery_app.task(name="workspace.process")
def process_task(job_id):
    process_job(job_id)


@celery_app.task(name="workspace.dispatch")
def dispatch_task():
    for job_id in pending_jobs():
        process_task.delay(job_id)


@celery_app.task(name="workspace.purge")
def purge_task():
    from backend.maintenance import purge_payloads

    return purge_payloads()


def start_local_worker():
    stop = threading.Event()
    pool = ThreadPoolExecutor(max_workers=5, thread_name_prefix="workspace-job")

    def loop():
        active = {}
        while not stop.is_set():
            active = {k: v for k, v in active.items() if not v.done()}
            try:
                for job_id in pending_jobs():
                    if job_id not in active and len(active) < 5:
                        active[job_id] = pool.submit(process_job, job_id)
            except Exception:
                logger.warning("Local dispatcher could not read pending jobs")
            stop.wait(0.5)
        pool.shutdown(wait=True)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return stop, thread
