from datetime import datetime, timezone
import uuid

from supabase import create_client, Client
from config import config
from observability import message_metadata, message_event_key

supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


def get_catalog_assets():
    """Return this isolated deployment's active dashboard-managed catalogs."""
    try:
        return (
            supabase.table("catalog_assets")
            .select("catalog_id,public_name,description,media_type,filename")
            .eq("business_id", config.BUSINESS_ID)
            .order("created_at")
            .execute()
            .data
            or []
        )
    except Exception:
        # Rolling upgrades keep the legacy Railway catalog working until the
        # canonical existing-brand SQL has been applied.
        return []


def _first(data):
    return data[0] if data else None


def get_or_create_customer_state(phone_number: str, name: str = "Cliente"):
    """Create customer/state rows safely and return the current state."""
    try:
        supabase.table("customers").upsert(
            {"phone_number": phone_number, "name": name},
            on_conflict="phone_number",
            ignore_duplicates=True,
        ).execute()
        supabase.table("conversation_states").upsert(
            {
                "phone_number": phone_number,
                "current_state": "GREETING",
                "is_paused": False,
            },
            on_conflict="phone_number",
            ignore_duplicates=True,
        ).execute()
        state_res = supabase.table("conversation_states").select("*").eq("phone_number", phone_number).execute()
        return _first(state_res.data)
    except Exception as e:
        print(f"Error en DB (get_or_create): {e}")
        return None


def update_bot_state(phone_number: str, new_state: str):
    supabase.table("conversation_states").update({"current_state": new_state}).eq("phone_number", phone_number).execute()


def pause_bot_for_handoff(phone_number: str, reason: str):
    supabase.table("conversation_states").update({
        "current_state": "HUMAN_HANDOFF",
        "is_paused": True,
        "handoff_reason": reason,
    }).eq("phone_number", phone_number).execute()


def pause_bot_for_human_activity(phone_number: str, conversation_id: int):
    """Preserve the original reason while restoring human control for this case."""
    return supabase.table("conversation_states").update({"current_state": "HUMAN_HANDOFF", "is_paused": True}).eq(
        "phone_number", phone_number).eq("chatwoot_conversation_id", conversation_id).execute()


def update_chatwoot_conversation_id(phone_number: str, conv_id: int):
    """Guarda el ID del ticket de Chatwoot en el usuario."""
    supabase.table("conversation_states").update({"chatwoot_conversation_id": conv_id}).eq("phone_number", phone_number).execute()


def recover_failed_handoff(phone_number: str):
    """Legacy callers must keep human control; recovery lives in handoff_delivery."""
    return supabase.table("conversation_states").update({
        "current_state": "HUMAN_HANDOFF", "is_paused": True,
    }).eq("phone_number", phone_number).eq("is_paused", True).is_("chatwoot_conversation_id", "null").execute()


def get_phone_by_chatwoot_id(conv_id: int):
    """Busca el número de WhatsApp usando el ID del ticket de Chatwoot."""
    res = supabase.table("conversation_states").select("phone_number").eq("chatwoot_conversation_id", conv_id).execute()
    row = _first(res.data)
    return row["phone_number"] if row else None


def claim_webhook_event(source: str, event_id: str, phone_number: str = None) -> bool:
    """Return True only the first time a webhook event is seen."""
    if not event_id:
        return True
    try:
        result = supabase.table("processed_webhook_events").upsert(
            {
                "source": source,
                "event_id": event_id,
                "phone_number": phone_number,
                "status": "received",
            },
            on_conflict="source,event_id",
            ignore_duplicates=True,
        ).execute()
        return bool(result.data)
    except Exception as e:
        print(f"[DB WARN] No se pudo registrar idempotencia {source}:{event_id}: {e}")
        return True


def mark_webhook_event_processed(source: str, event_id: str, status: str = "processed", error: str = None):
    if not event_id:
        return
    try:
        supabase.table("processed_webhook_events").update({
            "status": status,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "error": error,
        }).eq("source", source).eq("event_id", event_id).execute()
    except Exception as e:
        print(f"[DB WARN] No se pudo actualizar idempotencia {source}:{event_id}: {e}")


def save_message_log(phone_number: str, role: str, content: str, *, metadata=None, event_key=None):
    """Guarda un mensaje en el historial."""
    if content is None:
        content = ""
    try:
        details = message_metadata(role)
        details.update(metadata or {})
        if details.get("source") in {"bot_response", "human"}:
            details["inbound_message_ids"] = unanswered_inbound_message_ids(phone_number, details.get("inbound_message_ids", []))
        row = {
            "phone_number": phone_number,
            "role": role,
            "content": content,
            "metadata": details,
            "event_key": event_key or message_event_key(phone_number, role, content, details),
        }
        if row["event_key"]:
            supabase.table("message_logs").upsert(row, on_conflict="event_key", ignore_duplicates=True).execute()
        else:
            supabase.table("message_logs").insert(row).execute()
    except Exception as e:
        print(f"Error guardando log de mensaje: {e}")


