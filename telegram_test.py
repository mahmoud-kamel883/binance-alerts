"""One-off private test with a persistent single-send guard.
Sends one message only to the private chat that sent the exact phrase.
Prints no token, chat id or names."""
import json, os, subprocess, sys, time, urllib.request, urllib.error

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TEXT = "اختبار ربط البوت نجح"
PHRASE = "ربط محمود 5831"
BOT_USERNAME = "kamel999bot"
GUARD = "test_send_guard.txt"
MAX_AGE = 30 * 60


def call(method, payload=None):
    url = "https://api.telegram.org/bot%s/%s" % (TOKEN, method)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            desc = json.load(e).get("description", "")
        except Exception:
            desc = ""
        print("telegram http error", e.code, method, desc)
        sys.exit(1)
    except Exception as e:
        print("telegram call failed", type(e).__name__, method)
        sys.exit(1)


def run(*args):
    return subprocess.run(args, capture_output=True, text=True)


def main():
    if not TOKEN:
        print("no token configured")
        sys.exit(1)
    if os.path.exists(GUARD):
        print("guard file exists, test already done, nothing sent")
        sys.exit(0)
    me = call("getMe").get("result", {})
    uname = str(me.get("username", ""))
    print("getMe username matches Kamel999bot:", uname.lower() == BOT_USERNAME)
    if uname.lower() != BOT_USERNAME:
        print("wrong bot, nothing sent")
        sys.exit(1)
    wh = call("getWebhookInfo").get("result", {})
    print("webhook url set:", bool(wh.get("url")), "pending:", wh.get("pending_update_count"))
    ups = call("getUpdates", {"limit": 100, "timeout": 0})
    print("updates returned:", len(ups.get("result", [])))
    now = time.time()
    chats = set()
    last_id = 0
    for u in ups.get("result", []):
        last_id = max(last_id, u.get("update_id", 0))
        m = u.get("message") or {}
        c = m.get("chat") or {}
        if c.get("type") == "private" and str(m.get("text", "")).strip() == PHRASE \
                and now - m.get("date", 0) <= MAX_AGE:
            chats.add(c["id"])
    print("private chats with exact phrase:", len(chats))
    if len(chats) != 1:
        print("not exactly one chat, nothing sent")
        sys.exit(0)
    # persistent guard first: if it cannot be saved, do not send
    with open(GUARD, "w") as f:
        f.write("test send started\n")
    steps = [
        ("git", "config", "user.name", "github-actions[bot]"),
        ("git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"),
        ("git", "add", GUARD),
        ("git", "commit", "-m", "test send guard"),
        ("git", "push"),
    ]
    for s in steps:
        r = run(*s)
        if r.returncode != 0:
            print("guard save failed at", s[1], ", nothing sent")
            sys.exit(1)
    print("guard saved")
    res = call("sendMessage", {"chat_id": list(chats)[0], "text": TEXT})
    print("sent:", bool(res.get("ok")))
    call("getUpdates", {"offset": last_id + 1, "limit": 1, "timeout": 0})
    print("updates acknowledged")


main()
