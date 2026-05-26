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
def get_eastern():
    """Returns EDT (UTC-4) during DST Mar-Nov, EST (UTC-5) Nov-Mar."""
    month = datetime.now(timezone.utc).month
    offset = -4 if 3 <= month <= 11 else -5
    return timezone(timedelta(hours=offset))

EST = get_eastern()  # Updated dynamically in scheduler

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
def fetch_options_data(symbol):
    try:
        today_ts = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        url = f"https://query2.finance.yahoo.com/v7/finance/options/{symbol}?date={today_ts}"
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        d = r.json()
        result = d["optionChain"]["result"][0]
        opts   = result["options"][0]
        calls  = opts.get("calls", [])
        puts   = opts.get("puts",  [])
        total_put_oi  = sum(p.get("openInterest", 0) for p in puts)
        total_call_oi = sum(c.get("openInterest", 0) for c in calls)
        pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else None
        all_strikes = sorted(set([o["strike"] for o in calls + puts]))
        min_pain = float("inf")
        max_pain_strike = None
        for S in all_strikes:
            call_pain = sum(max(0, S - c["strike"]) * c.get("openInterest", 0) for c in calls)
            put_pain  = sum(max(0, p["strike"] - S) * p.get("openInterest", 0) for p in puts)
            total = call_pain + put_pain
            if total < min_pain:
                min_pain = total
                max_pain_strike = S
        underlying = result.get("quote", {}).get("regularMarketPrice")
        atm_iv = None
        if underlying and calls:
            atm = min(calls, key=lambda c: abs(c["strike"] - underlying))
            atm_iv = round(atm.get("impliedVolatility", 0) * 100, 1)
        return {"pcr": pcr, "max_pain": max_pain_strike, "atm_iv": atm_iv}
    except Exception as e:
        print(f"Options fetch error for {symbol}: {e}")
        return {"pcr": None, "max_pain": None, "atm_iv": None}

def pcr_label(pcr):
    if pcr is None: return "N/A"
    if pcr > 1.5:  return f"{pcr} VERY BEARISH"
    if pcr > 1.1:  return f"{pcr} BEARISH LEAN"
    if pcr > 0.85: return f"{pcr} NEUTRAL"
    if pcr > 0.6:  return f"{pcr} BULLISH LEAN"
    return             f"{pcr} VERY BULLISH"


