# ─────────────────────────────────────────────────────────────────────────────
# Jarvis Trading Bot — Full Suite
# Features:
#   - TradingView webhook alerts (sweep, target, weakness)
#   - Daily 8am HST morning brief (macro + premarket + econ calendar)
#   - 15-min warning before high impact economic events
#   - Alert cooldown (no spam)
#   - Two-way commands: text "levels", "brief", "status"
#   - Trade journal: text "win", "loss" to track results
# ─────────────────────────────────────────────────────────────────────────────

import os, json, requests, threading, time
from flask import Flask, request, jsonify
from datetime import datetime, timezone, timedelta

app = Flask(__name__)

BOT_TOKEN      = "8765588779:AAGoP0mLTY_IHEvTcgqtv4UgRkGMy3H2Tgk"
CHAT_ID        = "8794039692"
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

HST = timezone(timedelta(hours=-10))

# ── COOLDOWN TRACKER (prevents spam on same signal) ───────────────────────────
last_alert = {}
COOLDOWN_MINUTES = 15

def is_cooldown(key):
    now = datetime.now(HST)
    if key in last_alert:
        diff = (now - last_alert[key]).total_seconds() / 60
        if diff < COOLDOWN_MINUTES:
            return True
    last_alert[key] = now
    return False

# ── TRADE JOURNAL ─────────────────────────────────────────────────────────────
journal = { "wins": 0, "losses": 0, "notes": [] }

# ── TELEGRAM SENDER ───────────────────────────────────────────────────────────
def send_telegram(message: str):
    url  = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    body = { "chat_id": CHAT_ID, "text": message, "parse_mode": "HTML" }
    try:
        r = requests.post(url, json=body, timeout=10)
        print(f"Telegram: {r.status_code} — {r.text[:120]}")
        return r.ok
    except Exception as e:
        print(f"Telegram failed: {e}")
        return False

# ── MACRO FETCH ───────────────────────────────────────────────────────────────
def fetch_quote(symbol):
    try:
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=2d"
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        d = r.json()
        result = d["chart"]["result"][0]
        closes = [c for c in result["indicators"]["quote"][0]["close"] if c]
        curr, prev = closes[-1], closes[-2]
        pct = ((curr - prev) / prev) * 100
        arrow = "▲" if curr > prev else "▼"
        color = "🟢" if curr > prev else "🔴"
        return { "price": curr, "pct": pct, "arrow": arrow, "color": color }
    except:
        return None

# ── ECONOMIC CALENDAR (key events hardcoded + day-of check) ──────────────────
# Format: (weekday 0=Mon, hour_est, minute_est, impact, name)
# This covers recurring weekly/monthly events — expand as needed
RECURRING_EVENTS = [
    (0, 10,  0, "🟡", "ISM Manufacturing PMI"),
    (1,  8, 30, "🔴", "JOLTS Job Openings"),
    (2,  8, 30, "🔴", "ADP Employment"),
    (2, 14,  0, "🔴", "FOMC Meeting / Fed Decision"),
    (2, 14, 30, "🔴", "Powell Press Conference"),
    (3,  8, 30, "🔴", "Initial Jobless Claims"),
    (3,  8, 30, "🔴", "GDP Release"),
    (4,  8, 30, "🔴", "Non-Farm Payrolls"),
    (4,  8, 30, "🔴", "Core PCE Price Index"),
    (4, 10,  0, "🟡", "Consumer Sentiment (UoM)"),
]

def get_todays_events():
    now_est = datetime.now(timezone(timedelta(hours=-5)))
    today_weekday = now_est.weekday()
    events = []
    for (wd, h, m, impact, name) in RECURRING_EVENTS:
        if wd == today_weekday:
            events.append((h, m, impact, name))
    return sorted(events, key=lambda x: x[0]*60+x[1])

def format_events(events):
    if not events:
        return "✅ No major events scheduled"
    lines = []
    for (h, m, impact, name) in events:
        ampm = "AM" if h < 12 else "PM"
        h12 = h if h <= 12 else h - 12
        if h12 == 0: h12 = 12
        lines.append(f"{impact} {h12}:{m:02d} {ampm} EST — {name}")
    return "\n".join(lines)

