import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit

import requests
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from fastapi.responses import PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import chatwoot_api
import handoff_delivery
from bot import FILE_CATALOG, process_message_logic, transcribe_audio_message
from conversation_summary import build_handoff_summary, format_handoff_history
from config import config
from database import (
    claim_webhook_event,
    get_message_logs,
    get_or_create_customer_state,
    get_phone_by_chatwoot_id,
    mark_webhook_event_processed,
    pause_bot_for_handoff,
    pause_bot_for_human_activity,
    reset_client_history,
    recover_failed_handoff,
    resume_bot_state,
    save_message_log,
    update_inbound_transcript,
    update_follow_up_preferences,
    update_chatwoot_conversation_id,
    supabase,
)
from http_client import MEDIA_TIMEOUT, get, post
from processing_lock import phone_lock
from queue_client import (
    claim_follow_up_attempt,
    colombia_service_window_is_open,
    enqueue,
    enqueue_in,
    follow_up_delay_seconds,
    get_queue_stats,
    invalidate_follow_up,
    meta_window_is_open,
    next_colombia_opening_delay_seconds,
    queue_enabled,
    register_follow_up,
    reserve_follow_up_attempt,
    begin_follow_up_send,
    finish_follow_up_attempt,
    record_customer_activity,
    claim_internal_alert,
    follow_ups_enabled,
)
from commercial_intents import FollowUpPlan, follow_up_plan, follow_up_suppression_reason, follow_up_preference_patch, human_control_active, customer_closes_without_pending_request, is_terminal_customer_message, normalize_follow_up_message
from follow_up_policy import parse_timestamp, load_business_policy, resolve_follow_up_schedule, customer_agreed_not_before, next_eligible_contact_at, message_announces_opening
from observability import operational_event, message_trace, current_trace, record_provider_message_id, update_trace
from webhook_utils import (
    WHATSAPP_SENDER_PATTERN,
    chatwoot_event_identity,
    is_restart_command,
    resolve_whatsapp_sender,
    valid_whatsapp_phone,
    valid_whatsapp_sender,
    whatsapp_destination_matches,
    whatsapp_recipient_fields,
)
from chatwoot_security import chatwoot_scope, verify_chatwoot_signature, chatwoot_event_diagnostics
from chatwoot_media import MediaValidationError, _safe_asset_url, get_media_asset, storage_error_code, prepare_image_for_whatsapp
from provider_errors import ProviderError, provider_error, provider_transport_error, sanitize_text
from outage_recovery import find_recoveries
from catalog_delivery_recovery import find_catalog_recoveries
from file_catalog import advisor_catalog_command
from dashboard_api import admin_auth, router as dashboard_router, validate_deployment_client

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.DASHBOARD_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Dashboard-API-Key"],
)
app.include_router(dashboard_router)


class ManualHandoffRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    phone_number: str = Field(pattern=WHATSAPP_SENDER_PATTERN)
    customer_name: str = Field(default="Cliente", min_length=1, max_length=120)
    reason: str = Field(default="Revisión manual solicitada desde el dashboard", min_length=3, max_length=500)


