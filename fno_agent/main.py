"""
FnO agent entrypoint — runs 24/7 on the VM.

    python -m fno_agent.main [path/to/.env]      (default: fno_agent/.env)

Connects to the broadcaster WebSocket, executes FNO signals, and runs daily jobs:
  08:00 IST  broker session refresh (Dhan TOTP login / Kite token-file reload)
  08:30 IST  instrument master refresh (retried every 10 min until it succeeds)
  09:00 IST  heartbeat notification
"""

import asyncio
import json
import logging
import os
import sys
from datetime import date, time
from logging.handlers import RotatingFileHandler
from typing import Optional

from dotenv import load_dotenv

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from fno_agent.brokers import create_broker  # noqa: E402
from fno_agent.executor import FnOExecutor  # noqa: E402
from fno_agent.instruments import InstrumentResolver  # noqa: E402
from fno_agent.journal import Journal  # noqa: E402
from fno_agent.market import load_holidays, now_ist  # noqa: E402
from fno_agent.notifier import Notifier  # noqa: E402
from fno_agent.settings import AGENT_DIR, Settings  # noqa: E402

logger = logging.getLogger("fno_agent")

RECONNECT_BASE_DELAY = 1
RECONNECT_MAX_DELAY = 60
SESSION_REFRESH_AT = time(8, 0)
INSTRUMENT_REFRESH_AT = time(8, 30)
HEARTBEAT_AT = time(9, 0)
INSTRUMENT_RETRY_SEC = 600


def setup_logging(log_dir: str):
    os.makedirs(log_dir, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    file = RotatingFileHandler(os.path.join(log_dir, "fno_agent.log"), maxBytes=10_000_000, backupCount=10)
    file.setFormatter(fmt)
    root.handlers = [stream, file]


class Agent:
    def __init__(self, settings: Settings):
        self.s = settings
        self.notifier = Notifier(settings.tg_bot_token, settings.tg_chat_id)
        self.broker = create_broker(settings)
        self.resolver = InstrumentResolver(settings.broker if settings.broker == "dhan" else "kite",
                                           settings.min_days_to_expiry)
        self.journal = Journal(settings.journal_path)
        self.executor = FnOExecutor(settings, self.broker, self.resolver, self.journal, self.notifier,
                                    holidays=load_holidays(settings.holidays_file))
        self.broker_ok = False
        self._tasks: set = set()
        self._session_refreshed_on: Optional[date] = None
        self._heartbeat_on: Optional[date] = None
        self._last_instrument_attempt = 0.0

    def _spawn(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # --- startup ------------------------------------------------------------

    async def connect_broker(self) -> bool:
        try:
            await asyncio.to_thread(self.broker.connect)
            self.broker_ok = True
            return True
        except Exception as e:
            self.broker_ok = False
            logger.error(f"Broker connect failed: {e}")
            await self.notifier.send(f"🚨 {self.s.broker} login failed: {e}")
            return False

    async def load_instruments(self):
        while not await asyncio.to_thread(self.resolver.refresh):
            await self.notifier.send("🚨 Instrument master download failed; retrying in 60s")
            await asyncio.sleep(60)

    # --- daily jobs -----------------------------------------------------------

    async def scheduler(self):
        loop = asyncio.get_running_loop()
        while True:
            now = now_ist()
            today = now.date()
            try:
                if now.time() >= SESSION_REFRESH_AT and self._session_refreshed_on != today:
                    self._session_refreshed_on = today
                    try:
                        await asyncio.to_thread(self.broker.refresh_session)
                        self.broker_ok = True
                        logger.info("Daily broker session refresh done.")
                    except Exception as e:
                        self.broker_ok = False
                        await self.notifier.send(f"🚨 Daily {self.s.broker} session refresh failed: {e}")

                if (now.time() >= INSTRUMENT_REFRESH_AT and self.resolver.loaded_on != today
                        and loop.time() - self._last_instrument_attempt >= INSTRUMENT_RETRY_SEC):
                    self._last_instrument_attempt = loop.time()
                    if not await asyncio.to_thread(self.resolver.refresh, today):
                        await self.notifier.send("⚠️ Instrument refresh failed; using yesterday's master, "
                                                 f"retrying in {INSTRUMENT_RETRY_SEC // 60} min")

                if now.time() >= HEARTBEAT_AT and self._heartbeat_on != today and now.weekday() < 5:
                    self._heartbeat_on = today
                    counts = self.journal.count_by_status()
                    await self.notifier.send(
                        f"💓 alive | broker={self.s.broker} ok={self.broker_ok} dry_run={self.s.dry_run} "
                        f"| instruments={self.resolver.loaded_on} | budget ₹{self.s.capital_per_trade:,.0f} "
                        f"| journal {counts}")
            except Exception:
                logger.exception("Scheduler iteration failed")
            await asyncio.sleep(60)

    # --- signal stream ------------------------------------------------------

    async def listen(self):
        import websockets

        url = self.s.ws_server_url
        if self.s.ws_auth_token:
            url += ("&" if "?" in url else "?") + f"token={self.s.ws_auth_token}"
        delay = RECONNECT_BASE_DELAY
        while True:
            try:
                logger.info(f"Connecting to {self.s.ws_server_url} ...")
                async with websockets.connect(url, ping_interval=20, ping_timeout=10, close_timeout=5) as ws:
                    logger.info("Connected to broadcaster.")
                    delay = RECONNECT_BASE_DELAY
                    async for raw in ws:
                        self._on_message(raw)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"WebSocket error: {e}. Reconnecting in {delay}s")
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    def _on_message(self, raw):
        try:
            signal = json.loads(raw)
        except (TypeError, ValueError):
            return  # e.g. "pong"
        if not isinstance(signal, dict) or signal.get("asset_class") != "FNO":
            return
        latency = signal.pop("_latency_ms", None)
        logger.info(f"FnO signal {signal.get('signal_id')} from '{signal.get('channel_label')}' "
                    f"(server latency {latency})")
        self._spawn(self.executor.handle(signal))

    async def run(self):
        mode = "DRY RUN" if self.s.dry_run else "LIVE"
        logger.info(f"FnO agent starting | broker={self.s.broker} | {mode} | budget ₹{self.s.capital_per_trade:,.0f}")
        if self.s.capital_per_trade <= 0:
            logger.warning("FNO_CAPITAL_PER_TRADE is 0: every signal will be skipped.")

        if await self.connect_broker() and now_ist().time() >= SESSION_REFRESH_AT:
            self._session_refreshed_on = now_ist().date()  # fresh session already; skip today's refresh
        await self.load_instruments()
        await self.notifier.send(f"🚀 FnO agent started ({self.s.broker}, {mode}, "
                                 f"budget ₹{self.s.capital_per_trade:,.0f}, broker_ok={self.broker_ok})")
        self._spawn(self.executor.reconcile())
        self._spawn(self.scheduler())
        await self.listen()


def main():
    env_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(AGENT_DIR, ".env")
    load_dotenv(env_path)
    settings = Settings.from_env()
    setup_logging(settings.log_dir)
    logger.info(f"Loaded env from {env_path}")
    try:
        asyncio.run(Agent(settings).run())
    except KeyboardInterrupt:
        logger.info("Shutting down.")


if __name__ == "__main__":
    main()
