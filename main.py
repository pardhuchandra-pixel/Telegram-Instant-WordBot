import os
import re
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

DICTIONARY_URL = "https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
TELEGRAM_SEND_URL = "https://api.telegram.org/bot{token}/sendMessage"

REQUEST_TIMEOUT = 8

# ── Helpers ──────────────────────────────────────────────────────────────────
def bold_word(text: str, word: str) -> str:
    return re.sub(rf"\b({re.escape(word)}[a-z]*)\b", r"<b>\1</b>", text, flags=re.IGNORECASE)

def fetch_word_data(word: str) -> dict | None:
    url = DICTIONARY_URL.format(word=word)
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return data[0] if isinstance(data, list) and data else None
    except (requests.RequestException, ValueError):
        return None

def generate_ai_sentences(word: str, meanings: list[dict]) -> str:
    pos_summary = ", ".join(set([m.get("partOfSpeech", "").lower() for m in meanings]))
    prompt = (
        f"Generate exactly 10 diverse, natural, real-world English sentences using the word '{word}'.\n"
        f"The word acts as these parts of speech: {pos_summary}.\n"
        f"Return ONLY a clean text list with each sentence starting with a bullet '•'. No extra notes."
    )
    try:
        client = genai.Client(api_key=GEMINI_KEY)
        response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        lines = [line.strip() for line in response.text.strip().splitlines() if line.strip()]
        
        formatted_bullets = []
        for line in lines:
            clean_sentence = line.lstrip("•*- ").strip().strip('"')
            if clean_sentence:
                formatted_bullets.append(f"  • \"{bold_word(clean_sentence, word)}\"")
        return "\n".join(formatted_bullets[:10])
    except Exception as exc:
        log.error("Gemini failed: %s", exc)
    return "  • <i>(Could not generate AI context sentences.)</i>"

def build_message(data: dict) -> str:
    word = data["word"]
    meanings = data.get("meanings", [])
    
    header = f"📚 <b>Requested Word: {word.capitalize()}</b>\n--------------------------------------"
    
    definitions = []
    for meaning in meanings:
        pos = meaning.get("partOfSpeech", "").upper()
        definitions.append(f"\n🔹 <b>{pos}</b>")  # <--- Fixed variable to 'pos'
        for idx, defn in enumerate(meaning.get("definitions", [])[:2], start=1):
            definitions.append(f"  {idx}. {defn['definition']}")
            
    definitions_block = "\n".join(definitions)
    sentences_block = "\n💡 <b>Real-World Sample Cases (by Gemini AI):</b>\n" + generate_ai_sentences(word, meanings)
    
    return "\n".join(filter(None, [header, definitions_block, sentences_block]))

def send_message(text: str) -> None:
    url = TELEGRAM_SEND_URL.format(token=BOT_TOKEN)
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=REQUEST_TIMEOUT).raise_for_status()
    except requests.RequestException as exc:
        log.error("Telegram fail: %s", exc)

# ── Webhook Endpoint (The Instant Receiver) ──────────────────────────────────
@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        payload = await request.json()
        message = payload.get("message", {})
        text = message.get("text", "").strip()
        from_id = str(message.get("from", {}).get("id", ""))
        
        if from_id == CHAT_ID and text and " " not in text and text.isalpha():
            word = text.lower()
            log.info(f"Instant webhook lookup triggered for: '{word}'")
            
            data = fetch_word_data(word)
            if data:
                output = build_message(data)
                send_message(output)
            else:
                send_message(f"⚠️ Could not find dictionary data for: '{word}'")
                
    except Exception as e:
        log.error(f"Webhook error processing packet: {e}")
        
    return {"status": "ok"}

@app.get("/")
def home():
    return {"message": "Vocabulary Bot Webserver is running active!"}
