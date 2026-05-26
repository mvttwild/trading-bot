# ─────────────────────────────────────────────────────────────────────────────
# Jarvis Trading Bot — Full Suite v3
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
    month = datetime.now(timezone.utc).month
    offset = -4 if 3 <= month <= 11 else -5
    return timezone(timedelta(hours=offset))

EST = get_eastern()

# ── PERSISTENT JOURNAL ────────────────────────────────────────────────────────
def load_journal():
    try:
        if os.path.exists(JOURNAL_FILE):
            with open(JOURNAL_FILE, "r") as f:
                return json.load(f)
    except:
        pass
    return {"wins": 0, "losses": 0, "trades": []}

def save_journal(j):
    try:
        with open(JOURNAL_FILE, "w") as f:
            json.dump(j, f)
    except Exception as e:
        print(f"Journal save error: {e}")

journal = load_journal()

check_state     = {}
trade_log_state = {}

last_alert       = {}
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
    body = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
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
        pct   = ((curr - prev) / prev) * 100
        arrow = "▲" if curr > prev else "▼"
        color = "🟢" if curr > prev else "🔴"
        return {"price": curr, "pct": pct, "arrow": arrow, "color": color}
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
    now_est = datetime.now(get_eastern())
    wd = now_est.weekday()
    events = [(h, m, imp, name) for (w, h, m, imp, name) in RECURRING_EVENTS if w == wd]
    return sorted(events, key=lambda x: x[0]*60+x[1])

def format_events(events):
    if not events: return "✅ No major events"
    lines = []
    for (h, m, imp, name) in events:
        h12  = h % 12 or 12
        ampm = "AM" if h < 12 else "PM"
        lines.append(f"{imp} {h12}:{m:02d} {ampm} EDT — {name}")
    return "\n".join(lines)

# ── OPTIONS DATA ──────────────────────────────────────────────────────────────
def fetch_options_data(symbol):
    try:
        today_ts = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        url = f"https://query2.finance.yahoo.com/v7/finance/options/{symbol}?date={today_ts}"
        r   = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        d   = r.json()
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
            cp = sum(max(0, S - c["strike"]) * c.get("openInterest", 0) for c in calls)
            pp = sum(max(0, p["strike"] - S) * p.get("openInterest", 0) for p in puts)
            if cp + pp < min_pain:
                min_pain = cp + pp
                max_pain_strike = S
        underlying = result.get("quote", {}).get("regularMarketPrice")
        atm_iv = None
        if underlying and calls:
            atm   = min(calls, key=lambda c: abs(c["strike"] - underlying))
            atm_iv = round(atm.get("impliedVolatility", 0) * 100, 1)
        return {"pcr": pcr, "max_pain": max_pain_strike, "atm_iv": atm_iv}
    except Exception as e:
        print(f"Options fetch error {symbol}: {e}")
        return {"pcr": None, "max_pain": None, "atm_iv": None}

def pcr_label(pcr):
    if pcr is None: return "N/A"
    if pcr > 1.5:   return f"{pcr} VERY BEARISH"
    if pcr > 1.1:   return f"{pcr} BEARISH LEAN"
    if pcr > 0.85:  return f"{pcr} NEUTRAL"
    if pcr > 0.6:   return f"{pcr} BULLISH LEAN"
    return              f"{pcr} VERY BULLISH"

