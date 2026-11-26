"""Read-only check of the bot's rights in the target channel. Sends nothing."""
import json, os, sys, urllib.request, urllib.error

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")


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
        sys.exit(0)
    except Exception as e:
        print("telegram call failed", type(e).__name__, method)
        sys.exit(0)


print("chat id configured:", bool(CHAT), CHAT if CHAT.startswith("@") else "(not a username)")
chat = call("getChat", {"chat_id": CHAT}).get("result", {})
print("chat type:", chat.get("type"), "| title:", chat.get("title"))
me = call("getMe").get("result", {})
m = call("getChatMember", {"chat_id": CHAT, "user_id": me.get("id")}).get("result", {})
print("bot status in chat:", m.get("status"))
print("can_post_messages:", m.get("can_post_messages"))
