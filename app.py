from fastapi import FastAPI, Request
import requests, os, sqlite3, datetime
from fastapi.responses import HTMLResponse

app = FastAPI()

# Use a persistent disk on Render and set this to /var/data/trades.db
DB_PATH = os.getenv("DB_PATH", "/var/data/trades.db")

START_EQUITY = float(os.getenv("START_EQUITY", "5000"))
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "200"))

PUSHOVER_TOKEN = os.getenv("PUSHOVER_TOKEN", "")
PUSHOVER_USER  = os.getenv("PUSHOVER_USER", "")

def db():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = db()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            symbol TEXT,
            tf TEXT,
            entry REAL,
            stop REAL,
            r_per_unit REAL,
            size REAL,
            status TEXT,           -- OPEN / PARTIAL / CLOSED
            entry_ts TEXT,
            notes TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT,
            side TEXT,             -- BUY / SELL
            qty REAL,
            price REAL,
            fee REAL,
            ts TEXT,
            FOREIGN KEY(trade_id) REFERENCES trades(trade_id)
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
        data={"token": PUSHOVER_TOKEN, "user": PUSHOVER_USER, "title": title[:250], "message": message[:1000]},
        timeout=10
    )

def now_iso():
    return datetime.datetime.utcnow().isoformat()

def compute_trade_pnl(conn, trade_id: str):
    """
    PnL = sum(Sell qty*price) - sum(Buy qty*price) - sum(fees)
    Also returns net position qty (buys - sells).
    """
    c = conn.cursor()
    c.execute("SELECT side, qty, price, COALESCE(fee,0) FROM fills WHERE trade_id=?", (trade_id,))
    rows = c.fetchall()
    buy_value = sell_value = fees = 0.0
    pos_qty = 0.0
    for side, qty, price, fee in rows:
        qty = float(qty); price = float(price); fee = float(fee)
        fees += fee
        if side == "BUY":
            buy_value += qty * price
            pos_qty += qty
        else:
            sell_value += qty * price
            pos_qty -= qty
    pnl = sell_value - buy_value - fees
    return pnl, pos_qty

@app.get("/")
def health():
    return {"status": "ok"}