# ── CONFLUENCE SCORER ─────────────────────────────────────────────────────────
def calc_confluence(dxy, tnx, vix, tlt, xlf, spy, qqq, tsla, nvda, amd, meta, iwm, gld, spy_opts, qqq_opts, events):
    bull = []; bear = []; neutral = []
    if dxy:
        if dxy["pct"] < -0.3:   bull.append("DXY weak — foreign capital flows into equities")
        elif dxy["pct"] > 0.3:  bear.append("DXY strong — headwind for equities")
        else:                   neutral.append("DXY flat — no dollar signal")
    if tnx:
        if tnx["pct"] < -0.3:   bull.append("10Y yields falling — easing financial conditions")
        elif tnx["pct"] > 0.5:  bear.append("10Y yields spiking — pressure on equities")
        else:                   neutral.append("10Y yields stable")
    if vix:
        if vix["price"] < 15:             bull.append("VIX low — calm markets, premium cheap")
        elif 15 <= vix["price"] <= 20:    bull.append("VIX normal — manageable conditions")
        elif 20 < vix["price"] <= 25:     bear.append("VIX elevated — reduce size")
        else:                             bear.append("VIX high — fear regime, extreme caution")
    if tlt:
        if tlt["pct"] > 0.3:    bull.append("TLT up — yields falling, bonds rallying")
        elif tlt["pct"] < -0.3: bear.append("TLT down — yields rising, bonds selling")
    if xlf:
        if xlf["pct"] > 0.3:    bull.append("XLF leading — broad rally has legs")
        elif xlf["pct"] < -0.3: bear.append("XLF lagging — financial stress")
    bull_t = sum(1 for _, d in [("SPY",spy),("QQQ",qqq),("TSLA",tsla),("NVDA",nvda),("AMD",amd),("META",meta),("IWM",iwm)] if d and d["pct"] > 0.3)
    bear_t = sum(1 for _, d in [("SPY",spy),("QQQ",qqq),("TSLA",tsla),("NVDA",nvda),("AMD",amd),("META",meta),("IWM",iwm)] if d and d["pct"] < -0.3)
    if bull_t >= 5:   bull.append(f"{bull_t}/7 watchlist tickers bullish — strong breadth")
    elif bull_t >= 3: bull.append(f"{bull_t}/7 watchlist tickers bullish — moderate breadth")
    elif bear_t >= 5: bear.append(f"{bear_t}/7 watchlist tickers bearish — broad selling")
    elif bear_t >= 3: bear.append(f"{bear_t}/7 watchlist tickers bearish — distribution")
    if gld:
        if gld["pct"] < -0.3:  bull.append("Gold down — risk-on, equities preferred")
        elif gld["pct"] > 0.5: bear.append("Gold up — risk-off, safe haven bid")
    if spy_opts["pcr"]:
        pcr = spy_opts["pcr"]
        if pcr < 0.7:    bull.append(f"SPY PCR {pcr} — bullish options positioning")
        elif pcr > 1.2:  bear.append(f"SPY PCR {pcr} — bearish options positioning")
        else:            neutral.append(f"SPY PCR {pcr} — neutral positioning")
    if spy_opts["max_pain"] and spy and spy["price"]:
        mp   = spy_opts["max_pain"]
        diff = ((mp - spy["price"]) / spy["price"]) * 100
        if diff > 0.3:    bull.append(f"Max Pain ${mp} above price — pull UP")
        elif diff < -0.3: bear.append(f"Max Pain ${mp} below price — pull DOWN")
    red_events = [e for e in events if e[2] == "🔴"]
    if red_events: bear.append(f"{len(red_events)} high-impact news event(s) today")
    else:          bull.append("No major news — clean tape")
    net = len(bull) - len(bear)
    if net >= 5:      bias="STRONG BULL"; emoji="🟢🟢"; rec="HIGH CONVICTION — size up on London Low sweeps"
    elif net >= 3:    bias="BULLISH";     emoji="🟢";   rec="Lean long — 1-2 contracts, London Low sweeps"
    elif net >= 1:    bias="SLIGHT BULL"; emoji="🟡";   rec="Mild bull lean — need clean setup"
    elif net == 0:    bias="NEUTRAL";     emoji="⚪";   rec="Mixed — only A+ setups, reduce size"
    elif net >= -2:   bias="SLIGHT BEAR"; emoji="🟠";   rec="Mild bear lean — London High sweeps"
    elif net >= -4:   bias="BEARISH";     emoji="🔴";   rec="Lean short — 1-2 contracts, London High sweeps"
    else:             bias="STRONG BEAR"; emoji="🔴🔴"; rec="HIGH CONVICTION — size up on London High sweeps"
    return {"bull":bull,"bear":bear,"neutral":neutral,"bull_score":len(bull),"bear_score":len(bear),"net":net,"bias":bias,"emoji":emoji,"size_rec":rec}

def format_confluence(c):
    lines = [f"{c['emoji']} <b>CONFLUENCE: {c['bias']}</b>  ({c['bull_score']} bull / {c['bear_score']} bear)", "━━━━━━━━━━━━━━━━━━━━"]
    for item in c["bull"]:    lines.append(f"✅ {item}")
    for item in c["bear"]:    lines.append(f"❌ {item}")
    for item in c["neutral"]: lines.append(f"⚪ {item}")
    lines += ["━━━━━━━━━━━━━━━━━━━━", f"🎯 {c['size_rec']}"]
    return "\n".join(lines)

