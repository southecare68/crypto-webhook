from fastapi import FastAPI, Request
import requests
import os
import time
print("BOOT:", time.time())

app = FastAPI()

PUSHOVER_TOKEN = os.getenv("PUSHOVER_TOKEN")
PUSHOVER_USER  = os.getenv("PUSHOVER_USER")

@app.post("/webhook")
@app.get("/")
def health():
    return {"status": "ok"}

async def webhook(req: Request):
    data = await req.json()

    requests.post(
        "https://api.pushover.net/1/messages.json",
        data={
            "token": PUSHOVER_TOKEN,
            "user": PUSHOVER_USER,
            "title": "Test Alert",
            "message": str(data)
        },
        timeout=10,
    )

    return {"status": "ok"}
