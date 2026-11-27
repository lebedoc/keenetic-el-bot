import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from telegram import Bot, Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()  # Наприклад: https://el-golovatogo-bot.onrender.com
PORT = int(os.getenv("PORT", "10000"))
EVENT_SECRET = os.getenv("EVENT_SECRET", "").strip()
NOTIFY_CHAT_ID_ENV = os.getenv("NOTIFY_CHAT_ID", "").strip()

STATE_FILE = "state.json"
KYIV_TZ = timezone(timedelta(hours=2))
STATE_HEADER = "⚙️ STATE_DB (не видаляти):"

UA_MONTHS = {
    "січня": 1,
    "лютого": 2,
    "березня": 3,
    "квітня": 4,
    "травня": 5,
    "червня": 6,
    "липня": 7,
    "серпня": 8,
    "вересня": 9,
    "жовтня": 10,
    "листопада": 11,
    "грудня": 12,
}


def load_local_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def save_local_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


async def load_persistent_state(bot: Bot, chat_id: int) -> dict:
    local = load_local_state()
    if local:
        return local

    try:
        chat = await bot.get_chat(chat_id)
        pinned = getattr(chat, "pinned_message", None)
        if pinned and pinned.text and pinned.text.startswith(STATE_HEADER):
            raw_json = pinned.text[len(STATE_HEADER):].strip()
            data = json.loads(raw_json)
            if isinstance(data, dict):
                save_local_state(data)
                return data
    except Exception as e:
        print(f"Не вдалося прочитати стан із закріпленого повідомлення: {e}")

    return {}


async def save_persistent_state(bot: Bot, chat_id: int, state: dict) -> None:
    save_local_state(state)
    payload = f"{STATE_HEADER}\n{json.dumps(state, ensure_ascii=False)}"

    try:
        chat = await bot.get_chat(chat_id)
        pinned = getattr(chat, "pinned_message", None)
        if pinned and pinned.text and pinned.text.startswith(STATE_HEADER):
            if pinned.text != payload:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=pinned.message_id,
                    text=payload,
                )
            return

        sent = await bot.send_message(chat_id=chat_id, text=payload)
        await bot.pin_chat_message(
            chat_id=chat_id,
            message_id=sent.message_id,
            disable_notification=True,
        )
    except Exception as e:
        print(f"Не вдалося зберегти стан у закріплене повідомлення: {e}")


