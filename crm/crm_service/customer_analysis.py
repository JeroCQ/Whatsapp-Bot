"""Run durable analysis jobs without putting a model call on the return-to-AI path."""

from datetime import timedelta

from sqlalchemy import select

from crm_service.bot_source import BotSourceAdapter
from crm_service.models import CustomerAnalysis, now
from crm_service.source import SourceFailure

MAX_ATTEMPTS = 2
LEASE = timedelta(minutes=10)


def consume_analysis(settings, sessions, business_id=None):
    enabled = set(settings.customer_insights_businesses) & set(settings.businesses)
    if business_id:
        enabled &= {business_id}
    if not enabled:
        return

    with sessions.begin() as session:
        moment = now()
        row = session.scalar(select(CustomerAnalysis).where(
            CustomerAnalysis.business_id.in_(enabled),
            CustomerAnalysis.status.in_(("pending", "processing")),
            CustomerAnalysis.retry_at <= moment,
        ).order_by(CustomerAnalysis.released_at).limit(1).with_for_update(skip_locked=True))
        if row is None:
            return
        if row.attempts >= MAX_ATTEMPTS:
            row.status = "failed"
            row.result = {"error": "analysis_attempts_exhausted"}
            return
        row.status, row.retry_at = "processing", moment + LEASE
        row.attempts += 1
        key, phone, cutoff, attempt = (row.business_id, row.command_id), row.phone_number, row.released_at, row.attempts
        previous = session.scalar(select(CustomerAnalysis).where(
            CustomerAnalysis.business_id == key[0], CustomerAnalysis.phone_number == phone,
            CustomerAnalysis.status == "done", CustomerAnalysis.released_at < cutoff,
        ).order_by(CustomerAnalysis.released_at.desc()).limit(1))
        prior = dict(previous.result or {}) if previous else {}

    gateway = None
    status, report = "done", {}
    try:
        gateway = BotSourceAdapter(settings.businesses[key[0]], test_mode=settings.bot_test_mode)
        report = gateway.return_analysis(phone, cutoff, int(prior.get("checkpoint", 0)))
        if "summary" not in report:
            report = {**prior, **report}
        else:
            report["labels"] = sorted(set(prior.get("labels", [])) | set(report.get("labels", [])))
            if any(label in report["labels"] for label in ("cliente_con_pedido", "cliente_recurrente")):
                report["labels"] = [label for label in report["labels"] if label != "cliente_nuevo_sin_comprar"]
    except SourceFailure as error:
        code = str(error)
        retriable = code == "customer_analysis_network_error" or code.startswith("customer_analysis_http_5")
        status = "pending" if attempt < MAX_ATTEMPTS and retriable else "failed"
        report = {"error": code}
    finally:
        if gateway:
            gateway.close()

    with sessions.begin() as session:
        row = session.get(CustomerAnalysis, key, with_for_update=True)
        if row and row.attempts == attempt and row.status == "processing":
            row.status, row.result, row.retry_at = status, report, now() + timedelta(minutes=1)


def latest_analysis(session, business, phone):
    row = session.scalar(select(CustomerAnalysis).where(
        CustomerAnalysis.business_id == business,
        CustomerAnalysis.phone_number == phone,
    ).order_by(CustomerAnalysis.released_at.desc()).limit(1))
    if row is None:
        return None
    verified = row if row.status == "done" else session.scalar(select(CustomerAnalysis).where(
        CustomerAnalysis.business_id == business, CustomerAnalysis.phone_number == phone,
        CustomerAnalysis.status == "done",
    ).order_by(CustomerAnalysis.released_at.desc()).limit(1))
    return {**(verified.result if verified else {}), "status": row.status, "released_at": row.released_at.isoformat()}
