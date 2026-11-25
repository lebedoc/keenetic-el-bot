import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Наприклад: https://el-golovatogo-bot.onrender.com
PORT = int(os.getenv("PORT", "10000"))
STATE_FILE = "state.json"
KYIV_TZ = timezone(timedelta(hours=2))

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
    client_match = re.search(r"Network client\s+['\"]([^'\"]+)['\"]", text, re.IGNORECASE)
    status_match = re.search(
        r"Network client\s+['\"][^'\"]+['\"].*?\b(connected|disconnected)\b",
        text,
        re.IGNORECASE | re.DOTALL,
    )

    if not client_match or not status_match:
        return None

    client = client_match.group(1).strip()
    status = status_match.group(1).lower()

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


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg:
        return

    state = load_state()
    if not state:
        await msg.reply_text("Поки що немає збережених даних про клієнтів.")
        return

    now_local = datetime.now(KYIV_TZ).replace(tzinfo=None)
    blocks = ["📊 *Поточний стан клієнтів:*"]

    for client, info in state.items():
        current_status = info.get("last_status", "unknown")
        last_change = info.get("last_change")
        outage_count = int(info.get("outage_count", 0))
        total_disc_sec = float(info.get("total_disconnected_seconds", 0.0))

        if current_status == "connected":
            badge = "🟢 Підключено (`connected`)"
        elif current_status == "disconnected":
            badge = "🔴 Відключено (`disconnected`)"
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
    if source_dt:
        local_dt = source_dt.astimezone(KYIV_TZ).replace(tzinfo=None)
    else:
        local_dt = datetime.now(KYIV_TZ).replace(tzinfo=None)

    parsed = parse_keenetic_message(msg.text, local_dt)

    if not parsed:
        await msg.reply_text("Не вдалося розпізнати формат повідомлення Keenetic.")
        return

    client, status, event_dt = parsed
    state = load_state()
    client_state = state.get(client, {})

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

    client_state["last_status"] = status
    client_state["last_change"] = event_dt.isoformat()
    client_state["outage_count"] = outage_count
    client_state["total_disconnected_seconds"] = total_disc_sec

    state[client] = client_state
    save_state(state)

    reply = (
        f"📌 *{client}*\n"
        f"{status_label}\n"
        f"🕒 Час: `{event_dt.strftime('%d.%m %H:%M:%S')}`"
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
    app.add_handler(CommandHandler("status", status_command))
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