# ── MORNING BRIEF ─────────────────────────────────────────────────────────────
def send_morning_brief():
    now_hst = datetime.now(HST)
    date_str = now_hst.strftime("%A %b %d")

    dxy  = fetch_quote("DX-Y.NYB")
    tnx  = fetch_quote("^TNX")
    vix  = fetch_quote("^VIX")
    spy  = fetch_quote("SPY")
    qqq  = fetch_quote("QQQ")

    def fmt(d, decimals=2):
        if not d: return "N/A"
        return f"{d['color']} {d['price']:.{decimals}f} ({d['pct']:+.2f}% {d['arrow']})"

    events = get_todays_events()
    events_str = format_events(events)

    # Conditions assessment
    warnings = []
    if vix and vix["price"] > 25:
        warnings.append("⚠️ VIX elevated — reduce size")
    if dxy and dxy["pct"] > 0.3:
        warnings.append("⚠️ DXY pumping — bearish SPY/QQQ")
    if dxy and dxy["pct"] < -0.3:
        warnings.append("✅ DXY weak — bullish SPY/QQQ")
    if tnx and tnx["pct"] > 0.5:
        warnings.append("⚠️ Yields spiking — watch SPY")
    if any(e[2] == "🔴" for e in events):
        warnings.append("🔴 High impact news today — trade carefully")

    conditions = "\n".join(warnings) if warnings else "✅ Conditions look clean"

    msg = f"""🌅 <b>GOOD MORNING MATT</b>
━━━━━━━━━━━━━━━━━━━━
📅 {date_str} | Jarvis Pre-Market Brief

📊 <b>MACRO</b>
DXY:  {fmt(dxy)}
10Y:  {fmt(tnx, 3)}
VIX:  {fmt(vix)}

📈 <b>PRE-MARKET</b>
SPY:  {fmt(spy)}
QQQ:  {fmt(qqq)}

⚡ <b>ECONOMIC EVENTS TODAY (EST)</b>
{events_str}

🎯 <b>CONDITIONS</b>
{conditions}
━━━━━━━━━━━━━━━━━━━━
🕐 {now_hst.strftime('%H:%M HST')} — Go get it 🤙"""

    send_telegram(msg)

# ── ALERT FORMATTER ───────────────────────────────────────────────────────────
def format_alert(data: dict) -> str:
    signal  = data.get("signal", "SIGNAL")
    ticker  = data.get("ticker", "SPY")
    side    = data.get("side", "")
    price   = data.get("price", "")
    target  = data.get("target", "")
    ldn_h   = data.get("ldnHigh", "")
    ldn_l   = data.get("ldnLow", "")
    poc     = data.get("poc", "")
    now_hst = datetime.now(HST)

    if "SWEEP HIGH" in signal.upper():
        emoji = "⚡🔴"; side_str = "SHORT ↓"
        action = "Failed break above London High — expansion toward London Low"
    elif "SWEEP LOW" in signal.upper():
        emoji = "⚡🟢"; side_str = "LONG ↑"
        action = "Failed break below London Low — expansion toward London High"
    elif "TARGET HIT" in signal.upper():
        emoji = "✅"; side_str = ""
        action = "Target reached — consider scaling out or closing"
    elif "WEAK" in signal.upper():
        emoji = "⚠️"; side_str = ""
        action = "Price action weakness detected at London level"
    else:
        emoji = "📊"; side_str = side; action = signal

    lines = [f"{emoji} <b>{signal}</b>", "━━━━━━━━━━━━━━━━━━━━"]
    if ticker:  lines.append(f"📈 <b>{ticker}</b>  {side_str}")
    if price:   lines.append(f"💰 Price:    <code>${price}</code>")
    if target:  lines.append(f"🎯 Target:   <code>${target}</code>")
    if ldn_h:   lines.append(f"🔴 LDN High: <code>${ldn_h}</code>")
    if ldn_l:   lines.append(f"🟢 LDN Low:  <code>${ldn_l}</code>")
    if poc:     lines.append(f"🟠 POC:      <code>${poc}</code>")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"<i>{action}</i>")
    lines.append(f"🕐 {now_hst.strftime('%H:%M HST')}")
    return "\n".join(lines)

# ── TWO-WAY COMMAND HANDLER ───────────────────────────────────────────────────
def handle_command(text: str):
    cmd = text.strip().lower()

    if cmd in ["brief", "morning", "gm"]:
        send_morning_brief()

    elif cmd == "levels":
        spy  = fetch_quote("SPY")
        qqq  = fetch_quote("QQQ")
        dxy  = fetch_quote("DX-Y.NYB")
        vix  = fetch_quote("^VIX")
        now  = datetime.now(HST)
        msg  = f"""📊 <b>LIVE LEVELS</b>
━━━━━━━━━━━━━━━━━━━━
SPY: {spy['color'] if spy else '—'} ${spy['price']:.2f if spy else 'N/A'}
QQQ: {qqq['color'] if qqq else '—'} ${qqq['price']:.2f if qqq else 'N/A'}
DXY: {dxy['color'] if dxy else '—'} {dxy['price']:.2f if dxy else 'N/A'}
VIX: {vix['color'] if vix else '—'} {vix['price']:.2f if vix else 'N/A'}
━━━━━━━━━━━━━━━━━━━━
🕐 {now.strftime('%H:%M HST')}"""
        send_telegram(msg)

    elif cmd in ["win", "w"]:
        journal["wins"] += 1
        total = journal["wins"] + journal["losses"]
        wr = (journal["wins"] / total * 100) if total > 0 else 0
        send_telegram(f"✅ <b>WIN logged!</b>\nRecord: {journal['wins']}W / {journal['losses']}L\nWin Rate: {wr:.1f}%")

    elif cmd in ["loss", "l"]:
        journal["losses"] += 1
        total = journal["wins"] + journal["losses"]
        wr = (journal["wins"] / total * 100) if total > 0 else 0
        send_telegram(f"❌ <b>LOSS logged.</b>\nRecord: {journal['wins']}W / {journal['losses']}L\nWin Rate: {wr:.1f}%")

    elif cmd in ["stats", "record", "journal"]:
        total = journal["wins"] + journal["losses"]
        wr = (journal["wins"] / total * 100) if total > 0 else 0
        send_telegram(f"""📋 <b>TRADE JOURNAL</b>
━━━━━━━━━━━━━━━━━━━━
✅ Wins:   {journal['wins']}
❌ Losses: {journal['losses']}
📊 Total:  {total}
🎯 Win Rate: {wr:.1f}%
━━━━━━━━━━━━━━━━━━━━""")

    elif cmd in ["status", "ping"]:
        now = datetime.now(HST)
        send_telegram(f"✅ Jarvis is online\n🕐 {now.strftime('%H:%M HST')}\n📡 Railway server active")

    elif cmd == "help":
        send_telegram("""🤖 <b>JARVIS COMMANDS</b>
━━━━━━━━━━━━━━━━━━━━
<b>brief</b> — morning market brief
<b>levels</b> — live SPY/QQQ/DXY/VIX
<b>win</b> — log a winning trade
<b>loss</b> — log a losing trade
<b>stats</b> — see your trade journal
<b>status</b> — check if Jarvis is online
━━━━━━━━━━━━━━━━━━━━""")