class CatalogRecoverySelection(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    phone_number: str = Field(pattern=WHATSAPP_SENDER_PATTERN)
    catalog_id: str = Field(pattern=r"^catalogo_[a-z0-9_]{1,52}$")


class BulkCatalogRecoveryRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    recoveries: list[CatalogRecoverySelection] = Field(min_length=1, max_length=50)
    confirmation: str = Field(pattern=r"^REENVIAR$", description="Explicit operator confirmation")


class FileDeliveryError(RuntimeError):
    """Safe technical context for an operational file-delivery handoff."""


app.mount("/public", StaticFiles(directory="public"), name="public")

DEPLOYMENT_COMMIT_SHA = os.getenv("RAILWAY_GIT_COMMIT_SHA") or os.getenv("GIT_COMMIT_SHA") or "unknown"
print(f"[BOOT] WhatsApp bot code loaded. Commit: {DEPLOYMENT_COMMIT_SHA}. Scalable queue build: 2026-07-24.2")
OUTAGE_RECOVERY_STATUS = {"status": "disabled"}
_BACKGROUND_TASKS = set()

WHATSAPP_MEDIA_TYPES = {"audio", "document", "image", "sticker", "video"}
CATALOG_FORMATS = (
    ("pdf", "application/pdf", "document"),
    ("jpg", "image/jpeg", "image"),
    ("jpeg", "image/jpeg", "image"),
    ("png", "image/png", "image"),
    ("webp", "image/webp", "image"),
)


@app.get("/")
async def health_check():
    """Railway/root health endpoint and visible deployment-version check."""
    return {
        "status": "ok",
        "commit": DEPLOYMENT_COMMIT_SHA,
        "queue_enabled": queue_enabled(),
        "queue": get_queue_stats(),
        "gemini_outage_recovery": OUTAGE_RECOVERY_STATUS,
        "scalable_queue_build": "2026-07-24.2",
    }


@app.get("/health")
async def health_alias():
    """Conventional platform health endpoint."""
    return await health_check()


@app.on_event("startup")
async def log_deployment_version():
    """Log the Railway commit so deployments can be verified."""
    print(f"[STARTUP] WhatsApp bot running commit: {DEPLOYMENT_COMMIT_SHA}. Queue enabled: {queue_enabled()}")
    task = asyncio.create_task(_recover_pending_handoffs_loop())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    if config.GEMINI_OUTAGE_RECOVERY_SINCE:
        OUTAGE_RECOVERY_STATUS.clear()
        OUTAGE_RECOVERY_STATUS.update({"status": "scheduled", "since": config.GEMINI_OUTAGE_RECOVERY_SINCE})
        task = asyncio.create_task(_run_outage_recovery(config.GEMINI_OUTAGE_RECOVERY_SINCE))
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        print("[STARTUP] Recuperación de handoffs Gemini programada en este servicio")


async def _run_outage_recovery(since: str):
    """Run locally so the repair cannot remain stuck behind a stale RQ job id."""
    OUTAGE_RECOVERY_STATUS.update({"status": "running", "since": since})
    try:
        result = await asyncio.to_thread(recover_gemini_outage_handoffs, since)
        OUTAGE_RECOVERY_STATUS.clear()
        OUTAGE_RECOVERY_STATUS.update({"status": "completed", "since": since, **result})
        print(f"[GEMINI RECOVERY] completed {result}")
    except Exception as exc:
        OUTAGE_RECOVERY_STATUS.clear()
        OUTAGE_RECOVERY_STATUS.update({"status": "failed", "since": since, "error": sanitize_text(exc)})
        print(f"[GEMINI RECOVERY ERROR] {sanitize_text(exc)}")


def _attachment_url(attachment: dict) -> str:
    """Return the original file, never its thumbnail."""
    return attachment.get("data_url") or attachment.get("download_url")


def _attachment_filename(attachment: dict, default: str = "archivo") -> str:
    """Return a stable filename for forwarding a Chatwoot attachment to WhatsApp."""
    explicit = attachment.get("file_name") or attachment.get("filename") or attachment.get("name")
    if explicit:
        return os.path.basename(str(explicit)) or default
    return os.path.basename(unquote(urlsplit(_attachment_url(attachment) or "").path)) or default


def normalize_media_type(file_type: str = None, mime_type: str = None, url: str = None) -> str:
    """Map Chatwoot/Meta attachment metadata to a WhatsApp Cloud API media type."""
    file_type = (file_type or "").lower()
    mime_type = (mime_type or "").lower().split(";")[0]
    url = (url or "").lower()

    if file_type in WHATSAPP_MEDIA_TYPES:
        return file_type
    if mime_type.startswith("audio/") or url.endswith((".ogg", ".oga", ".opus", ".mp3", ".m4a", ".aac", ".wav", ".webm")):
        return "audio"
    if mime_type.startswith("image/") or url.endswith((".jpg", ".jpeg", ".png", ".webp")):
        return "image"
    if mime_type.startswith("video/") or url.endswith((".mp4", ".3gp", ".mov", ".m4v")):
        return "video"
    return "document"


def send_whatsapp_message(to_number: str, text: str):
    """Envía un mensaje de texto usando la API de Meta."""
    # A preceding catalog send must not lend its wamid to a failed text send.
    update_trace(provider_message_id="")
    _check_customer_window()
    url = f"https://graph.facebook.com/v20.0/{config.WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {config.WA_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        **whatsapp_recipient_fields(to_number),
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    try:
        response = post(url, headers=headers, json=payload)
        if not 200 <= response.status_code < 300:
            raise provider_error("meta", "send_text", response, (config.WA_TOKEN,))
        record_provider_message_id(response)
        return True
    except requests.exceptions.RequestException as e:
        raise provider_transport_error("meta", "send_text", e) from e


def _check_customer_window():
    expiry = current_trace().get("meta_window_expires_at")
    if expiry is not None and not meta_window_is_open(expiry):
        raise ProviderError("meta", "window_validation", 400, message="original_customer_window_expired")
    if current_trace().get("source") == "follow_up":
        now = datetime.now(timezone.utc)
        eligible = next_eligible_contact_at(now, business_id=config.BUSINESS_ID,
            requires_operation=bool(current_trace().get("requires_operation")))
        if eligible > now:
            raise ProviderError("meta", "contact_calendar_validation", 400, message="contact_calendar_closed")


def send_scheduled_follow_up(phone_number: str, token: str, message: str,
                             stage: str = "LEGACY", attempt: int = 1,
                             scheduled_at: str = "", meta_window_expires_at: str = "", *,
                             business_id=None, phone_number_id=None, reference_at=None, source_event_id="", preflight_retry=0):
    """Legacy positional jobs remain loadable; every send uses the current guard."""
    if (business_id is not None and business_id != config.BUSINESS_ID) or (phone_number_id is not None and str(phone_number_id) != str(config.WA_PHONE_NUMBER_ID)):
        return {"status": "wrong_deployment"}
    if not follow_ups_enabled():
        operational_event("FOLLOW_UP_SKIPPED", phone=phone_number, stage=stage,
                          reason="disabled_by_configuration", attempt=attempt, result="not_sent")
        return {"status": "disabled_by_configuration"}
    with message_trace(source="follow_up", sequence_token=token, attempt=attempt,
                       follow_up_stage=stage,
                       requires_operation=stage == "DEFERRED_DELIVERY" or message_announces_opening(message),
                       source_event_id=source_event_id, inbound_message_ids=[],
                       meta_window_expires_at=meta_window_expires_at, job_id=_current_job_id()):
        def event(reason, result="not_sent", name="FOLLOW_UP_SKIPPED"):
            operational_event(name, phone=phone_number, stage=stage, reason=reason, attempt=attempt,
                              scheduled_at=scheduled_at, result=result)
            return {"status": reason}

        now = datetime.now(timezone.utc)
        expiry = parse_timestamp(meta_window_expires_at)
        reference = parse_timestamp(reference_at) or (expiry - timedelta(hours=24) if expiry else None)
        reference_verified = bool(reference_at)
        requires_operation = stage == "DEFERRED_DELIVERY" or message_announces_opening(message)
        if not expiry or not meta_window_is_open(meta_window_expires_at, now):
            return event("meta_24h_window_closed_no_approved_template" if expiry else "legacy_missing_original_window")
        if not message or not message.strip():
            return event("explicit_empty_follow_up")
        policy = load_business_policy(config.BUSINESS_ID)

        def requeue(target, reason, *, preflight_failure=False):
            if preflight_failure and preflight_retry >= 2:
                return event("preflight_recovery_exhausted")
            target = next_eligible_contact_at(target, business_id=config.BUSINESS_ID,
                                              requires_operation=requires_operation)
            if target >= expiry:
                return event("reschedule_outside_original_meta_window")
            delay = max(1, int((target - datetime.now(timezone.utc)).total_seconds()) + 1)
            enqueue_in(delay, send_scheduled_follow_up, phone_number, token, message, stage, attempt,
                       target.isoformat(), meta_window_expires_at, business_id=config.BUSINESS_ID,
                       phone_number_id=config.WA_PHONE_NUMBER_ID, reference_at=reference.isoformat() if reference_verified else None,
                       source_event_id=source_event_id, preflight_retry=preflight_retry + int(preflight_failure),
                       job_id=f"follow-up-resume-{config.BUSINESS_ID}-{token}-{attempt}-{int(target.timestamp())}")
            return event(reason, "queued", "FOLLOW_UP_REPROGRAMMED")

        scheduled_due = parse_timestamp(scheduled_at)
        earliest = max(now, scheduled_due) if scheduled_due else now
        eligible = next_eligible_contact_at(earliest, business_id=config.BUSINESS_ID, requires_operation=requires_operation)
        if eligible > now:
            return requeue(eligible, "before_scheduled_time" if earliest > now else "outside_contact_or_operating_calendar")
        with phone_lock(phone_number):
            state = get_or_create_customer_state(phone_number)
            if not state:
                return requeue(now + timedelta(seconds=60), "conversation_state_unavailable", preflight_failure=True)
            if human_control_active(state):
                return event("human_control")
            if state.get("follow_up_suppressed"):
                return event(state.get("follow_up_suppression_reason") or "customer_contact_suppressed")
            not_before = parse_timestamp(state.get("follow_up_not_before"))
            if not_before and not_before > now:
                return requeue(not_before, "agreed_customer_date")
            logs = get_message_logs(phone_number, limit=50)
            latest_user = next((row for row in reversed(logs) if row.get("role") == "user"), None)
            if not reference_at:
                # Old producers could anchor expiry to dequeue time. An expiry
                # argument alone is therefore insufficient evidence of the
                # customer's original window after an upgrade.
                original = parse_timestamp(((latest_user or {}).get("metadata") or {}).get("received_at"))
                if original is None:
                    return event("legacy_missing_verified_original_timestamp")
                reference = original
                reference_verified = True
                expiry = min(expiry, original + timedelta(hours=24))
                meta_window_expires_at = expiry.isoformat()
                update_trace(meta_window_expires_at=meta_window_expires_at)
                if not meta_window_is_open(meta_window_expires_at):
                    return event("meta_24h_window_closed_no_approved_template")
            last_sent = next((row for row in reversed(logs) if row.get("role") == "model" and
                (not row.get("metadata") or (row.get("metadata") or {}).get("source") == "follow_up")), None)
            sent_at = parse_timestamp(last_sent.get("created_at")) if last_sent else None
            if sent_at and sent_at + timedelta(seconds=policy.min_spacing_seconds) > now:
                return requeue(sent_at + timedelta(seconds=policy.min_spacing_seconds), "recent_follow_up_or_legacy_send")
            if latest_user:
                metadata = latest_user.get("metadata") or {}
                received = parse_timestamp(metadata.get("received_at") or latest_user.get("created_at"))
                if (received and received > reference) or (source_event_id and metadata.get("source_event_id") and metadata["source_event_id"] != source_event_id):
                    return event("customer_replied_after_sequence")
                reason = follow_up_suppression_reason(latest_user.get("content", ""), state=state)
                if reason:
                    return event(reason)
            reservation = reserve_follow_up_attempt(phone_number, token, stage, attempt,
                                                    min_spacing_seconds=policy.min_spacing_seconds)
            if reservation["status"] == "deferred":
                return requeue(datetime.fromtimestamp(reservation["retry_at"], timezone.utc), "minimum_contact_spacing")
            if reservation["status"] != "reserved":
                if reservation["status"] == "unavailable":
                    return requeue(now + timedelta(seconds=60), "reservation_store_unavailable", preflight_failure=True)
                return event("attempt_" + reservation["status"])
            lease = reservation["reservation_id"]
            # Re-read after reservation. Ingress can invalidate the token while
            # a worker waits on DB or another process owns the contact lock.
            state = get_or_create_customer_state(phone_number)
            if not state:
                result = finish_follow_up_attempt(phone_number, token, stage, attempt, lease, outcome="confirmed_failure")
                if result["status"] == "retry":
                    return requeue(now + timedelta(seconds=60), "state_unavailable_before_send", preflight_failure=True)
                return event("preflight_recovery_exhausted")
            if human_control_active(state) or state.get("follow_up_suppressed") or not meta_window_is_open(meta_window_expires_at):
                finish_follow_up_attempt(phone_number, token, stage, attempt, lease, outcome="cancelled")
                return event("state_or_window_changed_before_send")
            not_before = parse_timestamp(state.get("follow_up_not_before"))
            if not_before and not_before > datetime.now(timezone.utc):
                finish_follow_up_attempt(phone_number, token, stage, attempt, lease, outcome="confirmed_failure")
                return requeue(not_before, "agreed_customer_date_changed")
            started = begin_follow_up_send(phone_number, token, stage, attempt, lease,
                                           min_spacing_seconds=policy.min_spacing_seconds)
            if started["status"] == "deferred":
                return requeue(datetime.fromtimestamp(started["retry_at"], timezone.utc), "minimum_contact_spacing")
            if started["status"] != "sending":
                return event("attempt_" + started["status"])
            try:
                send_whatsapp_message(phone_number, normalize_follow_up_message(message))
            except Exception as exc:
                confirmed = isinstance(exc, ProviderError) and (exc.confirmed_not_sent or (exc.status is not None and 400 <= exc.status < 500))
                outcome = "confirmed_failure" if confirmed else "unknown"
                result = finish_follow_up_attempt(phone_number, token, stage, attempt, lease, outcome=outcome)
                if confirmed and result["status"] == "retry":
                    reason = "confirmed_pre_send_failure_retry" if isinstance(exc, ProviderError) and exc.confirmed_not_sent else "confirmed_provider_rejection_retry"
                    return requeue(datetime.fromtimestamp(result["retry_at"], timezone.utc), reason)
                reason = "pre_send_failure_exhausted" if isinstance(exc, ProviderError) and exc.confirmed_not_sent else ("provider_rejected_exhausted" if confirmed else "provider_result_ambiguous_manual_review")
                return event(reason)
            result = finish_follow_up_attempt(phone_number, token, stage, attempt, lease, outcome="accepted",
                                              provider_message_id=current_trace().get("provider_message_id", ""))
            save_message_log(phone_number, "model", normalize_follow_up_message(message))
            return event("customer_silent", result["status"], "FOLLOW_UP_SENT")


def _current_job_id():
    try:
        from rq import get_current_job
        job = get_current_job()
        return job.id if job else ""
    except Exception:
        return ""


def schedule_follow_up(phone_number: str, message: str, delay_minutes: int):
    """Persist and enqueue the AI-authored follow-up."""
    if not message or not queue_enabled():
        if message:
            print("[FOLLOW UP WARN] REDIS_URL no configurado; no se puede programar el mensaje durablemente")
        return
    reference = parse_timestamp(current_trace().get("received_at"))
    schedule_follow_up_sequence(phone_number, FollowUpPlan("LEGACY", (delay_minutes,), (message,)), customer_message_received_at=reference)


def schedule_follow_up_sequence(phone_number: str, plan, *, customer_message_received_at=None, agreed_not_before=None):
    """Replace the prior sequence and enqueue at most three durable attempts."""
    if plan is None:
        return
    if not follow_ups_enabled():
        operational_event("FOLLOW_UP_SKIPPED", phone=phone_number, stage=plan.stage,
                          reason="disabled_by_configuration", result="not_scheduled")
        return
    if not queue_enabled():
        operational_event("FOLLOW_UP_SKIPPED", phone=phone_number, stage=plan.stage,
                          reason="redis_unavailable", result="not_scheduled")
        return
    try:
        now = datetime.now(timezone.utc)
        window_start = parse_timestamp(customer_message_received_at)
        if window_start is None:
            operational_event("FOLLOW_UP_SKIPPED", phone=phone_number, stage=plan.stage,
                              reason="missing_original_inbound_timestamp", result="not_scheduled")
            return
        expiry = window_start + timedelta(hours=24)
        eligible = resolve_follow_up_schedule(plan, window_start, expiry, business_id=config.BUSINESS_ID,
                                              agreed_not_before=agreed_not_before, now=now)
        if not eligible:
            operational_event("FOLLOW_UP_SKIPPED", phone=phone_number, stage=plan.stage,
                              reason="outside_whatsapp_window_no_template", result="not_scheduled")
            return
        token = register_follow_up(phone_number, int((eligible[-1].send_at - now).total_seconds()),
                                   reference_at=window_start, event_id=current_trace().get("source_event_id", ""))
        for item in eligible:
            attempt, scheduled = item.attempt, item.send_at
            seconds = max(1, int((scheduled - now).total_seconds()))
            enqueue_in(
                seconds, send_scheduled_follow_up, phone_number, token,
                plan.messages[attempt - 1], plan.stage, attempt, scheduled.isoformat(),
                expiry.isoformat(), business_id=config.BUSINESS_ID, phone_number_id=config.WA_PHONE_NUMBER_ID,
                reference_at=window_start.isoformat(), source_event_id=current_trace().get("source_event_id", ""),
                job_id=f"follow-up-{config.BUSINESS_ID}-{phone_number}-{plan.stage}-{attempt}-{token}",
            )
            operational_event("FOLLOW_UP_SCHEDULED", phone=phone_number, stage=plan.stage,
                              reason="customer_silent", attempt=attempt,
                              scheduled_at=scheduled, result="queued")
    except Exception as exc:
        operational_event("FOLLOW_UP_SKIPPED", phone=phone_number, stage=plan.stage,
                          reason="queue_failure", result=type(exc).__name__)


def send_whatsapp_media(to_number: str, media_id: str, media_type: str, caption: str = None, filename: str = None):
    """Send any WhatsApp-supported media type using an uploaded Meta media id."""
    _check_customer_window()
    media_type = normalize_media_type(media_type)
    url = f"https://graph.facebook.com/v20.0/{config.WA_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {config.WA_TOKEN}",
        "Content-Type": "application/json",
    }
    media_payload = {"id": media_id}
    if caption and media_type in {"document", "image", "video"}:
        media_payload["caption"] = caption
    if filename and media_type == "document":
        media_payload["filename"] = filename

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        **whatsapp_recipient_fields(to_number),
        "type": media_type,
        media_type: media_payload,
    }
    try:
        response = post(url, headers=headers, json=payload)
        if not 200 <= response.status_code < 300:
            raise provider_error("meta", f"send_{media_type}", response, (config.WA_TOKEN,))
        record_provider_message_id(response)
        return True
    except requests.exceptions.RequestException as e:
        raise ProviderError("meta", f"send_{media_type}", getattr(getattr(e, "response", None), "status_code", None), message="transport error") from e



