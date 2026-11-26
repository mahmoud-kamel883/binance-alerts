#!/usr/bin/env python3
"""Binance technical-condition alert bot (dry-run by default). Standard library only.
Neutral "conditions met" messages. Not advice, no prediction, no trading.
Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (e.g. @channelname), DRY_RUN (default 1; 0 = publish),
     STATE_FILE (default state.json). Exit code 1 if a send failed or a rate limit stopped the scan.
"""
import json, os, sys, time, datetime, urllib.request, urllib.parse, urllib.error

BASE = os.environ.get("BINANCE_BASE", "https://data-api.binance.vision")
HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "config.json")) as _f: CFG = json.load(_f)
INTERVAL_MS = {"15m": 900000, "1h": 3600000, "4h": 14400000, "1d": 86400000}
STATE_KEEP_MS = 7 * 86400000

class RateLimited(Exception):
    def __init__(self, where, retry_after):
        super().__init__(f"{where} rate limited/banned, retry_after={retry_after}")
        self.where = where
        self.retry_after = retry_after

def _retry_after(e):
    try: return int(e.headers.get("Retry-After", "0"))
    except Exception: return 0

def get(path, params=None, sleep=time.sleep):
    """429/418 -> raise RateLimited immediately (no retry, caller stops the scan). 5xx/network -> 3 tries."""
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    for i in range(3):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (429, 418): raise RateLimited("binance", _retry_after(e))
            if e.code < 500 or i == 2: raise
        except (urllib.error.URLError, TimeoutError):
            if i == 2: raise
        sleep(2 * (i + 1))

def rsi_series(closes, n):
    out = [None] * len(closes)
    if len(closes) <= n: return out
    ag = sum(max(closes[i] - closes[i-1], 0) for i in range(1, n+1)) / n
    al = sum(max(closes[i-1] - closes[i], 0) for i in range(1, n+1)) / n
    val = lambda ag, al: 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    out[n] = val(ag, al)
    for i in range(n+1, len(closes)):
        d = closes[i] - closes[i-1]
        ag = (ag*(n-1) + max(d, 0)) / n
        al = (al*(n-1) + max(-d, 0)) / n
        out[i] = val(ag, al)
    return out

def prepare(k, c=None):
    """Build per-list arrays once (no id()-based cache, so no identity-reuse risk)."""
    c = c or CFG
    closes = [float(x[4]) for x in k]
    return {"k": k, "closes": closes, "highs": [float(x[2]) for x in k],
            "vols": [float(x[5]) for x in k], "rsi": rsi_series(closes, c["rsi_period"])}

def _sma(v, n, i): return sum(v[i-n+1:i+1]) / n

def conditions_at(d, i, c=None):
    c = c or CFG
    closes, highs, vols, rsi = d["closes"], d["highs"], d["vols"], d["rsi"]
    window = [rsi[j] for j in range(i - c["rsi_lookback"], i) if rsi[j] is not None]
    lvl = c["rsi_cross_level"]
    return {
        "rsi_recovery": bool(window) and min(window) < lvl and rsi[i] is not None and rsi[i] > lvl,
        "above_ma": closes[i] > _sma(closes, c["ma_period"], i),
        "volume_spike": vols[i] > c["volume_multiple"] * _sma(vols, c["volume_period"], i-1),
        "breakout": closes[i] > max(highs[i - c["breakout_period"]:i]),
    }

def passes(res, c=None):
    c = c or CFG
    return all(res[x] for x in c["required_conditions"]) and sum(res.values()) >= c["min_conditions_met"]

def min_len(c=None):
    c = c or CFG
    return max(c["ma_period"], c["breakout_period"], c["volume_period"] + 1, c["rsi_period"] + c["rsi_lookback"] + 2) + c["cooldown_candles"] + 2

def watchlist(c=None):
    return _watch(get, c or CFG)

def event_id(symbol, interval, open_time_ms): return f"{symbol}|{interval}|{open_time_ms}"

def load_state(path):
    try:
        with open(path) as f: return json.load(f)
    except FileNotFoundError: return {"sent": {}, "blocked_until": {}}

def save_state(path, st, now_ms):
    st.setdefault("blocked_until", {})
    st["sent"] = {k: v for k, v in st["sent"].items() if now_ms - v < STATE_KEEP_MS}
    tmp = path + ".tmp"
    with open(tmp, "w") as f: json.dump(st, f, indent=1, sort_keys=True)
    os.replace(tmp, path)

def _num(x):
    """Plain decimal text, no scientific notation, up to 8 significant digits."""
    if x >= 1000: t = "%.2f" % x
    elif x >= 1: t = "%.4f" % x
    else: t = ("%.8f" % x) if x >= 0.0001 else ("%.10f" % x)
    return t.rstrip("0").rstrip(".") if "." in t else t

