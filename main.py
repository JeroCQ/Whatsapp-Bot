import os
import json
import re
import requests
import psycopg2
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, Response
from pydantic import BaseModel
import google.generativeai as genai

app = FastAPI()

# Get connection string from Railway variable
database_url = os.getenv('DATABASE_URL')

# Global API configurations
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "https://your-evolution-api-domain.com")
INSTANCE_NAME = os.getenv("INSTANCE_NAME", "company_main_line")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "your_global_api_key_here")

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

# Configure Gemini API
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)
gemini_model = genai.GenerativeModel('gemini-1.5-pro-latest')

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
    return psycopg2.connect(database_url)

class WhatsAppMessage(BaseModel):
    sender_id: str  # Phone number
    message_type: str  # text, image, document, etc.
    text_content: Optional[str] = None

def get_client_state(phone: str):
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
        "options": {
            "delay": 1200, 
            "presence": "composing"
        },
        "textMessage": {
            "text": text
        }
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

def load_business_file(alias: str) -> str:
    """Load a text knowledge file configured as BUSINESS_FILE_<alias>."""
    variable_name = f"BUSINESS_FILE_{alias.upper()}"
    source = os.getenv(variable_name)
    if not source:
        raise ValueError(f"Missing Railway variable {variable_name}")

    if source.startswith("text:"):
        content = source.removeprefix("text:")
        if len(content.encode("utf-8")) > MAX_BUSINESS_FILE_BYTES:
            raise ValueError(f"{variable_name} exceeds MAX_BUSINESS_FILE_BYTES")
        return content

    if source.startswith(("https://", "http://")):
        response = requests.get(source, timeout=10)
        response.raise_for_status()
        if len(response.content) > MAX_BUSINESS_FILE_BYTES:
            raise ValueError(f"{variable_name} exceeds MAX_BUSINESS_FILE_BYTES")
        return response.content.decode("utf-8-sig")

    if not os.path.isfile(source):
        raise ValueError(f"{variable_name} is not a valid file path or HTTP(S) URL")
    if os.path.getsize(source) > MAX_BUSINESS_FILE_BYTES:
        raise ValueError(f"{variable_name} exceeds MAX_BUSINESS_FILE_BYTES")
    with open(source, encoding="utf-8-sig") as business_file:
        return business_file.read()


def generate_system_prompt(inventory_string: str) -> str:
    """Render the deployment prompt and only the files it explicitly references."""
    prompt = SYSTEM_PROMPT.replace("{{inventory}}", inventory_string)

    def replace_file(match):
        alias = match.group(1)
        try:
            return load_business_file(alias)
        except (OSError, UnicodeError, ValueError, requests.RequestException) as error:
            print(f"Could not load business file {alias}: {error}")
            return f"[Business file {alias} is unavailable]"

    return BUSINESS_FILE_PATTERN.sub(replace_file, prompt)

def run_llm_agent(user_text: str, inventory_string: str, phone: str):
    messages = [
        {"role": "user", "parts": [generate_system_prompt(inventory_string)]},
        {"role": "user", "parts": [user_text]}
    ]

    response = gemini_model.generate_content(
        contents=messages,
        tools=handoff_tool,
        tool_config={"function_calling_config": "auto"},
        generation_config={
            "temperature": 0.2,
            "max_output_tokens": 1024
        }
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

@app.post("/webhook")
async def whatsapp_webhook(payload: WhatsAppMessage, background_tasks: BackgroundTasks):
    background_tasks.add_task(process_chat_logic, payload)
    return Response(status_code=200)
