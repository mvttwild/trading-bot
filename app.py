# ─────────────────────────────────────────────────────────────────────────────
# Trading Alert Bot — Telegram Webhook Server
# Receives TradingView webhook → sends formatted Telegram message to Matt
# Deploy on Railway (free tier): railway.app
# ─────────────────────────────────────────────────────────────────────────────

import os, json, requests
from flask import Flask, request, jsonify
from datetime import datetime, timezone, timedelta

app = Flask(__name__)

BOT_TOKEN = "8765588779:AAGoP0mLTY_IHEvTcgqtv4UgRkGMy3H2Tgk"
CHAT_ID   = "8794039692"
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

# ── TELEGRAM SENDER ───────────────────────────────────────────────────────────
def send_telegram(message: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("ERROR: Missing BOT_TOKEN or CHAT_ID env vars")
        return False
    url  = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    body = { "chat_id": CHAT_ID, "text": message, "parse_mode": "HTML" }
    try:
        r = requests.post(url, json=body, timeout=10)
        print(f"Telegram response: {r.status_code} — {r.text[:100]}")
        return r.ok
    except Exception as e:
        print(f"Telegram send failed: {e}")
        return False

# ── MESSAGE FORMATTER ─────────────────────────────────────────────────────────
def format_message(data: dict) -> str:
    signal  = data.get("signal", "SIGNAL")
    ticker  = data.get("ticker", "SPY")
    side    = data.get("side", "")
    price   = data.get("price", "")
    target  = data.get("target", "")
    ldn_h   = data.get("ldnHigh", "")
    ldn_l   = data.get("ldnLow", "")
    poc     = data.get("poc", "")

    hst = timezone(timedelta(hours=-10))
    now_hst = datetime.now(hst)

    if "SWEEP HIGH" in signal.upper():
        emoji = "⚡🔴"
        side_str = "SHORT ↓"
        action = "Failed break above London High — expansion toward London Low"
    elif "SWEEP LOW" in signal.upper():
        emoji = "⚡🟢"
        side_str = "LONG ↑"
        action = "Failed break below London Low — expansion toward London High"
    elif "TARGET HIT" in signal.upper():
        emoji = "✅"
        side_str = ""
        action = "Target reached — consider scaling out or closing"
    elif "WEAK" in signal.upper():
        emoji = "⚠️"
        side_str = ""
        action = "Price action weakness detected at London level"
    else:
        emoji = "📊"
        side_str = side
        action = signal

    lines = [f"{emoji} <b>{signal}</b>"]
    lines.append(f"━━━━━━━━━━━━━━━━━━━━")

    if ticker:   lines.append(f"📈 <b>{ticker}</b>  {side_str}")
    if price:    lines.append(f"💰 Price:    <code>${price}</code>")
    if target:   lines.append(f"🎯 Target:   <code>${target}</code>")
    if ldn_h:    lines.append(f"🔴 LDN High: <code>${ldn_h}</code>")
    if ldn_l:    lines.append(f"🟢 LDN Low:  <code>${ldn_l}</code>")
    if poc:      lines.append(f"🟠 POC:      <code>${poc}</code>")

    lines.append(f"━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"<i>{action}</i>")
    lines.append(f"🕐 {now_hst.strftime('%H:%M HST')}")

    return "\n".join(lines)

# ── WEBHOOK ENDPOINT ──────────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET:
        incoming = request.args.get("secret", "")
        if incoming != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 401

    raw  = request.get_data(as_text=True)
    print(f"Incoming payload: {raw[:300]}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = { "signal": raw.strip() }

    msg = format_message(data)
    success = send_telegram(msg)

    if success:
        return jsonify({ "status": "sent", "signal": data.get("signal") }), 200
    else:
        return jsonify({ "status": "error", "detail": "Telegram send failed" }), 500

# ── TEST ENDPOINT ─────────────────────────────────────────────────────────────
@app.route("/test", methods=["GET"])
def test():
    test_payload = {
        "signal":  "SWEEP HIGH",
        "ticker":  "SPY",
        "side":    "SHORT",
        "price":   "589.45",
        "target":  "585.90",
        "ldnHigh": "589.45",
        "ldnLow":  "585.90",
        "poc":     "587.20"
    }
    msg     = format_message(test_payload)
    success = send_telegram(msg)
    return jsonify({ "status": "sent" if success else "failed", "message": msg }), 200 if success else 500

# ── HEALTH CHECK ──────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status":    "online",
        "bot":       "Trading Alert Bot",
        "token_set": bool(BOT_TOKEN),
        "chat_set":  bool(CHAT_ID),
    }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