def fmt(sym, price, res, rsi_v, chg, close_ms, c=None):
    c = c or CFG
    base = sym[:-len(c["quote"])] if sym.endswith(c["quote"]) else sym
    lines = ["🚀 تنبيه فني - BINANCE",
             "🪙 الزوج: %s/%s" % (base, c["quote"]),
             "📊 السوق: SPOT فقط",
             "💰 سعر الرصد: %s %s" % (_num(price), c["quote"]),
             "📐 أهداف مرجعية:"]
    lines += ["• +%g٪ ➜ %s" % (p, _num(price * (1 + p / 100.0))) for p in c["reference_levels_pct"]]
    sl = c.get("stop_loss_pct", 7)   # user-requested line, shown as plain arithmetic from the observed price
    lines += ["• Stop Loss (-%g٪) ➜ %s" % (sl, _num(price * (1 - sl / 100.0)))]
    lines += ["⚠️ رصد فني فقط، وليس توصية بالشراء أو البيع."]
    return "\n".join(lines)

def send(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=20) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 429: raise RateLimited("telegram", _retry_after(e))
        raise
    if not body.get("ok"): raise RuntimeError("telegram not ok")
    return body

FOLLOW_WINDOW_MS = 48 * 3600 * 1000
FOLLOW_TEXT = "بفضل الله وصل السعر للمستوى المرجعي الأول +2٪ ✅"

