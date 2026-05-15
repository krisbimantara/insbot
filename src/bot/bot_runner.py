"""Bot runner — single entry point for the Telegram Inspection Bot.

Initializes all components (Settings, structlog, Redis, FrappeClient,
RedisSessionStore, aiogram Bot/Dispatcher) and runs the aiohttp webhook
server alongside aiogram long polling. Implements graceful shutdown.

Requirements: 11.3, 11.5, 12.1, 12.2
"""

from __future__ import annotations

import asyncio
import signal

import redis.asyncio as aioredis
from aiohttp import web
from aiogram import Bot, Dispatcher

from bot.adapters.frappe import FrappeClient
from bot.adapters.redis_store import RedisSessionStore
from bot.auth_middleware import FrappeAuthMiddleware
from bot.config import Settings
from bot.handlers.checklist import router as checklist_router
from bot.handlers.commands import router as commands_router
from bot.handlers.motor_selection import router as motor_selection_router
from bot.handlers.photos import router as photos_router
from bot.handlers.stnk import router as stnk_router
from bot.handlers.submit import router as submit_router
from bot.handlers.summary import router as summary_router
from bot.handlers.text_router import router as text_router
from bot.logging import configure_logging, get_logger
from bot.webhook import create_webhook_app

log = get_logger(__name__)


async def run() -> None:
    """Main async entry point: initialize components and start services."""
    # 1. Load settings (fails fast if required env vars are missing)
    settings = Settings()

    # 2. Configure structured logging (JSON to STDOUT)
    configure_logging(settings.log_level)

    log.info("bot_starting", webhook_port=settings.webhook_port)

    # 3. Create Redis connection
    redis_client: aioredis.Redis = aioredis.from_url(
        settings.redis_url, decode_responses=True
    )

    # 4. Create adapters
    session_store = RedisSessionStore(redis_client, ttl=settings.redis_ttl)
    frappe_client = FrappeClient(settings)

    # 5. Create aiogram Bot and Dispatcher
    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    dp = Dispatcher()

    # 6. Register auth middleware on the dispatcher
    auth_middleware = FrappeAuthMiddleware(frappe=frappe_client, settings=settings)
    dp.message.middleware(auth_middleware)
    dp.callback_query.middleware(auth_middleware)

    # 7. Store shared dependencies in dispatcher workflow data
    dp["session_store"] = session_store
    dp["frappe_client"] = frappe_client
    dp["settings"] = settings

    # 8. Register all handler routers
    dp.include_router(commands_router)
    dp.include_router(motor_selection_router)
    dp.include_router(text_router)
    dp.include_router(photos_router)
    dp.include_router(checklist_router)
    dp.include_router(stnk_router)
    dp.include_router(summary_router)
    dp.include_router(submit_router)

    # 9. Create webhook aiohttp app (webhook server + healthz)
    webhook_app = create_webhook_app(
        settings=settings,
        session_store=session_store,
        bot=bot,
    )

    # 10. Start aiohttp server and aiogram polling concurrently
    runner = web.AppRunner(webhook_app)
    await runner.setup()
    site = web.TCPSite(runner, settings.webhook_host, settings.webhook_port)

    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        log.info("shutdown_signal_received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows does not support add_signal_handler
            pass

    try:
        await site.start()
        log.info(
            "webhook_server_started",
            host=settings.webhook_host,
            port=settings.webhook_port,
        )

        # Delete any existing Telegram webhook before starting polling
        await bot.delete_webhook(drop_pending_updates=False)
        log.info("telegram_webhook_deleted")

        # Start polling in background
        polling_task = asyncio.create_task(
            dp.start_polling(bot, handle_signals=False)
        )
        log.info("polling_started")

        # Wait for shutdown signal
        await shutdown_event.wait()

    finally:
        # Graceful shutdown
        log.info("shutting_down")

        # Stop polling
        await dp.stop_polling()
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass

        # Stop aiohttp server
        await site.stop()
        await runner.cleanup()

        # Close bot session (aiohttp ClientSession inside aiogram Bot)
        await bot.session.close()

        # Close Redis connection
        await redis_client.aclose()

        log.info("shutdown_complete")


def main() -> None:
    """Synchronous entry point for the bot runner."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
