"""Environment-driven settings for the FnO agent."""

import os
from dataclasses import dataclass, field
from typing import List

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))


def _bool(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes")


@dataclass
class Settings:
    broker: str = "dhan"
    dry_run: bool = True

    ws_server_url: str = "ws://localhost:8000/ws"
    ws_auth_token: str = ""
    allowed_channels: List[str] = field(default_factory=list)

    # Sizing
    capital_per_trade: float = 0.0
    max_lots: int = 0  # 0 = no cap

    # Execution
    chase_pct: float = 3.0  # skip if LTP > entry_max by more than this %
    entry_timeout_sec: int = 300
    poll_interval_sec: float = 2.0
    min_days_to_expiry: int = 1
    sl_limit_buffer_pct: float = 5.0  # Kite GTT SL leg: limit price this % below trigger
    enforce_market_hours: bool = True

    # Dhan
    dhan_client_id: str = ""
    dhan_access_token: str = ""
    dhan_pin: str = ""
    dhan_totp_secret: str = ""

    # Kite
    kite_api_key: str = ""
    kite_api_secret: str = ""
    kite_access_token: str = ""
    kite_token_file: str = os.path.join(AGENT_DIR, ".kite_token")

    # Notifications
    tg_bot_token: str = ""
    tg_chat_id: str = ""

    journal_path: str = os.path.join(AGENT_DIR, "fno_journal.db")
    holidays_file: str = os.path.join(AGENT_DIR, "holidays.json")
    log_dir: str = os.path.join(AGENT_DIR, "logs")

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            broker=e("BROKER", "dhan").strip().lower(),
            dry_run=_bool("DRY_RUN", "true"),
            ws_server_url=e("WS_SERVER_URL", "ws://localhost:8000/ws"),
            ws_auth_token=e("WS_AUTH_TOKEN", ""),
            allowed_channels=[c.strip().lower() for c in e("ALLOWED_CHANNELS", "").split(",") if c.strip()],
            capital_per_trade=float(e("FNO_CAPITAL_PER_TRADE", "0")),
            max_lots=int(e("FNO_MAX_LOTS", "0")),
            chase_pct=float(e("FNO_CHASE_PCT", "3")),
            entry_timeout_sec=int(e("FNO_ENTRY_TIMEOUT_SEC", "300")),
            min_days_to_expiry=int(e("FNO_MIN_DAYS_TO_EXPIRY", "1")),
            sl_limit_buffer_pct=float(e("FNO_SL_LIMIT_BUFFER_PCT", "5")),
            enforce_market_hours=_bool("FNO_ENFORCE_MARKET_HOURS", "true"),
            dhan_client_id=e("DHAN_CLIENT_ID", ""),
            dhan_access_token=e("DHAN_ACCESS_TOKEN", ""),
            dhan_pin=e("DHAN_PIN", ""),
            dhan_totp_secret=e("DHAN_TOTP_SECRET", ""),
            kite_api_key=e("KITE_API_KEY", ""),
            kite_api_secret=e("KITE_API_SECRET", ""),
            kite_access_token=e("KITE_ACCESS_TOKEN", ""),
            kite_token_file=e("KITE_TOKEN_FILE", os.path.join(AGENT_DIR, ".kite_token")),
            tg_bot_token=e("FNO_TG_BOT_TOKEN", ""),
            tg_chat_id=e("FNO_TG_CHAT_ID", ""),
            journal_path=e("FNO_JOURNAL_PATH", os.path.join(AGENT_DIR, "fno_journal.db")),
        )
