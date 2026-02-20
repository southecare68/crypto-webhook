from fastapi import FastAPI, Request
import requests, os, time, math

print("BOOT:", time.time())

app = FastAPI()

PUSHOVER_TOKEN = os.getenv("PUSHOVER_TOKEN", "")
PUSHOVER_USER  = os.getenv("PUSHOVER_USER", "")

# --- Trading parameters (edit these)
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "200"))
MAX_NOTIONAL   = float(os.getenv("MAX_NOTIONAL", "1500"))
TAKER_FEE_RATE = float(os.getenv("TAKER_FEE_RATE", "0.006"))  # 0.6% default estimate

def fmt(x, d=4):
    try:
        return f"{float(x):.{d}f}"
    except Exception:
        return str(x)

def push(title: str, message: str):
    if not PUSHOVER_TOKEN or not PUSHOVER_USER:
        # If env vars missing, at least don't crash
        print("Missing PUSHOVER env vars. token_len/user_len:", len(PUSHOVER_TOKEN), len(PUSHOVER_USER))
        return

    requests.post(
        "https://api.pushover.net/1/messages.json",
        data={
            "token": PUSHOVER_TOKEN,
            "user": PUSHOVER_USER,
            "title": title[:250],
            "message": message[:1000],
        },
        timeout=10,
    )

@app.get("/")
def health():
    return {"status": "ok"}

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    t = str(data.get("type", "UNKNOWN")).upper()
    symbol = str(data.get("symbol", "UNKNOWN"))

    # ----- CORE alerts (pass-through)
    if t in {"CORE_ON", "CORE_OFF"}:
        title = f"{t} • {symbol}"
        msg = f"tf={data.get('tf','?')}  price={data.get('price','?')}\n{data.get('ts','')}"
        push(title, msg)
        return {"status": "ok"}

    # ----- SAT entry sizing alerts
    if t == "SAT_ENTRY":
        # Expect these fields from TradingView JSON
        entry = data.get("entry", data.get("price"))
        limit_price = data.get("limit_price", entry)
        stop = data.get("stop")
        r = data.get("r")  # risk per coin

        # Defensive parsing
        try:
            entry_f = float(entry)
            limit_f = float(limit_price)
        except Exception:
            push(f"SAT_ENTRY • {symbol}", f"Bad entry/limit in payload:\n{data}")
            return {"status": "ok"}

        try:
            stop_f = float(stop)
        except Exception:
            stop_f = None

        # If r wasn't provided, compute it if we have stop
        try:
            r_f = float(r) if r is not None else (entry_f - stop_f if stop_f is not None else None)
        except Exception:
            r_f = None

        if stop_f is None or r_f is None or r_f <= 0:
            push(f"SAT_ENTRY • {symbol}", f"Missing/invalid stop or R.\nentry={entry}\nstop={stop}\nr={r}")
            return {"status": "ok"}

        # Risk-based max size
        size_by_risk = RISK_PER_TRADE / r_f
        notional_by_risk = size_by_risk * entry_f

        # Cap by max notional
        if notional_by_risk > MAX_NOTIONAL:
            size = MAX_NOTIONAL / entry_f
            capped = True
        else:
            size = size_by_risk
            capped = False

        notional = size * entry_f

        # Fee estimate (taker) on entry and hypothetical full exit (2 sides)
        est_fees_roundtrip = notional * TAKER_FEE_RATE * 2

        title = f"SAT_ENTRY • {symbol}"
        msg_lines = [
            f"TF {data.get('tf','?')}  price {fmt(data.get('price','?'),2)}",
            f"LIMIT {fmt(limit_f,2)}  ENTRY {fmt(entry_f,2)}",
            f"STOP  {fmt(stop_f,2)}  R/coin {fmt(r_f,4)}",
            f"Risk$ {fmt(RISK_PER_TRADE,0)}  Size {fmt(size,4)}  Notional ${fmt(notional,0)}" + (" (CAPPED)" if capped else ""),
            f"Est fees (rt) ~${fmt(est_fees_roundtrip,2)} @ {fmt(TAKER_FEE_RATE*100,2)}% taker",
        ]
        push(title, "\n".join(msg_lines))
        return {"status": "ok"}

    # ----- Default: pass-through for anything else
    push(f"{t} • {symbol}", str(data)[:900])
    return {"status": "ok"}