def calc_confluence(dxy, tnx, vix, tlt, xlf, spy, qqq, tsla, nvda, amd, meta, iwm, gld, spy_opts, qqq_opts, events):
    """Score bullish and bearish confluence 0-10 and return detailed breakdown."""
    bull = []
    bear = []
    neutral = []

    # DXY
    if dxy:
        if dxy["pct"] < -0.3:   bull.append("DXY weak — foreign capital flows into equities")
        elif dxy["pct"] > 0.3:  bear.append("DXY strong — headwind for equities")
        else:                    neutral.append("DXY flat — no dollar signal")

    # 10Y Yields
    if tnx:
        if tnx["pct"] < -0.3:   bull.append("10Y yields falling — easing financial conditions")
        elif tnx["pct"] > 0.5:  bear.append("10Y yields spiking — pressure on equities")
        else:                    neutral.append("10Y yields stable")

    # VIX
    if vix:
        if vix["price"] < 15:              bull.append("VIX low — calm markets, premium cheap")
        elif 15 <= vix["price"] <= 20:     bull.append("VIX normal — manageable conditions")
        elif 20 < vix["price"] <= 25:      bear.append("VIX elevated — reduce size, be selective")
        else:                              bear.append("VIX high — fear regime, extreme caution")

    # TLT (bonds)
    if tlt:
        if tlt["pct"] > 0.3:    bull.append("TLT up — yields falling, bonds rallying")
        elif tlt["pct"] < -0.3: bear.append("TLT down — yields rising, bonds selling off")

    # XLF (financials lead)
    if xlf:
        if xlf["pct"] > 0.3:    bull.append("XLF leading — broad market rally has legs")
        elif xlf["pct"] < -0.3: bear.append("XLF lagging — financial stress, rally suspect")

    # SPY/QQQ direction
    bull_tickers = 0
    bear_tickers = 0
    for label, d in [("SPY", spy), ("QQQ", qqq), ("TSLA", tsla), ("NVDA", nvda), ("AMD", amd), ("META", meta), ("IWM", iwm)]:
        if d:
            if d["pct"] > 0.3:   bull_tickers += 1
            elif d["pct"] < -0.3: bear_tickers += 1

    if bull_tickers >= 5:    bull.append(f"{bull_tickers}/7 watchlist tickers bullish premarket — strong breadth")
    elif bull_tickers >= 3:  bull.append(f"{bull_tickers}/7 watchlist tickers bullish — moderate breadth")
    elif bear_tickers >= 5:  bear.append(f"{bear_tickers}/7 watchlist tickers bearish — broad selling")
    elif bear_tickers >= 3:  bear.append(f"{bear_tickers}/7 watchlist tickers bearish — distribution")

    # GLD (risk-on/off)
    if gld:
        if gld["pct"] < -0.3:   bull.append("Gold down — risk-on, equities preferred")
        elif gld["pct"] > 0.5:  bear.append("Gold up — risk-off, safe haven bid")

    # PCR
    if spy_opts["pcr"]:
        pcr = spy_opts["pcr"]
        if pcr < 0.7:    bull.append(f"SPY PCR {pcr} — bullish options positioning")
        elif pcr > 1.2:  bear.append(f"SPY PCR {pcr} — bearish options positioning")
        else:            neutral.append(f"SPY PCR {pcr} — neutral positioning")

    # Max Pain
    if spy_opts["max_pain"] and spy and spy["price"]:
        mp = spy_opts["max_pain"]
        price = spy["price"]
        diff_pct = ((mp - price) / price) * 100
        if diff_pct > 0.3:    bull.append(f"Max Pain ${mp} above price — gravitational pull UP")
        elif diff_pct < -0.3: bear.append(f"Max Pain ${mp} below price — gravitational pull DOWN")

    # News events
    red_events = [e for e in events if e[2] == "🔴"]
    if red_events:
        bear.append(f"{len(red_events)} high-impact news event(s) today — avoid trading around them")
    else:
        bull.append("No major news events — clean tape conditions")

    bull_score = len(bull)
    bear_score = len(bear)
    total = bull_score + bear_score
    net = bull_score - bear_score

    if net >= 5:       bias = "STRONG BULL"; emoji = "🟢🟢"; size_rec = "HIGH CONVICTION — consider sizing up on confirmed London Low sweeps"
    elif net >= 3:     bias = "BULLISH";     emoji = "🟢";   size_rec = "Lean long — standard 1-2 contracts, look for London Low sweeps"
    elif net >= 1:     bias = "SLIGHT BULL"; emoji = "🟡";   size_rec = "Mild bull lean — standard size, need clean setup confirmation"
    elif net == 0:     bias = "NEUTRAL";     emoji = "⚪";   size_rec = "Mixed signals — reduce size, only A+ setups"
    elif net >= -2:    bias = "SLIGHT BEAR"; emoji = "🟠";   size_rec = "Mild bear lean — standard size, look for London High sweeps"
    elif net >= -4:    bias = "BEARISH";     emoji = "🔴";   size_rec = "Lean short — standard 1-2 contracts, look for London High sweeps"
    else:              bias = "STRONG BEAR"; emoji = "🔴🔴"; size_rec = "HIGH CONVICTION — consider sizing up on confirmed London High sweeps"

    return {
        "bull": bull, "bear": bear, "neutral": neutral,
        "bull_score": bull_score, "bear_score": bear_score,
        "net": net, "bias": bias, "emoji": emoji, "size_rec": size_rec
    }