@app.post("/webhook")
async def webhook(req: Request):
    data = await req.json()
    t = str(data.get("type","")).upper()

    if t == "SAT_ENTRY":
        trade_id = data.get("trade_id") or f"{data.get('symbol','UNK')}-{data.get('tf','?')}-{data.get('ts','')}"
        symbol = str(data["symbol"])
        tf = str(data.get("tf",""))
        entry = float(data.get("entry", data.get("price")))
        stop  = float(data["stop"])
        r_per_unit = entry - stop
        if r_per_unit <= 0:
            push("SAT_ENTRY error", f"{symbol} invalid R (entry<=stop)\n{data}")
            return {"status":"ok"}

        # size based on risk; you can cap notional in Pine/webhook later if desired
        size = RISK_PER_TRADE / r_per_unit

        conn = db()
        c = conn.cursor()
        c.execute("""
            INSERT OR REPLACE INTO trades (trade_id, symbol, tf, entry, stop, r_per_unit, size, status, entry_ts, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (trade_id, symbol, tf, entry, stop, r_per_unit, size, "OPEN", now_iso(), ""))

        # Log a synthetic BUY fill at entry (fee left 0 unless you want to estimate)
        c.execute("""
            INSERT INTO fills (trade_id, side, qty, price, fee, ts)
            VALUES (?, 'BUY', ?, ?, ?, ?)
        """, (trade_id, size, entry, 0.0, now_iso()))

        conn.commit()
        conn.close()

        push("SAT_ENTRY logged", f"{symbol} {tf}\nID {trade_id}\nEntry {entry:.4f}\nStop {stop:.4f}\nSize {size:.4f}")
        return {"status":"ok"}

    if t in {"SAT_TP1", "SAT_EXIT"}:
        trade_id = str(data["trade_id"])
        exit_price = float(data["exit_price"])

        conn = db()
        c = conn.cursor()
        c.execute("SELECT size, symbol, tf, status FROM trades WHERE trade_id=?", (trade_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            push("Exit error", f"Unknown trade_id {trade_id}")
            return {"status":"ok"}

        size, symbol, tf, status = float(row[0]), str(row[1]), str(row[2]), str(row[3])

        # TP1 sells half, EXIT sells remaining (based on current net position)
        pnl_before, pos_qty = compute_trade_pnl(conn, trade_id)
        if pos_qty <= 0:
            conn.close()
            push("Exit error", f"{symbol} {trade_id}\nNo open position qty to sell.")
            return {"status":"ok"}

        if t == "SAT_TP1":
            sell_qty = pos_qty * 0.5
            new_status = "PARTIAL"
        else:
            sell_qty = pos_qty
            new_status = "CLOSED"

        c.execute("""
            INSERT INTO fills (trade_id, side, qty, price, fee, ts)
            VALUES (?, 'SELL', ?, ?, ?, ?)
        """, (trade_id, sell_qty, exit_price, 0.0, now_iso()))

        c.execute("UPDATE trades SET status=? WHERE trade_id=?", (new_status, trade_id))

        conn.commit()

        # compute updated pnl + R
        pnl_after, pos_qty_after = compute_trade_pnl(conn, trade_id)
        c.execute("SELECT r_per_unit FROM trades WHERE trade_id=?", (trade_id,))
        r_per_unit = float(c.fetchone()[0])

        # Normalize R on original risk: (RISK_PER_TRADE) is based on size*r_per_unit originally.
        # Since we sized as RISK_PER_TRADE / r_per_unit, original risk ~= RISK_PER_TRADE.
        r_mult = pnl_after / RISK_PER_TRADE

        conn.close()

        push(
            f"{t} • {symbol}",
            f"{symbol} {tf}\nID {trade_id}\nExit {exit_price:.4f} qty {sell_qty:.4f}\nPnL ${pnl_after:,.2f}  R {r_mult:.2f}\nStatus {new_status}"
        )
        return {"status":"ok"}

    # CORE alerts passthrough
    if t in {"CORE_ON", "CORE_OFF"}:
        push(f"{t} • {data.get('symbol','BTC')}", str(data)[:900])
        return {"status":"ok"}

    push(f"{t} • {data.get('symbol','UNK')}", str(data)[:900])
    return {"status":"ok"}

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    conn = db()
    c = conn.cursor()

    c.execute("SELECT trade_id, symbol, tf, entry, stop, size, status, entry_ts FROM trades ORDER BY entry_ts DESC")
    trades = c.fetchall()

    # Compute closed-trade stats
    pnls = []
    rs = []
    for trade_id, *_ in trades:
        pnl, pos_qty = compute_trade_pnl(conn, trade_id)
        # consider only CLOSED trades for stats
        c.execute("SELECT status FROM trades WHERE trade_id=?", (trade_id,))
        st = c.fetchone()[0]
        if st == "CLOSED":
            pnls.append(pnl)
            rs.append(pnl / RISK_PER_TRADE)

    total_pnl = sum(pnls)
    equity = START_EQUITY + total_pnl
    n_closed = len(pnls)
    avg_r = (sum(rs) / n_closed) if n_closed else 0.0
    win_rate = (sum(1 for x in rs if x > 0) / n_closed) if n_closed else 0.0

    conn.close()

    html = f"""
    <h1>Trading Dashboard</h1>
    <p><b>Start Equity:</b> ${START_EQUITY:,.2f}</p>
    <p><b>Closed-trade Equity:</b> ${equity:,.2f} (PnL ${total_pnl:,.2f})</p>
    <p><b>Closed trades:</b> {n_closed} &nbsp; <b>Win rate:</b> {win_rate:.1%} &nbsp; <b>Avg R/trade:</b> {avg_r:.2f}</p>

    <h2>Trades</h2>
    <table border="1" cellpadding="6" cellspacing="0">
        <tr>
            <th>Trade ID</th><th>Symbol</th><th>TF</th><th>Entry</th><th>Stop</th><th>Size</th><th>Status</th><th>Entry TS</th>
        </tr>
    """
    for row in trades:
        trade_id, symbol, tf, entry, stop, size, status, entry_ts = row
        html += f"<tr><td>{trade_id}</td><td>{symbol}</td><td>{tf}</td><td>{entry:.4f}</td><td>{stop:.4f}</td><td>{size:.4f}</td><td>{status}</td><td>{entry_ts}</td></tr>"
    html += "</table>"
    return html

