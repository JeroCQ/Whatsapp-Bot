import os
import json
import re
import requests
import psycopg2
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, Request, Response
from pydantic import BaseModel
from google import genai
from google.genai import types

app = FastAPI()

# Get connection string from Railway variable
database_url = os.getenv('DATABASE_URL')

# Global API configurations
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "https://your-evolution-api-domain.com")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "company_main_line")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "your_global_api_key_here")
# Keep the model alias that existing deployments already use. The alias lets
# Google route requests to the currently supported Flash model.
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)

# Business-specific instructions live in Railway instead of in the source code.
# A prompt can include {{file:ALIAS}} to inject BUSINESS_FILE_ALIAS.
DEFAULT_SYSTEM_PROMPT = """You are a helpful and concise sales assistant for our retail company.
Your ONLY goal is to assist customers with retail purchases based on the inventory below.

CURRENT INVENTORY:
{{inventory}}

RULES:
1. NEVER make up information, prices, or products. If it is not in the inventory, you do not know it.
2. NEVER attempt to negotiate or offer wholesale prices.
3. Keep responses under 3 sentences. Use a friendly, professional tone.
"""
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)
BUSINESS_FILE_PATTERN = re.compile(r"\{\{file:([A-Za-z][A-Za-z0-9_]*)\}\}")
MAX_BUSINESS_FILE_BYTES = int(os.getenv("MAX_BUSINESS_FILE_BYTES", "1000000"))

# Only variables with this prefix can be inserted into the system prompt.
BUSINESS_FILE_PATTERN = re.compile(r"\{\{(BUSINESS_FILE_[A-Z0-9_]+)\}\}")
MAX_BUSINESS_FILE_BYTES = int(os.getenv("MAX_BUSINESS_FILE_BYTES", "1000000"))

# Configure Gemini API
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
gemini_client = genai.Client(api_key=GOOGLE_API_KEY) if GOOGLE_API_KEY else None

# The SQL schema
sql_schema = """
-- 1. Clients & Session State
CREATE TABLE IF NOT EXISTS clients (
    phone_number VARCHAR(20) PRIMARY KEY,
    name VARCHAR(100),
    is_vip BOOLEAN DEFAULT FALSE,
    bot_paused BOOLEAN DEFAULT FALSE,
    paused_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- 2. Live Inventory (For Dynamic Prompt Injection)
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    sku VARCHAR(50) UNIQUE,
    name VARCHAR(150) NOT NULL,
    price_retail DECIMAL(10, 2) NOT NULL,
    description TEXT,
    is_available BOOLEAN DEFAULT TRUE
);

-- 3. Global Metadata & Business Configurations
CREATE TABLE IF NOT EXISTS business_config (
    key VARCHAR(50) PRIMARY KEY,
    value TEXT NOT NULL
);

-- 4. Internal Manager Notifications
CREATE TABLE IF NOT EXISTS handoff_alerts (
    id SERIAL PRIMARY KEY,
    phone_number VARCHAR(20) REFERENCES clients(phone_number),
    reason TEXT,
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
"""

@app.on_event("startup")
async def startup():
    if database_url:
        try:
            conn = psycopg2.connect(database_url)
            cursor = conn.cursor()
            cursor.execute(sql_schema)
            conn.commit()
            cursor.close()
            conn.close()
            print("Tablas creadas o verificadas exitosamente.")
        except Exception as e:
            print(f"Error during startup DB initialization: {e}")
    else:
        print("WARNING: DATABASE_URL is not set in environment variables.")

def get_db_connection():
    """Establishes a new database connection."""
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg2.connect(database_url)

class WhatsAppMessage(BaseModel):
    sender_id: str  # Phone number
    message_type: str  # text, image, document, etc.
    text_content: Optional[str] = None

def get_client_state(phone: str):
    if not database_url:
        return {"is_vip": False, "bot_paused": False}
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT is_vip, bot_paused FROM clients WHERE phone_number = %s;", (phone,)
            )
            result = cursor.fetchone()
            if result:
                return {"is_vip": result[0], "bot_paused": result[1]}
            else:
                cursor.execute(
                    "INSERT INTO clients (phone_number) VALUES (%s) RETURNING is_vip, bot_paused;", (phone,)
                )
                conn.commit()
                result = cursor.fetchone()
                return {"is_vip": result[0], "bot_paused": result[1]}

def get_active_inventory_string():
    if not database_url:
        return "No database inventory configured."
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT name, price_retail, description FROM products WHERE is_available = TRUE;"
            )
            products = cursor.fetchall()
            inventory_list = []
            for product in products:
                inventory_list.append(f"{product[0]} : ${product[1]:.2f} - {product[2]}")
            if not inventory_list:
                return "No active inventory available."
            return "\n".join(inventory_list)