def unanswered_inbound_message_ids(phone_number, current_ids=()):
    """Link a direct reply to unanswered incoming IDs; notes/follow-ups don't count."""
    try:
        rows = supabase.table("message_logs").select("role,content,metadata").eq("phone_number", phone_number).order("created_at", desc=True).order("id", desc=True).limit(50).execute().data or []
        answered, pending = set(), []
        for row in rows:
            data = row.get("metadata") or {}
            if data.get("business_id") not in (None, config.BUSINESS_ID):
                continue
            if data.get("source") in {"bot_response", "human"}:
                answered.update(data.get("inbound_message_ids") or [])
                break
            if row.get("role") == "system" and str(row.get("content", "")).startswith("RESOLVED:"):
                break
            elif row.get("role") == "user" and data.get("source_event_id"):
                identifier = data["source_event_id"]
                if identifier not in answered:
                    pending.append(identifier)
        return list(dict.fromkeys([*reversed(pending), *current_ids]))
    except Exception:
        return list(current_ids)


def update_inbound_transcript(phone_number, event_id, transcript):
    if not event_id:
        return
    supabase.table("message_logs").update({"content": transcript}).eq("phone_number", phone_number).eq(
        "role", "user").eq("metadata->>source_event_id", event_id).execute()


def update_follow_up_preferences(phone_number, values):
    allowed = {key: value for key, value in values.items() if key in {
        "follow_up_not_before", "follow_up_suppressed", "follow_up_suppression_reason"}}
    if allowed:
        supabase.table("conversation_states").update(allowed).eq("phone_number", phone_number).execute()


def update_conversation_memory(phone_number: str, customer_data: dict, order_summary: str):
    """Persist checkout facts so they survive context windows and handoffs."""
    try:
        supabase.table("conversation_states").update({
            "customer_data": customer_data or {},
            "order_summary": order_summary or None,
        }).eq("phone_number", phone_number).execute()
    except Exception as e:
        print(f"[MEMORY WARN] No se pudo guardar memoria estructurada: {e}")


def has_successful_file_delivery(phone_number: str, file_id: str) -> bool:
    """Check the durable success marker instead of a limited prompt window."""
    try:
        result = supabase.table("message_logs").select("id").eq(
            "phone_number", phone_number
        ).eq("role", "system").ilike("content", f"Archivos enviados:%{file_id}%").limit(1).execute()
        return bool(result.data)
    except Exception as e:
        print(f"[FILE DELIVERY WARN] No se pudo consultar entrega previa: {e}")
        return False


def register_follow_up(phone_number: str) -> str:
    """Replace any pending follow-up and return its one-time token."""
    token = uuid.uuid4().hex
    supabase.table("conversation_states").update({"follow_up_token": token}).eq("phone_number", phone_number).execute()
    return token


def invalidate_follow_up(phone_number: str):
    """Cancel a pending follow-up when the customer writes again."""
    try:
        supabase.table("conversation_states").update({"follow_up_token": None}).eq("phone_number", phone_number).execute()
    except Exception as e:
        print(f"[FOLLOW UP WARN] No se pudo cancelar el follow up de {phone_number}: {e}")


def claim_follow_up(phone_number: str, token: str) -> bool:
    """Atomically consume a still-current follow-up token."""
    try:
        result = supabase.table("conversation_states").update({"follow_up_token": None}).eq(
            "phone_number", phone_number
        ).eq("follow_up_token", token).eq("is_paused", False).execute()
        return bool(result.data)
    except Exception as e:
        print(f"[FOLLOW UP WARN] No se pudo validar el follow up de {phone_number}: {e}")
        return False


def get_message_logs(phone_number: str, limit: int = 6):
    """Recupera los últimos N mensajes para darle contexto a Gemini."""
    try:
        bounded_limit = max(1, min(int(limit), 50))
        res = supabase.table("message_logs") \
            .select("role,content,created_at,id,metadata") \
            .eq("phone_number", phone_number) \
            .order("created_at", desc=True) \
            .order("id", desc=True) \
            .limit(bounded_limit) \
            .execute()
        return list(reversed(res.data)) if res.data else []
    except Exception as e:
        print(f"Error obteniendo logs de mensajes: {e}")
        return []


def resume_bot_state(conv_id: int):
    """Cuando el asesor cierra el ticket, reiniciamos el bot y conservamos su memoria."""
    try:
        phone = get_phone_by_chatwoot_id(conv_id)
        if not phone:
            return None
        print(f"[DEBUG DB] Ticket resuelto para {phone}. Conservando historial de mensajes.")
        result = supabase.table("conversation_states").update({
            "current_state": "GREETING",
            "is_paused": False,
            "chatwoot_conversation_id": None,
            "handoff_reason": None,
            "follow_up_suppressed": True,
            "follow_up_suppression_reason": "conversation_closed",
        }).eq("phone_number", phone).eq("chatwoot_conversation_id", conv_id).execute()
        return phone if result.data else None
    except Exception as e:
        print(f"Error al actualizar estado en resume_bot_state: {e}")
        return None


def reset_client_history(phone: str):
    """Borra el historial de mensajes y resetea el estado del cliente para pruebas."""
    try:
        supabase.table("message_logs").delete().eq("phone_number", phone).execute()
        supabase.table("conversation_states").update({
            "current_state": "GREETING",
            "is_paused": False,
            "handoff_reason": None,
            "chatwoot_conversation_id": None,
            "follow_up_not_before": None,
        }).eq("phone_number", phone).execute()
    except Exception as e:
        print(f"[DB ERROR] Falló el reset de historial para {phone}: {e}")
