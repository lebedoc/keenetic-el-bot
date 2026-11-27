import asyncio
import os
import re
from telethon import TelegramClient, events
from telethon.sessions import StringSession

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
TELETHON_SESSION = os.getenv("TELETHON_SESSION", "").strip()
PORT = int(os.getenv("PORT", "10000"))

KEENETIC_BOT = os.getenv("KEENETIC_BOT", "KeeneticBot").strip()
TARGET_BOT = os.getenv("TARGET_BOT", "el_Golovatogo_bot").strip()
CLIENT_FILTER = os.getenv("CLIENT_FILTER", "Електроенергія Бойлер").strip()

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

    client = TelegramClient(StringSession(TELETHON_SESSION), API_ID, API_HASH)
    await client.start()

    keenetic_peer = int(KEENETIC_BOT) if KEENETIC_BOT.lstrip("-").isdigit() else KEENETIC_BOT
    target_peer = int(TARGET_BOT) if TARGET_BOT.lstrip("-").isdigit() else TARGET_BOT

    target_entity = await client.get_entity(target_peer)
    print(f"Автопересилка запущена: '{keenetic_peer}' -> '{target_peer}'")

    @client.on(events.NewMessage(chats=keenetic_peer))
    async def on_keenetic_message(event):
        text = event.raw_text or ""
        if not is_client_event(text):
            return
        await client.send_message(target_entity, text)
        first_line = text.splitlines()[0] if text.splitlines() else ""
        print(f"Надіслано: {first_line}")

    await client.run_until_disconnected()


async def main():
    await asyncio.gather(
        _start_http_server(),
        _start_telethon_forwarder(),
    )


if __name__ == "__main__":
    asyncio.run(main())
