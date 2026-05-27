"""Telegram command handlers (python-telegram-bot v20+, async)."""
from __future__ import annotations

import asyncio
import json
import re

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import booker
from config import SLOT_GRID, config
from scheduler import BookingScheduler, format_day

REQUIRED_COOKIES = [".ASPXAUTH_SOCIALBOOKING", "ASP.NET_SessionId", "__RequestVerificationToken"]

BOOKMARKLET_HINT = (
    "📌 *Come ottenere i cookie* (i cookie di login sono protetti, quindi serve DevTools)\n"
    "1. Apri Chrome/Edge su ecomm.sportrick.com/sportpolimi e fai login con *SPID*.\n"
    "2. Premi *F12* → scheda *Network* (Rete).\n"
    "3. Clicca una richiesta a `ecomm.sportrick.com`.\n"
    "4. Tasto destro → *Copy* → *Copy as cURL*.\n"
    "5. Torna qui e manda `/cookies` + spazio + incolla tutto.\n\n"
    "Apri `bookmarklet.html` nel repo per le istruzioni con immagini."
)


def parse_cookie_input(raw: str) -> str:
    """Extract a 'k=v; k2=v2' cookie string from cURL / raw / JSON input."""
    raw = raw.strip()
    # JSON from older bookmarklet: {"cookies": "..."}
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and obj.get("cookies"):
            return str(obj["cookies"]).strip()
    except json.JSONDecodeError:
        pass
    # cURL: find a Cookie header (-H 'cookie: ...') or -b/--cookie value.
    if "curl" in raw[:10].lower() or "-H" in raw or "--cookie" in raw or "-b " in raw:
        m = re.search(r"""-H\s+(['"])\s*cookie:\s*(.*?)\1""", raw, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(2).strip()
        m = re.search(r"""(?:-b|--cookie)\s+(['"])(.*?)\1""", raw, re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(2).strip()
    # Raw cookie string already.
    return raw


def missing_required(cookie_string: str) -> list[str]:
    present = {p.split("=", 1)[0].strip() for p in cookie_string.split(";") if "=" in p}
    return [c for c in REQUIRED_COOKIES if c not in present]


def _authorized(update: Update) -> bool:
    if not config.telegram_chat_id:
        return True  # not locked yet (first run)
    return str(update.effective_chat.id) == str(config.telegram_chat_id)


def _scheduler(context: ContextTypes.DEFAULT_TYPE) -> BookingScheduler:
    return context.application.bot_data["scheduler"]


def _chunks(text: str, size: int = 3900) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [text]


async def _session_line() -> str:
    ok = await asyncio.to_thread(booker.validate_session, config.cookie_string())
    return "✅ valida" if ok else "⚠️ scaduta/assente"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    sched = _scheduler(context)
    await update.message.reply_text(
        "🏋️ *Gym Bot Sport Polimi*\n\n"
        f"Slot preferito: *{config.slot_time}*\n"
        f"Sessione: {await _session_line()}\n"
        f"Prossima prenotazione: {sched.next_booking_text()}\n\n"
        "Comandi: /setup /cookies /slot /status /test /help",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.reply_text(
        "*Comandi*\n"
        "/start — stato e configurazione\n"
        "/setup — come estrarre i cookie\n"
        "/cookies <json> — salva e valida i cookie\n"
        "/slot [orario] — mostra o cambia lo slot\n"
        "/status — sessione, prossima e ultima prenotazione\n"
        "/test — prova senza prenotare\n"
        "/friends — mostra JSON raw amici invitabili\n"
        "/book — prenota subito lo slot di oggi+2\n"
        "/stop — metti in pausa le prenotazioni\n"
        "/resume — riattiva le prenotazioni\n"
        "/help — questo messaggio",
        parse_mode="Markdown",
    )


async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.reply_text(BOOKMARKLET_HINT, parse_mode="Markdown")


async def cmd_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    raw = update.message.text.partition(" ")[2].strip()
    if not raw:
        await update.message.reply_text(
            "Manda i cookie dopo il comando. Accetto:\n"
            "• il comando *cURL* copiato da DevTools (consigliato)\n"
            "• la stringa cookie grezza `nome=valore; nome2=valore2`\n"
            "Vedi /setup per come ottenerli.",
            parse_mode="Markdown",
        )
        return

    cookie_string = parse_cookie_input(raw)
    if "=" not in cookie_string:
        await update.message.reply_text(
            "❌ Non ho trovato cookie validi. Usa *Copy as cURL* da DevTools e incolla tutto.",
            parse_mode="Markdown",
        )
        return

    missing = missing_required(cookie_string)
    config.set_session(json.dumps({"cookies": cookie_string}))
    valid = await asyncio.to_thread(booker.validate_session, config.cookie_string())

    if valid:
        await update.message.reply_text(
            f"✅ Sessione salvata! Prenoto ogni notte alle 00:00 per le {config.slot_time}."
        )
    elif missing:
        await update.message.reply_text(
            "❌ Sessione non valida. Mancano cookie importanti: "
            f"{', '.join(missing)}.\nUsa *Copy as cURL* (li include tutti) e riprova.",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "❌ Sessione non valida o scaduta. Rifai il login SPID e ricopia il cURL."
        )


async def cmd_slot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    arg = update.message.text.partition(" ")[2].strip()
    if not arg:
        grid = "  ".join(SLOT_GRID)
        await update.message.reply_text(
            f"Slot attuale: *{config.slot_time}*\n"
            f"Per cambiare: `/slot 08:30`\n\nGriglia valida:\n{grid}",
            parse_mode="Markdown",
        )
        return
    if arg not in SLOT_GRID:
        await update.message.reply_text(
            f"❌ '{arg}' non è nella griglia. Valori: {', '.join(SLOT_GRID)}"
        )
        return
    config.set_slot(arg)
    sched = _scheduler(context)
    await update.message.reply_text(
        f"✅ Slot impostato: {arg}. Prossima prenotazione: {sched.next_booking_text()}"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    sched = _scheduler(context)
    state = "▶️ attivo" if config.booking_active else "⏸ in pausa (/resume)"
    await update.message.reply_text(
        f"Stato: {state}\n"
        f"Sessione: {await _session_line()}\n"
        f"Slot: {config.slot_time}\n"
        f"Prossima prenotazione: {sched.next_booking_text()}\n"
        f"Ultima prenotazione: {config.last_booking_result}\n"
        "(prenoto solo Lun–Ven)"
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    config.set_active(False)
    await update.message.reply_text(
        "⏸ Bot in pausa. Nessuna prenotazione finché non scrivi /resume."
    )


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    config.set_active(True)
    sched = _scheduler(context)
    await update.message.reply_text(
        f"▶️ Bot attivo. Prossima prenotazione: {sched.next_booking_text()}"
    )


async def cmd_book(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Immediate one-shot booking of the configured slot for today+2.

    Manual override: bypasses the /stop pause and the weekend-skip guard,
    since the user is explicitly asking to book now.
    """
    if not _authorized(update):
        return
    await update.message.reply_text("🎯 Prenoto adesso…")
    if not await asyncio.to_thread(booker.validate_session, config.cookie_string()):
        await update.message.reply_text("⚠️ Sessione scaduta/assente. Manda /cookies.")
        return
    day = booker.target_date()
    try:
        session = booker.make_session(config.cookie_string())
        slots = await asyncio.to_thread(booker.fetch_slots, session, day)
        slot = booker.find_slot(slots, config.slot_time, day)
        if slot.start_hhmm != config.slot_time or (slot.day is not None and slot.day != day):
            await update.message.reply_text(
                f"❌ Slot inatteso ({slot.start_hhmm} {slot.day}) ≠ {config.slot_time} {day}."
            )
            return
        await asyncio.to_thread(booker.book_with_retries, session, slot)
        msg = f"✅ Prenotato! {format_day(day)} alle {config.slot_time} 💪"
        config.last_booking_result = msg
        await update.message.reply_text(msg)
    except booker.SlotFull:
        await update.message.reply_text(
            f"😕 Slot delle {config.slot_time} di {format_day(day)} già pieno."
        )
    except booker.SlotNotFound:
        await update.message.reply_text(
            f"⚠️ Slot delle {config.slot_time} non trovato per {format_day(day)}."
        )
    except booker.SessionExpired:
        await update.message.reply_text("⚠️ Sessione scaduta! Manda /cookies.")
    except booker.BookingError as e:
        await update.message.reply_text(f"❌ Errore prenotazione: {e}")


async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.reply_text("🔎 Test in corso…")
    if not await asyncio.to_thread(booker.validate_session, config.cookie_string()):
        await update.message.reply_text(
            "⚠️ Sessione scaduta/assente. Manda /cookies."
        )
        return
    day = booker.target_date()
    try:
        session = booker.make_session(config.cookie_string())
        slots = await asyncio.to_thread(booker.fetch_slots, session, day)
        slot = booker.find_slot(slots, config.slot_time, day)
        state = "PIENO ❌" if slot.is_full else "disponibile ✅"
        await update.message.reply_text(
            f"✅ Slot {config.slot_time} di {format_day(day)} trovato "
            f"(id: {slot.id}, {state}). Pronto per prenotare. Nessuna prenotazione fatta."
        )
    except booker.SlotNotFound:
        await update.message.reply_text(
            f"⚠️ Slot {config.slot_time} non trovato per {format_day(day)}."
        )
    except booker.SessionExpired:
        await update.message.reply_text("⚠️ Sessione scaduta! Manda /cookies.")
    except booker.BookingError as e:
        await update.message.reply_text(f"❌ Errore: {e}")


async def cmd_friends(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    await update.message.reply_text("🔎 Recupero amici invitabili…")
    try:
        session = booker.make_session(config.cookie_string())
        slot = await asyncio.to_thread(booker.first_available_slot_today_or_tomorrow, session)
    except booker.SlotNotFound as e:
        await update.message.reply_text(f"⚠️ {e}")
        return
    except booker.SessionExpired as e:
        await update.message.reply_text(f"⚠️ Sessione scaduta/non autorizzata: {e}")
        return
    except booker.BookingError as e:
        await update.message.reply_text(f"❌ Errore GetActivitySchedule: {e}")
        return

    for param_name in ("query", "name", "search", "term"):
        label = f"GetFriendsToInvite?appointmentId={slot.id}&{param_name}=a"
        try:
            raw = await asyncio.to_thread(
                booker.fetch_friends_raw,
                session,
                slot.id,
                {param_name: "a"},
            )
            message = f"{label}\n{raw}"
        except booker.SessionExpired as e:
            message = f"{label}\n⚠️ Sessione scaduta/non autorizzata: {e}"
        except booker.BookingError as e:
            message = f"{label}\n❌ Errore GetFriendsToInvite: {e}"

        for chunk in _chunks(message):
            await update.message.reply_text(chunk)


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("setup", cmd_setup))
    app.add_handler(CommandHandler("cookies", cmd_cookies))
    app.add_handler(CommandHandler("slot", cmd_slot))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("test", cmd_test))
    app.add_handler(CommandHandler("friends", cmd_friends))
    app.add_handler(CommandHandler("book", cmd_book))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("resume", cmd_resume))
    # If the user pastes cookies without the command, nudge them.
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, _nudge)
    )


async def _nudge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _authorized(update):
        return
    txt = update.message.text or ""
    if "=" in txt or txt.strip().startswith("{"):
        await update.message.reply_text(
            "Per salvare i cookie usa il comando: `/cookies <incolla qui>`",
            parse_mode="Markdown",
        )
