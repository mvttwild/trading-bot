# ─────────────────────────────────────────────────────────────────────────────
# Jarvis Trading Bot — Full Suite v3
# Features:
#   - Expanded morning brief: SPY, QQQ, TSLA, NVDA, AMD, GLD, META, IWM, DIA
#   - Macro: DXY, 10Y, VIX, TLT, XLF, GLD
#   - Persistent trade journal (file-based, survives restarts)
#   - Setup quality checker (text "check" before a trade)
#   - Post-trade context logging
#   - Weekly edge report every Friday 4pm HST
#   - Two-way commands with threading
#   - 15-min news warnings
#   - Alert cooldown
#   - TradingView webhook alerts
# ─────────────────────────────────────────────────────────────────────────────

import os, json, requests, threading, time
from flask import Flask, request, jsonify
from datetime import datetime, timezone, timedelta

app = Flask(__name__)

BOT_TOKEN      = "8765588779:AAGoP0mLTY_IHEvTcgqtv4UgRkGMy3H2Tgk"
CHAT_ID        = "8794039692"
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
JOURNAL_FILE   = "/tmp/jarvis_journal.json"

HST = timezone(timedelta(hours=-10))
EST = timezone(timedelta(hours=-5))

# ── PERSISTENT JOURNAL ────────────────────────────────────────────────────────
def load_journal():
    try:
        if os.path.exists(JOURNAL_FILE):
            with open(JOURNAL_FILE, "r") as f:
                return json.load(f)
    except:
        pass
    return { "wins": 0, "losses": 0, "trades": [] }

def save_journal(j):
    try:
        with open(JOURNAL_FILE, "w") as f:
            json.dump(j, f)
    except Exception as e:
        print(f"Journal save error: {e}")

journal = load_journal()

# ── SETUP CHECK STATE ─────────────────────────────────────────────────────────
check_state = {}

# ── COOLDOWN ──────────────────────────────────────────────────────────────────
last_alert = {}
COOLDOWN_MINUTES = 15

def is_cooldown(key):
    now = datetime.now(HST)
    if key in last_alert:
        if (now - last_alert[key]).total_seconds() / 60 < COOLDOWN_MINUTES:
            return True
    last_alert[key] = now
    return False

# ── TELEGRAM ──────────────────────────────────────────────────────────────────
def send_telegram(message: str):
    url  = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    body = { "chat_id": CHAT_ID, "text": message, "parse_mode": "HTML" }
    try:
        r = requests.post(url, json=body, timeout=10)
        print(f"Telegram: {r.status_code}")
        return r.ok
    except Exception as e:
        print(f"Telegram failed: {e}")
        return False

# ── QUOTE FETCHER ─────────────────────────────────────────────────────────────
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

def fmt_quote(d, decimals=2):
    if not d: return "N/A"
    return f"{d['color']} ${d['price']:.{decimals}f} ({d['pct']:+.2f}% {d['arrow']})"

def fmt_macro(d, decimals=2):
    if not d: return "N/A"
    return f"{d['color']} {d['price']:.{decimals}f} ({d['pct']:+.2f}% {d['arrow']})"

# ── ECONOMIC CALENDAR ─────────────────────────────────────────────────────────
RECURRING_EVENTS = [
    (0, 10,  0, "🟡", "ISM Manufacturing PMI"),
    (1,  8, 30, "🔴", "JOLTS Job Openings"),
    (2,  8, 30, "🔴", "ADP Employment"),
    (2, 14,  0, "🔴", "FOMC Decision"),
    (2, 14, 30, "🔴", "Powell Press Conference"),
    (3,  8, 30, "🔴", "Initial Jobless Claims"),
    (3,  8, 30, "🔴", "GDP Release"),
    (4,  8, 30, "🔴", "Non-Farm Payrolls"),
    (4,  8, 30, "🔴", "Core PCE"),
    (4, 10,  0, "🟡", "Consumer Sentiment"),
]

def get_todays_events():
    now_est = datetime.now(EST)
    wd = now_est.weekday()
    events = [(h, m, imp, name) for (w, h, m, imp, name) in RECURRING_EVENTS if w == wd]
    return sorted(events, key=lambda x: x[0]*60+x[1])

def format_events(events):
    if not events: return "✅ No major events"
    lines = []
    for (h, m, imp, name) in events:
        h12 = h % 12 or 12
        ampm = "AM" if h < 12 else "PM"
        lines.append(f"{imp} {h12}:{m:02d} {ampm} EST — {name}")
    return "\n".join(lines)

