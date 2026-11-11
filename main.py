import os
import re
import json
import logging
from fastapi import FastAPI, Request
import requests
from google import genai

# ── Logging & FastAPI Initialization ────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = FastAPI()

# ── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

TELEGRAM_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"
REQUEST_TIMEOUT = 10

# ── Helpers ──────────────────────────────────────────────────────────────────
def bold_word(text: str, word: str) -> str:
    return re.sub(rf"\b({re.escape(word)}[a-z]*)\b", r"<b>\1</b>", text, flags=re.IGNORECASE)

def fetch_word_and_sentences(word: str) -> dict | None:
    """
    Uses Gemini to look up the phonetic pronunciation, definitions,
    and generate 10 natural sentences in a single prompt.
    """
    prompt = f"""
You are a lexicographer and language tutor.
Analyze the requested English word: "{word}".

Provide:
1. IPA phonetic pronunciation (e.g., "/wɜːd/").
2. 1-2 concise definitions for each valid part of speech (VERB, NOUN, ADJECTIVE, ADVERB, etc.).
3. Exactly 10 diverse, natural, real-world example sentences demonstrating the word in context.

Respond ONLY with valid JSON in this exact structure:
{{
  "word": "{word}",
  "phonetic": "/.../",
  "meanings": [
    {{
      "partOfSpeech": "NOUN",
      "definitions": ["Definition 1", "Definition 2"]
    }}
  ],
  "sentences": [
    "Sentence 1",
    "Sentence 2"
  ]
}}
If the text is gibberish or not a real English word, respond with:
{{"error": "not_a_word"}}
"""
    try:
        client = genai.Client(api_key=GEMINI_KEY)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        
        raw_text = response.text.strip()
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?\n", "", raw_text)
            raw_text = re.sub(r"\n```$", "", raw_text)

        data = json.loads(raw_text.strip())
        if data.get("error") == "not_a_word":
            return None
        return data

    except Exception as exc:
        log.error("Gemini lookup failed for '%s': %s", word, exc)
        return None

def build_message(word: str, data: dict) -> str:
    phonetic = data.get("phonetic", "").strip()
    header = f"📚 <b>Requested Word: {word.capitalize()}</b>"
    if phonetic:
        header += f"  <i>{phonetic}</i>"
    header += "\n--------------------------------------"

    defs_list = []
    for meaning in data.get("meanings", []):
        pos = meaning.get("partOfSpeech", "").upper()
        defs_list.append(f"\n🔹 <b>{pos}</b>")
        for idx, d in enumerate(meaning.get("definitions", [])[:2], start=1):
            defs_list.append(f"  {idx}. {d}")
    definitions_block = "\n".join(defs_list)

    sentence_lines = []
    for s in data.get("sentences", [])[:10]:
        clean = s.lstrip("•*- 1234567890.").strip().strip('"')
        if clean:
            sentence_lines.append(f"  • \"{bold_word(clean, word)}\"")
    sentences_block = "\n💡 <b>Real-World Sample Cases (by Gemini AI):</b>\n" + "\n".join(sentence_lines)

    return f"{header}\n{definitions_block}\n{sentences_block}"

def reply_telegram(chat_id: str, text: str) -> None:
    url = TELEGRAM_SEND_URL.format(token=BOT_TOKEN)
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    try:
        resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log.error("Telegram send fail: %s", exc)

# ── Webhook Endpoint (The Multi-User Instant Receiver) ───────────────────────
@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        payload = await request.json()
        message = payload.get("message", {})
        text = message.get("text", "").strip()
        sender_id = str(message.get("from", {}).get("id", ""))
        
        allowed_users = [uid.strip() for uid in CHAT_ID.split(",") if uid.strip()]
        
        # Check authorization and ensure input is a single alphabetical word
        if sender_id in allowed_users and text and " " not in text and text.isalpha():
            word = text.lower()
            log.info("Instant lookup triggered by user %s for: '%s'", sender_id, word)
            
            data = fetch_word_and_sentences(word)
            if data:
                output = build_message(word, data)
                reply_telegram(sender_id, output)
            else:
                reply_telegram(sender_id, f"⚠️ Could not find dictionary data for: '<b>{word}</b>'")
                
    except Exception as e:
        log.error("Webhook error processing packet: %s", e)
        
    return {"status": "ok"}

@app.get("/")
def home():
    return {"message": "Vocabulary Bot Webserver is running active!"}
