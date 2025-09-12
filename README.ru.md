# Collector — SAS Slot Machine Client 🚀

[English](./README.md)

**Что делает этот репозиторий**

Компактный, ориентированный на продакшн Python-агент, который подключает мини-ПК к электронному игровому аппарату (EGM) по протоколу SAS. Выполняет низкоуровневую последовательную (serial) связь, опрашивает счётчики и события, отправляет управляющие команды (AFT/кредиты, джекпот), сохраняет транзакции в MS SQL и принимает защищённые действия через WebSocket. Также есть [pip-пакет](https://github.com/mdedz/sas_comm_py.git), который я сделал!

---

# Кратко 🧾

* **Язык:** Python 3.11+
* **Основные демонстрируемые навыки:** низкоуровневые последовательные интеграции (RS-232 / USB-Serial), двоичные протоколы (BCD, CRC16-Kermit), надёжное взаимодействие с БД (pyodbc + retry/queue), конкурентная и асинхронная интеграция (threads + asyncio/websockets), защищённый обмен сообщениями (HMAC-подпись), производственное логирование и устойчивость.
* **Почему важно:** демонстрирует умение проектировать и эксплуатировать ПО, которое надёжно общается с физическим оборудованием в реальных условиях.

---

# Короткие ключевые моменты ✨

* Низкоуровневая обработка последовательного протокола с wakeup-битом, адресацией и формированием кадра полезной нагрузки.
* Вычисление и проверка CRC16-Kermit.
* Поддерживаемые типы опросов SAS: `R`, `S`, `M` и парсинг метр `2F`.
* `CreditSender` формирует команды AFT с суммами в BCD, флагами, ID транзакции и обработкой срока действия.
* Надёжная запись в БД с JSON-поддержкой очереди при недоступности MS SQL и фоновой повторной отправкой.
* WebSocket-клиент с проверкой полезной нагрузки по HMAC для удалённых команд (например, джекпот).

---

# Архитектура и основные модули 🏗️

* `app.modules.collector` — класс `SlotMachine`: низкоуровневый serial I/O, формирование команд и парсинг ответов.
* `app.modules.collector.credits` — `CreditSender`: формирует и обрабатывает операции по AFT (переводы кредитов).
* `app.modules.db` — `Database`: обёртка над `pyodbc` с логикой переподключения и JSON-поддержкой очереди (`tmp_db_data.json`) для оффлайн-записей.
* `app.modules.network.connection_server` — WebSocket-клиент: принимает подписанные действия и диспатчит их.
* `app.modules.utils.codes` — парсинг счётчиков (например, `2F`) и детекция изменений.
* `main.py` — оркестрация: инициализирует `Collector`, регистрирует слушатели, запускает цикл опроса и WebSocket-клиент.

---

# Как это работает (кратко) 🔍

1. Агент открывает последовательный порт (`pyserial`) с чётностью `EVEN` и заданным baudrate.
2. Команды кодируются как байты: `[wakeup_bit, address, command, ...optional_data, CRC]`.
3. CRC вычисляется по алгоритму CRC16-Kermit (за исключением байта wakeup) и добавляется в кадр.
4. При чтении: синхронизация по байту команды, чтение дополнительного байта длины при необходимости, чтение полезной нагрузки + CRC, проверка CRC и передача распарсенной полезной нагрузки соответствующему обработчику команды.
5. Обработчики (например, `Commands._2f`) извлекают изменившиеся значения счётчиков и вставляют строки в таблицу `gaming_transactions`.

---

# Установка и запуск ▶️

**Требования:** Python 3.11+, система с доступом к COM/serial-устройству, ODBC-драйвер для MS SQL.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Задайте конфигурацию и переменные окружения (см. следующий раздел), затем запустите:

```bash
python -m app.main
# или
python main.py
```

Рекомендация: запускать под `systemd` или `supervisor` на устройстве на границе сети (edge device) и включить ротацию логов.

---

# Конфигурация / пример .env 🔐

Создайте файл `.env` с чувствительными значениями:

```ini
host=DB_HOST
user=DB_USER
password=DB_PASS
database=DB_NAME
API_KEY=your_hmac_api_key
WS_SERVER_URL=wss://example.com/ws
table_name=gaming_transactions
```

`settings.json` (пример):

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

# Типичные сценарии использования / примеры ⚙️

**Добавить кредиты / AFT** — вызовите `CreditSender.send_credits(config)`, где `config` содержит `transfer_type`, `cashable` (в центах), `asset_number`, `transaction_id` (необязательно), флаги вроде `receipt_request` и т.д. См. docstring функции для деталей полей.

**Отправить джекпот** — WebSocket-сервер может отправить подписанную полезную нагрузку, которая вызывает `collector.jackpot(value)`. Агент форматирует джекпот в SAS-кадр типа `S` и отправляет его в режиме ack/nack.

**Собирать счётчики** — настройте `listeners.json` с пунктами `2F` и `length_to_read_per_meter`; коллектор опрашивает и сохраняет изменившиеся счётчики в БД.

**API** — Небольшой пример API для отправки обновления (джекпота):

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

**Демо** — Небольшое демо-видео, показывающее работу:

* [Демо-видео](https://www.youtube.com/watch?v=Y_qeEQYPn6A)

---

# Отказоустойчивость и безопасность 🛡️

* Записи в БД ставятся в очередь в `tmp_db_data.json`, когда БД недоступна, и воспроизводятся после восстановления соединения.
* Фоновый поток переподключения пытается открыть БД и воспроизвести накопленные записи из очереди.
* WebSocket-сообщения проверяются по HMAC с использованием `API_KEY` и защитой от сдвига временных меток (timestamp skew).
* Все чтения из последовательного порта проверяют CRC и при несоответствии выбрасывают явное исключение `WrongCRC`.

---

# Лицензия

* **Лицензия:** Этот проект лицензирован под MIT License.

---

# Обо мне

* 📧 Email: iliaromanovich33@gmail.com  
* 📂 Портфолио (английский): [Resume in English](https://github.com/mdedz/Resume-en)  
* 📂 Портфолио (русский): [Resume in Russian](https://github.com/mdedz/Resume-ru)