# ── MORNING BRIEF ─────────────────────────────────────────────────────────────
def send_morning_brief():
    now_hst = datetime.now(HST)
    date_str = now_hst.strftime("%A %b %d")

    # Macro
    dxy = fetch_quote("DX-Y.NYB")
    tnx = fetch_quote("^TNX")
    vix = fetch_quote("^VIX")
    tlt = fetch_quote("TLT")
    xlf = fetch_quote("XLF")

    # Equities watchlist
    spy  = fetch_quote("SPY")
    qqq  = fetch_quote("QQQ")
    tsla = fetch_quote("TSLA")
    nvda = fetch_quote("NVDA")
    amd  = fetch_quote("AMD")
    meta = fetch_quote("META")
    iwm  = fetch_quote("IWM")
    dia  = fetch_quote("DIA")
    gld  = fetch_quote("GLD")

    events = get_todays_events()
    events_str = format_events(events)

    # Conditions
    warnings = []
    if vix and vix["price"] > 25:
        warnings.append("⚠️ VIX elevated — reduce size")
    if vix and vix["price"] > 20:
        warnings.append("⚠️ VIX above 20 — be selective")
    if dxy and dxy["pct"] > 0.3:
        warnings.append("🔴 DXY pumping — bearish equities")
    if dxy and dxy["pct"] < -0.3:
        warnings.append("✅ DXY weak — bullish equities")
    if tnx and tnx["pct"] > 0.5:
        warnings.append("⚠️ Yields spiking — watch SPY")
    if gld and gld["pct"] > 0.5:
        warnings.append("⚠️ Gold up — risk-off tone")
    if gld and gld["pct"] < -0.3:
        warnings.append("✅ Gold down — risk-on tone")
    if any(e[2] == "🔴" for e in events):
        warnings.append("🔴 High impact news today — be careful")
    if not warnings:
        warnings.append("✅ Conditions look clean — go get it")

    conditions = "\n".join(warnings)

    msg = f"""🌅 <b>GOOD MORNING MATT</b>
━━━━━━━━━━━━━━━━━━━━
📅 {date_str} | Jarvis Pre-Market Brief

📊 <b>MACRO</b>
DXY:  {fmt_macro(dxy)}
10Y:  {fmt_macro(tnx, 3)}
VIX:  {fmt_macro(vix)}
TLT:  {fmt_quote(tlt)}
XLF:  {fmt_quote(xlf)}

📈 <b>INDICES</b>
SPY:  {fmt_quote(spy)}
QQQ:  {fmt_quote(qqq)}
IWM:  {fmt_quote(iwm)}
DIA:  {fmt_quote(dia)}

⚡ <b>WATCHLIST</b>
TSLA: {fmt_quote(tsla)}
NVDA: {fmt_quote(nvda)}
AMD:  {fmt_quote(amd)}
META: {fmt_quote(meta)}
GLD:  {fmt_quote(gld)}

🗓 <b>ECONOMIC EVENTS (EST)</b>
{events_str}

🎯 <b>CONDITIONS</b>
{conditions}
━━━━━━━━━━━━━━━━━━━━
🕐 {now_hst.strftime('%H:%M HST')} — King better be walked 🐕"""

    send_telegram(msg)

# ── SETUP QUALITY CHECKER ─────────────────────────────────────────────────────
SETUP_QUESTIONS = [
    "1️⃣ Is London H/L clearly defined? (yes/no)",
    "2️⃣ Did price sweep AND close back inside the level? (yes/no)",
    "3️⃣ Is price action weakness confirmed at level? (yes/no)",
    "4️⃣ Is VWAP and POC on your side? (yes/no)",
    "5️⃣ Is VIX below 25 and no news in next 30 min? (yes/no)",
]

def start_check():
    check_state["active"] = True
    check_state["answers"] = []
    check_state["q"] = 0
    send_telegram(f"🔍 <b>SETUP QUALITY CHECK</b>\n━━━━━━━━━━━━━━━━━━━━\n{SETUP_QUESTIONS[0]}")

def process_check_answer(text):
    ans = text.strip().lower()
    if ans not in ["yes", "no", "y", "n"]:
        send_telegram("Reply <b>yes</b> or <b>no</b>")
        return

    check_state["answers"].append(1 if ans in ["yes", "y"] else 0)
    check_state["q"] += 1

    if check_state["q"] < len(SETUP_QUESTIONS):
        send_telegram(SETUP_QUESTIONS[check_state["q"]])
    else:
        # Score it
        score = sum(check_state["answers"])
        total = len(SETUP_QUESTIONS)
        pct = (score / total) * 100
        check_state["active"] = False

        if score == 5:
            grade = "A+ 🔥 HIGH CONVICTION — Take it"
            color = "✅"
        elif score == 4:
            grade = "B+ 👍 GOOD SETUP — Take it"
            color = "✅"
        elif score == 3:
            grade = "C ⚠️ MARGINAL — Reduce size or skip"
            color = "⚠️"
        else:
            grade = "D ❌ WEAK SETUP — Stand aside"
            color = "❌"

        criteria = ["London H/L", "Sweep confirmed", "PA weakness", "VWAP/POC aligned", "Clean conditions"]
        missed = [criteria[i] for i, a in enumerate(check_state["answers"]) if a == 0]
        missed_str = "\n".join([f"❌ {m}" for m in missed]) if missed else "All criteria met"

        send_telegram(f"""{color} <b>SETUP SCORE: {score}/{total} ({pct:.0f}%)</b>
━━━━━━━━━━━━━━━━━━━━
<b>{grade}</b>

<b>Missing:</b>
{missed_str}
━━━━━━━━━━━━━━━━━━━━
🕐 {datetime.now(HST).strftime('%H:%M HST')}""")

