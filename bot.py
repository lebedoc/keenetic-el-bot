import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Наприклад: https://el-golovatogo-bot.onrender.com
PORT = int(os.getenv("PORT", "10000"))
STATE_FILE = "state.json"

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


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def format_duration(seconds: float) -> str:
    total_minutes = max(0, int(seconds // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours > 0:
        return f"{hours} год {minutes:02d} хв"
    return f"{minutes} хв"


def parse_keenetic_message(text: str, fallback_dt: datetime):
    client_match = re.search(r"Network client\s+'([^']+)'", text)
    status_match = re.search(r"\b(connected|disconnected)\b", text, re.IGNORECASE)
    time_match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    date_match = re.search(
        r"\b(\d{1,2})\s+(січня|лютого|березня|квітня|травня|червня|липня|серпня|вересня|жовтня|листопада|грудня)\b",
        text,
        re.IGNORECASE,
    )

    if not client_match or not status_match:
        return None

    client = client_match.group(1).strip()
    status = status_match.group(1).lower()

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
        hh, mm = int(time_match.group(1)), int(time_match.group(2))
        candidate = event_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if not date_match and candidate > fallback_dt:
            candidate -= timedelta(days=1)
        event_dt = candidate

    return client, status, event_dt


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text:
        return

    msg_time = msg.date.astimezone() if msg.date else datetime.now().astimezone()
    parsed = parse_keenetic_message(msg.text, msg_time.replace(tzinfo=None))

    if not parsed:
        await msg.reply_text("Не вдалося розпізнати формат повідомлення Keenetic.")
        return

    client, status, event_dt = parsed
    state = load_state()
    client_state = state.get(client, {})

    duration_line = ""
    if status == "connected":
        last_disc = client_state.get("last_disconnected")
        if last_disc:
            prev_dt = datetime.fromisoformat(last_disc)
            diff = (event_dt - prev_dt).total_seconds()
            if diff >= 0:
                duration_line = f"\n⏱ Не було: *{format_duration(diff)}*"
        client_state["last_connected"] = event_dt.isoformat()
        status_label = "🟢 Підключено (`connected`)"
    else:
        last_conn = client_state.get("last_connected")
        if last_conn:
            prev_dt = datetime.fromisoformat(last_conn)
            diff = (event_dt - prev_dt).total_seconds()
            if diff >= 0:
                duration_line = f"\n⏱ Було підключено: *{format_duration(diff)}*"
        client_state["last_disconnected"] = event_dt.isoformat()
        status_label = "🔴 Відключено (`disconnected`)"

    state[client] = client_state
    save_state(state)

    reply = (
        f"📌 *{client}*\n"
        f"{status_label}\n"
        f"🕒 Час: `{event_dt.strftime('%d.%m %H:%M')}`"
        f"{duration_line}"
    )
    await msg.reply_text(reply, parse_mode="Markdown")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не задано змінну оточення BOT_TOKEN")
    if not WEBHOOK_URL:
        raise RuntimeError("Не задано змінну оточення WEBHOOK_URL")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    webhook_path = f"/{BOT_TOKEN}"
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=webhook_path.lstrip("/"),
        webhook_url=f"{WEBHOOK_URL.rstrip('/')}{webhook_path}",
    )


if __name__ == "__main__":
    main()
