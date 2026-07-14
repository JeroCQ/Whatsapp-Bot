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

def resume_bot_state(conv_id: int):
    """Cuando el asesor cierra el ticket, reiniciamos el bot."""
    supabase.table("conversation_states").update({
        "current_state": "GREETING",
        "is_paused": False,
        "chatwoot_conversation_id": None
    }).eq("chatwoot_conversation_id", conv_id).execute()
