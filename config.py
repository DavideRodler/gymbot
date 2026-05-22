"""Runtime config for the gym bot.

Source of truth precedence:
1. Environment variables (GitHub Actions Secrets inject these).
2. Local config.json (for running on your own machine; git-ignored).

State that changes at runtime (slot_time, session) is held in this module and,
when GitHub API creds are present, mirrored back into repo Secrets by
github_secret.py so it survives the next workflow restart. Locally it is also
written to config.json.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(__file__).with_name("config.json")

# Fixed 90-minute slot grid (HH:MM). The only valid preferred-slot values.
SLOT_GRID = [
    "07:00", "08:30", "10:00", "11:30", "13:00", "14:30",
    "16:00", "17:30", "19:00", "20:30", "22:00",
]

TIMEZONE = "Europe/Rome"

# Confirmed site constants (see FINDINGS.md §8).
BASE_URL = "https://ecomm.sportrick.com"
GYM_PATH = "/sportpolimi"
BOOKING_PATH = f"{GYM_PATH}/Booking"
LOGIN_PATH = f"{GYM_PATH}/Account/Login"

# How many days ahead bookings open for (today + N).
BOOKING_LEAD_DAYS = 2

# Activity selector for GetActivitySchedule (confirmed from HAR).
# ACTIVITY_ID identifies the Sport Polimi activity you book (e.g. Sala Pesi).
# If yours differs, find it in the BookActivity URL (…?activityId=XXX) and set
# the ACTIVITY_ID env var. skillID/activityFlags rarely change.
ACTIVITY_ID = os.environ.get("ACTIVITY_ID", "245765508040862208")
SKILL_ID = os.environ.get("SKILL_ID", "0")
ACTIVITY_FLAGS = os.environ.get("ACTIVITY_FLAGS", "Classes")


def _parse_bool(value, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _load_json_file() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


class Config:
    def __init__(self) -> None:
        fileconf = _load_json_file()

        self.telegram_bot_token: str = (
            os.environ.get("TELEGRAM_BOT_TOKEN") or fileconf.get("telegram_bot_token", "")
        )
        self.telegram_chat_id: str = (
            os.environ.get("TELEGRAM_CHAT_ID") or fileconf.get("telegram_chat_id", "")
        )
        self.slot_time: str = (
            os.environ.get("SLOT_TIME") or fileconf.get("slot_time") or "08:30"
        )
        # SESSION_COOKIES holds the JSON produced by the bookmarklet:
        #   {"cookies": "k=v; k2=v2", "localStorage": {...}}
        self.session_raw: str = (
            os.environ.get("SESSION_COOKIES") or fileconf.get("session_raw") or ""
        )
        # Pause switch: BOOKING_ACTIVE=true/false. Default active.
        self.booking_active: bool = _parse_bool(
            os.environ.get("BOOKING_ACTIVE", fileconf.get("booking_active")), True
        )

        # GitHub API config for persisting Secrets across restarts (optional).
        self.github_token: str = os.environ.get("GITHUB_TOKEN", "")
        self.github_repo: str = os.environ.get("GITHUB_REPOSITORY", "")  # "owner/repo"

        # Last booking result, for /status (in-memory only).
        self.last_booking_result: str = "Nessuna prenotazione ancora."

    # --- session helpers -------------------------------------------------
    def cookie_string(self) -> str:
        """Return the raw 'k=v; k2=v2' cookie string from the stored session."""
        if not self.session_raw:
            return ""
        try:
            data = json.loads(self.session_raw)
            return data.get("cookies", "") if isinstance(data, dict) else ""
        except json.JSONDecodeError:
            # Backwards-tolerant: maybe a bare cookie string was pasted.
            return self.session_raw.strip()

    def has_session(self) -> bool:
        return bool(self.cookie_string())

    # --- mutation + persistence -----------------------------------------
    def set_slot(self, slot_time: str) -> None:
        self.slot_time = slot_time
        self._persist({"SLOT_TIME": slot_time})

    def set_session(self, session_raw: str) -> None:
        self.session_raw = session_raw
        self._persist({"SESSION_COOKIES": session_raw})

    def set_active(self, active: bool) -> None:
        self.booking_active = active
        self._persist({"BOOKING_ACTIVE": "true" if active else "false"})

    def _persist(self, secrets: dict[str, str]) -> None:
        # Local mirror.
        data = _load_json_file()
        for k, v in secrets.items():
            field_key = {
                "SLOT_TIME": "slot_time",
                "SESSION_COOKIES": "session_raw",
                "BOOKING_ACTIVE": "booking_active",
            }[k]
            data[field_key] = v
        try:
            CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass
        # Remote mirror into GitHub Secrets (best effort).
        if self.github_token and self.github_repo:
            try:
                from github_secret import update_secrets

                update_secrets(self.github_repo, self.github_token, secrets)
            except Exception:
                pass


config = Config()
