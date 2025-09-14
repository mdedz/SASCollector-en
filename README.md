# Collector — SAS Slot Machine Client 🚀

[Русский](./README.ru.md)

**What this repo does**

A compact, production-focused Python agent that connects a mini-PC to an Electronic Gaming Machine (EGM) over the SAS protocol. It performs low-level serial communication, polls meters and events, sends control commands (AFT/credits, jackpot), persists transactions to MS SQL and receives secure actions via WebSocket.There is also [robust pip package](https://github.com/mdedz/sas_comm_py.git) I made! 

---

# TL;DR 🧾

* **Language:** Python 3.11+
* **Primary skills demonstrated:** low-level serial integrations (RS-232 / USB-Serial), binary protocols (BCD, CRC16-Kermit), robust DB interaction (pyodbc + retry/queue), concurrent and async integration (threads + asyncio/websockets), secure messaging (HMAC signature), production logging and resilience.
* **Why it matters:** shows ability to design and operate software that talks to physical hardware reliably under real-world constraints.

---

# Quick highlights ✨

* Raw serial protocol handling with wakeup bit, addressing and payload framing.
* CRC16-Kermit calculation and validation.
* SAS poll types supported: `R`, `S`, `M` and `2F`-style meters parsing.
* `CreditSender` builds AFT (Automated Funds Transfer) commands with BCD amounts, flags, transaction IDs and expiration handling.
* Reliable DB writes with JSON-backed queue when MS SQL is unreachable and background reconnection.
* WebSocket client with HMAC-signed payload verification for remote commands (e.g. jackpot).

---

# Architecture & main modules 🏗️

* `app.modules.collector` — `SlotMachine` class: low-level serial I/O, command framing and response parsing.
* `app.modules.collector.credits` — `CreditSender`: constructs and handles AFT credit operations.
* `app.modules.db` — `Database`: `pyodbc` wrapper with reconnect logic and JSON-backed queue (`tmp_db_data.json`) for offline writes.
* `app.modules.network.connection_server` — WebSocket client: receives signed actions and dispatches them.
* `app.modules.utils.codes` — meter parsing (e.g., `2F`) and change detection.
* `main.py` — orchestration: bootstraps `Collector`, registers listeners, runs the capture loop and WebSocket client.

---

# How it works (brief) 🔍

1. The agent opens a serial port (`pyserial`) with `EVEN` parity and configured baudrate.
2. Commands are encoded as bytes: `[wakeup_bit, address, command, ...optional_data, CRC]`.
3. CRC is calculated with CRC16-Kermit (excluding wakeup byte) and appended to the frame.
4. On read: sync to command byte, read optional ‘length’ byte when required, read payload + CRC, validate CRC and hand parsed payload to the configured command handler.
5. Handlers (e.g. `Commands._2f`) extract changed meter values and insert rows in `gaming_transactions` table.

---

# Install & run ▶️

**Prereqs:** Python 3.11+, system with access to the COM/serial device, ODBC driver for MS SQL.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set configuration and environment (see next section), then run:

```bash
python -m app.main
# or
python main.py
```

Recommended: run under `systemd` or `supervisor` on the edge device and enable log rotation.

---

# Config / .env example 🔐

Create a `.env` file with sensitive values:

```ini
host=DB_HOST
user=DB_USER
password=DB_PASS
database=DB_NAME
API_KEY=your_hmac_api_key
WS_SERVER_URL=wss://example.com/ws
table_name=gaming_transactions
```

`settings.json` (example):

```json
{
  "db_driver": "{ODBC Driver 17 for SQL Server}",
  "com_port": "/dev/ttyUSB0",
  "baudrate": "19200",
  "address": "1",
  "wakeup_bit": "128"
}
```

---

# Common use cases / examples ⚙️

**Add credits / AFT** — call `CreditSender.send_credits(config)` where `config` contains `transfer_type`, `cashable` (in cents), `asset_number`, `transaction_id` (optional), flags like `receipt_request`, etc. See function docstring for field details.

**Send jackpot** — the WebSocket server can send a signed payload which triggers `collector.jackpot(value)`. The agent formats the jackpot into the SAS `S` frame and sends it using `ack/nack` mode.

**Collect meters** — Configure `listeners.json` with `2F` and `length_to_read_per_meter` entries; the collector polls and persists changed meters to the DB.

**API** — Here is small api how to call jackpot(example):

```python
#api.main
from fastapi import FastAPI, WebSocket
from pydantic import BaseModel
from api.utils.websocket import ConnectionManager
import asyncio
import logging


app = FastAPI()
manager = ConnectionManager()

class UpdatePayload(BaseModel):
    action: str
    data: dict

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    while True:
        await manager.connect(websocket, client_id)
        logging.info("connection open")
        while True:
            data = await websocket.receive_text()

@app.post("/send_update/")
async def send_update(payload: UpdatePayload):
    await manager.broadcast(payload.dict())
    return {"message": "Update sent to all clients"}


#api.utils.websocket
import json
import time
import hmac
import hashlib
import os
from dotenv import load_dotenv
from fastapi import WebSocket


load_dotenv()
API_KEY = os.getenv("API_KEY")

def sign_payload(payload: dict):
    timestamp = str(int(time.time()))
    message = timestamp + json.dumps(payload, separators=(",", ":"), sort_keys=True)
    signature = hmac.new(API_KEY.encode(), message.encode(), hashlib.sha256).hexdigest()
    return signature, timestamp

class ConnectionManager:
    def __init__(self):
        self.active_connections = {} 

    async def connect(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        self.active_connections[client_id] = websocket

    def disconnect(self, client_id: str):
        self.active_connections.pop(client_id, None)

    async def send(self, payload: dict, websocket: WebSocket):
        signature, timestamp = sign_payload(payload)
        message = json.dumps({
            "payload": payload,
            "signature": signature,
            "timestamp": timestamp
        })
        await websocket.send_text(message)

    async def broadcast(self, payload: dict):
        for ws in self.active_connections.values():
            await self.send(payload, ws)
```

**Demo** — Here is a small demo video that shows how it works:

* [Demo-video](https://www.youtube.com/watch?v=Y_qeEQYPn6A)

---

# Fault tolerance & security 🛡️

* Writes to DB are queued into `tmp_db_data.json` when the DB is unavailable and replayed once reconnected.
* Background reconnection thread attempts to reopen DB and replays queued writes.
* WebSocket messages are verified with HMAC using `API_KEY` and timestamp skew protection.
* All serial reads validate CRC and raise explicit `WrongCRC` on mismatch.

---

# License

* **License:** This project is licensed under the MIT License.

---

# About Me

* 📧 Email: iliaromanovich33@gmail.com
---
