"""Sportrick (Sport Polimi) booking client — endpoints confirmed from HAR.

Flow (reverse-engineered, see FINDINGS.md):
  1. POST /Booking/GetActivitySchedule  {currentDate, activityID, skillID,
       activityFlags, getForCatchUp}  -> {status:"OK", data:[ {TimeStart,
       Status, AppointmentId, EventName, ...} ]}
  2. POST /Booking/BookAppointment  {appointmentID}  -> {status, message, url}

Slot identifier = AppointmentId. Slot time = TimeStart[:5]. A slot is bookable
when Status == "Available". No anti-forgery token is required on these AJAX POSTs
(confirmed: HAR requests carried only X-Requested-With).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from http.cookies import SimpleCookie
from typing import Optional

import requests

from config import (
    ACTIVITY_FLAGS,
    ACTIVITY_ID,
    BASE_URL,
    BOOKING_LEAD_DAYS,
    BOOKING_PATH,
    GYM_PATH,
    LOGIN_PATH,
    SKILL_ID,
    TIMEZONE,
)

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

# --- Endpoints (confirmed from HAR) ---------------------------------------
SCHEDULE_PATH = f"{BOOKING_PATH}/GetActivitySchedule"
BOOK_PATH = f"{BOOKING_PATH}/BookAppointment"
FRIENDS_PATH = f"{BOOKING_PATH}/GetFriendsToInvite"
CONFIRM_PATH = f"{BOOKING_PATH}/ConfirmActivityBooking"
BOOKACTIVITY_PATH = f"{BOOKING_PATH}/BookActivity"

AVAILABLE_STATUS = "available"
# Substrings (lowercase) meaning the slot is full / not bookable.
FULL_MARKERS = [
    "esaurito", "completo", "pieno", "full", "no places",
    "non disponibile", "posti esauriti", "lista d'attesa", "waitlist",
]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE_URL,
    "Referer": BASE_URL + BOOKACTIVITY_PATH
    + f"?activityId={ACTIVITY_ID}&activityFlags={ACTIVITY_FLAGS}",
}


@dataclass
class Slot:
    id: str            # AppointmentId
    start_hhmm: str    # TimeStart[:5]
    status: str        # raw Status string
    day: Optional[date] = None  # slot's calendar date, if the payload carries one
    raw: dict = field(default_factory=dict)

    @property
    def is_available(self) -> bool:
        return self.status.lower() == AVAILABLE_STATUS

    @property
    def is_full(self) -> bool:
        return not self.is_available


class SessionExpired(Exception):
    pass


class BookingError(Exception):
    pass


class SlotFull(Exception):
    pass


class SlotNotFound(Exception):
    pass


def now_local() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE)) if ZoneInfo else datetime.now()


def target_date(reference: Optional[datetime] = None) -> date:
    """Date the upcoming midnight opens bookings for.

    Anchored on the midnight boundary so the 23:59:45 pre-fetch and the
    00:00:00 booking resolve to the SAME date:
      - evening (hour >= 12): relevant midnight is tonight -> open day = tomorrow
      - early hours (hour < 12): past midnight -> open day = today
    target = open day + BOOKING_LEAD_DAYS. Fri 23:59 and Sat 00:00 both -> Monday.
    """
    ref = reference or now_local()
    open_day = ref.date() + timedelta(days=1) if ref.hour >= 12 else ref.date()
    return open_day + timedelta(days=BOOKING_LEAD_DAYS)


def _session_from_cookies(cookie_string: str) -> requests.Session:
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    for pair in cookie_string.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        s.cookies.set(name.strip(), value.strip(), domain="ecomm.sportrick.com")
    return s


def _looks_like_login(resp: requests.Response) -> bool:
    return LOGIN_PATH in (resp.url or "") or "Account/Login" in (resp.url or "")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def make_session(cookie_string: str) -> requests.Session:
    if not cookie_string:
        raise SessionExpired("Nessun cookie salvato.")
    return _session_from_cookies(cookie_string)


def validate_session(cookie_string: str, timeout: float = 10.0) -> bool:
    """True if the session can load the authorized Booking page."""
    if not cookie_string:
        return False
    try:
        r = make_session(cookie_string).get(
            BASE_URL + BOOKING_PATH, timeout=timeout, allow_redirects=True
        )
    except requests.RequestException:
        return False
    return r.status_code == 200 and not _looks_like_login(r)


def session_expired(cookie_string: str, timeout: float = 10.0) -> bool:
    """True only when we can tell the saved cookies are expired or absent.

    Network/server failures are not treated as expiry, because the booking
    scheduler should keep retrying while Sportrick is temporarily unavailable.
    """
    if not cookie_string:
        return True
    try:
        r = make_session(cookie_string).get(
            BASE_URL + BOOKING_PATH, timeout=timeout, allow_redirects=True
        )
    except requests.RequestException:
        return False
    return _looks_like_login(r)


def fetch_slots(session: requests.Session, day: date, timeout: float = 10.0) -> list[Slot]:
    """POST GetActivitySchedule for `day`, return parsed slots."""
    body = {
        "currentDate": day.isoformat(),
        "activityID": ACTIVITY_ID,
        "skillID": SKILL_ID,
        "activityFlags": ACTIVITY_FLAGS,
        "getForCatchUp": "false",
    }
    try:
        r = session.post(
            BASE_URL + SCHEDULE_PATH, data=body, timeout=timeout, allow_redirects=True
        )
    except requests.RequestException as e:
        raise BookingError(f"Errore di rete nel recupero slot: {e}") from e

    if _looks_like_login(r):
        raise SessionExpired("Sessione scaduta.")
    if r.status_code != 200:
        raise BookingError(f"HTTP {r.status_code} nel recupero slot.")

    try:
        payload = r.json()
    except ValueError as e:
        raise BookingError("Risposta GetActivitySchedule non in JSON.") from e

    if isinstance(payload, dict) and str(payload.get("status", "OK")).upper() == "ERROR":
        raise BookingError(payload.get("error_message") or "GetActivitySchedule ERROR.")

    return parse_slots(payload)


def fetch_friends_raw(
    session: requests.Session,
    appointment_id: str,
    extra_params: Optional[dict[str, str]] = None,
    timeout: float = 10.0,
) -> str:
    """GET the invite-friends endpoint and return the raw response body."""
    params = {"appointmentId": appointment_id}
    if extra_params:
        params.update(extra_params)
    try:
        r = session.get(
            BASE_URL + FRIENDS_PATH,
            params=params,
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException as e:
        raise BookingError(f"Errore di rete nel recupero amici: {e}") from e

    if _looks_like_login(r):
        raise SessionExpired("Redirect alla pagina di login.")
    if r.status_code in (401, 403):
        raise SessionExpired(f"HTTP {r.status_code}: sessione non autorizzata.")
    if r.status_code != 200:
        raise BookingError(f"HTTP {r.status_code} nel recupero amici.")

    body = r.text.strip()
    if not body:
        raise BookingError("Risposta vuota da GetFriendsToInvite.")
    return body


def first_available_slot_today_or_tomorrow(session: requests.Session) -> Slot:
    """Return the first available schedule slot from today or tomorrow."""
    today = now_local().date()
    for day in (today, today + timedelta(days=1)):
        slots = fetch_slots(session, day)
        for slot in slots:
            if slot.is_available:
                return slot
    raise SlotNotFound("Nessuno slot disponibile trovato oggi o domani.")


_HHMM_RE = re.compile(r"(\d{1,2}):(\d{2})")
# Fields that may carry the slot's calendar date in an ASP.NET schedule payload.
_DATE_FIELDS = ("TimeStart", "Day", "Date", "StartDate", "AppointmentDate", "DataInizio")


def _extract_hhmm(time_start: str) -> str:
    """Pull HH:MM out of '17:30', '17:30:00' or '2026-05-25T17:30:00'."""
    m = _HHMM_RE.search(time_start)
    if not m:
        return ""
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _extract_day(ev: dict) -> Optional[date]:
    """Best-effort calendar date from common schedule fields (None if absent)."""
    for key in _DATE_FIELDS:
        val = ev.get(key)
        if not val:
            continue
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(val))  # ISO yyyy-mm-dd
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                continue
    return None


def parse_slots(payload) -> list[Slot]:
    data = payload.get("data", []) if isinstance(payload, dict) else payload
    slots: list[Slot] = []
    if not isinstance(data, list):
        return slots
    for ev in data:
        if not isinstance(ev, dict):
            continue
        sid = ev.get("AppointmentId")
        time_start = str(ev.get("TimeStart") or "")
        if sid is None or not time_start:
            continue
        hhmm = _extract_hhmm(time_start)
        if not hhmm:
            continue
        slots.append(
            Slot(
                id=str(sid),
                start_hhmm=hhmm,
                status=str(ev.get("Status", "")),
                day=_extract_day(ev),
                raw=ev,
            )
        )
    return slots


def find_slot(slots: list[Slot], slot_time: str, day: Optional[date] = None) -> Slot:
    """Slot matching the preferred time (and day, if known); prefer an Available one.

    The day-guard only fires when both `day` is given AND the slot carries its own
    date — preventing a wrong-day booking if the schedule ever returns >1 day. When
    no date info is present it is a no-op, so the existing exact-time match stands.
    """
    matches = [
        s for s in slots
        if s.start_hhmm == slot_time and (day is None or s.day is None or s.day == day)
    ]
    if not matches:
        raise SlotNotFound(f"Nessuno slot delle {slot_time} trovato.")
    for s in matches:
        if s.is_available:
            return s
    return matches[0]  # exists but full — book_slot/caller decides


def book_slot(session: requests.Session, slot: Slot, timeout: float = 10.0) -> str:
    """POST BookAppointment. Returns success message; raises SlotFull/BookingError."""
    if slot.is_full:
        raise SlotFull(f"Slot delle {slot.start_hhmm} non disponibile ({slot.status}).")

    try:
        r = session.post(
            BASE_URL + BOOK_PATH,
            data={"appointmentID": slot.id},
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException as e:
        raise BookingError(f"Errore di rete nella prenotazione: {e}") from e

    if _looks_like_login(r):
        raise SessionExpired("Sessione scaduta durante la prenotazione.")
    if r.status_code not in (200, 201, 204, 302):
        raise BookingError(f"HTTP {r.status_code} nella prenotazione.")

    try:
        data = r.json()
    except ValueError:
        return "ok"  # non-JSON 200 — accept

    if not isinstance(data, dict):
        return "ok"

    status = str(data.get("status", "")).upper()
    msg = str(data.get("message") or data.get("error_message") or "")
    msg_low = msg.lower()

    if status == "ERROR" or data.get("success") is False:
        if any(m in msg_low for m in FULL_MARKERS):
            raise SlotFull(msg or "Slot pieno.")
        raise BookingError(msg or "Prenotazione rifiutata.")
    if any(m in msg_low for m in FULL_MARKERS):
        raise SlotFull(msg)
    return msg or "ok"


def book_with_retries(
    session: requests.Session, slot: Slot, attempts: int = 3, gap: float = 2.0
) -> str:
    last: Optional[Exception] = None
    for i in range(attempts):
        try:
            return book_slot(session, slot)
        except (SlotFull, SessionExpired):
            raise
        except BookingError as e:
            last = e
            if i < attempts - 1:
                time.sleep(gap)
    raise last or BookingError("Prenotazione fallita.")