def pause_bot_and_notify_manager(phone: str, reason: str):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # 1. Update the client state to pause the bot
                cursor.execute(
                    "UPDATE clients SET bot_paused = TRUE, paused_at = NOW() WHERE phone_number = %s;",
                    (phone,)
                )
                # 2. Register the structured alert in the database
                cursor.execute(
                    "INSERT INTO handoff_alerts (phone_number, reason) VALUES (%s, %s);",
                    (phone, reason)
                )
                conn.commit()
        print(f"Internal alert registered for {phone}. Reason: {reason}")
    except Exception as e:
        print(f"Database error during handoff update: {e}")

def send_whatsapp_message(phone_number: str, text: str):
    url = f"{EVOLUTION_API_URL}/message/sendText/{INSTANCE_NAME}"

    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "number": phone_number,
        "text": text,
        "delay": 1200,
    }

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error sending message: {e}")
        return None

# The tool definition for handoffs in Gemini's format
handoff_tool = {
    "function_declarations": [
        {
            "name": "transfer_to_manager",
            "description": "Call this function immediately if the user asks for wholesale pricing, bulk discounts, B2B sales, OR if they ask a question that is not covered by the inventory or business context provided.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "A short explanation of why the handoff is occurring (e.g., 'Requested wholesale prices' or 'Asked about store hours')."
                    }
                },
                "required": ["reason"]
            }
        }
    ]
}