def format_confluence(c):
    total = c["bull_score"] + c["bear_score"]
    lines = [
        f"{c['emoji']} <b>CONFLUENCE: {c['bias']}</b>  ({c['bull_score']} bull / {c['bear_score']} bear)",
        "━━━━━━━━━━━━━━━━━━━━"
    ]
    for item in c["bull"]:    lines.append(f"✅ {item}")
    for item in c["bear"]:    lines.append(f"❌ {item}")
    for item in c["neutral"]: lines.append(f"⚪ {item}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"🎯 {c['size_rec']}")
    return "\n".join(lines)

def get_ai_analysis(data_summary):
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json"},
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 300,
                "messages": [{
                    "role": "user",
                    "content": f"""You are Jarvis, a sharp 0DTE options trading analyst for Matt Nakamoto who trades SPY, QQQ, TSLA, NVDA, AMD, META, IWM, DIA and GLD using an ICT London failed break strategy.

Based on this pre-market data give a 3-4 sentence bias analysis. Be direct and actionable.
State overall directional bias, key risk to watch, and whether conditions favor trading or standing aside today.
No fluff, no disclaimers. Write like a sharp trading desk analyst texting a colleague.

DATA:
{data_summary}"""
                }]
            },
            timeout=20
        )
        result = r.json()
        return result["content"][0]["text"].strip()
    except Exception as e:
        print(f"Claude API error: {e}")
        return "AI analysis unavailable — check data manually."

