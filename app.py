#!/usr/bin/env python3
import os
import json
import hmac
import hashlib
from datetime import datetime
from flask import Flask, request, jsonify
import requests
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Configuration
META_ACCESS_TOKEN = os.getenv('META_ACCESS_TOKEN')
META_APP_SECRET = os.getenv('META_APP_SECRET')
META_IG_USER_ID = os.getenv('META_IG_USER_ID')
FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv('FACEBOOK_PAGE_ACCESS_TOKEN')
FACEBOOK_PAGE_ID = os.getenv('FACEBOOK_PAGE_ID')
FACEBOOK_APP_SECRET = os.getenv('FACEBOOK_APP_SECRET')
LLM_API_KEY = os.getenv('LLM_API_KEY')
LLM_BASE_URL = os.getenv('LLM_BASE_URL', 'https://generativelanguage.googleapis.com/openai/')
LLM_MODEL = os.getenv('LLM_MODEL', 'gemini-3.5-flash')
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
WEBHOOK_VERIFY_TOKEN = os.getenv('WEBHOOK_VERIFY_TOKEN')
DRY_RUN = os.getenv('DRY_RUN', 'false').lower() == 'true'

STORE_PATH = 'data/store.json'
KNOWLEDGE_PATH = 'data/knowledge.json'

def ensure_dir():
    os.makedirs('data', exist_ok=True)

def load_store():
    ensure_dir()
    if not os.path.exists(STORE_PATH):
        return []
    try:
        with open(STORE_PATH) as f:
            return json.load(f)
    except:
        return []

def save_store(data):
    ensure_dir()
    with open(STORE_PATH, 'w') as f:
        json.dump(data, f, indent=2)

def load_knowledge():
    try:
        with open(KNOWLEDGE_PATH) as f:
            return json.load(f)
    except:
        return {"products": []}

def validate_signature(body, signature):
    if not signature:
        return False
    parts = signature.split('=')
    if len(parts) != 2:
        return False
    secret = META_APP_SECRET or FACEBOOK_APP_SECRET
    computed = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return computed == parts[1]

def draft_reply(message):
    try:
        knowledge = load_knowledge()
        system_prompt = f"Ты ассистент Ларисы (@mamavdele.ai) для AI-видео и AI-агентов. Отвечай кратко на языке пользователя. Продукты: {json.dumps(knowledge.get('products', []))}. На оскорбления - твёрдый ответ без извинений. На юридику/скидки - скажи что нужен handoff."

        response = requests.post(
            f"{LLM_BASE_URL}chat/completions",
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message}
                ],
                "temperature": 0.7,
                "max_tokens": 300
            },
            headers={"Authorization": f"Bearer {LLM_API_KEY}"}
        )
        text = response.json()['choices'][0]['message']['content']
        return {"handoff": False, "risk": "normal", "reply": text[:300]}
    except Exception as e:
        print(f"LLM error: {e}")
        return {"handoff": True, "risk": "normal", "reply": ""}

def send_telegram_card(item):
    try:
        message = f"📝 *Новый комментарий*\n\n{item['sourceType']}\n\n_{item['text'][:200]}_\n\n💬 *Предложенный ответ:*\n_{item['draftReply']['reply'][:200]}_"

        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown",
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "✅ Одобрить", "callback_data": f"approve_{item['id']}"},
                            {"text": "❌ Отклонить", "callback_data": f"reject_{item['id']}"}
                        ],
                        [
                            {"text": "✏️ Редактировать", "callback_data": f"edit_{item['id']}"}
                        ]
                    ]
                }
            }
        )
        print(f"✅ Approval card sent for {item['id']}")
    except Exception as e:
        print(f"Telegram error: {e}")

@app.route('/webhook', methods=['GET'])
def webhook_verify():
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    if mode == 'subscribe' and token == WEBHOOK_VERIFY_TOKEN:
        print('✅ Webhook verified')
        return challenge
    return 'Forbidden', 403

@app.route('/webhook', methods=['POST'])
def webhook_post():
    body = request.get_data(as_text=True)
    signature = request.headers.get('x-hub-signature-256')

    if not validate_signature(body, signature):
        return 'Invalid signature', 403

    try:
        webhook = request.get_json()

        # Parse messages
        messages = []
        if 'entry' in webhook:
            for entry in webhook['entry']:
                if 'messaging' in entry:
                    for msg in entry['messaging']:
                        if msg.get('message') and not msg['message'].get('is_echo'):
                            messages.append({
                                'sourceType': 'Instagram DM',
                                'sourceId': msg['sender']['id'],
                                'text': msg['message'].get('text', '')
                            })

        # Process messages
        for msg in messages:
            draft = draft_reply(msg['text'])
            item = {
                'id': f"{int(datetime.now().timestamp()*1000)}_{os.urandom(4).hex()}",
                'status': 'pending',
                'sourceType': msg['sourceType'],
                'sourceId': msg['sourceId'],
                'text': msg['text'],
                'draftReply': draft,
                'createdAt': datetime.now().isoformat()
            }

            store = load_store()
            store.append(item)
            save_store(store)

            send_telegram_card(item)

        return 'OK', 200
    except Exception as e:
        print(f"Webhook error: {e}")
        return 'Error', 500

if __name__ == '__main__':
    print(f"🚀 Agent running on port 3000")
    print(f"📡 Webhook URL: https://your-domain.com/webhook")
    app.run(host='0.0.0.0', port=3000, debug=False)