def catalog_link_for_whatsapp(file_id: str, link: str) -> str:
    """Add a cache-busting query to dashboard-managed catalog links sent through Meta."""
    if not file_id.startswith("catalogo_"):
        return link
    try:
        response = requests.head(link, timeout=MEDIA_TIMEOUT, allow_redirects=True)
        response.raise_for_status()
        version = response.headers.get("etag") or response.headers.get("last-modified") or response.headers.get("content-length")
    except requests.exceptions.RequestException as exc:
        print(f"[FILE CATALOG] Could not version catalog link for WhatsApp cache busting: {exc}")
        version = str(int(time.time()))
    parts = urlsplit(link)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["v"] = str(version or int(time.time()))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def current_catalog_for_whatsapp(catalog_id: str = "catalogo_pdf") -> tuple[str, str, str, str] | None:
    """Find this deployment's active PDF/image catalog from public Storage metadata."""
    for extension, expected_content_type, media_type in CATALOG_FORMATS:
        link = config.catalog_public_url(extension=extension, catalog_id=catalog_id)
        try:
            response = requests.head(link, timeout=MEDIA_TIMEOUT, allow_redirects=True)
        except requests.exceptions.RequestException:
            continue
        if not 200 <= response.status_code < 300:
            continue
        content_type = (response.headers.get("Content-Type") or expected_content_type).split(";", 1)[0].lower()
        if content_type != expected_content_type:
            print("[FILE CATALOG] Stored catalog has an unexpected content type")
            return None
        return link, content_type, media_type, extension
    return None


def catalog_display_filename(configured_filename: str | None, extension: str, fallback: str) -> str:
    """Return the configured customer-facing catalog name with its real extension."""
    if not configured_filename:
        return fallback
    # The dashboard can replace a PDF with an image (or vice versa). Keep the
    # commercial name but always advertise the format that is actually sent.
    safe_name = os.path.basename(configured_filename.strip())
    stem, _configured_extension = os.path.splitext(safe_name)
    return f"{stem or safe_name}.{extension}"



def upload_public_url_to_meta_media(file_url: str, filename: str, fallback_mime_type: str = "application/pdf") -> str:
    """Download a public file and upload it to Meta so WhatsApp receives fresh media bytes."""
    try:
        res = get(file_url, timeout=MEDIA_TIMEOUT)
        res.raise_for_status()
        mime_type = (res.headers.get("Content-Type") or fallback_mime_type).split(";")[0]
        url = f"https://graph.facebook.com/v20.0/{config.WA_PHONE_NUMBER_ID}/media"
        headers = {"Authorization": f"Bearer {config.WA_TOKEN}"}
        files = {"file": (filename, res.content, mime_type)}
        data = {"messaging_product": "whatsapp"}
        response = post(url, headers=headers, files=files, data=data, timeout=MEDIA_TIMEOUT)
        response.raise_for_status()
        media_id = response.json().get("id")
        if media_id:
            print(f"[FILE CATALOG] Uploaded fresh catalog bytes to Meta media_id={media_id}")
        else:
            print(f"[FILE CATALOG] Meta did not return media id for fresh catalog upload: {response.text}")
        return media_id
    except requests.exceptions.RequestException as exc:
        response = getattr(exc, "response", None)
        detail = response.text if response is not None else str(exc)
        print(f"[FILE CATALOG] Error uploading fresh catalog bytes to Meta: {detail}")
        return None


def send_presaved_file(to_number: str, file_id: str, *, raise_on_failure: bool = False):
    """Send one allow-listed file selected by Gemini from the configured catalog."""
    item = FILE_CATALOG.get(file_id)
    if not item:
        print(f"[FILE CATALOG] Ignoring unknown file id requested by AI: {file_id}")
        if raise_on_failure:
            raise FileDeliveryError(f"unknown_or_stale_file_id={file_id}")
        return
    resolved_filename = item.filename
    display_filename = item.filename
    media_type = item.media_type
    catalog_content_type = "application/pdf"
    if item.media_id:
        media_reference = {"id": item.media_id}
    else:
        source_link = item.link
        catalog_extension = "pdf"
        if file_id.startswith("catalogo_"):
            current_catalog = current_catalog_for_whatsapp(file_id)
            if not current_catalog:
                print("[FILE CATALOG] No active PDF/image catalog exists for this deployment")
                if raise_on_failure:
                    raise FileDeliveryError(f"storage_object_not_found catalog_id={file_id}")
                return False
            source_link, catalog_content_type, media_type, catalog_extension = current_catalog
        resolved_link = catalog_link_for_whatsapp(file_id, source_link)
        media_reference = {"link": resolved_link}
        if file_id.startswith("catalogo_"):
            version = dict(parse_qsl(urlsplit(resolved_link).query, keep_blank_values=True)).get("v", "")
            digest = hashlib.sha256(version.encode("utf-8")).hexdigest()[:12] if version else str(int(time.time()))
            resolved_filename = f"catalogo-{config.BUSINESS_ID}-{digest}.{catalog_extension}"
            display_filename = catalog_display_filename(item.filename, catalog_extension, resolved_filename)
            print(f"[FILE CATALOG] Uploading current {media_type} catalog to Meta")
            media_id = upload_public_url_to_meta_media(resolved_link, resolved_filename, catalog_content_type)
            if not media_id:
                print("[FILE CATALOG] Catalog delivery stopped because Meta upload failed")
                if raise_on_failure:
                    raise FileDeliveryError(f"meta_media_upload_failed catalog_id={file_id}")
                return False
            media_reference = {"id": media_id}
    caption = item.default_caption
    if file_id.startswith("catalogo_") and media_type == "image" and display_filename:
        caption = os.path.splitext(display_filename)[0]
    if caption and media_type in {"document", "image", "video"}:
        media_reference["caption"] = caption
    if display_filename and media_type == "document":
        media_reference["filename"] = display_filename
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        **whatsapp_recipient_fields(to_number),
        "type": media_type,
        media_type: media_reference,
    }
    url = f"https://graph.facebook.com/v20.0/{config.WA_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {config.WA_TOKEN}", "Content-Type": "application/json"}
    try:
        _check_customer_window()
        response = post(url, headers=headers, json=payload)
        if not 200 <= response.status_code < 300:
            raise provider_error("meta", "send_catalog", response, (config.WA_TOKEN,))
        record_provider_message_id(response)
        print(f"[FILE CATALOG] Sent file id={file_id} to={to_number}")
        return True
    except requests.exceptions.RequestException as exc:
        raise ProviderError("meta", "send_catalog", getattr(getattr(exc, "response", None), "status_code", None), message="transport error") from exc


def deliver_presaved_file(to_number: str, file_id: str) -> tuple[bool, str | None]:
    """Deliver one file and retain safe technical context instead of abandoning the turn."""
    try:
        delivered = send_presaved_file(to_number, file_id, raise_on_failure=True)
        return bool(delivered), None
    except Exception as exc:
        technical = sanitize_text(exc, (config.WA_TOKEN, config.SUPABASE_KEY))
        import traceback
        print(
            f"[FILE DELIVERY ERROR] phone={to_number} file_id={file_id} "
            f"error_type={type(exc).__name__} detail={technical}"
        )
        traceback.print_exc()
        return False, f"{file_id}: {type(exc).__name__}: {technical}"


def upload_chatwoot_attachment_to_meta(attachment_url: str, fallback_mime_type: str = "application/octet-stream", filename: str = "archivo", *, attachment_created_at=None) -> str:
    """Download a Chatwoot attachment and upload it to Meta's temporary media store."""
    diagnostic = {"phase": "chatwoot_download", "host": "", "http_status": None,
                  "storage_error_code": ""}
    update_trace(chatwoot_attachment_error="")
    try:
        attachment_url = urljoin(config.CHATWOOT_BASE_URL + "/", attachment_url)
        configured = urlsplit(config.CHATWOOT_BASE_URL)
        target = urlsplit(attachment_url)
        if target.username or target.password or target.fragment or target.scheme != "https" or target.netloc != configured.netloc:
            raise MediaValidationError("attachment_origin_rejected")
        chatwoot_headers = {"api_access_token": config.CHATWOOT_API_TOKEN}
        created_at = parse_timestamp(attachment_created_at)
        age = (datetime.now(timezone.utc) - created_at).total_seconds() if created_at else None
        # Chatwoot can publish message_created before ActiveStorage has finished
        # uploading its object. Only a recent, dated message can use this brief
        # availability wait; old/missing objects keep their existing fail-fast path.
        pending_deadline = time.monotonic() + 20 if age is not None and -30 <= age <= 120 else None
        pending_delays = (1, 2, 4, 8)
        pending_retries = 0
        while True:
            current_url = attachment_url
            try:
                for redirects in range(4):
                    current_target = urlsplit(current_url)
                    is_chatwoot_origin = current_target.netloc == configured.netloc
                    diagnostic.update(phase="chatwoot_download" if is_chatwoot_origin else "storage_download",
                                      host=current_target.hostname or "", http_status=None,
                                      storage_error_code="", redirects_followed=redirects)
                    request_timeout = MEDIA_TIMEOUT
                    if pending_retries:
                        remaining = pending_deadline - time.monotonic()
                        if remaining <= 0:
                            raise MediaValidationError("attachment_pending_upload_timeout")
                        request_timeout = tuple(min(value, remaining) for value in MEDIA_TIMEOUT)
                    fetch = get if is_chatwoot_origin else get_media_asset
                    res = fetch(current_url, headers=chatwoot_headers if is_chatwoot_origin else {},
                                timeout=request_timeout, allow_redirects=False, stream=True)
                    try:
                        diagnostic["http_status"] = res.status_code
                        if res.status_code in {301, 302, 303, 307, 308}:
                            if redirects == 3:
                                raise MediaValidationError("attachment_redirect_limit")
                            location = res.headers.get("Location")
                            if not location:
                                raise MediaValidationError("attachment_redirect_missing")
                            current_url = _safe_asset_url(urljoin(current_url, location))
                            continue
                        if res.status_code != 200:
                            if res.status_code >= 400:
                                diagnostic["storage_error_code"] = storage_error_code(res)
                            raise MediaValidationError("attachment_download_http_error")
                        try:
                            declared_size = int(res.headers.get("Content-Length", "0") or 0)
                        except (TypeError, ValueError) as error:
                            raise MediaValidationError("attachment_invalid_length") from error
                        if declared_size < 0 or declared_size > config.CHATWOOT_MAX_ATTACHMENT_BYTES:
                            raise MediaValidationError("attachment_too_large")
                        chunks = []
                        size = 0
                        for chunk in res.iter_content(64 * 1024):
                            if pending_retries and time.monotonic() >= pending_deadline:
                                raise MediaValidationError("attachment_pending_upload_timeout")
                            size += len(chunk)
                            diagnostic["downloaded_bytes"] = size
                            if size > config.CHATWOOT_MAX_ATTACHMENT_BYTES:
                                raise MediaValidationError("attachment_too_large")
                            chunks.append(chunk)
                        media_bytes = b"".join(chunks)
                        if not media_bytes:
                            raise MediaValidationError("attachment_empty")
                        mime_type = (res.headers.get("Content-Type") or fallback_mime_type or "application/octet-stream").split(";")[0].strip().lower()
                        if mime_type == "application/octet-stream" and fallback_mime_type:
                            mime_type = fallback_mime_type.split(";")[0].strip().lower()
                        break
                    finally:
                        res.close()
            except MediaValidationError as error:
                pending_object = (
                    error.reason == "attachment_download_http_error"
                    and diagnostic["phase"] == "storage_download"
                    and diagnostic["http_status"] == 404
                    and diagnostic["storage_error_code"] == "NoSuchKey"
                )
                if not pending_object or pending_deadline is None or pending_retries >= len(pending_delays):
                    raise
                delay = pending_delays[pending_retries]
                if time.monotonic() + delay >= pending_deadline:
                    raise MediaValidationError("attachment_pending_upload_timeout") from error
                _check_customer_window()
                pending_retries += 1
                diagnostic["pending_upload_retries"] = pending_retries
                print(f"[CHATWOOT ATTACHMENT WAIT] {json.dumps({**diagnostic, 'result': 'waiting', 'reason': 'recent_storage_object_not_ready', 'delay_seconds': delay}, sort_keys=True)}", flush=True)
                time.sleep(delay)
                _check_customer_window()
                # Reopen the validated Chatwoot URL rather than reusing an S3
                # signature that could expire while its upload is still pending.
                continue
            break

        _check_customer_window()

        if normalize_media_type(mime_type=mime_type, url=filename) == "image":
            diagnostic["phase"] = "image_prepare"
            media_bytes, mime_type, extension = prepare_image_for_whatsapp(media_bytes, mime_type)
            filename = f"{os.path.splitext(filename)[0] or 'imagen'}{extension}"
            diagnostic["prepared_bytes"] = len(media_bytes)

        # Meta rejects some containers produced by Chatwoot, including WAV.
        if normalize_media_type(mime_type=mime_type, url=filename) == "audio":
            media_bytes, mime_type, extension = chatwoot_api.prepare_audio_for_delivery(media_bytes, mime_type)
            filename = f"{os.path.splitext(filename)[0] or 'audio'}{extension}"

        diagnostic.update(phase="meta_upload", host="graph.facebook.com", http_status=None)
        _check_customer_window()
        url = f"https://graph.facebook.com/v20.0/{config.WA_PHONE_NUMBER_ID}/media"
        headers = {"Authorization": f"Bearer {config.WA_TOKEN}"}
        files = {"file": (filename, media_bytes, mime_type)}
        data = {"messaging_product": "whatsapp"}
        response = post(url, headers=headers, files=files, data=data, timeout=MEDIA_TIMEOUT, allow_redirects=False)
        try:
            diagnostic["http_status"] = response.status_code
            if response.status_code not in {200, 201}:
                error = provider_error("meta", "upload_media", response, (config.WA_TOKEN,))
                for field, value in (("provider_error_code", error.code), ("provider_error_subcode", error.subcode)):
                    if value.isascii() and value.isdecimal() and len(value) <= 12:
                        diagnostic[field] = value
                raise MediaValidationError("attachment_meta_upload_http_error")
            media_id = response.json().get("id")
            if not media_id:
                raise MediaValidationError("attachment_meta_upload_missing_id")
        finally:
            response.close()
        print(f"[CHATWOOT ATTACHMENT] {json.dumps({**diagnostic, 'result': 'uploaded', 'reason': 'meta_media_created'}, sort_keys=True)}", flush=True)
        return media_id
    except MediaValidationError as error:
        reason = error.reason
    except requests.exceptions.RequestException:
        reason = "attachment_transport_error"
    except Exception as error:
        diagnostic["error_type"] = type(error).__name__
        reason = "attachment_processing_error"
    update_trace(chatwoot_attachment_error=reason)
    print(f"[CHATWOOT ATTACHMENT] {json.dumps({**diagnostic, 'result': 'failed', 'reason': reason}, sort_keys=True)}", flush=True)
    return None