# ── AI ANALYSIS ───────────────────────────────────────────────────────────────
def get_ai_analysis(data_summary):
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"Content-Type": "application/json"},
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 300,
                "messages": [{"role": "user", "content":
                    f"You are Jarvis, a sharp 0DTE analyst for Matt Nakamoto trading SPY/QQQ/TSLA/NVDA/AMD/META/IWM/DIA/GLD using ICT London failed break strategy. "
                    f"Give a 3-4 sentence bias analysis. Direct, actionable, no fluff. State bias, key risk, and whether to trade or stand aside.\n\nDATA:\n{data_summary}"}]
            }, timeout=20)
        return r.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"Claude API error: {e}")
        return "AI analysis unavailable — check data manually."

# ── MORNING BRIEF ─────────────────────────────────────────────────────────────
def send_morning_brief():
    now_hst  = datetime.now(HST)
    date_str = now_hst.strftime("%A %b %d")
    dxy  = fetch_quote("DX-Y.NYB"); tnx = fetch_quote("^TNX")
    vix  = fetch_quote("^VIX");     tlt = fetch_quote("TLT")
    xlf  = fetch_quote("XLF");      spy = fetch_quote("SPY")
    qqq  = fetch_quote("QQQ");      tsla= fetch_quote("TSLA")
    nvda = fetch_quote("NVDA");     amd = fetch_quote("AMD")
    meta = fetch_quote("META");     iwm = fetch_quote("IWM")
    dia  = fetch_quote("DIA");      gld = fetch_quote("GLD")
    spy_opts = fetch_options_data("SPY")
    qqq_opts = fetch_options_data("QQQ")
    events     = get_todays_events()
    events_str = format_events(events)
    confluence = calc_confluence(dxy,tnx,vix,tlt,xlf,spy,qqq,tsla,nvda,amd,meta,iwm,gld,spy_opts,qqq_opts,events)
    conf_str   = format_confluence(confluence)
    def p(d, dec=2): return f"{d['price']:.{dec}f} ({d['pct']:+.2f}%)" if d else "N/A"
    summary = (f"DXY:{p(dxy)} 10Y:{p(tnx,3)} VIX:{p(vix)} TLT:{p(tlt)} XLF:{p(xlf)} "
               f"SPY:{p(spy)} QQQ:{p(qqq)} TSLA:{p(tsla)} NVDA:{p(nvda)} AMD:{p(amd)} META:{p(meta)} GLD:{p(gld)} "
               f"SPY_PCR:{spy_opts['pcr']} SPY_MP:{spy_opts['max_pain']} "
               f"Bias:{confluence['bias']} ({confluence['bull_score']}bull/{confluence['bear_score']}bear)")
    ai_bias = get_ai_analysis(summary)
    parts = [
        "🌅 <b>GOOD MORNING MATT</b>", "━"*20,
        f"📅 {date_str} | Jarvis Pre-Market Brief", "",
        "📊 <b>MACRO</b>",
        f"DXY:  {fmt_macro(dxy)}", f"10Y:  {fmt_macro(tnx,3)}",
        f"VIX:  {fmt_macro(vix)}", f"TLT:  {fmt_quote(tlt)}", f"XLF:  {fmt_quote(xlf)}", "",
        "📈 <b>INDICES</b>",
        f"SPY:  {fmt_quote(spy)}", f"QQQ:  {fmt_quote(qqq)}",
        f"IWM:  {fmt_quote(iwm)}", f"DIA:  {fmt_quote(dia)}", "",
        "⚡ <b>WATCHLIST</b>",
        f"TSLA: {fmt_quote(tsla)}", f"NVDA: {fmt_quote(nvda)}",
        f"AMD:  {fmt_quote(amd)}",  f"META: {fmt_quote(meta)}", f"GLD:  {fmt_quote(gld)}", "",
        "🎰 <b>OPTIONS (0DTE)</b>",
        f"SPY PCR:      {pcr_label(spy_opts['pcr'])}",
        f"SPY Max Pain: {'$'+str(spy_opts['max_pain']) if spy_opts['max_pain'] else 'N/A'}",
        f"SPY ATM IV:   {str(spy_opts['atm_iv'])+'%' if spy_opts['atm_iv'] else 'N/A (pre-market)'}",
        f"QQQ PCR:      {pcr_label(qqq_opts['pcr'])}",
        f"QQQ Max Pain: {'$'+str(qqq_opts['max_pain']) if qqq_opts['max_pain'] else 'N/A'}", "",
        "🗓 <b>EVENTS TODAY</b>", events_str, "",
        conf_str, "",
        "🤖 <b>JARVIS ANALYSIS</b>", ai_bias,
        "━"*20,
        now_hst.strftime("%H:%M HST") + " — Walk King first 🐕"
    ]
    send_telegram("\n".join(parts))