# ── WEEKLY EDGE REPORT ────────────────────────────────────────────────────────
def send_weekly_report():
    j = load_journal()
    total = j["wins"] + j["losses"]
    wr = (j["wins"] / total * 100) if total > 0 else 0
    trades = j.get("trades", [])

    # Analyze recent trades
    recent = trades[-20:] if len(trades) > 20 else trades
    recent_wins = sum(1 for t in recent if t.get("result") == "win")
    recent_wr = (recent_wins / len(recent) * 100) if recent else 0

    now = datetime.now(HST)
    send_telegram(f"""📊 <b>WEEKLY EDGE REPORT</b>
━━━━━━━━━━━━━━━━━━━━
Week ending {now.strftime('%b %d, %Y')}

<b>ALL TIME</b>
✅ Wins:   {j['wins']}
❌ Losses: {j['losses']}
📊 Total:  {total}
🎯 Win Rate: {wr:.1f}%

<b>LAST 20 TRADES</b>
🎯 Win Rate: {recent_wr:.1f}%
━━━━━━━━━━━━━━━━━━━━
Keep executing the system 🤙""")

# ── COMMAND HANDLER ───────────────────────────────────────────────────────────
def handle_command(text: str):
    # If setup check is active, process answers
    if check_state.get("active"):
        process_check_answer(text)
        return

    cmd = text.strip().lower()

    if cmd in ["brief", "morning", "gm"]:
        send_morning_brief()

    elif cmd == "levels":
        spy = fetch_quote("SPY")
        qqq = fetch_quote("QQQ")
        dxy = fetch_quote("DX-Y.NYB")
        vix = fetch_quote("^VIX")
        gld = fetch_quote("GLD")
        now = datetime.now(HST)
        send_telegram(f"""📊 <b>LIVE LEVELS</b>
━━━━━━━━━━━━━━━━━━━━
SPY:  {fmt_quote(spy)}
QQQ:  {fmt_quote(qqq)}
GLD:  {fmt_quote(gld)}
DXY:  {fmt_macro(dxy)}
VIX:  {fmt_macro(vix)}
━━━━━━━━━━━━━━━━━━━━
🕐 {now.strftime('%H:%M HST')}""")

    elif cmd == "check":
        start_check()

    elif cmd in ["win", "w"]:
        journal["wins"] += 1
        journal["trades"].append({"result": "win", "time": datetime.now(HST).isoformat()})
        save_journal(journal)
        total = journal["wins"] + journal["losses"]
        wr = (journal["wins"] / total * 100) if total > 0 else 0
        send_telegram(f"✅ <b>WIN logged!</b>\nRecord: {journal['wins']}W / {journal['losses']}L\nWin Rate: {wr:.1f}%\nTotal trades: {total}/100")

    elif cmd in ["loss", "l"]:
        journal["losses"] += 1
        journal["trades"].append({"result": "loss", "time": datetime.now(HST).isoformat()})
        save_journal(journal)
        total = journal["wins"] + journal["losses"]
        wr = (journal["wins"] / total * 100) if total > 0 else 0
        send_telegram(f"❌ <b>LOSS logged.</b>\nRecord: {journal['wins']}W / {journal['losses']}L\nWin Rate: {wr:.1f}%\nTotal trades: {total}/100")

    elif cmd in ["stats", "record", "journal"]:
        total = journal["wins"] + journal["losses"]
        wr = (journal["wins"] / total * 100) if total > 0 else 0
        remaining = max(0, 100 - total)
        send_telegram(f"""📋 <b>TRADE JOURNAL</b>
━━━━━━━━━━━━━━━━━━━━
✅ Wins:      {journal['wins']}
❌ Losses:    {journal['losses']}
📊 Total:     {total}/100
🎯 Win Rate:  {wr:.1f}%
📍 Remaining: {remaining} trades
━━━━━━━━━━━━━━━━━━━━""")

    elif cmd in ["status", "ping"]:
        now = datetime.now(HST)
        total = journal["wins"] + journal["losses"]
        send_telegram(f"✅ <b>Jarvis is online</b>\n🕐 {now.strftime('%H:%M HST')}\n📡 Railway server active\n📊 Trades logged: {total}/100")

    elif cmd == "report":
        send_weekly_report()

    elif cmd == "help":
        send_telegram("""🤖 <b>JARVIS COMMANDS</b>
━━━━━━━━━━━━━━━━━━━━
<b>brief</b> — full pre-market brief
<b>levels</b> — live SPY/QQQ/GLD/DXY/VIX
<b>check</b> — setup quality scorer
<b>win</b> — log a winning trade
<b>loss</b> — log a losing trade
<b>stats</b> — trade journal + progress
<b>report</b> — weekly edge report
<b>status</b> — confirm Jarvis is online
━━━━━━━━━━━━━━━━━━━━""")

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
        action = "Target reached — consider closing"
    elif "WEAK" in signal.upper():
        emoji = "⚠️"; side_str = ""
        action = "Price action weakness at London level"
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
    lines.append(f"\nText <b>check</b> to score this setup before entering")
    return "\n".join(lines)