def _report_chatwoot_media_failure(phone, conversation_id, media_type, reason):
    """Keep human control and expose a failed attachment without a blind resend."""
    operational_event("CHATWOOT_MEDIA_DELIVERY_FAILED", phone=phone, stage="HUMAN_MEDIA",
                      reason=reason, result="manual_review", conversation_id=conversation_id,
                      media_type=media_type)
    save_message_log(phone, "system", f"ERROR entregando adjunto a Chatwoot: tipo={media_type}; motivo={reason}.")
    label = {"image": "la imagen", "audio": "la transcripción de la nota de voz"}.get(media_type, "el archivo")
    chatwoot_api.send_message_to_chatwoot(conversation_id,
        f"⚠️ No se pudo confirmar la entrega de {label} que envió el cliente. "
        "Este contenido sigue pendiente de revisión; solicita al cliente que lo envíe de nuevo si no puedes abrirlo.",
        is_private=True)
    return {"status": "delivery_failed", "reason": reason}


def _audio_transcript_note(transcript):
    if transcript:
        return f"🎙️ Transcripción automática de nota de voz del cliente:\n{transcript}"
    return ("🎙️ El cliente envió una nota de voz, pero no fue posible transcribirla. "
            "Se necesita solicitar su contenido por escrito. No se adjuntó el audio porque su reproducción no está verificada.")


def _create_handoff_ticket_if_needed(sender_phone: str, sender_name: str, new_state: dict, effective_media_id: str, message_body: str, mime_type: str, filename: str, effective_media_type: str = None, media_bytes: bytes = None, downloaded_mime_type: str = None, *, audio_transcript: str = None):
    if not new_state or not new_state.get("is_paused"):
        return {"status": "not_requested"}
    if new_state.get("chatwoot_conversation_id"):
        return {"status": "created", "conversation_id": new_state["chatwoot_conversation_id"]}

    invalidate_follow_up(sender_phone)

    print("[DEBUG] 8. Bot decidió pausarse, creando ticket...")
    print(
        f"[CHATWOOT CONFIG] account_id={config.CHATWOOT_ACCOUNT_ID} "
        f"inbox_id={config.CHATWOOT_INBOX_ID} "
        f"assignment_mode={config.CHATWOOT_ASSIGNMENT_MODE} "
        f"assignee_id={config.CHATWOOT_ASSIGNEE_ID or 'not-configured'}"
    )
    state_check = get_or_create_customer_state(sender_phone)
    if not state_check or not state_check.get("is_paused", True):
        return {"status": "cancelled"}
    if state_check.get("chatwoot_conversation_id"):
        return {"status": "created", "conversation_id": state_check["chatwoot_conversation_id"]}

    if effective_media_type == "audio" and audio_transcript:
        # Recovery jobs retain the durable conversation logs rather than the
        # original audio bytes/arguments. Keep the cause explicit across retries.
        save_message_log(sender_phone, "system", "HANDOFF_SOURCE: nota de voz interpretada mediante transcripción automática.",
                         metadata={"handoff_trigger": "audio", "trigger_transcript": audio_transcript})

    try:
        request = handoff_delivery.ensure_request(sender_phone)
        reserved = handoff_delivery.reserve_request(sender_phone, request) if request else None
    except Exception as exc:
        operational_event("HANDOFF_PENDING", phone=sender_phone, stage="HANDOFF",
                          reason="durable_state_unavailable", result=type(exc).__name__)
        return {"status": "pending"}
    if not reserved:
        return {"status": request.get("status", "pending") if request else "pending"}

    display_name = f"{sender_name} (+{sender_phone})" if valid_whatsapp_phone(sender_phone) else sender_name
    contact_id = chatwoot_api.get_or_create_contact(sender_phone, name=display_name)
    if not contact_id:
        operational_event("HANDOFF_FAILED", phone=sender_phone, stage="HANDOFF", reason=new_state.get("handoff_reason", "contact_creation"), result="contact_failed",
                          request_id=reserved["request_id"], attempt=reserved.get("attempts", 0), job_id=_current_job_id())
        failed = handoff_delivery.fail_request(sender_phone, reserved, "contact_creation_failed_before_conversation")
        _schedule_handoff_retry(sender_phone, failed)
        return {"status": "pending"}

    if not handoff_delivery.mark_creating(sender_phone, reserved):
        return {"status": "pending"}
    try:
        conv_id = chatwoot_api.create_conversation(contact_id, raise_on_error=True)
        if not conv_id:
            raise RuntimeError("conversation_creation_result_missing")
    except Exception as exc:
        confirmed = isinstance(exc, requests.exceptions.ConnectTimeout) or (isinstance(exc, ProviderError) and (exc.confirmed_not_sent or (exc.status is not None and 400 <= exc.status < 500)))
        failed = handoff_delivery.fail_request(sender_phone, reserved, exc, ambiguous=not confirmed)
        _schedule_handoff_retry(sender_phone, failed)
        operational_event("HANDOFF_FAILED", phone=sender_phone, stage="HANDOFF", reason=new_state.get("handoff_reason", "conversation_creation"), result="conversation_failed",
                          request_id=reserved["request_id"], attempt=reserved.get("attempts", 0), job_id=_current_job_id())
        return {"status": "pending" if confirmed else "unknown"}

    update_chatwoot_conversation_id(sender_phone, conv_id)
    handoff_delivery.complete_request(sender_phone, reserved, conv_id)
    logs = get_message_logs(sender_phone, limit=50)
    context_str = format_handoff_history(logs)
    reason = new_state.get("handoff_reason", "Razón no especificada")
    save_message_log(sender_phone, "system", f"HANDOFF: Transferido a humano. Razón: {reason}")
    operational_event("HANDOFF_CREATED", phone=sender_phone, stage="B2B_HIGH_VALUE" if reason.startswith("B2B_HIGH_VALUE") else "HANDOFF", reason=reason, result="chatwoot_created",
                      request_id=reserved["request_id"], conversation_id=conv_id, attempt=reserved.get("attempts", 0), job_id=_current_job_id())
    short_alert = f"🔔 {reason}"
    context_details = (
        f"{build_handoff_summary(logs, state_check.get('customer_data'), state_check.get('order_summary'), trigger_is_audio=effective_media_type == 'audio', trigger_transcript=audio_transcript)}\n"
        f"**Motivo de transferencia:** {reason}\n\n"
        f"**Últimos mensajes:**\n{context_str}"
    )

    if effective_media_type == "audio":
        # Local conversion or an HTTP 200 cannot prove playback in the advisor's
        # Chatwoot client. The transcript is the reliable handoff content.
        chatwoot_api.send_message_to_chatwoot(conv_id, context_details, is_private=True)
        chatwoot_api.send_message_to_chatwoot(conv_id, short_alert, is_private=True)
        operational_event("CHATWOOT_AUDIO_TRANSCRIPT_ONLY", phone=sender_phone, stage="HANDOFF",
                          reason="playback_not_verified", result="transcript_only" if audio_transcript else "transcript_unavailable",
                          conversation_id=conv_id)
    elif effective_media_id:
        file_bytes, downloaded_mime = (media_bytes, downloaded_mime_type) if media_bytes is not None else chatwoot_api.download_meta_media(effective_media_id)
        final_mime_type = (downloaded_mime or mime_type or "audio/ogg") if effective_media_type == "audio" else (mime_type or downloaded_mime or "application/octet-stream")
        extension = chatwoot_api.extension_from_mime(final_mime_type, ".bin")
        chatwoot_api.send_message_to_chatwoot(conv_id, context_details, is_private=True)
        if file_bytes:
            delivered = chatwoot_api.send_media_to_chatwoot(conv_id, short_alert, file_bytes, final_mime_type, filename or f"archivo_cliente{extension}", is_private=True)
            if delivered is None:
                _report_chatwoot_media_failure(sender_phone, conv_id, effective_media_type or "document", "attachment_not_verified")
        else:
            _report_chatwoot_media_failure(sender_phone, conv_id, effective_media_type or "document", "download_failed")
    else:
        chatwoot_api.send_message_to_chatwoot(conv_id, context_details, is_private=True)
        chatwoot_api.send_message_to_chatwoot(conv_id, short_alert, is_private=True)
    if reason.startswith("B2B_HIGH_VALUE") and queue_enabled():
        enqueue_in(
            5 * 60, escalate_unattended_b2b_handoff, sender_phone, conv_id, reason,
            job_id=f"b2b-escalation-{config.BUSINESS_ID}-{conv_id}",
        )
    return {"status": "created", "conversation_id": conv_id}