# ── SETUP CHECKER ─────────────────────────────────────────────────────────────
SETUP_QUESTIONS = [
    "1️⃣ Is London H/L clearly defined? (yes/no)",
    "2️⃣ Did price sweep AND close back inside the level? (yes/no)",
    "3️⃣ Is price action weakness confirmed at level? (yes/no)",
    "4️⃣ Is VWAP and POC on your side? (yes/no)",
    "5️⃣ Is VIX below 25 and no news in next 30 min? (yes/no)",
]

def start_check():
    check_state["active"]  = True
    check_state["answers"] = []
    check_state["q"]       = 0
    send_telegram(f"🔍 <b>SETUP QUALITY CHECK</b>\n━━━━━━━━━━━━━━━━━━━━\n{SETUP_QUESTIONS[0]}")

def process_check_answer(text):
    ans = text.strip().lower()
    if ans not in ["yes","no","y","n"]:
        send_telegram("Reply <b>yes</b> or <b>no</b>")
        return
    check_state["answers"].append(1 if ans in ["yes","y"] else 0)
    check_state["q"] += 1
    if check_state["q"] < len(SETUP_QUESTIONS):
        send_telegram(SETUP_QUESTIONS[check_state["q"]])
    else:
        score = sum(check_state["answers"])
        total = len(SETUP_QUESTIONS)
        check_state["active"] = False
        if score == 5:   grade="A+ 🔥 HIGH CONVICTION — Take it"; col="✅"
        elif score == 4: grade="B+ 👍 GOOD SETUP — Take it"; col="✅"
        elif score == 3: grade="C ⚠️ MARGINAL — Reduce size or skip"; col="⚠️"
        else:            grade="D ❌ WEAK SETUP — Stand aside"; col="❌"
        criteria = ["London H/L","Sweep confirmed","PA weakness","VWAP/POC aligned","Clean conditions"]
        missed   = [criteria[i] for i,a in enumerate(check_state["answers"]) if a==0]
        missed_str = "\n".join([f"❌ {m}" for m in missed]) if missed else "All criteria met"
        send_telegram(f"""{col} <b>SETUP SCORE: {score}/{total} ({score/total*100:.0f}%)</b>
━━━━━━━━━━━━━━━━━━━━
<b>{grade}</b>

<b>Missing:</b>
{missed_str}
━━━━━━━━━━━━━━━━━━━━
🕐 {datetime.now(HST).strftime('%H:%M HST')}""")

# ── BEHAVIORAL TRADE JOURNAL ──────────────────────────────────────────────────
BEHAVIOR_PATTERNS = {
    "sizing":    "Oversized relative to account",
    "fear":      "Fear-based early exit — left money on table",
    "revenge":   "Revenge trade after a loss",
    "fomo":      "FOMO entry — chased price",
    "noexit":    "No predefined exit — winged it",
    "emotional": "Added money from external account emotionally",
    "deviated":  "Deviated from predefined setup/plan",
    "profit":    "Failed to take profits at target",
    "other":     "Other behavioral issue",
}

def start_trade_log():
    trade_log_state.clear()
    trade_log_state["active"] = True
    trade_log_state["step"]   = 1
    trade_log_state["data"]   = {}
    send_telegram("""📝 <b>TRADE LOG</b>
━━━━━━━━━━━━━━━━━━━━
<b>Step 1/6</b> — Was this your predefined London Failed Break setup?

Reply: <b>yes</b> or <b>no</b>""")

