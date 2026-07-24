from datetime import datetime, timezone

from supabase import create_client, Client
from config import config

supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)


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


def update_chatwoot_conversation_id(phone_number: str, conv_id: int):
    """Guarda el ID del ticket de Chatwoot en el usuario."""
    supabase.table("conversation_states").update({"chatwoot_conversation_id": conv_id}).eq("phone_number", phone_number).execute()


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


def save_message_log(phone_number: str, role: str, content: str):
    """Guarda un mensaje en el historial."""
    if content is None:
        content = ""
    try:
        supabase.table("message_logs").insert({
            "phone_number": phone_number,
            "role": role,
            "content": content,
        }).execute()
    except Exception as e:
        print(f"Error guardando log de mensaje: {e}")


def get_message_logs(phone_number: str, limit: int = 6):
    """Recupera los últimos N mensajes para darle contexto a Gemini."""
    try:
        bounded_limit = max(1, min(int(limit), 50))
        res = supabase.table("message_logs") \
            .select("role", "content") \
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
        supabase.table("conversation_states").update({
            "current_state": "GREETING",
            "is_paused": False,
            "chatwoot_conversation_id": None,
            "handoff_reason": None,
        }).eq("phone_number", phone).execute()
        return phone
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
        }).eq("phone_number", phone).execute()
    except Exception as e:
        print(f"[DB ERROR] Falló el reset de historial para {phone}: {e}")