def _schedule_handoff_retry(phone, request):
    if request and request.get("status") == "pending" and queue_enabled():
        try:
            due = parse_timestamp(request.get("retry_at")) or datetime.now(timezone.utc) + timedelta(minutes=1)
            enqueue_in(max(1, int((due - datetime.now(timezone.utc)).total_seconds()) + 1),
                       retry_pending_handoff, phone, request["request_id"], config.BUSINESS_ID,
                       job_id=f"handoff-retry-{config.BUSINESS_ID}-{request['request_id']}-{request['attempts']}")
        except Exception:
            # The DB row is the durable recovery source, including Redis outages.
            operational_event("HANDOFF_PENDING", phone=phone, stage="HANDOFF", reason="retry_queue_unavailable", result="database_recovery_pending")


def retry_pending_handoff(phone, request_id, business_id):
    if business_id != config.BUSINESS_ID:
        return
    with phone_lock(phone):
        state = get_or_create_customer_state(phone)
        if not state:
            return
        row = handoff_delivery.get_request(phone)
        if not row or row["request_id"] != request_id or row["status"] not in {"pending", "reserved", "sending"}:
            return
        if not state.get("is_paused") or state.get("chatwoot_conversation_id"):
            handoff_delivery.cancel_request(phone, request_id)
            return
        return _create_handoff_ticket_if_needed(phone, "Cliente", state, None, "Recuperación de derivación pendiente", None, None)


async def _recover_pending_handoffs_loop():
    while True:
        try:
            await asyncio.to_thread(recover_pending_handoffs)
        except Exception as exc:
            print(f"[HANDOFF RECOVERY] pending-state check unavailable: {type(exc).__name__}", flush=True)
        await asyncio.sleep(60)


def recover_pending_handoffs():
    for row in handoff_delivery.pending_requests(actionable_only=True):
        if row["status"] in {"pending", "reserved", "sending"}:
            retry_pending_handoff(row["phone_number"], row["request_id"], config.BUSINESS_ID)


@app.get("/api/pending-handoffs", dependencies=[Depends(admin_auth)])
def pending_handoffs(client_name: str = Query(min_length=1)):
    try:
        validate_deployment_client(client_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"client_name": config.BUSINESS_ID, "requests": handoff_delivery.pending_requests()}


def escalate_unattended_b2b_handoff(phone_number: str, conversation_id: int, reason: str):
    """Raise one priority reminder when no advisor has logged a public response."""
    logs = get_message_logs(phone_number, limit=50)
    handoff_index = max((i for i, row in enumerate(logs) if row.get("role") == "system" and str(row.get("content", "")).startswith("HANDOFF:")), default=-1)
    if any(row.get("role") == "asesor" for row in logs[handoff_index + 1:]):
        operational_event("FOLLOW_UP_CANCELLED", phone=phone_number, stage="B2B_ESCALATION",
                          reason="advisor_intervened", attempt=1, result="not_alerted")
        return
    state = get_or_create_customer_state(phone_number)
    if not state or not state.get("is_paused") or state.get("chatwoot_conversation_id") != conversation_id:
        operational_event("FOLLOW_UP_SKIPPED", phone=phone_number, stage="B2B_ESCALATION",
                          reason="handoff_no_longer_active", attempt=1, result="not_alerted")
        return
    if not claim_internal_alert(f"b2b-priority:{conversation_id}"):
        return
    alert = f"🚨 PRIORIDAD: lead empresarial sin gestión después de 5 minutos. {reason}"
    with message_trace(source="internal_alert", source_event_id=f"b2b-priority:{conversation_id}"):
        accepted = chatwoot_api.send_message_to_chatwoot(conversation_id, alert, is_private=True)
        if accepted is not None:
            save_message_log(phone_number, "system", alert)
        operational_event("INTERNAL_ALERT_SENT" if accepted is not None else "INTERNAL_ALERT_FAILED",
                          phone=phone_number, stage="B2B_ESCALATION", reason="advisor_sla_exceeded",
                          attempt=1, result="priority_alert_created" if accepted is not None else "provider_rejected")