def process_trade_log(text):
    step = trade_log_state.get("step", 0)
    data = trade_log_state.get("data", {})
    t    = text.strip().lower()

    if step == 1:
        data["on_setup"] = t in ["yes","y"]
        trade_log_state["step"] = 2
        send_telegram("""<b>Step 2/6</b> — Result?

Reply: <b>win</b>, <b>loss</b>, or <b>scratch</b>""")

    elif step == 2:
        data["result"] = t if t in ["win","loss","scratch"] else "scratch"
        trade_log_state["step"] = 3
        send_telegram("""<b>Step 3/6</b> — Did you follow your exit plan?

Reply: <b>yes</b>, <b>no</b>, or <b>partial</b>""")

    elif step == 3:
        data["followed_exit"] = t
        trade_log_state["step"] = 4
        send_telegram("""<b>Step 4/6</b> — % of peak unrealized gains captured?

Reply: <b>100</b>, <b>75</b>, <b>50</b>, <b>25</b>, or <b>0</b>""")

    elif step == 4:
        try:    data["captured_pct"] = int(t.replace("%","").strip())
        except: data["captured_pct"] = 50
        trade_log_state["step"] = 5
        pat_list = "\n".join([f"<b>{k}</b> — {v}" for k,v in BEHAVIOR_PATTERNS.items()])
        send_telegram(f"""<b>Step 5/6</b> — Any behavioral patterns? (or <b>none</b>)

{pat_list}

Reply with keyword(s) e.g. <b>fear profit</b>""")

    elif step == 5:
        data["patterns"] = [] if t=="none" else [p for p in t.split() if p in BEHAVIOR_PATTERNS]
        trade_log_state["step"] = 6
        send_telegram("""<b>Step 6/6</b> — One sentence lesson from this trade?""")

    elif step == 6:
        data["lesson"] = text.strip()
        trade_log_state["active"] = False
        trade_log_state["step"]   = 0
        j     = load_journal()
        entry = {
            "result":        data.get("result","scratch"),
            "on_setup":      data.get("on_setup", False),
            "followed_exit": data.get("followed_exit","no"),
            "captured_pct":  data.get("captured_pct", 50),
            "patterns":      data.get("patterns",[]),
            "lesson":        data.get("lesson",""),
            "time":          datetime.now(HST).isoformat(),
        }
        if not isinstance(j.get("trades"), list): j["trades"] = []
        j["trades"].append(entry)
        if data.get("on_setup"):
            if data.get("result") == "win":   j["wins"]     = j.get("wins",0)     + 1
            elif data.get("result") == "loss": j["losses"]   = j.get("losses",0)   + 1
        else:
            j["off_plan"] = j.get("off_plan",0) + 1
        save_journal(j)
        total_sys = j.get("wins",0) + j.get("losses",0)
        sys_wr    = (j["wins"] / total_sys * 100) if total_sys > 0 else 0
        cap       = data.get("captured_pct",50)
        cap_emoji = "🔥" if cap >= 80 else "⚠️" if cap >= 50 else "❌"
        pat_str   = "\n".join([f"⚠️ {BEHAVIOR_PATTERNS[p]}" for p in data.get("patterns",[])]) or "✅ No behavioral issues"
        setup_str = "✅ ON SETUP — counts toward edge" if data.get("on_setup") else "⚪ OFF PLAN — not counted toward edge win rate"
        send_telegram(f"""📋 <b>TRADE LOGGED</b>
━━━━━━━━━━━━━━━━━━━━
{setup_str}
Result: <b>{data.get("result","scratch").upper()}</b>
Exit plan followed: {data.get("followed_exit","?")}
{cap_emoji} Gains captured: {cap}%

<b>Behavior:</b>
{pat_str}

<b>Lesson:</b>
<i>{data.get("lesson","")}</i>
━━━━━━━━━━━━━━━━━━━━
📊 System: {j.get("wins",0)}W / {j.get("losses",0)}L ({sys_wr:.1f}% WR)
⚪ Off-plan trades: {j.get("off_plan",0)}""")

