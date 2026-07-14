from supabase import create_client, Client
from config import config

# Inicializamos el cliente de Supabase
supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_KEY)

def get_or_create_customer_state(phone_number: str, name: str = "Cliente"):
    """
    Busca al cliente y su estado. Si no existe, lo crea y retorna el estado inicial.
    """
    try:
        # 1. Buscar o crear el cliente
        customer_res = supabase.table("customers").select("*").eq("phone_number", phone_number).execute()
        
        if not customer_res.data:
            # Insertar nuevo cliente
            new_customer = supabase.table("customers").insert({
                "phone_number": phone_number,
                "name": name
            }).execute()
            
            # Crear su estado inicial en conversation_states
            state_data = supabase.table("conversation_states").insert({
                "phone_number": phone_number,
                "current_state": "GREETING",
                "is_paused": False
            }).execute()
            return state_data.data[0]
        else:
            # 2. Si existe, buscar su estado actual
            state_res = supabase.table("conversation_states").select("*").eq("phone_number", phone_number).execute()
            return state_res.data[0]
            
    except Exception as e:
        print(f"Error en DB (get_or_create): {e}")
        return None

def update_bot_state(phone_number: str, new_state: str):
    """Actualiza la fase en la que se encuentra el usuario."""
    try:
        supabase.table("conversation_states").update({
            "current_state": new_state
        }).eq("phone_number", phone_number).execute()
    except Exception as e:
        print(f"Error en DB (update_state): {e}")

def pause_bot_for_handoff(phone_number: str, reason: str):
    """Pausa la automatización y marca el estado como HUMAN_HANDOFF."""
    try:
        supabase.table("conversation_states").update({
            "current_state": "HUMAN_HANDOFF",
            "is_paused": True,
            "handoff_reason": reason
        }).eq("phone_number", phone_number).execute()
    except Exception as e:
        print(f"Error en DB (pause_bot): {e}")