# ── BACKGROUND SCHEDULER ──────────────────────────────────────────────────────
def scheduler():
    morning_sent_date = None
    event_warned = set()

    while True:
        now_hst = datetime.now(HST)
        now_est = datetime.now(timezone(timedelta(hours=-5)))
        today_str = now_hst.strftime("%Y-%m-%d")

        # Morning brief at 8:00 AM HST (= 1:00 PM UTC / 2:00 PM EST)
        if now_hst.hour == 8 and now_hst.minute == 0 and morning_sent_date != today_str:
            # Only on weekdays
            if now_hst.weekday() < 5:
                send_morning_brief()
                morning_sent_date = today_str

        # Reset event warnings daily
        if now_hst.hour == 0 and now_hst.minute == 0:
            event_warned.clear()

        # 15-min warning before high impact events
        events = get_todays_events()
        for (h, m, impact, name) in events:
            if impact == "🔴":
                event_time_est = now_est.replace(hour=h, minute=m, second=0, microsecond=0)
                diff = (event_time_est - now_est).total_seconds() / 60
                key = f"{today_str}_{name}"
                if 14 <= diff <= 16 and key not in event_warned:
                    event_warned.add(key)
                    h12 = h if h <= 12 else h - 12
                    if h12 == 0: h12 = 12
                    ampm = "AM" if h < 12 else "PM"
                    send_telegram(f"⚠️ <b>NEWS IN 15 MIN</b>\n🔴 {name}\n🕐 {h12}:{m:02d} {ampm} EST\n\nConsider staying out or tightening stops.")

        time.sleep(60)

# Start scheduler in background thread
scheduler_thread = threading.Thread(target=scheduler, daemon=True)
scheduler_thread.start()

# ── INCOMING TELEGRAM MESSAGES (two-way) ──────────────────────────────────────
@app.route("/telegram", methods=["POST"])
def telegram_incoming():
    data = request.get_json(silent=True) or {}
    text = data.get("message", {}).get("text", "")
    if text:
        t = threading.Thread(target=handle_command, args=(text,))
        t.start()
    return jsonify({"ok": True}), 200

# ── TRADINGVIEW WEBHOOK ───────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET:
        if request.args.get("secret", "") != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 401

    raw = request.get_data(as_text=True)
    print(f"Payload: {raw[:300]}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {"signal": raw.strip()}

    signal = data.get("signal", "")
    ticker = data.get("ticker", "SPY")
    cooldown_key = f"{ticker}_{signal}"

    if is_cooldown(cooldown_key):
        print(f"Cooldown active for {cooldown_key} — skipping")
        return jsonify({"status": "cooldown"}), 200

    msg = format_alert(data)
    success = send_telegram(msg)
    return jsonify({"status": "sent" if success else "error"}), 200 if success else 500

# ── TEST ──────────────────────────────────────────────────────────────────────
@app.route("/test", methods=["GET"])
def test():
    test_payload = {
        "signal": "SWEEP HIGH", "ticker": "SPY", "side": "SHORT",
        "price": "589.45", "target": "585.90",
        "ldnHigh": "589.45", "ldnLow": "585.90", "poc": "587.20"
    }
    msg = format_alert(test_payload)
    success = send_telegram(msg)
    return jsonify({"status": "sent" if success else "failed"}), 200 if success else 500

@app.route("/testbrief", methods=["GET"])
def test_brief():
    send_morning_brief()
    return jsonify({"status": "brief sent"}), 200

# ── HEALTH ────────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    now = datetime.now(HST)
    return jsonify({
        "status": "online", "bot": "Jarvis",
        "time_hst": now.strftime("%H:%M HST"),
        "journal": journal,
    }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