def send_morning_brief():
    now_hst = datetime.now(HST)
    date_str = now_hst.strftime("%A %b %d")
    dxy = fetch_quote("DX-Y.NYB")
    tnx = fetch_quote("^TNX")
    vix = fetch_quote("^VIX")
    tlt = fetch_quote("TLT")
    xlf = fetch_quote("XLF")
    spy  = fetch_quote("SPY")
    qqq  = fetch_quote("QQQ")
    tsla = fetch_quote("TSLA")
    nvda = fetch_quote("NVDA")
    amd  = fetch_quote("AMD")
    meta = fetch_quote("META")
    iwm  = fetch_quote("IWM")
    dia  = fetch_quote("DIA")
    gld  = fetch_quote("GLD")
    # Options chain only available during market hours — N/A at 2am is expected
    spy_opts = fetch_options_data("SPY")
    qqq_opts = fetch_options_data("QQQ")
    opts_note = "" if spy_opts["pcr"] else "\n⏰ Options data loads at market open (2:30am HST)"
    events     = get_todays_events()
    events_str = format_events(events)
    flags = []
    if vix and vix["price"] > 25:    flags.append("VIX ELEVATED - reduce size")
    elif vix and vix["price"] > 20:  flags.append("VIX above 20 - be selective")
    else:                            flags.append("VIX normal range")
    if dxy and dxy["pct"] > 0.3:    flags.append("DXY pumping - bearish equities")
    elif dxy and dxy["pct"] < -0.3: flags.append("DXY weak - bullish equities")
    else:                            flags.append("DXY flat - no signal")
    if tnx and tnx["pct"] > 0.5:    flags.append("Yields spiking - caution on longs")
    elif tnx and tnx["pct"] < -0.3: flags.append("Yields falling - bullish tailwind")
    if gld and gld["pct"] > 0.5:    flags.append("Gold up - risk-off tone")
    elif gld and gld["pct"] < -0.3: flags.append("Gold down - risk-on tone")
    if any(e[2] == "🔴" for e in events): flags.append("HIGH IMPACT NEWS TODAY")
    conditions = "\n".join(flags)
    def p(d, dec=2): return f"{d['price']:.{dec}f} ({d['pct']:+.2f}%)" if d else "N/A"
    data_summary = f"""DXY: {p(dxy)} | 10Y: {p(tnx,3)} | VIX: {p(vix)} | TLT: {p(tlt)} | XLF: {p(xlf)}
SPY: {p(spy)} | QQQ: {p(qqq)} | IWM: {p(iwm)} | DIA: {p(dia)}
TSLA: {p(tsla)} | NVDA: {p(nvda)} | AMD: {p(amd)} | META: {p(meta)} | GLD: {p(gld)}
SPY PCR: {spy_opts['pcr']} | SPY Max Pain: {spy_opts['max_pain']} | SPY ATM IV: {spy_opts['atm_iv']}%
QQQ PCR: {qqq_opts['pcr']} | QQQ Max Pain: {qqq_opts['max_pain']}
Events: {events_str} | Flags: {" | ".join(flags)}"""
    # Confluence scoring
    confluence = calc_confluence(dxy, tnx, vix, tlt, xlf, spy, qqq, tsla, nvda, amd, meta, iwm, gld, spy_opts, qqq_opts, events)
    confluence_str = format_confluence(confluence)

    # AI analysis with confluence context
    data_summary_with_confluence = data_summary + f"\nBias: {confluence['bias']} ({confluence['bull_score']} bull factors vs {confluence['bear_score']} bear factors)"
    ai_bias = get_ai_analysis(data_summary_with_confluence)

    spy_pcr_str = pcr_label(spy_opts["pcr"])
    spy_mp_str  = ("$" + str(spy_opts["max_pain"])) if spy_opts["max_pain"] else "N/A"
    spy_iv_str  = (str(spy_opts["atm_iv"]) + "%") if spy_opts["atm_iv"] else "N/A"
    qqq_pcr_str = pcr_label(qqq_opts["pcr"])
    qqq_mp_str  = ("$" + str(qqq_opts["max_pain"])) if qqq_opts["max_pain"] else "N/A"

    # Build message in parts to keep clean
    parts = []
    parts.append(f"\U0001f305 <b>GOOD MORNING MATT</b>")
    parts.append(f"\u2501" * 20)
    parts.append(f"\U0001f4c5 {date_str} | Jarvis Pre-Market Brief")
    parts.append("")
    parts.append(f"\U0001f4ca <b>MACRO</b>")
    parts.append(f"DXY:  {fmt_macro(dxy)}")
    parts.append(f"10Y:  {fmt_macro(tnx, 3)}")
    parts.append(f"VIX:  {fmt_macro(vix)}")
    parts.append(f"TLT:  {fmt_quote(tlt)}")
    parts.append(f"XLF:  {fmt_quote(xlf)}")
    parts.append("")
    parts.append(f"\U0001f4c8 <b>INDICES</b>")
    parts.append(f"SPY:  {fmt_quote(spy)}")
    parts.append(f"QQQ:  {fmt_quote(qqq)}")
    parts.append(f"IWM:  {fmt_quote(iwm)}")
    parts.append(f"DIA:  {fmt_quote(dia)}")
    parts.append("")
    parts.append(f"\u26a1 <b>WATCHLIST</b>")
    parts.append(f"TSLA: {fmt_quote(tsla)}")
    parts.append(f"NVDA: {fmt_quote(nvda)}")
    parts.append(f"AMD:  {fmt_quote(amd)}")
    parts.append(f"META: {fmt_quote(meta)}")
    parts.append(f"GLD:  {fmt_quote(gld)}")
    parts.append("")
    parts.append(f"\U0001f3b0 <b>OPTIONS (0DTE)</b>")
    parts.append(f"SPY PCR:      {spy_pcr_str}")
    parts.append(f"SPY Max Pain: {spy_mp_str}")
    parts.append(f"SPY ATM IV:   {spy_iv_str}")
    parts.append(f"QQQ PCR:      {qqq_pcr_str}")
    parts.append(f"QQQ Max Pain: {qqq_mp_str}")
    parts.append("")
    parts.append(f"\U0001f5d3 <b>EVENTS TODAY (EST)</b>")
    parts.append(events_str)
    parts.append("")
    parts.append(confluence_str)
    parts.append("")
    parts.append(f"\U0001f916 <b>JARVIS ANALYSIS</b>")
    parts.append(ai_bias)
    parts.append(f"\u2501" * 20)
    parts.append(now_hst.strftime("%H:%M HST") + " — Walk King first 🐕")

    send_telegram("\n".join(parts))

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
def send_intraday_update():
    """Fires during market hours if conditions shift significantly."""
    spy = fetch_quote("SPY")
    qqq = fetch_quote("QQQ")
    vix = fetch_quote("^VIX")
    dxy = fetch_quote("DX-Y.NYB")
    now = datetime.now(HST)
    alerts = []
    if vix and vix["pct"] > 8:   alerts.append(f"VIX SPIKING +{vix['pct']:.1f}% — fear entering the market")
    if vix and vix["pct"] < -8:  alerts.append(f"VIX DROPPING {vix['pct']:.1f}% — fear fading, conditions improving")
    if dxy and dxy["pct"] > 0.4: alerts.append(f"DXY SURGING +{dxy['pct']:.1f}% — dollar strength headwind for longs")
    if dxy and dxy["pct"] < -0.4:alerts.append(f"DXY DROPPING {dxy['pct']:.1f}% — dollar weakness, tailwind for longs")
    if spy and abs(spy["pct"]) > 1.0: alerts.append(f"SPY MOVING {spy['pct']:+.2f}% — significant intraday shift")
    if alerts:
        msg = "⚡ <b>INTRADAY CONDITIONS UPDATE</b>\n━━━━━━━━━━━━━━━━━━━━\n"
        msg += "\n".join(alerts)
        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n🕐 {now.strftime('%H:%M HST')}"
        send_telegram(msg)