def format_duration(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, rem = divmod(total_seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours} год {minutes:02d} хв"
    if minutes > 0:
        return f"{minutes} хв {secs:02d} сек"
    return f"{secs} сек"


def format_dt(iso_str: str | None) -> str:
    if not iso_str:
        return "немає даних"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%d.%m %H:%M:%S")
    except ValueError:
        return iso_str


def parse_keenetic_message(text: str, fallback_dt: datetime):
    client = None
    status = None

    ua_match = re.search(
        r"[«\"']([^»\"']+)[»\"'].*?\b(підключено|відключено)\b",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if ua_match:
        client = ua_match.group(1).strip()
        raw_status = ua_match.group(2).lower()
        status = "connected" if raw_status == "підключено" else "disconnected"

    if not client or not status:
        en_match = re.search(
            r"Network client\s+['\"]([^'\"]+)['\"].*?\b(connected|disconnected)\b",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if en_match:
            client = en_match.group(1).strip()
            status = en_match.group(2).lower()

    if not client or not status:
        return None

    time_match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?\b", text)
    date_match = re.search(
        r"\b(\d{1,2})\s+(січня|лютого|березня|квітня|травня|червня|липня|серпня|вересня|жовтня|листопада|грудня)\b",
        text,
        re.IGNORECASE,
    )

    event_dt = fallback_dt
    if date_match:
        day = int(date_match.group(1))
        month = UA_MONTHS[date_match.group(2).lower()]
        year = fallback_dt.year
        if fallback_dt.month == 12 and month == 1:
            year += 1
        elif fallback_dt.month == 1 and month == 12:
            year -= 1
        try:
            event_dt = event_dt.replace(year=year, month=month, day=day)
        except ValueError:
            pass

    if time_match:
        hh = int(time_match.group(1))
        mm = int(time_match.group(2))
        ss = int(time_match.group(3)) if time_match.group(3) else 0
        candidate = event_dt.replace(hour=hh, minute=mm, second=ss, microsecond=0)
        if not date_match and candidate > fallback_dt:
            candidate -= timedelta(days=1)
        event_dt = candidate

    return client, status, event_dt


async def process_event_text(bot: Bot, text: str, chat_id: int | None, fallback_dt: datetime):
    parsed = parse_keenetic_message(text, fallback_dt)
    if not parsed:
        return False, "Не вдалося розпізнати формат повідомлення Keenetic."

    target_chat_id = chat_id
    local = load_local_state()
    if not target_chat_id and isinstance(local.get("_meta"), dict):
        target_chat_id = local["_meta"].get("chat_id")
    if not target_chat_id and NOTIFY_CHAT_ID_ENV.lstrip("-").isdigit():
        target_chat_id = int(NOTIFY_CHAT_ID_ENV)

    if not target_chat_id:
        return False, "Невідомий chat_id. Напиши боту /status у чаті, щоб зафіксувати chat_id."

    client, status, event_dt = parsed
    state = await load_persistent_state(bot, target_chat_id)
    meta = state.get("_meta", {}) if isinstance(state.get("_meta"), dict) else {}
    meta["chat_id"] = target_chat_id
    state["_meta"] = meta

    client_state = state.get(client, {}) if isinstance(state.get(client), dict) else {}
    outage_count = int(client_state.get("outage_count", 0))
    total_disc_sec = float(client_state.get("total_disconnected_seconds", 0.0))

    duration_line = ""
    if status == "connected":
        last_disc = client_state.get("last_disconnected")
        if last_disc:
            prev_dt = datetime.fromisoformat(last_disc)
            diff = (event_dt - prev_dt).total_seconds()
            if diff >= 0:
                duration_line = f"\n⏱ Не було: *{format_duration(diff)}*"
                if client_state.get("last_status") == "disconnected":
                    outage_count += 1
                    total_disc_sec += diff
        client_state["last_connected"] = event_dt.isoformat()
        status_label = "🟢 Підключено"
    else:
        last_conn = client_state.get("last_connected")
        if last_conn:
            prev_dt = datetime.fromisoformat(last_conn)
            diff = (event_dt - prev_dt).total_seconds()
            if diff >= 0:
                duration_line = f"\n⏱ Було підключено: *{format_duration(diff)}*"
        client_state["last_disconnected"] = event_dt.isoformat()
        status_label = "🔴 Відключено"

    client_state["last_status"] = status
    client_state["last_change"] = event_dt.isoformat()
    client_state["outage_count"] = outage_count
    client_state["total_disconnected_seconds"] = total_disc_sec

    state[client] = client_state
    await save_persistent_state(bot, target_chat_id, state)

    reply = (
        f"📌 *{client}*\n"
        f"{status_label}\n"
        f"🕒 Час: `{event_dt.strftime('%d.%m %H:%M:%S')}`"
        f"{duration_line}"
    )
    await bot.send_message(chat_id=target_chat_id, text=reply, parse_mode="Markdown")
    return True, "OK"


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return

    state = await load_persistent_state(context.bot, msg.chat_id)
    meta = state.get("_meta", {}) if isinstance(state.get("_meta"), dict) else {}
    if meta.get("chat_id") != msg.chat_id:
        meta["chat_id"] = msg.chat_id
        state["_meta"] = meta
        await save_persistent_state(context.bot, msg.chat_id, state)

    clients = {k: v for k, v in state.items() if k != "_meta" and isinstance(v, dict)}
    if not clients:
        await msg.reply_text("Поки що немає збережених даних про клієнтів.")
        return

    now_local = datetime.now(KYIV_TZ).replace(tzinfo=None)
    blocks = ["📊 *Поточний стан клієнтів:*"]

    for client, info in clients.items():
        current_status = info.get("last_status", "unknown")
        last_change = info.get("last_change")
        outage_count = int(info.get("outage_count", 0))
        total_disc_sec = float(info.get("total_disconnected_seconds", 0.0))

        if current_status == "connected":
            badge = "🟢 Підключено"
        elif current_status == "disconnected":
            badge = "🔴 Відключено"
        else:
            badge = f"⚪ Невідомо (`{current_status}`)"

        lines = [
            f"\n📌 *{client}*",
            f"Статус: {badge}",
            f"🕒 Остання зміна: `{format_dt(last_change)}`",
            f"🔢 Відключень зафіксовано: *{outage_count}*",
            f"⏳ Накопичено без зв'язку: *{format_duration(total_disc_sec)}*",
        ]

        if current_status == "disconnected" and info.get("last_disconnected"):
            disc_dt = datetime.fromisoformat(info["last_disconnected"])
            active_outage = (now_local - disc_dt).total_seconds()
            if active_outage >= 0:
                lines.append(f"⏱ Триває зараз: *{format_duration(active_outage)}*")

        blocks.append("\n".join(lines))

    await msg.reply_text("\n".join(blocks), parse_mode="Markdown")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text:
        return

    source_dt = (
        msg.forward_origin.date
        if getattr(msg, "forward_origin", None) and getattr(msg.forward_origin, "date", None)
        else msg.date
    )
    local_dt = (
        source_dt.astimezone(KYIV_TZ).replace(tzinfo=None)
        if source_dt
        else datetime.now(KYIV_TZ).replace(tzinfo=None)
    )

    ok, err_text = await process_event_text(context.bot, msg.text, msg.chat_id, local_dt)
    if not ok:
        await msg.reply_text(err_text)


def _http_response(status_code: int, body: str, content_type: str = "text/plain; charset=utf-8") -> bytes:
    reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found"}.get(status_code, "OK")
    raw = body.encode("utf-8")
    return (
        f"HTTP/1.1 {status_code} {reason}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(raw)}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode("ascii") + raw


async def _serve_http(app):
    webhook_path = f"/{BOT_TOKEN}"

    async def handle_conn(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            req_line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not req_line:
                return
            parts = req_line.decode("latin-1", errors="ignore").strip().split()
            method = parts[0] if len(parts) >= 1 else "GET"
            path = parts[1].split("?", 1)[0] if len(parts) >= 2 else "/"

            headers = {}
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=10)
                if not line or line == b"\r\n":
                    break
                decoded = line.decode("latin-1", errors="ignore")
                if ":" in decoded:
                    k, v = decoded.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            content_len = int(headers.get("content-length", "0") or "0")
            body_bytes = b""
            if content_len > 0:
                body_bytes = await asyncio.wait_for(reader.readexactly(content_len), timeout=15)

            if path == webhook_path and method == "POST":
                data = json.loads(body_bytes.decode("utf-8"))
                update = Update.de_json(data, app.bot)
                await app.update_queue.put(update)
                writer.write(_http_response(200, "OK"))
            elif path == "/event" and method == "POST":
                if EVENT_SECRET and headers.get("x-event-secret", "") != EVENT_SECRET:
                    writer.write(_http_response(401, "Unauthorized"))
                else:
                    payload = json.loads(body_bytes.decode("utf-8"))
                    text = (payload.get("text") or "").strip()
                    if not text:
                        writer.write(_http_response(400, "Missing text"))
                    else:
                        ts_raw = payload.get("timestamp")
                        if ts_raw:
                            dt = datetime.fromisoformat(ts_raw).astimezone(KYIV_TZ).replace(tzinfo=None)
                        else:
                            dt = datetime.now(KYIV_TZ).replace(tzinfo=None)
                        ok, msg_res = await process_event_text(app.bot, text, None, dt)
                        code = 200 if ok else 400
                        writer.write(_http_response(code, msg_res))
            else:
                writer.write(_http_response(200, "OK"))

            await writer.drain()
        except Exception as e:
            print(f"HTTP помилка: {e}")
            try:
                writer.write(_http_response(200, "OK"))
                await writer.drain()
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(handle_conn, "0.0.0.0", PORT)
    print(f"Сервер запущено на порту {PORT}")
    async with server:
        await server.serve_forever()


async def run_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    webhook_path = f"/{BOT_TOKEN}"
    full_webhook_url = f"{WEBHOOK_URL.rstrip('/')}{webhook_path}"

    await app.initialize()
    await app.start()
    await app.bot.set_webhook(url=full_webhook_url)
    try:
        await _serve_http(app)
    finally:
        await app.stop()
        await app.shutdown()


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задано змінну оточення BOT_TOKEN")
    if not WEBHOOK_URL:
        raise RuntimeError("Не задано змінну оточення WEBHOOK_URL")
    asyncio.run(run_app())


if __name__ == "__main__":
    main()
