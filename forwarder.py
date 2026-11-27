import asyncio
import os
import re
from datetime import timezone
import httpx
from telethon import TelegramClient, events
from telethon.sessions import StringSession

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
TELETHON_SESSION = os.getenv("TELETHON_SESSION", "").strip()
PORT = int(os.getenv("PORT", "10000"))

KEENETIC_BOT = os.getenv("KEENETIC_BOT", "KeeneticBot").strip()
CLIENT_FILTER = os.getenv("CLIENT_FILTER", "Електроенергія Бойлер").strip()
BOT_EVENT_URL = os.getenv("BOT_EVENT_URL", "").strip()  # Наприклад: https://el-golovatogo-bot.onrender.com/event
EVENT_SECRET = os.getenv("EVENT_SECRET", "").strip()

EVENT_PATTERN = re.compile(
    r"\b(підключено|відключено|connected|disconnected)\b",
    re.IGNORECASE,
)


def is_client_event(text: str) -> bool:
    if not text:
        return False
    if CLIENT_FILTER and CLIENT_FILTER.lower() not in text.lower():
        return False
    return bool(EVENT_PATTERN.search(text))


async def _handle_http_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        request_line = await asyncio.wait_for(reader.readline(), timeout=5)
        if request_line:
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=5)
                if not line or line == b"\r\n":
                    break
            body = b"OK"
            response = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain; charset=utf-8\r\n"
                + f"Content-Length: {len(body)}\r\n".encode("ascii")
                + b"Connection: close\r\n\r\n"
                + body
            )
            writer.write(response)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def _start_http_server():
    server = await asyncio.start_server(_handle_http_client, "0.0.0.0", PORT)
    print(f"HTTP health-сервер слухає порт {PORT}")
    async with server:
        await server.serve_forever()


async def _start_telethon_forwarder():
    if not (API_ID and API_HASH and TELETHON_SESSION):
        raise RuntimeError("Не задано API_ID, API_HASH або TELETHON_SESSION")
    if not BOT_EVENT_URL:
        raise RuntimeError("Не задано BOT_EVENT_URL")

    client = TelegramClient(StringSession(TELETHON_SESSION), API_ID, API_HASH)
    await client.start()

    keenetic_peer = int(KEENETIC_BOT) if KEENETIC_BOT.lstrip("-").isdigit() else KEENETIC_BOT
    print(f"Автопересилка по HTTP запущена: '{keenetic_peer}' -> '{BOT_EVENT_URL}'")

    headers = {"Content-Type": "application/json"}
    if EVENT_SECRET:
        headers["X-Event-Secret"] = EVENT_SECRET

    @client.on(events.NewMessage(chats=keenetic_peer))
    async def on_keenetic_message(event):
        text = event.raw_text or ""
        if not is_client_event(text):
            return

        msg_dt = event.message.date
        ts_iso = msg_dt.astimezone(timezone.utc).isoformat() if msg_dt else None

        async with httpx.AsyncClient(timeout=15.0) as http_client:
            resp = await http_client.post(
                BOT_EVENT_URL,
                json={"text": text, "timestamp": ts_iso},
                headers=headers,
            )
            resp.raise_for_status()

        first_line = text.splitlines()[0] if text.splitlines() else ""
        print(f"Відправлено HTTP-подію: {first_line}")

    await client.run_until_disconnected()


async def main():
    await asyncio.gather(
        _start_http_server(),
        _start_telethon_forwarder(),
    )


if __name__ == "__main__":
    asyncio.run(main())
