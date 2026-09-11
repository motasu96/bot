"""
TradingView -> Telegram Bot
============================
يستقبل تنبيهات (webhook) من استراتيجية "Flipping Markets" في TradingView
ويرسلها كرسالة منسقة إلى تيليجرام.
"""

import os
import logging

from flask import Flask, request, jsonify
import requests

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tv-telegram-bot")

app = Flask(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


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
    return (
        f"{emoji} <b>{action}</b> — {ticker}\n"
        f"السعر: <code>{price}</code>\n"
        f"وقف الخسارة: <code>{sl}</code>\n"
        f"الهدف: <code>{tp}</code>\n"
        f"<i>Flipping Markets Strategy</i>"
    )


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
    return "TradingView -> Telegram bot is running.", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)