@app.post("/api/manual-handoff", dependencies=[Depends(admin_auth)])
def manual_handoff(body: ManualHandoffRequest, client_name: str = Query(min_length=1)):
    """Let an authenticated read-only operations app open a Chatwoot conversation."""
    try:
        validate_deployment_client(client_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    state = get_or_create_customer_state(body.phone_number, body.customer_name)
    if not state:
        raise HTTPException(502, "No fue posible recuperar el estado del contacto")
    if state.get("chatwoot_conversation_id"):
        return {"ok": True, "status": "already_open", "conversation_id": state["chatwoot_conversation_id"]}
    pause_bot_for_handoff(body.phone_number, body.reason)
    state = get_or_create_customer_state(body.phone_number, body.customer_name)
    _create_handoff_ticket_if_needed(
        body.phone_number, body.customer_name, state, None, "Handoff manual desde dashboard", None, None,
    )
    final_state = get_or_create_customer_state(body.phone_number, body.customer_name)
    conversation_id = final_state.get("chatwoot_conversation_id") if final_state else None
    if not conversation_id:
        raise HTTPException(502, "No fue posible crear la conversación en Chatwoot")
    return {"ok": True, "status": "created", "conversation_id": conversation_id}


@app.get("/api/catalog-delivery-recoveries", dependencies=[Depends(admin_auth)])
def catalog_delivery_recoveries(client_name: str = Query(min_length=1), limit: int = Query(500, ge=1, le=5000)):
    """List unresolved legacy delivery errors so Lovable can recover them deliberately."""
    try:
        validate_deployment_client(client_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    newest_rows = (
        supabase.table("message_logs")
        .select("phone_number,role,content,created_at,id")
        .order("created_at", desc=True)
        .order("id", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )
    candidates = [item.as_dict() for item in find_catalog_recoveries(list(reversed(newest_rows)))]
    return {"client_name": client_name, "count": len(candidates), "recoveries": candidates}


def recover_catalog_delivery(phone_number: str, catalog_id: str):
    """RQ job: retry one still-eligible delivery or create a safe manual handoff."""
    from bot import refresh_managed_catalogs

    with phone_lock(phone_number):
        rows = (
            supabase.table("message_logs")
            .select("phone_number,role,content,created_at,id")
            .eq("phone_number", phone_number)
            .order("created_at")
            .order("id")
            .limit(500)
            .execute().data or []
        )
        candidate = next(
            (item for item in find_catalog_recoveries(rows) if item.catalog_id == catalog_id),
            None,
        )
        if not candidate:
            return {"status": "already_resolved"}
        if not candidate.within_service_window:
            reason = f"Recuperar manualmente catálogo no entregado: {catalog_id}. Fuera de ventana automática."
            pause_bot_for_handoff(phone_number, reason)
            state = get_or_create_customer_state(phone_number, "Cliente")
            _create_handoff_ticket_if_needed(phone_number, "Cliente", state, None, reason, None, None)
            return {"status": "manual_handoff", "catalog_id": catalog_id}

        refresh_managed_catalogs()
        send_whatsapp_message(
            phone_number,
            "Parece que hubo un error y el catálogo no se envió. Te lo comparto por aquí.",
        )
        delivered, technical_error = deliver_presaved_file(phone_number, catalog_id)
        if delivered:
            save_message_log(phone_number, "system", f"Archivos enviados: {catalog_id}. Recuperación administrativa.")
            return {"status": "resent", "catalog_id": catalog_id}
        reason = f"Falló el reenvío administrativo de {catalog_id}. Un asesor debe enviarlo manualmente."
        save_message_log(phone_number, "system", f"ERROR reenviando archivo: {technical_error or catalog_id}")
        pause_bot_for_handoff(phone_number, reason)
        try:
            send_whatsapp_message(
                phone_number,
                "El adjunto volvió a fallar. Se necesita revisión del equipo para ayudarte personalmente.",
            )
        except Exception:
            print("[CATALOG RECOVERY] No fue posible enviar el aviso final al cliente")
        state = get_or_create_customer_state(phone_number, "Cliente")
        _create_handoff_ticket_if_needed(phone_number, "Cliente", state, None, reason, None, None)
        return {"status": "manual_handoff", "catalog_id": catalog_id}


@app.post("/api/catalog-delivery-recoveries/resend", dependencies=[Depends(admin_auth)])
def enqueue_catalog_delivery_recoveries(body: BulkCatalogRecoveryRequest, client_name: str = Query(min_length=1)):
    """Queue explicitly confirmed recovery attempts without blocking the admin request."""
    try:
        validate_deployment_client(client_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not queue_enabled():
        raise HTTPException(503, "La cola Redis es obligatoria para reenvíos por lote")
    queued = []
    for selection in body.recoveries:
        job = enqueue(
            recover_catalog_delivery,
            selection.phone_number,
            selection.catalog_id,
            job_id=(
                f"catalog-recovery-{selection.phone_number}-{selection.catalog_id}-"
                f"{time.time_ns()}"
            ),
        )
        queued.append({"phone_number": selection.phone_number, "catalog_id": selection.catalog_id, "job_id": job.id})
    return {"ok": True, "queued": queued}


def recover_gemini_outage_handoffs(since: str):
    """Create missing Chatwoot tickets for unanswered turns from an old outage."""
    rows = (
        supabase.table("message_logs")
        .select("phone_number,role,content,created_at,id")
        .gte("created_at", since)
        .order("created_at")
        .order("id")
        .limit(10000)
        .execute()
        .data
        or []
    )
    recoveries = find_recoveries(rows)
    result = {"candidates": len(recoveries), "tickets_created": 0, "skipped": 0, "failed": 0}
    print(f"[GEMINI RECOVERY] handoffs_pending={len(recoveries)}")
    for recovery in recoveries:
        try:
            state = get_or_create_customer_state(recovery.phone)
            if not state or state.get("chatwoot_conversation_id"):
                result["skipped"] += 1
                continue
            reason = "Gemini no respondió: no había créditos prepagados"
            pause_bot_for_handoff(recovery.phone, reason)
            state = get_or_create_customer_state(recovery.phone)
            _create_handoff_ticket_if_needed(
                recovery.phone, "Cliente", state, None,
                "\n".join(recovery.questions), None, None,
            )
            final_state = get_or_create_customer_state(recovery.phone)
            if final_state and final_state.get("chatwoot_conversation_id"):
                result["tickets_created"] += 1
            else:
                result["failed"] += 1
        except Exception as exc:
            result["failed"] += 1
            print(f"[GEMINI RECOVERY ERROR] phone={recovery.phone} error={sanitize_text(exc)}")
    return result


def _recover_whatsapp_processing_error(sender_phone, sender_name, message_body, error, expiry,
                                      mime_type=None, filename=None, has_media=False):
    """Run recovery while the original conversation lock and trace are held."""
    technical = f"{type(error).__name__}: {sanitize_text(error, (config.WA_TOKEN, config.SUPABASE_KEY, config.CHATWOOT_API_TOKEN))}"
    text_delivery_failed = isinstance(error, ProviderError) and error.provider == "meta" and error.operation == "send_text"
    try:
        if text_delivery_failed:
            update_trace(provider_message_id="")
            confirmed = error.confirmed_not_sent or (error.status is not None and 400 <= error.status < 500)
            outcome = "not_sent" if confirmed else "unknown"
            reason = ("confirmed_pre_send_failure" if error.confirmed_not_sent else
                      "provider_rejected" if confirmed else "provider_result_ambiguous_manual_review")
            save_message_log(sender_phone, "system", f"ERROR DE ENTREGA META: {technical}. Resultado: {outcome}.",
                             metadata={"delivery_outcome": outcome, "delivery_failure_reason": reason})
            operational_event("RESPONSE_DELIVERY_FAILED", phone=sender_phone, stage="DIRECT_RESPONSE",
                              reason=reason, result=outcome, provider_status=error.status,
                              provider_code=error.code, provider_subcode=error.subcode,
                              transport_reason=error.safe_message)
            state = get_or_create_customer_state(sender_phone, sender_name or "Cliente")
            if state and not has_media and customer_closes_without_pending_request(message_body, state):
                operational_event("RESPONSE_RECOVERY_SKIPPED", phone=sender_phone, stage="DIRECT_RESPONSE",
                                  reason="conversation_closed_without_pending_request", result="no_handoff_no_resend")
                return technical
            reason = ("No se pudo confirmar la entrega de la respuesta por WhatsApp. "
                      "Revisar el mensaje en Meta antes de reenviarlo; puede haber sido aceptado."
                      if not confirmed else
                      "Meta no aceptó la respuesta por WhatsApp. La solicitud necesita revisión del equipo.")
        else:
            print(f"[PROCESSING ERROR] {technical}")
            save_message_log(sender_phone, "system", f"ERROR NO MANEJADO: {technical}")
            reason = "El bot encontró un error técnico y no pudo completar la solicitud. Requiere revisión humana."
        pause_bot_for_handoff(sender_phone, reason)
        state = get_or_create_customer_state(sender_phone, sender_name or "Cliente")
        _create_handoff_ticket_if_needed(sender_phone, sender_name or "Cliente", state, None,
                                        message_body, mime_type, filename)
        # A failed POST can already have reached Meta. Do not send the original
        # reply or a second fallback through the failing channel automatically.
        if not text_delivery_failed and meta_window_is_open(expiry):
            send_whatsapp_message(sender_phone,
                "Tuve un problema técnico y tu solicitud necesita revisión del equipo. La atención humana puede seguir pendiente.")
    except Exception as recovery_error:
        print(f"[ERROR RECOVERY] {sanitize_text(recovery_error, (config.WA_TOKEN, config.SUPABASE_KEY, config.CHATWOOT_API_TOKEN))}")
        operational_event("RESPONSE_RECOVERY_FAILED", phone=sender_phone, stage="DIRECT_RESPONSE",
                          reason="recovery_failed", result=type(recovery_error).__name__)
    return technical


def process_whatsapp_message(sender_phone: str, sender_name: str, message_body: str, is_image: bool = False, media_id: str = None, is_audio: bool = False, audio_media_id: str = None, media_type: str = None, mime_type: str = None, filename: str = None, event_id: str = None, customer_message_received_at=None):
    """Procesador que enruta entre el Bot y Chatwoot basado en el estado."""
    started_at = time.perf_counter()
    received = parse_timestamp(customer_message_received_at)
    expiry = (received + timedelta(hours=24)).isoformat() if received else ""
    with message_trace(source="bot_response", source_event_id=event_id or "",
          inbound_message_ids=[event_id] if event_id else [], received_at=received.isoformat() if received else "",
          meta_window_expires_at=expiry, job_id=_current_job_id(), provider_message_id=""):
        try:
            with phone_lock(sender_phone):
                try:
                    result = _process_whatsapp_message_unlocked(sender_phone, sender_name, message_body, is_image, media_id, is_audio, audio_media_id, media_type, mime_type, filename, customer_message_received_at=received)
                    if isinstance(result, dict) and result.get("status") == "delivery_failed":
                        mark_webhook_event_processed("whatsapp", event_id, status="failed", error=result["reason"])
                        return
                except Exception as error:
                    technical = _recover_whatsapp_processing_error(sender_phone, sender_name, message_body, error, expiry,
                        mime_type, filename, has_media=bool(media_id or audio_media_id or is_image or is_audio))
                    mark_webhook_event_processed("whatsapp", event_id, status="failed", error=technical)
                    return
        except Exception as lock_error:
            # Never mutate conversation state without owning its lock: another
            # worker may already be handling the next customer message.
            operational_event("RESPONSE_PROCESSING_FAILED", phone=sender_phone, stage="INBOUND",
                              reason="conversation_lock_unavailable", result=type(lock_error).__name__)
            mark_webhook_event_processed("whatsapp", event_id, status="failed", error=type(lock_error).__name__)
            return
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        print(f"[METRIC] whatsapp_message_processed event_id={event_id} phone={sender_phone} duration_ms={duration_ms}")
        mark_webhook_event_processed("whatsapp", event_id)


def _process_whatsapp_message_unlocked(sender_phone: str, sender_name: str, message_body: str, is_image: bool = False, media_id: str = None, is_audio: bool = False, audio_media_id: str = None, media_type: str = None, mime_type: str = None, filename: str = None, *, customer_message_received_at=None):
    customer_message_received_at = parse_timestamp(customer_message_received_at)
    effective_media_id = media_id or audio_media_id
    effective_media_type = normalize_media_type(media_type or ("image" if is_image else "audio" if is_audio else None), mime_type) if effective_media_id else None
    is_image = effective_media_type == "image"
    is_audio = effective_media_type == "audio"
    print(f"\n[DEBUG] 1. Recibido mensaje de {sender_phone} (Media: {effective_media_type or 'texto'})")
    # Any inbound customer activity cancels the previously planned reminder.
    terminal_customer_signal = is_terminal_customer_message(message_body)
    # Ingress records the activity watermark. Re-recording here could move an
    # equal-second batch back to an older event while a worker drains the queue.
    operational_event("FOLLOW_UP_CANCELLED", phone=sender_phone, stage="ACTIVE",
                      reason="customer_replied" if not terminal_customer_signal else "terminal_customer_signal",
                      result="invalidated")

    if is_restart_command(message_body):
        invalidate_follow_up(sender_phone)
        reset_client_history(sender_phone)
        send_whatsapp_message(sender_phone, "🔄 Conversación reiniciada. El bot está activo y empezamos de cero.")
        return

    state_record = get_or_create_customer_state(sender_phone, sender_name or "Cliente")
    if not state_record:
        print("[DEBUG] 3. ERROR: No se pudo obtener ni crear el state_record")
        raise RuntimeError("customer_state_unavailable")

    incoming_text = f"[Archivo/{effective_media_type}] {message_body or ''}" if effective_media_id else (message_body or "")
    with message_trace(input_type=effective_media_type or "text"):
        save_message_log(sender_phone, "user", incoming_text)

    preferences = follow_up_preference_patch(message_body or "", state_record)
    agreed_date = customer_agreed_not_before(message_body or "", customer_message_received_at,
        business_id=config.BUSINESS_ID) if customer_message_received_at else None
    if agreed_date:
        preferences["follow_up_not_before"] = agreed_date.isoformat()
    if preferences:
        update_follow_up_preferences(sender_phone, preferences)
        state_record.update(preferences)

    expiry = customer_message_received_at + timedelta(hours=24) if customer_message_received_at else None
    if not expiry or expiry <= datetime.now(timezone.utc):
        operational_event("RESPONSE_SKIPPED", phone=sender_phone, stage="INBOUND",
                          reason="original_meta_window_expired" if expiry else "legacy_missing_original_timestamp", result="no_freeform_send")
        if expiry:
            pause_bot_for_handoff(sender_phone, "Mensaje pendiente cuya ventana de WhatsApp venció en cola; revisión humana sin envío libre automático")
            state_record = get_or_create_customer_state(sender_phone)
            _create_handoff_ticket_if_needed(sender_phone, sender_name, state_record, effective_media_id, message_body, mime_type, filename, effective_media_type)
        return

    if human_control_active(state_record):
        print("[DEBUG] 4. Bot pausado, derivando mensaje al asesor humano en Chatwoot")
        conv_id = state_record.get("chatwoot_conversation_id")
        if is_audio:
            audio_bytes, downloaded_mime = chatwoot_api.download_meta_media(effective_media_id)
            transcript = transcribe_audio_message(audio_bytes, downloaded_mime or mime_type or "audio/ogg")
            if transcript:
                update_inbound_transcript(sender_phone, current_trace().get("source_event_id"), transcript)
            if not conv_id:
                _create_handoff_ticket_if_needed(sender_phone, sender_name, state_record, effective_media_id,
                    message_body, mime_type, filename, effective_media_type, audio_bytes, downloaded_mime,
                    audio_transcript=transcript)
                return
            delivered = chatwoot_api.send_message_to_chatwoot(conv_id, _audio_transcript_note(transcript),
                                                            is_private=not bool(transcript))
            operational_event("CHATWOOT_AUDIO_TRANSCRIPT_ONLY", phone=sender_phone, stage="HUMAN_MEDIA",
                              reason="playback_not_verified", result="transcript_only" if transcript else "transcript_unavailable",
                              conversation_id=conv_id)
            if delivered is None:
                return _report_chatwoot_media_failure(sender_phone, conv_id, "audio", "transcript_delivery_failed")
            return
        if not conv_id:
            _create_handoff_ticket_if_needed(sender_phone, sender_name, state_record, effective_media_id, message_body, mime_type, filename, effective_media_type)
            return
        if effective_media_id:
            file_bytes, downloaded_mime = chatwoot_api.download_meta_media(effective_media_id)
            final_mime_type = (downloaded_mime or mime_type or "audio/ogg") if effective_media_type == "audio" else (mime_type or downloaded_mime or "application/octet-stream")
            extension = chatwoot_api.extension_from_mime(final_mime_type, ".bin")
            final_filename = filename or f"archivo_cliente{extension}"
            label = "📎 El usuario envió un archivo"
            if effective_media_type == "image":
                label = "📸 El usuario envió una imagen"
            elif effective_media_type == "audio":
                label = "🎙️ El usuario envió una nota de voz"
            elif effective_media_type == "video":
                label = "🎥 El usuario envió un video"
            texto_chatwoot = f"{label}: {message_body}" if message_body else label
            if file_bytes:
                delivered = chatwoot_api.send_media_to_chatwoot(conv_id, texto_chatwoot, file_bytes, final_mime_type, final_filename, is_private=False)
                if delivered is None:
                    return _report_chatwoot_media_failure(sender_phone, conv_id, effective_media_type, "attachment_not_verified")
            else:
                if message_body:
                    chatwoot_api.send_message_to_chatwoot(conv_id, message_body, is_private=False)
                return _report_chatwoot_media_failure(sender_phone, conv_id, effective_media_type, "download_failed")
        else:
            chatwoot_api.send_message_to_chatwoot(conv_id, message_body, is_private=False)
        return

    audio_bytes = None
    downloaded_mime_type = None
    transcript = None
    if is_audio:
        print("[DEBUG] 5. Audio recibido con bot activo. Descargando y transcribiendo...")
        audio_bytes, downloaded_mime_type = chatwoot_api.download_meta_media(effective_media_id) if effective_media_id else (None, None)
        transcript = transcribe_audio_message(audio_bytes, downloaded_mime_type or mime_type or "audio/ogg")
        if not transcript:
            reason = "No fue posible transcribir la nota de voz. Requiere revisión humana."
            save_message_log(sender_phone, "system", "ERROR procesando audio: transcription_failed")
            pause_bot_for_handoff(sender_phone, reason)
            send_whatsapp_message(
                sender_phone,
                "Perdón, no pude procesar bien tu nota de voz. Tu solicitud necesita revisión del equipo.",
            )
            state = get_or_create_customer_state(sender_phone, sender_name or "Cliente")
            _create_handoff_ticket_if_needed(
                sender_phone, sender_name or "Cliente", state, effective_media_id,
                message_body, mime_type, filename, effective_media_type, audio_bytes, downloaded_mime_type,
            )
            return
        print(f"[DEBUG] 5.1 Transcripción de audio: {transcript}")
        message_body = transcript
        update_inbound_transcript(sender_phone, current_trace().get("source_event_id"), transcript)
        audio_preferences = follow_up_preference_patch(transcript, state_record)
        agreed_date = customer_agreed_not_before(transcript, customer_message_received_at, business_id=config.BUSINESS_ID)
        if agreed_date:
            audio_preferences["follow_up_not_before"] = agreed_date.isoformat()
        if audio_preferences:
            update_follow_up_preferences(sender_phone, audio_preferences)
            preferences.update(audio_preferences)
            state_record.update(audio_preferences)

    print("[DEBUG] 5. Procesando lógica del bot...")
    ai_turn = process_message_logic(sender_phone, message_body, is_image, False)
    print("[DEBUG] 6. Respuesta IA generada")

    if ai_turn:
        # Creating the case precedes any assertion to the customer that an
        # advisor was notified. A catalog never overrides this paused state.
        initial_state = get_or_create_customer_state(sender_phone)
        if initial_state and initial_state.get("is_paused"):
            handoff = _create_handoff_ticket_if_needed(sender_phone, sender_name, initial_state,
                effective_media_id, message_body, mime_type, filename, effective_media_type, audio_bytes, downloaded_mime_type,
                **({"audio_transcript": transcript} if is_audio else {}))
            if not handoff or handoff.get("status") != "created":
                ai_turn.response = "Tu solicitud requiere revisión del equipo. La atención humana sigue pendiente; todavía no he podido confirmar que un asesor la haya recibido."
        primary_delivered = not bool(ai_turn.response)
        delivered_files = []
        file_errors = []
        if ai_turn.send_files_before_response:
            for file_id in ai_turn.requested_files:
                delivered, technical_error = deliver_presaved_file(sender_phone, file_id)
                if delivered:
                    delivered_files.append(file_id)
                    save_message_log(sender_phone, "model", f"[Archivo enviado: {file_id}]")
                elif technical_error:
                    file_errors.append(technical_error)
        if ai_turn.response:
            send_whatsapp_message(sender_phone, ai_turn.response)
            save_message_log(sender_phone, "model", ai_turn.response)
            primary_delivered = True
        if not ai_turn.send_files_before_response:
            for file_id in ai_turn.requested_files:
                delivered, technical_error = deliver_presaved_file(sender_phone, file_id)
                if delivered:
                    delivered_files.append(file_id)
                    save_message_log(sender_phone, "model", f"[Archivo enviado: {file_id}]")
                elif technical_error:
                    file_errors.append(technical_error)
        if delivered_files:
            save_message_log(sender_phone, "system", f"Archivos enviados: {', '.join(delivered_files)}")
        failed_files = [file_id for file_id in ai_turn.requested_files if file_id not in delivered_files]
        if failed_files:
            technical_summary = "; ".join(file_errors) or "fallo_no_clasificado"
            save_message_log(
                sender_phone,
                "system",
                f"ERROR enviando archivos: {', '.join(failed_files)}. Detalle técnico: {technical_summary}",
            )
            reason = (
                "No se pudo entregar el catálogo solicitado. "
                f"Archivos afectados: {', '.join(failed_files)}. Un asesor debe enviarlo manualmente."
            )
            pause_bot_for_handoff(sender_phone, reason)
            try:
                send_whatsapp_message(
                    sender_phone,
                    "Perdón, no pude adjuntar el catálogo. Se necesita revisión del equipo para enviártelo manualmente.",
                )
                save_message_log(
                    sender_phone,
                    "model",
                    "Perdón, no pude adjuntar el catálogo. Se necesita revisión del equipo para enviártelo manualmente.",
                )
            except Exception as exc:
                print(f"[FILE DELIVERY ERROR] Could not send customer fallback: {sanitize_text(exc, (config.WA_TOKEN,))}")
        new_state = get_or_create_customer_state(sender_phone)
        if new_state:
            new_state.update(preferences)
        _create_handoff_ticket_if_needed(
            sender_phone, sender_name, new_state, effective_media_id, message_body,
            mime_type, filename, effective_media_type, audio_bytes, downloaded_mime_type,
            **({"audio_transcript": transcript} if is_audio else {}),
        )
        if primary_delivered and new_state and not new_state.get("is_paused") and not terminal_customer_signal:
            not_before = agreed_date or parse_timestamp(new_state.get("follow_up_not_before"))
            if not_before and not_before <= customer_message_received_at:
                not_before = None
            plan = follow_up_plan(ai_turn.response, delivered_files, ai_turn.follow_up_message,
                                  customer_message=message_body, state=new_state, handoff_requested=getattr(ai_turn, "handoff_requested", False),
                                  not_before=not_before)
            if plan is None:
                operational_event("FOLLOW_UP_SKIPPED", phone=sender_phone, stage="DECISION",
                                  reason=follow_up_suppression_reason(message_body, state=new_state,
                                  model_message=ai_turn.follow_up_message, handoff_requested=getattr(ai_turn, "handoff_requested", False)) or "no_follow_up", result="not_scheduled")
            schedule_follow_up_sequence(
                sender_phone, plan,
                customer_message_received_at=customer_message_received_at,
                agreed_not_before=not_before,
            )


def resolved_customer_message() -> str:
    """Return the closing message for the business served by this deployment."""
    if config.BUSINESS_ID == "tanaka":
        return "Gracias por elegirnos. Cuando necesites algo más, escríbenos ☺️"
    return "✅ Tu solicitud ha sido resuelta. Si necesitas algo más, envíame un mensaje."


def _chatwoot_flag(value, default: bool = False) -> bool:
    """Normalize booleans from Chatwoot webhook payloads.

    Chatwoot normally emits JSON booleans, but integrations and webhook relays can
    serialize them as strings.  In particular, ``bool("false")`` is true in
    Python and used to make a public agent reply look like a private note.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no", "", "null"}:
            return False
    if value is None:
        return default
    return bool(value)


def _chatwoot_sender_label(data: dict) -> str:
    """Return a non-PII sender identifier useful for delivery diagnostics."""
    sender = data.get("sender") if isinstance(data.get("sender"), dict) else {}
    sender_id = sender.get("id")
    sender_type = sender.get("type") or sender.get("role") or "unknown"
    return f"{sender_type}:{sender_id if sender_id is not None else 'unknown'}"


def _notify_advisor_attachment_failure(conversation_id, message_id, error):
    """Expose an outgoing file failure even when its UI status is overlooked."""
    reason = error.safe_message if isinstance(error, ProviderError) else ""
    explanations = {
        "attachment_original_url_missing": "Falta el enlace al archivo original.",
        "attachment_download_http_error": "No se pudo descargar el archivo original desde el almacenamiento.",
        "attachment_pending_upload_timeout": "El archivo no terminó de estar disponible en el almacenamiento dentro del tiempo de espera.",
        "attachment_meta_upload_http_error": "WhatsApp rechazó la subida del archivo; revisa su tamaño y formato.",
        "attachment_image_format_unsupported": "La imagen debe estar en formato JPEG o PNG.",
        "attachment_too_large": "El archivo supera el tamaño admitido por esta integración.",
    }
    detail = explanations.get(reason, "El envío necesita revisión; no hay confirmación de entrega.")
    chatwoot_api.send_message_to_chatwoot(
        conversation_id,
        f"⚠️ No se confirmó el envío a WhatsApp del adjunto del mensaje #{message_id}. {detail}\n"
        "El botón Reintentar de Chatwoot no confirma un nuevo envío por esta integración. "
        "Revisa el fallo antes de volver a enviarlo como un mensaje nuevo para evitar duplicados.",
        is_private=True,
    )


def process_chatwoot_event(data: dict, event_id: str = None):
    with message_trace(source="human", source_event_id=event_id or "", job_id=_current_job_id(), inbound_message_ids=[]):
        return _process_chatwoot_event_untraced(data, event_id)


def _process_chatwoot_event_untraced(data: dict, event_id: str = None):
    """Process a Chatwoot webhook event from the queue/worker."""
    started_at = time.perf_counter()
    event_conv_id = None
    message_id = None
    is_public_outgoing = False
    attachments = None
    try:
        account_id, inbox_id = chatwoot_scope(data)
        if account_id != str(config.CHATWOOT_ACCOUNT_ID) or inbox_id != str(config.CHATWOOT_INBOX_ID):
            raise ValueError("Chatwoot event scope does not match this deployment")
        event = data.get("event")
        conversation = data.get("conversation") if isinstance(data.get("conversation"), dict) else {}
        event_status = data.get("status") or conversation.get("status")
        event_conv_id = conversation.get("id") or data.get("id")
        print(
            f"[CHATWOOT EVENT] event={event} conversation_id={event_conv_id} "
            f"status={event_status} account_id={account_id} inbox_id={inbox_id}"
        )
        if event in {"conversation_status_changed", "conversation_updated"}:
            operational_event("CHATWOOT_CONVERSATION_OBSERVED", phone="", stage="CHATWOOT_STATE",
                              reason="provider_event", result="observed", **chatwoot_event_diagnostics(data))
        message_type = str(data.get("message_type") or "").strip().lower()
        is_private = _chatwoot_flag(data.get("private"), default=False)
        message_id = data.get("id") if event == "message_created" else None
        is_public_outgoing = event == "message_created" and message_type == "outgoing" and not is_private
        if is_public_outgoing:
            conv_id = event_conv_id
            content = data.get("content")
            attachments = data.get("attachments")
            phone = get_phone_by_chatwoot_id(conv_id) if conv_id else None
            if phone:
                invalidate_follow_up(phone)
                pause_bot_for_human_activity(phone, conv_id)
                inbound = next((row for row in reversed(get_message_logs(phone, limit=50)) if row.get("role") == "user"), {})
                original = parse_timestamp((inbound.get("metadata") or {}).get("received_at"))
                update_trace(meta_window_expires_at=(original + timedelta(hours=24)).isoformat() if original else "")
                operational_event("FOLLOW_UP_CANCELLED", phone=phone, stage="ACTIVE",
                                  reason="advisor_intervened", result="invalidated")
                catalog_command = advisor_catalog_command(content) if not attachments else None
                if catalog_command:
                    from bot import refresh_managed_catalogs

                    refresh_managed_catalogs()
                    delivered, technical_error = deliver_presaved_file(phone, catalog_command)
                    if not delivered:
                        raise ProviderError(
                            "whatsapp", "advisor_catalog_command", None,
                            message=technical_error or f"catalog unavailable: {catalog_command}",
                        )
                    save_message_log(phone, "asesor", f"Catálogo enviado por asesor: {catalog_command}")
                elif attachments:
                    # Validate all original links before contacting Meta, so a
                    # missing later file cannot silently complete a partial send.
                    if not isinstance(attachments, list) or any(
                        not isinstance(attachment, dict) or not _attachment_url(attachment)
                        for attachment in attachments
                    ):
                        raise ProviderError("chatwoot", "forward_attachment", None,
                                            message="attachment_original_url_missing", confirmed_not_sent=True)
                    for attachment in attachments:
                        data_url = _attachment_url(attachment)
                        attachment_mime = attachment.get("content_type") or attachment.get("mime_type") or "application/octet-stream"
                        attachment_filename = _attachment_filename(attachment)
                        whatsapp_media_type = normalize_media_type(attachment.get("file_type"), attachment_mime, data_url)
                        meta_media_id = upload_chatwoot_attachment_to_meta(
                            data_url, attachment_mime, attachment_filename,
                            attachment_created_at=data.get("created_at"),
                        )
                        if not meta_media_id:
                            raise ProviderError("chatwoot", "forward_attachment", None,
                                                message=current_trace().get("chatwoot_attachment_error") or "attachment upload failed")
                        send_whatsapp_media(phone, meta_media_id, whatsapp_media_type, content, attachment_filename)
                if content and not attachments and not catalog_command:
                    send_whatsapp_message(phone, content)
                if not catalog_command:
                    save_message_log(phone, "asesor", content or "[Adjunto enviado por asesor]")
                # Meta's synchronous response acknowledges acceptance. It does
                # not prove that the customer's device received the message.
                chatwoot_api.update_message_status(conv_id, message_id, "sent")
                print(
                    f"[CHATWOOT DELIVERY] forwarded event_id={event_id} conversation_id={conv_id} "
                    f"sender={_chatwoot_sender_label(data)} has_content={bool(content)} "
                    f"attachment_count={len(attachments or [])}"
                )
            else:
                chatwoot_api.update_message_status(
                    conv_id, message_id, "failed", "No se encontró el teléfono asociado a la conversación"
                )
                print(
                    f"[CHATWOOT EVENT WARN] Public outgoing message not forwarded: "
                    f"reason=conversation_not_mapped event_id={event_id} conversation_id={conv_id} "
                    f"sender={_chatwoot_sender_label(data)}"
                )
        elif event == "message_created":
            reason = "private_note" if is_private else f"message_type_{message_type or 'missing'}"
            print(
                f"[CHATWOOT EVENT] Message intentionally not forwarded: reason={reason} "
                f"event_id={event_id} conversation_id={event_conv_id} "
                f"sender={_chatwoot_sender_label(data)}"
            )
        elif event == "conversation_status_changed" and event_status == "resolved":
            conv_id = event_conv_id
            phone = resume_bot_state(conv_id) if conv_id else None
            if phone:
                invalidate_follow_up(phone)
                save_message_log(phone, "system", "RESOLVED: Chatwoot marcó la conversación como resuelta.")
                inbound = next((row for row in reversed(get_message_logs(phone, limit=50)) if row.get("role") == "user"), {})
                original = parse_timestamp((inbound.get("metadata") or {}).get("received_at"))
                expiry = (original + timedelta(hours=24)).isoformat() if original else ""
                update_trace(meta_window_expires_at=expiry)
                if meta_window_is_open(expiry):
                    send_whatsapp_message(phone, resolved_customer_message())
                else:
                    operational_event("RESPONSE_SKIPPED", phone=phone, stage="RESOLVED", reason="original_meta_window_expired", result="no_customer_notice")
                print(f"[CHATWOOT EVENT] Bot resumed phone={phone} conversation_id={conv_id}")
            else:
                print(f"[CHATWOOT EVENT WARN] No paused customer found for resolved conversation_id={conv_id}")
        duration_ms = int((time.perf_counter() - started_at) * 1000)
        print(f"[METRIC] chatwoot_event_processed event_id={event_id} event={event} duration_ms={duration_ms}")
        mark_webhook_event_processed("chatwoot", event_id)
    except Exception as e:
        import traceback
        print("[ERROR CRÍTICO] Falló process_chatwoot_event:")
        traceback.print_exc()
        if is_public_outgoing and event_conv_id and message_id:
            chatwoot_api.update_message_status(
                event_conv_id, message_id, "failed", "No se pudo entregar el mensaje a WhatsApp"
            )
            if attachments:
                _notify_advisor_attachment_failure(event_conv_id, message_id, e)
        mark_webhook_event_processed("chatwoot", event_id, status="failed", error=str(e))
        # Let RQ apply its failure/retry policy instead of reporting a dropped
        # attachment as a successfully completed job.
        if isinstance(e, ProviderError):
            raise


def _dispatch_before_ack(background_tasks: BackgroundTasks, func, *args, event_id: str = None, **kwargs):
    """Durably enqueue with a bounded Redis call, then let the endpoint ACK.

    The previous after-response enqueue could be lost or become invisible when the
    request lifecycle ended. Supabase claiming remains worker-side, while the
    producer Redis connection has a two-second socket timeout. If Redis is down,
    FastAPI runs the actual work after the ACK as a last-resort fallback.
    """
    func_kwargs = dict(kwargs)
    if event_id:
        func_kwargs["event_id"] = event_id
    if queue_enabled():
        job_id = f"{func.__name__}-{event_id}" if event_id else None
        try:
            enqueue(func, *args, job_id=job_id, **func_kwargs)
            print(f"[WEBHOOK QUEUE] queued event_id={event_id} job={job_id}", flush=True)
            return "queued"
        except Exception as exc:
            print(f"[QUEUE ERROR] Failed to enqueue event_id={event_id}; processing after ACK: {exc}", flush=True)
    background_tasks.add_task(func, *args, **func_kwargs)
    return "background_task"


def process_claimed_whatsapp_event(
    *args,
    event_id: str = None,
    business_id: str = None,
    phone_number_id: str = None,
    customer_message_received_at=None,
):
    """Claim a Meta event off the request path, then perform the expensive work."""
    sender_phone = args[0] if args else None
    if business_id != config.BUSINESS_ID or str(phone_number_id or "") != str(config.WA_PHONE_NUMBER_ID or ""):
        print(
            "[WEBHOOK ISOLATION] Rejected queued WhatsApp event for a different deployment "
            f"event_id={event_id} job_business={business_id!r}",
            flush=True,
        )
        return
    if not valid_whatsapp_sender(sender_phone):
        print(f"[WEBHOOK INVALID] Rejected WhatsApp event without a valid sender event_id={event_id}", flush=True)
        return
    if not claim_webhook_event("whatsapp", event_id, sender_phone):
        print(f"[WEBHOOK DEBUG] WhatsApp duplicado ignorado: {event_id}")
        return
    received_kwargs = {"customer_message_received_at": customer_message_received_at} if customer_message_received_at is not None else {}
    process_whatsapp_message(*args, event_id=event_id, **received_kwargs)


def process_claimed_chatwoot_event(data: dict, event_id: str = None):
    """Claim a Chatwoot event off the request path before processing it."""
    conv_id = data.get("conversation", {}).get("id") or data.get("id")
    if not claim_webhook_event("chatwoot", event_id, str(conv_id) if conv_id else None):
        print(f"[WEBHOOK DEBUG] Chatwoot duplicado ignorado: {event_id}")
        return
    process_chatwoot_event(data, event_id=event_id)


@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    if hub_mode == "subscribe" and hub_verify_token == config.WA_VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge)
    raise HTTPException(status_code=403, detail="Invalid token")


@app.post("/webhook")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    accepted = 0
    rejected = 0
    try:
        if data.get("object") == "whatsapp_business_account":
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    if "messages" not in value:
                        continue
                    if not whatsapp_destination_matches(value, config.WA_PHONE_NUMBER_ID):
                        rejected += len(value.get("messages") or [])
                        print(
                            "[WEBHOOK ISOLATION] Rejected Meta change for a different or missing "
                            "phone_number_id",
                            flush=True,
                        )
                        continue
                    contacts = value.get("contacts") or []
                    for message in value["messages"]:
                        sender_phone, sender_name = resolve_whatsapp_sender(
                            message, contacts, allow_single_contact=len(value["messages"]) == 1,
                        )
                        message_type = message.get("type")
                        event_id = message.get("id")
                        if not valid_whatsapp_sender(sender_phone):
                            rejected += 1
                            print(
                                f"[WEBHOOK INVALID] Rejected message without a valid sender event_id={event_id}",
                                flush=True,
                            )
                            continue
                        received_at = parse_timestamp(message.get("timestamp"))
                        if received_at:
                            record_customer_activity(sender_phone, received_at, event_id or "")
                        if message_type == "text":
                            body = message.get("text", {}).get("body")
                            _dispatch_before_ack(
                                background_tasks,
                                process_claimed_whatsapp_event,
                                sender_phone,
                                sender_name,
                                body,
                                False,
                                None,
                                False,
                                None,
                                event_id=event_id,
                                business_id=config.BUSINESS_ID,
                                phone_number_id=config.WA_PHONE_NUMBER_ID,
                                **({"customer_message_received_at": received_at.isoformat()} if received_at else {}),
                            )
                            accepted += 1
                        elif message_type in WHATSAPP_MEDIA_TYPES:
                            media_payload = message.get(message_type, {})
                            inbound_media_id = media_payload.get("id")
                            caption = media_payload.get("caption", "")
                            body = caption or ("[Audio Nota]" if message_type == "audio" else "")
                            _dispatch_before_ack(
                                background_tasks,
                                process_claimed_whatsapp_event,
                                sender_phone,
                                sender_name,
                                body,
                                message_type == "image",
                                inbound_media_id,
                                message_type == "audio",
                                inbound_media_id if message_type == "audio" else None,
                                message_type,
                                media_payload.get("mime_type"),
                                media_payload.get("filename"),
                                event_id=event_id,
                                business_id=config.BUSINESS_ID,
                                phone_number_id=config.WA_PHONE_NUMBER_ID,
                                **({"customer_message_received_at": received_at.isoformat()} if received_at else {}),
                            )
                            accepted += 1
        return {
            "status": "success",
            "accepted": accepted,
            "rejected": rejected,
            "queue_enabled": queue_enabled(),
        }
    except Exception as e:
        print(f"Error Webhook Meta: {e}")
        return {"status": "error"}


@app.post("/chatwoot-webhook")
@app.post("/chatwoo-webhook", include_in_schema=False)
async def chatwoot_webhook(request: Request, background_tasks: BackgroundTasks):
    raw_body = await request.body()
    timestamp = request.headers.get("X-Chatwoot-Timestamp", "")
    signature = request.headers.get("X-Chatwoot-Signature", "")
    valid_signature = any(
        verify_chatwoot_signature(raw_body, timestamp, signature, secret)
        for secret in (
            config.CHATWOOT_WEBHOOK_SECRET,
            config.CHATWOOT_API_INBOX_WEBHOOK_SECRET,
        )
        if secret
    )
    if not valid_signature:
        raise HTTPException(status_code=401, detail="Invalid Chatwoot webhook signature")
    try:
        data = json.loads(raw_body)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid JSON")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Invalid Chatwoot event")
    account_id, inbox_id = chatwoot_scope(data)
    if account_id != str(config.CHATWOOT_ACCOUNT_ID) or inbox_id != str(config.CHATWOOT_INBOX_ID):
        operational_event("CHATWOOT_WEBHOOK_REJECTED", phone="", stage="CHATWOOT_INGRESS",
                          reason="scope_missing" if not account_id or not inbox_id else "scope_mismatch",
                          result="http_403", expected_account_id=str(config.CHATWOOT_ACCOUNT_ID),
                          expected_inbox_id=str(config.CHATWOOT_INBOX_ID), **chatwoot_event_diagnostics(data))
        raise HTTPException(status_code=403, detail="Chatwoot account or inbox mismatch")
    event_id = chatwoot_event_identity(data)
    conv_id = data.get("conversation", {}).get("id") or data.get("id")
    mode = _dispatch_before_ack(
        background_tasks,
        process_claimed_chatwoot_event,
        data,
        event_id=event_id,
    )
    print(
        f"[CHATWOOT WEBHOOK] accepted event={data.get('event')} "
        f"conversation_id={conv_id} event_id={event_id} mode=after_response"
    )
    return {"status": "success", "mode": "after_response"}


@app.post("/")
async def root_webhook_dispatcher(request: Request, background_tasks: BackgroundTasks):
    """Retain only the legacy Meta root callback; Chatwoot has an authenticated route."""
    data = await request.json()
    if data.get("object") == "whatsapp_business_account":
        return await receive_webhook(request, background_tasks)
    if data.get("event"):
        raise HTTPException(status_code=404, detail="Use /chatwoot-webhook")
    print(f"[WEBHOOK DEBUG] POST / payload no reconocido: keys={list(data.keys())}")
    return {"status": "ignored", "reason": "unrecognized_root_webhook_payload"}
