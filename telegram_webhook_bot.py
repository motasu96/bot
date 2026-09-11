"""
TradingView -> Gemini -> Telegram Bot
=======================================
يستقبل تنبيهات (webhook) من استراتيجية "Flipping Markets" في TradingView،
يطلب من Gemini تعليقًا نصيًا قصيرًا يشرح سبب الإشارة، ثم يرسل كل شي كرسالة
واحدة منسقة إلى تيليجرام.

متغيرات البيئة المطلوبة:
    TELEGRAM_BOT_TOKEN  - التوكن من BotFather
    TELEGRAM_CHAT_ID    - الـ chat_id اللي بتوصل له الرسائل
    WEBHOOK_SECRET      - (اختياري) كلمة سر تُضاف كـ query param لحماية الرابط
    GEMINI_API_KEY      - مفتاح Gemini API (من aistudio.google.com/apikey)
"""

import os
import logging

from flask import Flask, request, jsonify
import requests

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tv-gemini-telegram-bot")

app = Flask(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
# gemini-flash-latest يشير دائمًا لأحدث نسخة فلاش متاحة من جوجل
GEMINI_API = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={GEMINI_API_KEY}"


def get_gemini_commentary(data: dict) -> str:
    """يطلب من Gemini تعليقًا قصيرًا (سطر أو سطرين) يشرح الإشارة."""
    if not GEMINI_API_KEY:
        return ""

    action = str(data.get("action", "")).upper()
    ticker = data.get("ticker", "?")
    price = data.get("price", "?")
    sl = data.get("sl", "?")
    tp = data.get("tp", "?")

    prompt = (
        f"إشارة تداول من استراتيجية Flipping Markets (FLIP بعد كسر هيكلي "
        f"واكتساح سيولة وإعادة اختبار):\n"
        f"النوع: {action}\nالرمز: {ticker}\nسعر الدخول: {price}\n"
        f"وقف الخسارة: {sl}\nالهدف: {tp}\n\n"
        f"اكتب تعليقًا قصيرًا جدًا (سطر أو سطرين بالعربية) يشرح باختصار سبب "
        f"احتمالية هذا الانعكاس، بدون أي نصيحة استثمارية مباشرة."
    )

    try:
        resp = requests.post(
            GEMINI_API,
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=20,
        )
        resp.raise_for_status()
        result = resp.json()
        return result["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (requests.RequestException, KeyError, IndexError) as e:
        log.error("Gemini request failed: %s", e)
        return ""


def send_telegram_message(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        log.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set")
        return
    try:
        resp = requests.post(
            TELEGRAM_API,
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error("Failed to send Telegram message: %s", e)


def format_signal(data: dict) -> str:
    action = str(data.get("action", "")).upper()
    ticker = data.get("ticker", "?")
    price = data.get("price", "?")
    sl = data.get("sl", "?")
    tp = data.get("tp", "?")

    emoji = "🟢" if action == "BUY" else "🔴" if action == "SELL" else "⚪️"
    msg = (
        f"{emoji} <b>{action}</b> — {ticker}\n"
        f"السعر: <code>{price}</code>\n"
        f"وقف الخسارة: <code>{sl}</code>\n"
        f"الهدف: <code>{tp}</code>\n"
        f"<i>Flipping Markets Strategy</i>"
    )

    commentary = get_gemini_commentary(data)
    if commentary:
        msg += f"\n\n🤖 <b>تحليل Gemini:</b>\n{commentary}"

    return msg


@app.route("/webhook", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET and request.args.get("secret", "") != WEBHOOK_SECRET:
        return jsonify({"error": "unauthorized"}), 401

    data = request.get_json(silent=True)
    if data is None:
        raw = request.data.decode("utf-8", errors="ignore")
        if raw:
            send_telegram_message(raw)
        return jsonify({"status": "sent-raw"}), 200

    send_telegram_message(format_signal(data))
    return jsonify({"status": "ok"}), 200


@app.route("/", methods=["GET"])
def health():
    return "TradingView -> Gemini -> Telegram bot is running.", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