def scheduler():
    morning_sent = None
    weekly_sent = None
    event_warned = set()
    last_intraday = None

    while True:
        now_hst = datetime.now(HST)
        eastern = get_eastern()
        now_est = datetime.now(eastern)
        today = now_hst.strftime("%Y-%m-%d")
        week  = now_hst.strftime("%Y-%W")

        # 8am HST morning brief weekdays
        brief_hour = 2 if now_hst.month in [3,4,5,6,7,8,9,10,11] else 3
        if now_hst.hour == brief_hour and now_hst.minute == 0 and now_hst.weekday() < 5 and morning_sent != today:
            send_morning_brief()
            morning_sent = today

        # Friday 4pm HST weekly report
        if now_hst.weekday() == 4 and now_hst.hour == 16 and now_hst.minute == 0 and weekly_sent != week:
            send_weekly_report()
            weekly_sent = week

        # Reset event warnings daily
        if now_hst.hour == 0 and now_hst.minute == 0:
            event_warned.clear()

        # 15-min news warnings (uses dynamic Eastern time — EDT/EST aware)
        for (h, m, imp, name) in get_todays_events():
            if imp == "🔴":
                event_time = now_est.replace(hour=h, minute=m, second=0, microsecond=0)
                diff = (event_time - now_est).total_seconds() / 60
                key = f"{today}_{name}_warned"
                if 13 <= diff <= 17 and key not in event_warned:
                    event_warned.add(key)
                    h12 = h % 12 or 12
                    ampm = "AM" if h < 12 else "PM"
                    tz_label = "EDT" if 3 <= now_hst.month <= 11 else "EST"
                    send_telegram(f"⚠️ <b>NEWS IN 15 MIN</b>\n🔴 {name}\n🕐 {h12}:{m:02d} {ampm} {tz_label}\n\nStay out or tighten stops.")

        # Intraday condition updates every 30 min during market hours (HST)
        market_open_hst  = 2 if 3 <= now_hst.month <= 11 else 3  # 2:30am DST, 3:30am EST
        market_close_hst = 10 if 3 <= now_hst.month <= 11 else 11
        in_market_hours = market_open_hst <= now_hst.hour < market_close_hst
        intraday_key = now_hst.strftime("%Y-%m-%d-%H-") + str(now_hst.minute // 30)
        if in_market_hours and now_hst.minute % 30 == 0 and last_intraday != intraday_key:
            last_intraday = intraday_key
            threading.Thread(target=send_intraday_update, daemon=True).start()

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
