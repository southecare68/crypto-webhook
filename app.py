from fastapi import FastAPI, Request
import requests, os, sqlite3, datetime
from fastapi.responses import HTMLResponse

app = FastAPI()

DB_PATH = "trades.db"
START_EQUITY = 5000
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "200"))

PUSHOVER_TOKEN = os.getenv("PUSHOVER_TOKEN", "")
PUSHOVER_USER  = os.getenv("PUSHOVER_USER", "")

# --- DB Setup ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            entry REAL,
            stop REAL,
            size REAL,
            r_value REAL,
            pnl REAL,
            timestamp TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def push(title, message):
    if not PUSHOVER_TOKEN or not PUSHOVER_USER:
        return
    requests.post(
        "https://api.pushover.net/1/messages.json",
        data={
            "token": PUSHOVER_TOKEN,
            "user": PUSHOVER_USER,
            "title": title,
            "message": message
        },
        timeout=10
    )

@app.get("/")
def health():
    return {"status": "ok"}

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    t = data.get("type")

    if t == "SAT_ENTRY":
        symbol = data["symbol"]
        entry = float(data["entry"])
        stop = float(data["stop"])
        r = entry - stop

        size = RISK_PER_TRADE / r
        pnl = 0  # entry only for now

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO trades (symbol, entry, stop, size, r_value, pnl, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol,
            entry,
            stop,
            size,
            r,
            pnl,
            datetime.datetime.utcnow().isoformat()
        ))
        conn.commit()
        conn.close()

        push("SAT_ENTRY Logged", f"{symbol} entry {entry} stop {stop}")

    return {"status": "ok"}

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT symbol, entry, stop, size, r_value, pnl, timestamp FROM trades")
    rows = c.fetchall()
    conn.close()

    total_pnl = sum(row[5] for row in rows)
    equity = START_EQUITY + total_pnl

    html = f"""
    <h1>Trading Dashboard</h1>
    <p>Start Equity: ${START_EQUITY}</p>
    <p>Current Equity: ${equity:.2f}</p>
    <p>Total PnL: ${total_pnl:.2f}</p>
    <h2>Trades</h2>
    <table border="1">
        <tr>
            <th>Symbol</th><th>Entry</th><th>Stop</th>
            <th>Size</th><th>R</th><th>PnL</th><th>Timestamp</th>
        </tr>
    """

    for row in rows:
        html += "<tr>" + "".join(f"<td>{col}</td>" for col in row) + "</tr>"

    html += "</table>"
    return html
