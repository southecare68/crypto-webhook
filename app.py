from fastapi import FastAPI, Request
import requests, os, time

print("BOOT:", time.time())

app = FastAPI()

PUSHOVER_TOKEN = os.getenv("PUSHOVER_TOKEN", "")
PUSHOVER_USER  = os.getenv("PUSHOVER_USER", "")

@app.get("/")
def health():
    return {"status": "ok"}

@app.post("/webhook")
async def webhook(req: Request):
    # 1) Safely parse body
    try:
        data = await req.json()
        parsed_as = "json"
    except Exception:
        raw = (await req.body()).decode("utf-8", errors="replace")
        data = {"raw": raw}
        parsed_as = "raw"

    # 2) Send to Pushover (and capture response)
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
        # Return details so you can see success/failure immediately
        return {
            "status": "ok",
            "parsed_as": parsed_as,
            "pushover_http": resp.status_code,
            "pushover_body": resp.text[:200],
            "token_len": len(PUSHOVER_TOKEN),
            "user_len": len(PUSHOVER_USER),
        }
    except Exception as e:
        return {
            "status": "error",
            "parsed_as": parsed_as,
            "exception": repr(e),
            "token_len": len(PUSHOVER_TOKEN),
            "user_len": len(PUSHOVER_USER),
        }