def show_patterns():
    j      = load_journal()
    trades = [t for t in j.get("trades",[]) if isinstance(t,dict) and "patterns" in t]
    if not trades:
        send_telegram("No trade data yet. Log trades with the <b>trade</b> command.")
        return
    pat_counts = {}
    for t in trades:
        for p in t.get("patterns",[]): pat_counts[p] = pat_counts.get(p,0) + 1
    captured  = [t.get("captured_pct",100) for t in trades if "captured_pct" in t]
    avg_cap   = sum(captured)/len(captured) if captured else 100
    on_setup  = [t for t in trades if t.get("on_setup")]
    off_plan  = [t for t in trades if not t.get("on_setup")]
    on_wr     = (sum(1 for t in on_setup if t.get("result")=="win") / len(on_setup) * 100) if on_setup else 0
    off_wr    = (sum(1 for t in off_plan if t.get("result")=="win") / len(off_plan) * 100) if off_plan else 0
    pat_str   = "\n".join([f"⚠️ {BEHAVIOR_PATTERNS.get(k,k)}: {v}x" for k,v in sorted(pat_counts.items(),key=lambda x:x[1],reverse=True)]) or "✅ No recurring patterns yet"
    send_telegram(f"""🧠 <b>BEHAVIORAL PATTERN REPORT</b>
━━━━━━━━━━━━━━━━━━━━
<b>SYSTEM (on-setup):</b> {len(on_setup)} trades — {on_wr:.1f}% WR
<b>OFF-PLAN:</b> {len(off_plan)} trades — {off_wr:.1f}% WR
<b>Avg gains captured:</b> {avg_cap:.0f}%

<b>Recurring behaviors:</b>
{pat_str}
━━━━━━━━━━━━━━━━━━━━
<i>Your edge shows in system trades.
Off-plan trades are where the account bleeds.</i>""")

# ── WEEKLY REPORT ─────────────────────────────────────────────────────────────
def send_weekly_report():
    j     = load_journal()
    total = j["wins"] + j["losses"]
    wr    = (j["wins"] / total * 100) if total > 0 else 0
    recent = j.get("trades",[])[-20:]
    rw     = sum(1 for t in recent if t.get("result")=="win")
    rwr    = (rw/len(recent)*100) if recent else 0
    now    = datetime.now(HST)
    send_telegram(f"""📊 <b>WEEKLY EDGE REPORT</b>
━━━━━━━━━━━━━━━━━━━━
Week ending {now.strftime('%b %d, %Y')}

<b>ALL TIME</b>
✅ Wins:   {j['wins']}
❌ Losses: {j['losses']}
📊 Total:  {total}
🎯 Win Rate: {wr:.1f}%

<b>LAST 20 TRADES</b>
🎯 Win Rate: {rwr:.1f}%
━━━━━━━━━━━━━━━━━━━━
Keep executing the system 🤙""")

# ── INTRADAY UPDATE ───────────────────────────────────────────────────────────
def send_intraday_update():
    spy = fetch_quote("SPY"); qqq = fetch_quote("QQQ")
    vix = fetch_quote("^VIX"); dxy = fetch_quote("DX-Y.NYB")
    now = datetime.now(HST)
    alerts = []
    if vix and vix["pct"] > 8:    alerts.append(f"VIX SPIKING +{vix['pct']:.1f}% — fear entering market")
    if vix and vix["pct"] < -8:   alerts.append(f"VIX DROPPING {vix['pct']:.1f}% — fear fading")
    if dxy and dxy["pct"] > 0.4:  alerts.append(f"DXY SURGING +{dxy['pct']:.1f}% — headwind for longs")
    if dxy and dxy["pct"] < -0.4: alerts.append(f"DXY DROPPING {dxy['pct']:.1f}% — tailwind for longs")
    if spy and abs(spy["pct"]) > 1.0: alerts.append(f"SPY MOVING {spy['pct']:+.2f}% — significant shift")
    if alerts:
        send_telegram("⚡ <b>INTRADAY UPDATE</b>\n━━━━━━━━━━━━━━━━━━━━\n" +
                      "\n".join(alerts) + f"\n━━━━━━━━━━━━━━━━━━━━\n🕐 {now.strftime('%H:%M HST')}")

