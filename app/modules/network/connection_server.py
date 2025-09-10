import asyncio
import websockets
import logging
import os
import json
import time
import hmac
import sys
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
load_dotenv()
SIGNATURE_SKEW = int(os.getenv("WS_SIGNATURE_SKEW", 60))
API_KEY = os.getenv("API_KEY")

def dispatch_action(payload: dict, collector):
    action = payload.get("action", "")
    data = payload.get("data", "")
    if action == "jackpot":
        collector.jackpot(data.get("value"))
        return {"message": "Success", "status": 200}

def verify_signature(payload: dict, signature: str, timestamp: str) -> bool:
    ts = int(timestamp)
    now = int(time.time())
    if abs(now - ts) > SIGNATURE_SKEW:
        logging.warning("Timestamp skew too large")
        return False
    payload_text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    msg = f"{timestamp}{payload_text}".encode()
    mac = hmac.new(API_KEY.encode(), msg, digestmod="sha256").hexdigest()
    return hmac.compare_digest(mac, signature)

async def client(collector):
    uri = os.getenv("WS_SERVER_URL")

    async with websockets.connect(uri) as ws:
        while True:
            message = await ws.recv()
            logging.info(message)
            
            data = json.loads(message)
            signature = data.get("signature")
            timestamp = data.get("timestamp")
            payload = data.get("payload")
            status_code = 200
            
            if verify_signature(payload, signature, timestamp):
                result = dispatch_action(payload, collector)    
                if isinstance(result, dict) and isinstance(result.get("status"), int):
                    status_code = result.pop("status")
            else:
                status_code = 404
                result = {"message": "Incorrect signature"}

            response = {
                "status": status_code,
                "result": result,
                "payload": payload,
                "signature": signature,
                "timestamp": timestamp,
            }    
            

