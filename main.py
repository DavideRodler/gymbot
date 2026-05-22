"""Entry point: start the Telegram bot and the nightly booking scheduler."""
from __future__ import annotations

import logging

from telegram.ext import Application

import bot as bot_module
from config import config
from scheduler import BookingScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("gymbot")


def build_application() -> Application:
    if not config.telegram_bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN mancante (env o config.json).")

    app = Application.builder().token(config.telegram_bot_token).build()
    bot_module.register_handlers(app)

    async def notify(text: str) -> None:
        if config.telegram_chat_id:
            await app.bot.send_message(chat_id=config.telegram_chat_id, text=text)
        else:
            log.warning("notify senza TELEGRAM_CHAT_ID: %s", text)

    scheduler = BookingScheduler(notify)
    app.bot_data["scheduler"] = scheduler

    async def _post_init(_: Application) -> None:
        scheduler.start()
        log.info("Scheduler avviato. Slot=%s. %s", config.slot_time,
                 scheduler.next_booking_text())

    app.post_init = _post_init
    return app


def main() -> None:
    app = build_application()
    log.info("Bot in avvio (long polling)…")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