# ── COMMAND HANDLER ───────────────────────────────────────────────────────────
def handle_command(text: str):
    if trade_log_state.get("active"):
        process_trade_log(text)
        return
    if check_state.get("active"):
        process_check_answer(text)
        return

    cmd = text.strip().lower()

    if cmd == "trade":
        start_trade_log()
    elif cmd == "patterns":
        show_patterns()
    elif cmd in ["brief","morning","gm"]:
        send_morning_brief()
    elif cmd == "levels":
        spy=fetch_quote("SPY"); qqq=fetch_quote("QQQ")
        dxy=fetch_quote("DX-Y.NYB"); vix=fetch_quote("^VIX"); gld=fetch_quote("GLD")
        now=datetime.now(HST)
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
    elif cmd in ["win","w"]:
        journal["wins"] += 1
        journal["trades"].append({"result":"win","time":datetime.now(HST).isoformat()})
        save_journal(journal)
        total = journal["wins"] + journal["losses"]
        wr    = (journal["wins"]/total*100) if total > 0 else 0
        send_telegram(f"✅ <b>WIN logged!</b>\nRecord: {journal['wins']}W / {journal['losses']}L\nWin Rate: {wr:.1f}%\nTotal: {total}/100")
    elif cmd in ["loss","l"]:
        journal["losses"] += 1
        journal["trades"].append({"result":"loss","time":datetime.now(HST).isoformat()})
        save_journal(journal)
        total = journal["wins"] + journal["losses"]
        wr    = (journal["wins"]/total*100) if total > 0 else 0
        send_telegram(f"❌ <b>LOSS logged.</b>\nRecord: {journal['wins']}W / {journal['losses']}L\nWin Rate: {wr:.1f}%\nTotal: {total}/100")
    elif cmd in ["stats","record","journal"]:
        total = journal["wins"] + journal["losses"]
        wr    = (journal["wins"]/total*100) if total > 0 else 0
        send_telegram(f"""📋 <b>TRADE JOURNAL</b>
━━━━━━━━━━━━━━━━━━━━
✅ Wins:      {journal['wins']}
❌ Losses:    {journal['losses']}
📊 Total:     {total}/100
🎯 Win Rate:  {wr:.1f}%
📍 Remaining: {max(0,100-total)} trades
━━━━━━━━━━━━━━━━━━━━""")
    elif cmd in ["status","ping"]:
        now   = datetime.now(HST)
        total = journal["wins"] + journal["losses"]
        send_telegram(f"✅ <b>Jarvis online</b>\n🕐 {now.strftime('%H:%M HST')}\n📡 Railway active\n📊 Trades: {total}/100")
    elif cmd == "report":
        send_weekly_report()
    elif cmd == "help":
        send_telegram("""🤖 <b>JARVIS COMMANDS</b>
━━━━━━━━━━━━━━━━━━━━
<b>brief</b> — full pre-market brief
<b>levels</b> — live SPY/QQQ/GLD/DXY/VIX
<b>check</b> — setup quality scorer
<b>trade</b> — log trade with behavioral metrics
<b>patterns</b> — behavioral pattern report
<b>stats</b> — journal + progress to 100
<b>report</b> — weekly edge report
<b>status</b> — confirm Jarvis is online
━━━━━━━━━━━━━━━━━━━━""")

# ── ALERT FORMATTER ───────────────────────────────────────────────────────────
def format_alert(data: dict) -> str:
    signal  = data.get("signal","SIGNAL"); ticker = data.get("ticker","SPY")
    price   = data.get("price","");        target = data.get("target","")
    ldn_h   = data.get("ldnHigh","");      ldn_l  = data.get("ldnLow","")
    poc     = data.get("poc","");          side   = data.get("side","")
    now_hst = datetime.now(HST)
    if "SWEEP HIGH" in signal.upper():   emoji="⚡🔴"; side_str="SHORT ↓"; action="Failed break above London High — expansion toward London Low"
    elif "SWEEP LOW" in signal.upper():  emoji="⚡🟢"; side_str="LONG ↑";  action="Failed break below London Low — expansion toward London High"
    elif "TARGET HIT" in signal.upper(): emoji="✅";   side_str="";         action="Target reached — consider closing"
    elif "WEAK" in signal.upper():       emoji="⚠️";   side_str="";         action="Price action weakness at London level"
    else:                                emoji="📊";   side_str=side;       action=signal
    lines = [f"{emoji} <b>{signal}</b>","━━━━━━━━━━━━━━━━━━━━"]
    if ticker: lines.append(f"📈 <b>{ticker}</b>  {side_str}")
    if price:  lines.append(f"💰 Price:    <code>${price}</code>")
    if target: lines.append(f"🎯 Target:   <code>${target}</code>")
    if ldn_h:  lines.append(f"🔴 LDN High: <code>${ldn_h}</code>")
    if ldn_l:  lines.append(f"🟢 LDN Low:  <code>${ldn_l}</code>")
    if poc:    lines.append(f"🟠 POC:      <code>${poc}</code>")
    lines += ["━━━━━━━━━━━━━━━━━━━━", f"<i>{action}</i>",
              f"🕐 {now_hst.strftime('%H:%M HST')}", "\nText <b>check</b> before entering"]
    return "\n".join(lines)