def follow_ups(st, now_ms, dry, state_path, getf, sendf, c, log):
    """Observation only: after an alert, if any closed 1m candle opened after publication has a high at or above
    the first reference level (+first pct of the alert price), post once. Not a realized profit. Expires after 48h."""
    fails = 0; posted = 0
    tr = st.setdefault("tracked", {})
    lvl = 1 + c["reference_levels_pct"][0] / 100.0
    for eid, t in list(tr.items()):
        post_ms = t.get("posted") or t["t0"]
        if now_ms > post_ms + FOLLOW_WINDOW_MS:
            del tr[eid]; log(f"follow-up expired (no post): {eid}"); continue
        # 1-minute candles that OPENED after the alert was published and have closed; nothing from before publication counts
        start = -(-post_ms // 60000) * 60000
        hit = False; cur = start
        for _ in range(4):
            k = [x for x in getf("/api/v3/klines", {"symbol": t["sym"], "interval": "1m", "startTime": cur, "limit": 1000}) if x[6] < now_ms and x[0] >= start]
            if any(float(x[2]) >= t["p"] * lvl for x in k): hit = True; break
            if len(k) < 1000: break
            cur = k[-1][6] + 1
        if not hit: continue
        base = t["sym"][:-len(c["quote"])]
        lines = [FOLLOW_TEXT, "🪙 الزوج: %s/%s" % (base, c["quote"])]
        chat = os.environ.get("TELEGRAM_CHAT_ID", "")
        if t.get("msg") and chat.startswith("@"):
            lines.append("🔗 التنبيه الأصلي: https://t.me/%s/%s" % (chat[1:], t["msg"]))
        msg = "\n".join(lines)
        log(("[DRY-RUN follow-up] " if dry else "[SEND follow-up] ") + msg + "\n---")
        if dry: continue
        try:
            sendf(msg)
        except RateLimited: raise
        except Exception as e:
            fails += 1; log(f"follow-up send failed for {eid}: {type(e).__name__}"); continue
        del tr[eid]; save_state(state_path, st, now_ms)
        posted += 1
        if posted >= 10: break
    return fails

TEST_TEXT = "اختبار تشغيل التنبيهات الفنية، ليست توصيات شراء أو بيع"

def test_post(state_path=None, sendf=None):
    """One-time approved channel test post. Guarded so it can never send twice."""
    sendf = sendf or send
    path = state_path or os.environ.get("STATE_FILE", os.path.join(HERE, "state.json"))
    st = load_state(path)
    if st.get("test_post_done"):
        print("test post already done; not sending"); return 0
    body = sendf(TEST_TEXT)
    st["test_post_done"] = int(time.time() * 1000)
    save_state(path, st, st["test_post_done"])
    print("test post sent, message_id:", ((body.get("result") or {}).get("message_id")))
    return 0

def run(now_ms=None, dry=True, state_path=None, c=None, getf=None, sendf=None, log=print):
    c = c or CFG; getf = getf or get; sendf = sendf or send
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    state_path = state_path or os.environ.get("STATE_FILE", os.path.join(HERE, "state.json"))
    st = load_state(state_path)
    st.setdefault("blocked_until", {})
    for svc, until in st["blocked_until"].items():
        if now_ms < until:
            msg = f"{svc} blocked until {until} (saved from an earlier rate limit); not calling APIs"
            log("STOP: " + msg); return {"sent": 0, "failures": 0, "stopped": msg}
    ivl = INTERVAL_MS[c["interval"]]
    sent = failures = 0; stopped = None
    try:
        failures += follow_ups(st, now_ms, dry, state_path, getf, sendf, c, log)
        rows = _watch(getf, c)
        for qv, sym, chg in rows:
            k = getf("/api/v3/klines", {"symbol": sym, "interval": c["interval"], "limit": 120})
            k = [x for x in k if x[6] < now_ms]            # closed candles only
            if len(k) < min_len(c): continue
            if now_ms - k[-1][6] > 2 * ivl:                 # stale-data check
                log(f"skip {sym}: last closed candle is stale"); continue
            d = prepare(k, c); i = len(k) - 1
            res = conditions_at(d, i, c)
            if not passes(res, c): continue
            eid = event_id(sym, c["interval"], k[i][0])
            if eid in st["sent"]: continue                  # durable same-candle dedupe
            if any(passes(conditions_at(d, j, c), c) for j in range(i - c["cooldown_candles"], i)): continue
            msg = fmt(sym, d["closes"][i], res, d["rsi"][i], chg, k[i][6], c)
            log(("[DRY-RUN] " if dry else "[SEND] ") + msg + "\n---")
            if not dry:
                try:
                    body = sendf(msg)
                except RateLimited: raise
                except Exception as e:
                    failures += 1; log(f"send failed for {eid}: {type(e).__name__}"); continue  # not marked sent -> retried next run
                st["sent"][eid] = now_ms
                mid = ((body or {}).get("result") or {}).get("message_id")
                st.setdefault("tracked", {})[eid] = {"sym": sym, "p": d["closes"][i], "t0": k[i][6] + 1, "posted": int(time.time() * 1000), "msg": mid}
                save_state(state_path, st, now_ms)
            sent += 1
            if sent >= c["max_alerts_per_run"]: break
    except RateLimited as e:
        stopped = str(e); log("STOP: " + stopped)
        st["blocked_until"][e.where] = now_ms + max(e.retry_after, 60) * 1000  # persisted, honored by later runs
        save_state(state_path, st, now_ms)
    log(f"done: alerts={sent} failures={failures} stopped={stopped} mode={'dry' if dry else 'live'}")
    return {"sent": sent, "failures": failures, "stopped": stopped}

def _load_watchlist(c):
    fn = c.get("watchlist_file")
    if not fn: return None
    with open(os.path.join(HERE, fn)) as f:
        return {l.strip().upper() for l in f if l.strip() and not l.startswith("#")}

def _watch(getf, c):
    rows = []
    wl = _load_watchlist(c)
    if wl is not None:   # explicit user list only: no top-N cut, no substitution, no name filters
        # live status check: only pairs Binance reports as TRADING and spot-allowed right now (fails closed on error)
        info = getf("/api/v3/exchangeInfo", {"permissions": "SPOT", "symbolStatus": "TRADING"})
        tradable = {y["symbol"] for y in info["symbols"] if y.get("status") == "TRADING" and y.get("isSpotTradingAllowed", True)}
        for x in getf("/api/v3/ticker/24hr"):
            s = x["symbol"]
            if s in tradable and s.endswith(c["quote"]) and s[:-len(c["quote"])] in wl:
                qv = float(x["quoteVolume"])
                if qv >= c.get("min_quote_volume_24h", 0): rows.append((qv, s, float(x["priceChangePercent"])))
        rows.sort(reverse=True)
        return rows
    for x in getf("/api/v3/ticker/24hr"):
        s = x["symbol"]
        if not s.endswith(c["quote"]) or s in c["exclude_symbols"]: continue
        base = s[:-len(c["quote"])]
        if any(base.endswith(b) for b in c["exclude_substrings"]): continue
        qv = float(x["quoteVolume"])
        if qv >= c["min_quote_volume_24h"]: rows.append((qv, s, float(x["priceChangePercent"])))
    rows.sort(reverse=True)
    return rows[:c["top_n"]]

def state_selftest(path=None):
    """Dry-run helper: records a harmless marker in state.json so the workflow's commit step can be verified. No network, no sends."""
    path = path or os.environ.get("STATE_FILE", os.path.join(HERE, "state.json"))
    now = int(time.time() * 1000)
    st = load_state(path); st["selftest_at_ms"] = now
    save_state(path, st, now)
    print("state selftest written:", now)

if __name__ == "__main__":
    if os.environ.get("TEST_POST") == "1":
        sys.exit(test_post())
    if os.environ.get("STATE_SELFTEST") == "1":
        state_selftest()
    r = run(dry=os.environ.get("DRY_RUN", "1") != "0")
    sys.exit(1 if (r["failures"] or r["stopped"]) else 0)
