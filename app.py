from fastapi import FastAPI, Request
import requests
import os
import time

print("BOOT:", time.time())

app = FastAPI()

PUSHOVER_TOKEN = os.getenv("PUSHOVER_TOKEN", "")
PUSHOVER_USER  = os.getenv("PUSHOVER_USER", "")

@app.get("/")
def health():
    return {"status": "ok"}

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()

    payload = {
        "token": PUSHOVER_TOKEN,
        "user": PUSHOVER_USER,
        "title": "TV Alert",
        "message": str(data)[:900],
    }

    try:
        resp = requests.post(
            "https://api.pushover.net/1/messages.json",
            data=payload,
            timeout=10,
        )
        print("Pushover status:", resp.status_code)
        print("Pushover body:", resp.text[:300])
        return {"status": "ok", "pushover_http": resp.status_code}
    except Exception as e:
        print("Pushover exception:", repr(e))
        return {"status": "error", "exception": repr(e)}
