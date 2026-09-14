"""
TradingView -> Groq -> Telegram Bot (+ محادثة تفاعلية مع تحليل شارت حي)
============================================================================
1) /webhook   : يستقبل تنبيهات من استراتيجية Flipping Markets في TradingView
                ويرسلها + تعليق Groq إلى تيليجرام.
2) /telegram  : يستقبل رسائلك أنت من تيليجرام، يرسم شارت شموع حي حقيقي
                لرمز XAUUSD من بيانات سوق مباشرة (بدون أي علاقة بحسابك في
                TradingView)، ويرسله مع بيانات الشموع الأخيرة لـ Groq ليحللها نصيًا.

متغيرات البيئة المطلوبة:
    TELEGRAM_BOT_TOKEN  - التوكن من BotFather
    TELEGRAM_CHAT_ID    - رقم محادثتك (نفس الرقم يُستخدم كحماية: البوت
                          يتجاهل أي رسالة تجيه من رقم مختلف)
    WEBHOOK_SECRET      - (اختياري) كلمة سر لحماية رابط /webhook
    GROQ_API_KEY        - مفتاح Groq Cloud API من console.groq.com (مجاني)
    TWELVEDATA_API_KEY  - مفتاح مجاني من twelvedata.com لجلب أسعار الذهب الحية
"""

import base64
import io
import logging
import os

import matplotlib
matplotlib.use("Agg")  # يرسم بدون شاشة (ضروري على السيرفر)
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
import requests
from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tv-groq-telegram-bot")

app = Flask(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY", "")

TELEGRAM_SEND_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
TELEGRAM_PHOTO_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
GROQ_API = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-120b"
SYMBOL = "XAU/USD"


# ---------------------------------------------------------------------------
# تيليجرام: إرسال
# ---------------------------------------------------------------------------
def send_telegram_message(text: str) -> None:
    if not BOT_TOKEN or not CHAT_ID:
        log.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set")
        return
    try:
        resp = requests.post(
            TELEGRAM_SEND_URL,
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error("Failed to send Telegram message: %s", e)


def send_telegram_photo(image_bytes: bytes, caption: str = "") -> None:
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        resp = requests.post(
            TELEGRAM_PHOTO_URL,
            data={"chat_id": CHAT_ID, "caption": caption[:1024], "parse_mode": "HTML"},
            files={"photo": ("chart.png", image_bytes, "image/png")},
            timeout=20,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error("Failed to send Telegram photo: %s", e)


# ---------------------------------------------------------------------------
# جلب بيانات السعر الحية ورسم الشارت
# ---------------------------------------------------------------------------
def fetch_ohlc(interval: str = "1min", outputsize: int = 50) -> pd.DataFrame:
    url = (
        "https://api.twelvedata.com/time_series"
        f"?symbol={SYMBOL}&interval={interval}&outputsize={outputsize}"
        f"&apikey={TWELVEDATA_API_KEY}"
    )
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if "values" not in data:
        raise RuntimeError(f"Unexpected market data response: {data}")

    df = pd.DataFrame(data["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()
    df = df.astype({"open": float, "high": float, "low": float, "close": float})
    return df


def render_chart(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    mpf.plot(
        df,
        type="candle",
        style="charles",
        title=f"{SYMBOL} - Live (1m)",
        volume=False,
        figsize=(6, 4),
        savefig=dict(fname=buf, dpi=90, bbox_inches="tight"),
    )
    plt.close("all")
    buf.seek(0)
    data = buf.read()
    buf.close()
    del df
    import gc
    gc.collect()
    return data


# ---------------------------------------------------------------------------
# Groq
# ---------------------------------------------------------------------------
def describe_candles(df: pd.DataFrame, n: int = 20) -> str:
    """يحول آخر n شمعة إلى وصف نصي رقمي (بديل الصورة لأن نماذج Groq المجانية نصية فقط)."""
    recent = df.tail(n)
    lines = []
    for ts, row in recent.iterrows():
        lines.append(f"{ts.strftime('%H:%M')} O:{row['open']:.2f} H:{row['high']:.2f} L:{row['low']:.2f} C:{row['close']:.2f}")
    return "\n".join(lines)


def ask_groq_chart(df: pd.DataFrame, question: str) -> str:
    if not GROQ_API_KEY:
        return "لم يتم ضبط GROQ_API_KEY بعد."

    candles_text = describe_candles(df)
    prompt = (
        f"هذه آخر شموع حقيقية حية لسعر {SYMBOL} (فريم دقيقة واحدة)، كل سطر "
        f"شمعة بترتيب الوقت (O=فتح H=أعلى L=أدنى C=إغلاق):\n\n{candles_text}\n\n"
        f"جاوب بالعربية وباختصار على سؤال المستخدم التالي بناءً على هذه الأرقام "
        f"فعليًا فقط (لا تخترع أرقامًا غير مذكورة):\n\n{question}"
    )
    payload = {"model": GROQ_MODEL, "messages": [{"role": "user", "content": prompt}]}
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    try:
        resp = requests.post(GROQ_API, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        return result["choices"][0]["message"]["content"].strip()
    except (requests.RequestException, KeyError, IndexError) as e:
        log.error("Groq chart request failed: %s", e)
        return "صار خطأ أثناء تحليل الشارت، جرب بعد شوي."


def ask_groq_text(question: str) -> str:
    if not GROQ_API_KEY:
        return "لم يتم ضبط GROQ_API_KEY بعد."
    payload = {"model": GROQ_MODEL, "messages": [{"role": "user", "content": question}]}
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    try:
        resp = requests.post(GROQ_API, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        return result["choices"][0]["message"]["content"].strip()
    except (requests.RequestException, KeyError, IndexError) as e:
        log.error("Groq text request failed: %s", e)
        return "صار خطأ أثناء التواصل مع Groq."


# ---------------------------------------------------------------------------
# مسار /webhook (تنبيهات TradingView)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# مسار /telegram (محادثتك مع Groq عبر البوت)
# ---------------------------------------------------------------------------
CHART_KEYWORDS = ["شارت", "الشارت", "حلل", "تحليل", "سعر", "chart", "analy"]


@app.route("/telegram", methods=["POST"])
def telegram_updates():
    update = request.get_json(silent=True) or {}
    message = update.get("message", {})
    text = message.get("text", "")
    sender_chat_id = str(message.get("chat", {}).get("id", ""))

    # حماية: تجاهل أي رسالة من غير محادثتك
    if not text or sender_chat_id != str(CHAT_ID):
        return jsonify({"status": "ignored"}), 200

    wants_chart = any(k in text.lower() for k in CHART_KEYWORDS)

    if wants_chart:
        try:
            df = fetch_ohlc()
            image_bytes = render_chart(df)
            answer = ask_groq_chart(df, text)
            send_telegram_photo(image_bytes, caption=f"🤖 {answer}")
        except Exception as e:  # noqa: BLE001
            log.error("Chart analysis failed: %s", e)
            send_telegram_message("تعذّر جلب الشارت حاليًا، جرب بعد شوي.")
    else:
        answer = ask_groq_text(text)
        send_telegram_message(f"🤖 {answer}")

    return jsonify({"status": "ok"}), 200


@app.route("/", methods=["GET"])
def health():
    return "TradingView -> Groq -> Telegram bot is running.", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
