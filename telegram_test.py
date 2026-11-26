"""One-off private test: reply once to the single private chat that sent /start.
Prints only counts, never the token, chat id or names."""
import json, os, sys, time, urllib.request, urllib.error

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TEXT = "اختبار ربط البوت نجح"
MAX_AGE = 30 * 60


def call(method, payload=None):
    url = "https://api.telegram.org/bot%s/%s" % (TOKEN, method)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        print("telegram http error", e.code, method)
        sys.exit(1)
    except Exception as e:
        print("telegram call failed", type(e).__name__, method)
        sys.exit(1)


def main():
    if not TOKEN:
        print("no token configured")
        sys.exit(1)
    me = call("getMe")
    print("getMe ok:", bool(me.get("ok")), "is_bot:", bool(me.get("result", {}).get("is_bot")))
    ups = call("getUpdates", {"limit": 100, "timeout": 0})
    now = time.time()
    chats = set()
    for u in ups.get("result", []):
        m = u.get("message") or {}
        c = m.get("chat") or {}
        if c.get("type") == "private" and str(m.get("text", "")).startswith("/start") \
                and now - m.get("date", 0) <= MAX_AGE:
            chats.add(c["id"])
    print("recent private /start chats:", len(chats))
    if len(chats) != 1:
        print("not exactly one chat, nothing sent")
        sys.exit(0)
    res = call("sendMessage", {"chat_id": list(chats)[0], "text": TEXT})
    print("sent:", bool(res.get("ok")))


main()
