"""Speed-optimised 3-phase nightly booking via APScheduler.

hourly    session monitor    → warn if expired
23:59:00  pre-check session  → warn if expired before booking
23:59:45  pre-fetch slots    → cache slot_id for the preferred time
00:00:00  fire booking POST  → using the cached slot (fresh-fetch fallback)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Awaitable, Callable, Optional

from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import booker
from config import TIMEZONE, config

log = logging.getLogger("gymbot.scheduler")

Notify = Callable[[str], Awaitable[None]]
ROME_TZ = ZoneInfo(TIMEZONE)

_GIORNI = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
_MESI = [
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
]


def format_day(d: date) -> str:
    return f"{_GIORNI[d.weekday()]} {d.day} {_MESI[d.month - 1]}"


def rome_cron(**kwargs) -> CronTrigger:
    return CronTrigger(timezone=ROME_TZ, **kwargs)


class BookingScheduler:
    def __init__(self, notify: Notify) -> None:
        self.notify = notify
        self.scheduler = AsyncIOScheduler(timezone=ROME_TZ)
        self._cached_slot: Optional[booker.Slot] = None
        self._cached_for: Optional[date] = None

    def start(self) -> None:
        self.scheduler.add_job(
            self.hourly_session_check, rome_cron(minute=0, second=0),
            id="hourly_session_check", replace_existing=True,
        )
        self.scheduler.add_job(
            self.phase1_precheck, rome_cron(hour=23, minute=59, second=0),
            id="phase1", replace_existing=True,
        )
        self.scheduler.add_job(
            self.phase2_prefetch, rome_cron(hour=23, minute=59, second=45),
            id="phase2", replace_existing=True,
        )
        self.scheduler.add_job(
            self.phase3_book, rome_cron(hour=0, minute=0, second=0),
            id="phase3", replace_existing=True,
        )
        self.scheduler.start()

    def next_bookable_target(self) -> date:
        """Next target date (today+2, advanced past weekends) that will be booked."""
        t = booker.target_date()
        while t.weekday() >= 5:  # Sat=5, Sun=6 → gym closed for booking
            t += timedelta(days=1)
        return t

    def next_booking_text(self) -> str:
        if not config.booking_active:
            return "in pausa (scrivi /resume)"
        if not self.scheduler.running or not self.scheduler.get_job("phase3"):
            return "non programmata"
        t = self.next_bookable_target()
        return f"{format_day(t)} alle {config.slot_time}"

    @staticmethod
    def _skip_reason(day: date) -> Optional[str]:
        """Return why tonight's booking is skipped, or None to proceed."""
        if not config.booking_active:
            return "bot in pausa"
        if day.weekday() >= 5:  # Sat/Sun target → no booking
            return f"weekend ({format_day(day)})"
        return None

    # --- session monitor -------------------------------------------------
    async def hourly_session_check(self) -> None:
        ok = await asyncio.to_thread(booker.validate_session, config.cookie_string())
        if ok:
            return
        await self.notify("⚠️ Sessione scaduta/assente. Manda /cookies per rinnovarla.")

    # --- phases ----------------------------------------------------------
    async def phase1_precheck(self) -> None:
        skip = self._skip_reason(booker.target_date())
        if skip:
            log.info("phase1 skip: %s", skip)
            return
        ok = await asyncio.to_thread(booker.validate_session, config.cookie_string())
        if not ok:
            await self.notify(
                "⚠️ [23:59] Sessione non valida — fai /cookies ORA prima di mezzanotte!"
            )

    async def phase2_prefetch(self) -> None:
        self._cached_slot = None
        self._cached_for = booker.target_date()
        if self._skip_reason(self._cached_for):
            return
        try:
            session = booker.make_session(config.cookie_string())
            slots = await asyncio.to_thread(booker.fetch_slots, session, self._cached_for)
            self._cached_slot = booker.find_slot(slots, config.slot_time, self._cached_for)
        except booker.SessionExpired:
            await self.notify("⚠️ Sessione scaduta! Manda /cookies per rinnovarla.")
        except booker.SlotNotFound:
            pass  # reported after midnight by phase3 if still missing
        except booker.BookingError:
            pass  # phase3 will retry a fresh fetch

    async def phase3_book(self) -> None:
        day = booker.target_date()
        skip = self._skip_reason(day)
        if skip:
            log.info("phase3 skip: %s — nessuna prenotazione", skip)
            return
        try:
            session = booker.make_session(config.cookie_string())
            slot = self._cached_slot
            if slot is None or self._cached_for != day:
                slots = await asyncio.to_thread(booker.fetch_slots, session, day)
                slot = booker.find_slot(slots, config.slot_time, day)

            # Safety net: never book a slot that isn't the configured time / target day.
            if slot.start_hhmm != config.slot_time or (slot.day is not None and slot.day != day):
                raise booker.SlotNotFound(
                    f"Slot inatteso ({slot.start_hhmm} {slot.day}) ≠ {config.slot_time} {day}."
                )

            await asyncio.to_thread(booker.book_with_retries, session, slot)
            msg = f"✅ Prenotato! {format_day(day)} alle {config.slot_time} 💪"
            config.last_booking_result = msg
            await self.notify(msg)

        except booker.SlotFull:
            msg = f"😕 Slot delle {config.slot_time} già pieno. Riprovo domani."
            config.last_booking_result = msg
            await self.notify(msg)
        except booker.SlotNotFound:
            msg = (
                f"⚠️ Slot delle {config.slot_time} non trovato per "
                f"{format_day(day)}. Controlla il calendario."
            )
            config.last_booking_result = msg
            await self.notify(msg)
        except booker.SessionExpired:
            msg = "⚠️ Sessione scaduta! Manda /cookies per rinnovarla."
            config.last_booking_result = msg
            await self.notify(msg)
        except booker.BookingError as e:
            msg = f"❌ Errore durante la prenotazione: {e}. Riprovo domani."
            config.last_booking_result = msg
            await self.notify(msg)
        finally:
            self._cached_slot = None
            self._cached_for = None