DEFAULT_SYSTEM_PROMPT = """You are a helpful and concise sales assistant for our retail company.
    Your ONLY goal is to assist customers with retail purchases based on the inventory below.

    CURRENT INVENTORY:
    {{inventory}}

    return BUSINESS_FILE_PATTERN.sub(replace_file, prompt)


def load_business_file(variable_name: str) -> str:
    """Read a Railway business-file variable as inline text or a text-file URL."""
    value = os.getenv(variable_name)
    if not value:
        return f"[{variable_name} is not configured]"

    if value.startswith(("https://", "http://")):
        response = requests.get(value, timeout=10)
        response.raise_for_status()
        if len(response.content) > MAX_BUSINESS_FILE_BYTES:
            raise ValueError(f"{variable_name} is larger than MAX_BUSINESS_FILE_BYTES")
        return response.content.decode("utf-8-sig")

    if len(value.encode("utf-8")) > MAX_BUSINESS_FILE_BYTES:
        raise ValueError(f"{variable_name} is larger than MAX_BUSINESS_FILE_BYTES")
    return value


def generate_system_prompt(inventory_string: str) -> str:
    prompt = os.getenv("SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)
    prompt = prompt.replace("{{inventory}}", inventory_string)
    return BUSINESS_FILE_PATTERN.sub(
        lambda match: load_business_file(match.group(1)), prompt
    )

def run_llm_agent(user_text: str, inventory_string: str, phone: str):
    if not gemini_client:
        raise RuntimeError("GOOGLE_API_KEY is not configured")

    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_text,
        config=types.GenerateContentConfig(
            system_instruction=generate_system_prompt(inventory_string),
            tools=[handoff_tool],
            temperature=0.2,
            max_output_tokens=1024,
        ),
    )

    if response.candidates and response.candidates[0].content.parts:
        for part in response.candidates[0].content.parts:
            if hasattr(part, 'function_call') and part.function_call:
                function_call = part.function_call
                if function_call.name == "transfer_to_manager":
                    reason = function_call.args.get("reason", "Unknown reason")
                    pause_bot_and_notify_manager(phone, reason)
                    send_whatsapp_message(phone, "Dame un momento, te voy a transferir con un asesor para que te ayude con esto.")
                    return

    if response.text:
        bot_reply = response.text
        send_whatsapp_message(phone, bot_reply)
        return bot_reply

async def process_chat_logic(msg: WhatsAppMessage):
    phone = msg.sender_id

    # Manager command to hand control back to the AI (Manager types this in Chatwoot)
    if msg.text_content and msg.text_content.strip().lower() == "#bot":
        if database_url:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "UPDATE clients SET bot_paused = FALSE WHERE phone_number = %s;",
                        (phone,)
                    )
                    conn.commit()
        send_whatsapp_message(phone, "🤖 Chatbot reactivado para esta conversación.")
        return

    client_state = get_client_state(phone)

    # RULE 1 & 2: Handoff Checks (If true, AI ignores the message)
    if client_state["is_vip"] or client_state["bot_paused"]:
        return

    # RULE 3: Payment Verification Intent
    if msg.message_type in ["image", "document"]:
        send_whatsapp_message(phone, "Recibido. Un asesor verificará tu comprobante de pago en un momento.")
        pause_bot_and_notify_manager(phone, "Payment receipt uploaded.")
        return

    # RULE 4 & 5: Pass to LLM
    inventory = get_active_inventory_string()
    if msg.text_content:
        run_llm_agent(msg.text_content, inventory, phone)

@app.get("/")
def read_root():
    return {"status": "ok", "message": "API is online"}


def _phone_from_jid(jid: Optional[str]) -> Optional[str]:
    if not jid:
        return None
    return jid.split("@", 1)[0].split(":", 1)[0].lstrip("+")


def _unwrap_webhook_payload(payload: dict) -> dict:
    """Unwrap proxy/provider envelopes without mistaking message data for one."""
    current = payload
    for _ in range(3):
        wrapped = next(
            (
                current.get(key)
                for key in ("body", "payload")
                if isinstance(current.get(key), dict)
            ),
            None,
        )
        if wrapped is None:
            break
        current = wrapped
    return current


def _extract_message_content(message) -> tuple[str, Optional[str]]:
    """Extract text/media type from common Baileys/Evolution message objects."""
    if isinstance(message, str):
        return "text", message
    if not isinstance(message, dict):
        return "text", None

    text = message.get("conversation") or message.get("text") or message.get("body")
    if isinstance(text, dict):
        text = text.get("body") or text.get("text")
    if not text and isinstance(message.get("extendedTextMessage"), dict):
        text = message["extendedTextMessage"].get("text")
    if isinstance(message.get("imageMessage"), dict):
        return "image", message["imageMessage"].get("caption")
    if isinstance(message.get("documentMessage"), dict):
        return "document", message["documentMessage"].get("caption")
    if isinstance(message.get("audioMessage"), dict):
        return "audio", None
    return "text", text


def normalize_webhook_payload(payload: dict) -> Optional[WhatsAppMessage]:
    """Convert Evolution API or Chatwoot webhook JSON into one internal message."""
    payload = _unwrap_webhook_payload(payload)
    event = str(payload.get("event", "")).lower().replace("_", ".")

    # Keep compatibility with the original, simple webhook body.
    if payload.get("sender_id") and payload.get("message_type"):
        return WhatsAppMessage(**payload)

    data = payload.get("data") or {}
    if isinstance(data, list):
        data = data[0] if data else {}
    looks_like_evolution = isinstance(data, dict) and any(
        key in data for key in ("key", "message", "remoteJid", "sender")
    )
    if event in {"messages.upsert", "messages.update"} or looks_like_evolution:
        key = data.get("key") or {}
        if key.get("fromMe") or data.get("fromMe"):
            return None

        phone = _phone_from_jid(
            key.get("remoteJidAlt")
            or key.get("remoteJid")
            or data.get("remoteJid")
            or data.get("sender")
            or data.get("from")
        )
        message_type, text = _extract_message_content(
            data.get("message") or data.get("text") or data.get("body")
        )
        if phone and (text or message_type != "text"):
            return WhatsAppMessage(
                sender_id=phone, message_type=message_type, text_content=text
            )
        return None

    # Meta Cloud API shape (also used by some Evolution proxies).
    entries = payload.get("entry") or []
    if entries:
        try:
            value = entries[0]["changes"][0]["value"]
            raw_message = value.get("messages", [])[0]
        except (IndexError, KeyError, TypeError):
            raw_message = None
        if raw_message:
            message_type = raw_message.get("type", "text")
            content = raw_message.get(message_type) or {}
            text = content.get("body") or content.get("caption")
            return WhatsAppMessage(
                sender_id=str(raw_message["from"]).lstrip("+"),
                message_type="image" if message_type == "image" else (
                    "document" if message_type == "document" else "text"
                ),
                text_content=text,
            )

    # Older/forwarded Evolution payloads can put sender and message at the root.
    phone = _phone_from_jid(
        payload.get("sender") or payload.get("from") or payload.get("remoteJid")
    )
    if phone and not payload.get("fromMe"):
        message_type, text = _extract_message_content(
            payload.get("message") or payload.get("text") or payload.get("body")
        )
        if text or message_type != "text":
            return WhatsAppMessage(
                sender_id=phone, message_type=message_type, text_content=text
            )

    if event == "message.created" or payload.get("message_type") is not None:
        if str(payload.get("message_type", "")).lower() not in {"incoming", "0"}:
            return None
        conversation = payload.get("conversation") or {}
        sender = payload.get("sender") or (conversation.get("meta") or {}).get("sender") or {}
        phone = sender.get("phone_number") or sender.get("identifier")
        attachments = payload.get("attachments") or []
        message_type = "text"
        if attachments:
            file_type = str(attachments[0].get("file_type", "")).lower()
            message_type = "image" if file_type == "image" else "document"
        if phone:
            return WhatsAppMessage(
                sender_id=str(phone).lstrip("+"),
                message_type=message_type,
                text_content=payload.get("content"),
            )
        return None

    return None


async def log_and_process_message(msg: WhatsAppMessage):
    try:
        await process_chat_logic(msg)
    except Exception as error:
        print(f"Error processing message from {msg.sender_id}: {error}")


@app.post("/webhook")
@app.post("/chatwoot-webhook")
@app.post("/")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()
    msg = normalize_webhook_payload(payload)
    if msg:
        print(
            f"Accepted incoming webhook for {msg.sender_id}; "
            f"message_type={msg.message_type}"
        )
        background_tasks.add_task(log_and_process_message, msg)
    else:
        print(
            "Ignored unsupported or outgoing webhook. "
            f"event={payload.get('event', 'unknown')!r} keys={sorted(payload.keys())}"
        )
    return Response(status_code=200)