# ── BACKGROUND SCHEDULER ──────────────────────────────────────────────────────
def scheduler():
    morning_sent = None
    weekly_sent = None
    event_warned = set()

    while True:
        now_hst = datetime.now(HST)
        now_est = datetime.now(EST)
        today = now_hst.strftime("%Y-%m-%d")
        week  = now_hst.strftime("%Y-%W")

        # 8am HST morning brief weekdays
        # 2am HST during DST (Mar-Nov), 3am HST during standard time (Nov-Mar)
brief_hour = 2 if now_hst.month in [3,4,5,6,7,8,9,10,11] else 3
if now_hst.hour == brief_hour and now_hst.minute == 0 and now_hst.weekday() < 5 and morning_sent != today:



        # Friday 3pm HST weekly report
        if now_hst.weekday() == 3 and now_hst.hour == 16 and now_hst.minute == 0 and weekly_sent != week:
            send_weekly_report()
            weekly_sent = week

        # Reset event warnings daily
        if now_hst.hour == 0 and now_hst.minute == 0:
            event_warned.clear()

        # 15-min news warnings
        for (h, m, imp, name) in get_todays_events():
            if imp == "🔴":
                event_time = now_est.replace(hour=h, minute=m, second=0, microsecond=0)
                diff = (event_time - now_est).total_seconds() / 60
                key = f"{today}_{name}"
                if 14 <= diff <= 16 and key not in event_warned:
                    event_warned.add(key)
                    h12 = h % 12 or 12
                    ampm = "AM" if h < 12 else "PM"
                    send_telegram(f"⚠️ <b>NEWS IN 15 MIN</b>\n🔴 {name}\n🕐 {h12}:{m:02d} {ampm} EST\n\nStay out or tighten stops.")

        time.sleep(60)

threading.Thread(target=scheduler, daemon=True).start()

# ── ROUTES ────────────────────────────────────────────────────────────────────
@app.route("/telegram", methods=["POST"])
def telegram_incoming():
    data = request.get_json(silent=True) or {}
    text = data.get("message", {}).get("text", "")
    if text:
        t = threading.Thread(target=handle_command, args=(text,))
        t.start()
    return jsonify({"ok": True}), 200

@app.route("/webhook", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET:
        if request.args.get("secret", "") != WEBHOOK_SECRET:
            return jsonify({"error": "Unauthorized"}), 401
    raw = request.get_data(as_text=True)
    try:
        data = json.loads(raw)
    except:
        data = {"signal": raw.strip()}
    key = f"{data.get('ticker','SPY')}_{data.get('signal','')}"
    if is_cooldown(key):
        return jsonify({"status": "cooldown"}), 200
    msg = format_alert(data)
    success = send_telegram(msg)
    return jsonify({"status": "sent" if success else "error"}), 200 if success else 500

@app.route("/test", methods=["GET"])
def test():
    msg = format_alert({
        "signal": "SWEEP HIGH", "ticker": "SPY", "side": "SHORT",
        "price": "589.45", "target": "585.90",
        "ldnHigh": "589.45", "ldnLow": "585.90", "poc": "587.20"
    })
    success = send_telegram(msg)
    return jsonify({"status": "sent" if success else "failed"}), 200

@app.route("/testbrief", methods=["GET"])
def test_brief():
    threading.Thread(target=send_morning_brief).start()
    return jsonify({"status": "brief sending"}), 200

@app.route("/", methods=["GET"])
def health():
    j = load_journal()
    total = j["wins"] + j["losses"]
    wr = (j["wins"] / total * 100) if total > 0 else 0
    return jsonify({
        "status": "online", "bot": "Jarvis v3",
        "time_hst": datetime.now(HST).strftime("%H:%M HST"),
        "journal": { "wins": j["wins"], "losses": j["losses"], "total": total, "win_rate": f"{wr:.1f}%" },
    }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