# ── SCHEDULER ─────────────────────────────────────────────────────────────────
def scheduler():
    morning_sent=None; weekly_sent=None; event_warned=set(); last_intraday=None
    while True:
        now_hst = datetime.now(HST)
        now_est = datetime.now(get_eastern())
        today   = now_hst.strftime("%Y-%m-%d")
        week    = now_hst.strftime("%Y-%W")
        brief_hour = 2 if now_hst.month in [3,4,5,6,7,8,9,10,11] else 3
        if now_hst.hour==brief_hour and now_hst.minute==0 and now_hst.weekday()<5 and morning_sent!=today:
            threading.Thread(target=send_morning_brief, daemon=True).start()
            morning_sent = today
        if now_hst.weekday()==4 and now_hst.hour==16 and now_hst.minute==0 and weekly_sent!=week:
            send_weekly_report(); weekly_sent = week
        if now_hst.hour==0 and now_hst.minute==0:
            event_warned.clear()
        for (h,m,imp,name) in get_todays_events():
            if imp=="🔴":
                event_time = now_est.replace(hour=h,minute=m,second=0,microsecond=0)
                diff = (event_time - now_est).total_seconds()/60
                key  = f"{today}_{name}_warned"
                if 13 <= diff <= 17 and key not in event_warned:
                    event_warned.add(key)
                    h12=h%12 or 12; ampm="AM" if h<12 else "PM"
                    tz_label="EDT" if 3<=now_hst.month<=11 else "EST"
                    send_telegram(f"⚠️ <b>NEWS IN 15 MIN</b>\n🔴 {name}\n🕐 {h12}:{m:02d} {ampm} {tz_label}\n\nStay out or tighten stops.")
        moh = 2 if 3<=now_hst.month<=11 else 3
        mch = 10 if 3<=now_hst.month<=11 else 11
        ikey = now_hst.strftime("%Y-%m-%d-%H-") + str(now_hst.minute//30)
        if moh<=now_hst.hour<mch and now_hst.minute%30==0 and last_intraday!=ikey:
            last_intraday=ikey
            threading.Thread(target=send_intraday_update, daemon=True).start()
        time.sleep(60)

threading.Thread(target=scheduler, daemon=True).start()

# ── ROUTES ────────────────────────────────────────────────────────────────────
@app.route("/telegram", methods=["POST"])
def telegram_incoming():
    data = request.get_json(silent=True) or {}
    text = data.get("message",{}).get("text","")
    if text:
        threading.Thread(target=handle_command, args=(text,), daemon=True).start()
    return jsonify({"ok": True}), 200

@app.route("/webhook", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET and request.args.get("secret","") != WEBHOOK_SECRET:
        return jsonify({"error":"Unauthorized"}), 401
    raw = request.get_data(as_text=True)
    try:    data = json.loads(raw)
    except: data = {"signal": raw.strip()}
    key = f"{data.get('ticker','SPY')}_{data.get('signal','')}"
    if is_cooldown(key): return jsonify({"status":"cooldown"}), 200
    success = send_telegram(format_alert(data))
    return jsonify({"status":"sent" if success else "error"}), 200 if success else 500

@app.route("/test", methods=["GET"])
def test():
    success = send_telegram(format_alert({"signal":"SWEEP HIGH","ticker":"SPY","side":"SHORT","price":"589.45","target":"585.90","ldnHigh":"589.45","ldnLow":"585.90","poc":"587.20"}))
    return jsonify({"status":"sent" if success else "failed"}), 200

@app.route("/testbrief", methods=["GET"])
def test_brief():
    threading.Thread(target=send_morning_brief, daemon=True).start()
    return jsonify({"status":"brief sending"}), 200

@app.route("/", methods=["GET"])
def health():
    j     = load_journal()
    total = j["wins"] + j["losses"]
    wr    = (j["wins"]/total*100) if total > 0 else 0
    return jsonify({"status":"online","bot":"Jarvis v3","time_hst":datetime.now(HST).strftime("%H:%M HST"),"journal":{"wins":j["wins"],"losses":j["losses"],"total":total,"win_rate":f"{wr:.1f}%"}}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)), debug=False)
