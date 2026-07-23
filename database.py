from supabase import create_client, Client
from config import config

supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

def get_or_create_customer_state(phone_number: str, name: str = "Cliente"):
    try:
        customer_res = supabase.table("customers").select("*").eq("phone_number", phone_number).execute()
        if not customer_res.data:
            supabase.table("customers").insert({"phone_number": phone_number, "name": name}).execute()
            state_data = supabase.table("conversation_states").insert({
                "phone_number": phone_number,
                "current_state": "GREETING",
                "is_paused": False
            }).execute()
            return state_data.data[0]
        else:
            state_res = supabase.table("conversation_states").select("*").eq("phone_number", phone_number).execute()
            return state_res.data[0]
    except Exception as e:
        print(f"Error en DB (get_or_create): {e}")
        return None

def update_bot_state(phone_number: str, new_state: str):
    supabase.table("conversation_states").update({"current_state": new_state}).eq("phone_number", phone_number).execute()

def pause_bot_for_handoff(phone_number: str, reason: str):
    supabase.table("conversation_states").update({
        "current_state": "HUMAN_HANDOFF",
        "is_paused": True,
        "handoff_reason": reason
    }).eq("phone_number", phone_number).execute()

# --- NUEVAS FUNCIONES PARA CHATWOOT ---

def update_chatwoot_conversation_id(phone_number: str, conv_id: int):
    """Guarda el ID del ticket de Chatwoot en el usuario."""
    supabase.table("conversation_states").update({"chatwoot_conversation_id": conv_id}).eq("phone_number", phone_number).execute()

def get_phone_by_chatwoot_id(conv_id: int):
    """Busca el número de WhatsApp usando el ID del ticket de Chatwoot."""
    res = supabase.table("conversation_states").select("phone_number").eq("chatwoot_conversation_id", conv_id).execute()
    return res.data[0]["phone_number"] if res.data else None

# --- NUEVAS FUNCIONES PARA EL LOG DE MENSAJES (MEMORIA DE GEMINI) ---

def save_message_log(phone_number: str, role: str, content: str):
    """Guarda un mensaje en el historial (role puede ser 'user' o 'model')."""
    try:
        supabase.table("message_logs").insert({
            "phone_number": phone_number,
            "role": role,
            "content": content
        }).execute()
    except Exception as e:
        print(f"Error guardando log de mensaje: {e}")

def get_message_logs(phone_number: str, limit: int = 6):
    """Recupera los últimos N mensajes para darle contexto a Gemini."""
    try:
        res = supabase.table("message_logs") \
            .select("role", "content") \
            .eq("phone_number", phone_number) \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
        # Volteamos el resultado para que quede en orden cronológico (viejo a nuevo)
        return list(reversed(res.data)) if res.data else []
    except Exception as e:
        print(f"Error obteniendo logs de mensajes: {e}")
        return []

# --- MODIFICACIÓN DE RESUME_BOT_STATE ---

def resume_bot_state(conv_id: int):
    """Cuando el asesor cierra el ticket, reiniciamos el bot y conservamos su memoria."""
    try:
        # Buscamos el teléfono asociado a esa conversación
        phone_res = supabase.table("conversation_states").select("phone_number").eq("chatwoot_conversation_id", conv_id).execute()
        
        if phone_res.data:
            phone = phone_res.data[0]["phone_number"]
            print(f"[DEBUG DB] Ticket resuelto para {phone}. Conservando historial de mensajes.")
            
        # (Mantén aquí el resto de tu código que hace el UPDATE/PATCH para cambiar is_paused a False)
        supabase.table("conversation_states").update({
            "is_paused": False,
            "chatwoot_conversation_id": None,
            "handoff_reason": None
        }).eq("chatwoot_conversation_id", conv_id).execute()

    except Exception as e:
        print(f"Error al actualizar estado en resume_bot_state: {e}")

    # Reiniciamos el estado del bot
    supabase.table("conversation_states").update({
        "current_state": "GREETING",
        "is_paused": False,
        "chatwoot_conversation_id": None
    }).eq("chatwoot_conversation_id", conv_id).execute()

def reset_client_history(phone: str):
    """Borra el historial de mensajes y resetea el estado del cliente para pruebas."""
    from config import get_db_connection # Asegúrate de importar tu conexión a la DB
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 1. Borrar todos los logs de este número
        cur.execute("DELETE FROM message_logs WHERE phone_number = %s", (phone,))
        
        # 2. Resetear el estado a GREETING y quitar la pausa
        cur.execute("""
            UPDATE conversation_states 
            SET is_paused = false, handoff_reason = NULL, current_state = 'GREETING' 
            WHERE phone_number = %s
        """, (phone,))
        
        conn.commit()
    except Exception as e:
        print(f"[DB ERROR] Falló el reset de historial para {phone}: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()
