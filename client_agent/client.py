"""
Trading Signal Execution Client — Standalone WebSocket Consumer.

Connects to the FastAPI broadcaster server, receives F&O trade signals,
and executes them via Zerodha Kite Connect or Dhan APIs.

Usage:
    python client.py                   # Uses .env in current directory
    python client.py /path/to/.env     # Uses custom env file

Broker selection via BROKER env var:
    BROKER=zerodha   → Zerodha Kite Connect (default)
    BROKER=dhan      → Dhan HQ

Intended to run on:
    - User's local PC (for static IP compliance with SEBI/Zerodha)
    - GCP Compute Engine with a static external IP
"""

import os
import sys
import io
import math
import csv
import json
import uuid
import asyncio
import logging
import time as time_mod
import requests
from datetime import datetime, time, timedelta, timezone

# --- Setup Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("client_agent")

# --- Load Environment ---
try:
    from dotenv import load_dotenv
except ImportError:
    logger.error("python-dotenv is required. Install: pip install python-dotenv")
    sys.exit(1)

env_file = sys.argv[1] if len(sys.argv) > 1 else ".env"
load_dotenv(env_file, override=True)
logger.info(f"Loaded configuration from {env_file}")

# --- Configuration ---
WS_SERVER_URL = os.environ.get("WS_SERVER_URL", "ws://localhost:8000/ws")
WS_AUTH_TOKEN = os.environ.get("WS_AUTH_TOKEN", "")

# Broker selection: "zerodha" or "dhan"
BROKER = os.environ.get("BROKER", "zerodha").lower().strip()

# Zerodha credentials
KITE_API_KEY = os.environ.get("KITE_API_KEY", "")
KITE_ACCESS_TOKEN = os.environ.get("KITE_ACCESS_TOKEN", "")

# Dhan credentials
DHAN_CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "")
DHAN_ACCESS_TOKEN = os.environ.get("DHAN_ACCESS_TOKEN", "")
DHAN_PIN = os.environ.get("DHAN_PIN", "")
DHAN_TOTP_SECRET = os.environ.get("DHAN_TOTP_SECRET", "")

# Execution filters
_RAW_ALLOWED_CHANNELS = os.environ.get("ALLOWED_CHANNELS", "").strip()
ALLOWED_CHANNELS = {c.strip().lower() for c in _RAW_ALLOWED_CHANNELS.split(",")} if _RAW_ALLOWED_CHANNELS else set()

# Capital Allocation
TRADE_AMOUNT_INR = int(os.environ.get("TRADE_AMOUNT_INR", "0"))
DEFAULT_QUANTITY = int(os.environ.get("DEFAULT_QUANTITY", "1"))

# Reconnection settings
RECONNECT_BASE_DELAY = 1.0    # seconds
RECONNECT_MAX_DELAY = 60.0    # seconds
RECONNECT_MULTIPLIER = 2.0


class InstrumentMap:
    """
    In-memory lookup for Kite instrument tokens.
    
    Loads the Zerodha instruments CSV into a dict keyed by
    (exchange, tradingsymbol) → instrument_token for O(1) lookups.
    
    The instruments CSV can be downloaded from:
        https://api.kite.trade/instruments
    """

    def __init__(self, csv_path: str = "instruments.csv"):
        self._map: dict[str, int] = {}
        self._csv_path = csv_path
        self._loaded = False

    def load(self):
        """Load instruments CSV into memory."""
        if not os.path.exists(self._csv_path):
            logger.warning(
                f"Instruments file '{self._csv_path}' not found. "
                f"Download from https://api.kite.trade/instruments"
            )
            return

        import csv

        count = 0
        with open(self._csv_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = f"{row['exchange']}:{row['tradingsymbol']}"
                self._map[key] = int(row["instrument_token"])
                count += 1

        self._loaded = True
        logger.info(f"Loaded {count} instruments into memory.")

    def get_token(self, exchange: str, tradingsymbol: str) -> int | None:
        """Look up instrument token by exchange and trading symbol."""
        key = f"{exchange}:{tradingsymbol}"
        return self._map.get(key)

    def build_tradingsymbol(self, symbol: str, strike: int, option_type: str, expiry: str = "") -> str:
        """
        Build the Zerodha-format trading symbol for an F&O instrument.
        
        Example: NIFTY2492524500CE (NIFTY + YYMDD expiry + strike + CE/PE)
        
        Note: If expiry is not provided, the caller must supply the full
        tradingsymbol directly. This is a helper for common cases.
        """
        if expiry:
            return f"{symbol}{expiry}{strike}{option_type}"
        # Without expiry, return a partial symbol the caller can complete
        return f"{symbol}{strike}{option_type}"


class ZerodhaBroker:
    """
    Broker execution layer using Zerodha Kite Connect.
    
    In production, this class would use the `kiteconnect` SDK.
    For safety, all order placements are logged but can be toggled 
    between LIVE and DRY_RUN mode.
    """

    def __init__(self, api_key: str, access_token: str, dry_run: bool = True):
        self.api_key = api_key
        self.access_token = access_token
        self.dry_run = dry_run
        self._kite = None
        self._instruments = InstrumentMap()

    def connect(self):
        """Initialize Kite Connect client and load instruments."""
        try:
            from kiteconnect import KiteConnect

            self._kite = KiteConnect(api_key=self.api_key)
            self._kite.set_access_token(self.access_token)
            logger.info("Kite Connect client initialized.")
        except ImportError:
            logger.warning(
                "kiteconnect SDK not installed. Running in simulation mode. "
                "Install: pip install kiteconnect"
            )
            self._kite = None

        # Load instrument map
        self._instruments.load()

    def place_order(self, signal: dict) -> dict | None:
        """
        Place a BUY or SELL order based on the trade signal.
        
        Args:
            signal: TradeSignal dict with action, symbol, strike, option_type, etc.
            
        Returns:
            Order response dict from Kite, or simulated response in dry-run mode.
        """
        action = signal["action"]
        symbol = signal["symbol"]
        strike = signal.get("strike", 0)
        option_type = signal.get("option_type", "CE")
        order_type = signal.get("order_type", "MARKET")
        entry_price = signal.get("entry_price")

        # Build trading symbol (without expiry — would need expiry logic in production)
        tradingsymbol = self._instruments.build_tradingsymbol(symbol, strike, option_type)

        # Determine exchange — NFO for all F&O instruments
        exchange = "NFO"

        # Look up instrument token
        token = self._instruments.get_token(exchange, tradingsymbol)
        if token:
            logger.info(f"Instrument token for {exchange}:{tradingsymbol} = {token}")
        else:
            logger.warning(
                f"Instrument token not found for {exchange}:{tradingsymbol}. "
                f"Order will proceed without token validation."
            )

        # Map action to Kite transaction type
        transaction_type = "BUY" if action == "BUY" else "SELL"

        # --- Capital Allocation & Quantity Calculation ---
        # For Zerodha equity/F&O
        quantity = DEFAULT_QUANTITY
        if TRADE_AMOUNT_INR > 0:
            if order_type == "LIMIT" and entry_price and entry_price > 0:
                quantity = max(1, math.floor(TRADE_AMOUNT_INR / entry_price))
            else:
                # MARKET order - fetch live price (LTP) to calculate quantity
                try:
                    if self._kite is not None:
                        quote_key = f"{exchange}:{tradingsymbol}"
                        logger.info(f"Fetching live quote for {quote_key}...")
                        t0 = time_mod.perf_counter()
                        quote = self._kite.quote([quote_key])
                        t1 = time_mod.perf_counter()
                        
                        ltp = quote.get(quote_key, {}).get("last_price", 0)
                        if ltp and ltp > 0:
                            quantity = max(1, math.floor(TRADE_AMOUNT_INR / ltp))
                            logger.info(f"Fetched LTP: {ltp} in {(t1-t0)*1000:.1f}ms. Calculated QTY: {quantity}")
                        else:
                            logger.warning(f"Failed to extract LTP from quote {quote}. Using DEFAULT_QUANTITY.")
                    else:
                        logger.info("[DRY RUN] No kite client, using DEFAULT_QUANTITY.")
                except Exception as e:
                    logger.error(f"Failed to fetch live price via Kite: {e}. Using DEFAULT_QUANTITY.")

        # Build order params
        order_params = {
            "tradingsymbol": tradingsymbol,
            "exchange": exchange,
            "transaction_type": transaction_type,
            "quantity": quantity,
            "product": "MIS",  # Intraday — configurable
            "order_type": order_type,
            "validity": "DAY",
        }

        if order_type == "LIMIT" and entry_price is not None:
            order_params["price"] = entry_price

        # Stoploss order (if SL provided)
        sl = signal.get("sl")

        if self.dry_run or self._kite is None:
            logger.info(f"[DRY RUN] Would place order: {json.dumps(order_params, indent=2)}")
            if sl:
                logger.info(f"[DRY RUN] Would set SL at: {sl}")
            return {"order_id": "DRY_RUN", "status": "SIMULATED", "params": order_params}

        try:
            order_id = self._kite.place_order(
                variety="regular",
                **order_params,
            )
            logger.info(f"Order placed successfully. Order ID: {order_id}")

            # Place SL order if stoploss is specified
            if sl:
                sl_params = {
                    "tradingsymbol": tradingsymbol,
                    "exchange": exchange,
                    "transaction_type": "SELL" if transaction_type == "BUY" else "BUY",
                    "quantity": order_params["quantity"],
                    "product": "MIS",
                    "order_type": "SL",
                    "trigger_price": sl,
                    "validity": "DAY",
                }
                sl_order_id = self._kite.place_order(variety="regular", **sl_params)
                logger.info(f"SL order placed. Order ID: {sl_order_id}")

            return {"order_id": order_id, "status": "PLACED"}

        except Exception as e:
            logger.error(f"Failed to place order: {e}")
            return None

    def exit_position(self, signal: dict) -> dict | None:
        """
        Exit an existing position by placing an opposite market order.
        
        For EXIT signals, we place a counter-trade at market price.
        """
        symbol = signal["symbol"]
        strike = signal.get("strike", 0)
        option_type = signal.get("option_type", "CE")

        tradingsymbol = self._instruments.build_tradingsymbol(symbol, strike, option_type)
        exchange = "NFO"

        # EXIT → place a SELL order (assuming we were long; reverse for shorts)
        # In production, query positions API to determine direction
        exit_params = {
            "tradingsymbol": tradingsymbol,
            "exchange": exchange,
            "transaction_type": "SELL",  # Assumes long position
            "quantity": 1,
            "product": "MIS",
            "order_type": "MARKET",
            "validity": "DAY",
        }

        if self.dry_run or self._kite is None:
            logger.info(f"[DRY RUN] Would exit position: {json.dumps(exit_params, indent=2)}")
            return {"order_id": "DRY_RUN_EXIT", "status": "SIMULATED", "params": exit_params}

        try:
            order_id = self._kite.place_order(variety="regular", **exit_params)
            logger.info(f"Exit order placed. Order ID: {order_id}")
            return {"order_id": order_id, "status": "EXITED"}
        except Exception as e:
            logger.error(f"Failed to exit position: {e}")
            return None

    def handle_signal(self, signal: dict) -> dict | None:
        """Route a trade signal to the appropriate handler."""
        action = signal.get("action", "").upper()

        if action in ("BUY", "SELL"):
            return self.place_order(signal)
        elif action == "EXIT":
            return self.exit_position(signal)
        elif action == "UPDATE":
            logger.info(f"UPDATE signal received (informational): {signal}")
            return {"status": "ACKNOWLEDGED", "action": "UPDATE"}
        else:
            logger.warning(f"Unknown action '{action}' in signal: {signal}")
            return None


# ============================================================================
# Dhan Broker
# ============================================================================

class DhanScripMap:
    """
    In-memory lookup for Dhan security IDs.

    Downloads the live Dhan scrip master CSV at startup and builds a
    dict keyed by trading symbol → {"id": security_id, "segment": exchange_segment}.
    Prioritizes NSE over BSE for equities available on both.
    """

    SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

    def __init__(self):
        self._equity_map: dict[str, dict] = {}
        self._fno_map: dict[str, dict] = {}
        self._loaded = False

    def load(self):
        """Download and parse the Dhan scrip master CSV."""
        logger.info("Downloading Dhan scrip master CSV...")
        try:
            response = requests.get(self.SCRIP_MASTER_URL, timeout=30)
            if response.status_code != 200:
                logger.error(f"Failed to download Dhan scrip master: HTTP {response.status_code}")
                return

            csv_data = csv.DictReader(io.StringIO(response.text))

            for row in csv_data:
                exchange = row.get("SEM_EXM_EXCH_ID", "")
                inst_type = row.get("SEM_INSTRUMENT_NAME", "")
                symbol = row.get("SEM_TRADING_SYMBOL", "")
                sec_id = row.get("SEM_SMST_SECURITY_ID", "")

                if not symbol or not sec_id:
                    continue

                # --- Equities ---
                if exchange in ("NSE", "BSE") and inst_type == "EQUITY":
                    clean_symbol = symbol.replace("-EQ", "").strip()
                    segment = "NSE_EQ" if exchange == "NSE" else "BSE_EQ"

                    # Prefer NSE over BSE
                    if clean_symbol not in self._equity_map or segment == "NSE_EQ":
                        self._equity_map[clean_symbol] = {
                            "id": sec_id,
                            "segment": segment,
                        }

                # --- F&O (Index Options on NSE) ---
                elif exchange == "NSE" and inst_type in ("OPTIDX", "FUTIDX"):
                    clean_symbol = symbol.strip()
                    self._fno_map[clean_symbol] = {
                        "id": sec_id,
                        "segment": "NSE_FNO",
                    }

            self._loaded = True
            logger.info(
                f"Loaded {len(self._equity_map)} equities and "
                f"{len(self._fno_map)} F&O instruments from Dhan."
            )

        except Exception as e:
            logger.error(f"Error loading Dhan scrip master: {e}")

    def get_equity(self, symbol: str) -> dict | None:
        """Look up equity scrip info by symbol."""
        return self._equity_map.get(symbol.upper())

    def get_fno(self, tradingsymbol: str) -> dict | None:
        """Look up F&O scrip info by exact trading symbol."""
        return self._fno_map.get(tradingsymbol)

    def build_fno_symbol(self, symbol: str, strike: int, option_type: str, expiry: str = "") -> str:
        """
        Build a Dhan-format F&O trading symbol.
        Example: NIFTY-2024-SEP-24500-CE (approximate — actual format depends on Dhan master).
        """
        if expiry:
            return f"{symbol}{expiry}{strike}{option_type}"
        return f"{symbol}{strike}{option_type}"


def _is_amo_window() -> bool:
    """
    Detect if current IST time is outside market hours (AMO window).
    AMO is active:
      - Weekends (Saturday/Sunday)
      - Before 9:15 AM IST or after 3:15 PM IST on weekdays
    """
    ist = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(ist)

    # Weekend
    if now.weekday() >= 5:
        return True

    market_start = time(9, 15)
    market_end = time(15, 15)

    return now.time() < market_start or now.time() >= market_end


class DhanBroker:
    """
    Broker execution layer using Dhan HQ REST API.

    Uses the Dhan v2 orders endpoint for both live and AMO orders.
    Downloads the scrip master at startup for instrument resolution.
    """

    ORDERS_URL = "https://api.dhan.co/v2/orders"

    def __init__(self, client_id: str, access_token: str, pin: str = "", totp_secret: str = "", dry_run: bool = True):
        self.client_id = client_id
        self.access_token = access_token
        self.pin = pin
        self.totp_secret = totp_secret
        self.dry_run = dry_run
        self._scrips = DhanScripMap()

    def connect(self):
        """Authenticate (if needed) and prepare for order placement."""
        if not self.access_token and self.pin and self.totp_secret:
            success = self._auto_login()
            if not success:
                logger.error("Initial Dhan auto-login failed. Exiting.")
                sys.exit(1)
        
        self._scrips.load()

    def _auto_login(self) -> bool:
        """Programmatically generate Dhan access token using pyotp."""
        try:
            import pyotp
            logger.info("Generating Dhan TOTP for auto-login...")
            totp = pyotp.TOTP(self.totp_secret)
            current_totp = totp.now()

            url = f"https://auth.dhan.co/app/generateAccessToken?dhanClientId={self.client_id}&pin={self.pin}&totp={current_totp}"
            response = requests.post(url, timeout=10)

            if response.status_code == 200:
                data = response.json()
                if "accessToken" in data:
                    self.access_token = data["accessToken"]
                    logger.info("Successfully generated Dhan access token via auto-login!")
                    return True
                else:
                    logger.error(f"Dhan auto-login failed: {data}")
                    return False
            else:
                logger.error(f"Dhan auto-login HTTP {response.status_code}: {response.text}")
                return False
        except ImportError:
            logger.error("pyotp is required for auto-login. Please run: pip install pyotp")
            return False
        except Exception as e:
            logger.error(f"Dhan auto-login error: {e}")
            return False

    def _build_headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "access-token": self.access_token,
        }

    def _place_dhan_order(self, payload: dict, is_retry: bool = False) -> dict | None:
        """Send an order to Dhan v2 API."""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would send Dhan order: {json.dumps(payload, indent=2)}")
            return {"order_id": "DRY_RUN", "status": "SIMULATED", "params": payload}

        try:
            response = requests.post(
                self.ORDERS_URL,
                headers=self._build_headers(),
                json=payload,
                timeout=10,
            )
            result = response.json()

            if response.status_code == 200 and "orderId" in result:
                order_id = result["orderId"]
                order_status = result.get("orderStatus", "QUEUED")
                logger.info(f"Dhan order placed. ID: {order_id}, Status: {order_status}")
                return {"order_id": order_id, "status": order_status}
            
            # --- 24/7 Self-Healing Auth ---
            # If token expired (401 Unauthorized or similar), auto-refresh and retry once.
            elif response.status_code in (401, 403) or "unauthorized" in str(result).lower():
                if not is_retry and self.pin and self.totp_secret:
                    logger.warning(f"Dhan token expired (HTTP {response.status_code}). Triggering mid-trade auto-login...")
                    if self._auto_login():
                        logger.info("Auto-login succeeded! Retrying order...")
                        return self._place_dhan_order(payload, is_retry=True)
                    else:
                        logger.error("Mid-trade auto-login failed. Order aborted.")
                
                logger.error(f"Dhan order rejected (Unauthorized): {result}")
                return {"status": "REJECTED_UNAUTHORIZED", "response": result}

            else:
                logger.error(f"Dhan order rejected: {result}")
                return {"status": "REJECTED", "response": result}

        except Exception as e:
            logger.error(f"Dhan API error: {e}")
            return None

    def place_order(self, signal: dict) -> dict | None:
        """
        Place a BUY equity order on Dhan based on the trade signal.
        
        This is BUY-ONLY equity mode matching the reference trading bot:
        - Looks up stock_symbol in equity scrip map
        - Uses CNC product type (cash-and-carry for delivery)
        - 1% price buffer for LIMIT orders, rounded to tick size
        - Auto-detects AMO windows
        """
        stock_symbol = signal.get("stock_symbol", "")
        order_type = signal.get("order_type", "MARKET")
        entry_price = signal.get("entry_price", 0)

        # Resolve equity instrument
        scrip_info = self._scrips.get_equity(stock_symbol)

        if not scrip_info:
            logger.error(f"Scrip not found for '{stock_symbol}'. Cannot place order.")
            return None

        scrip_id = scrip_info["id"]
        segment = scrip_info["segment"]

        use_amo = _is_amo_window()

        # Strictly CNC for equities
        product_type = "CNC"

        # Price handling: for LIMIT orders, add 1% buffer and round to tick size (0.05)
        if order_type == "LIMIT" and entry_price > 0:
            raw_price = round((entry_price * 1.01) / 0.05) * 0.05
            price = float(f"{raw_price:.2f}")
        else:
            order_type = "MARKET"
            price = 0.0

        # --- Capital Allocation & Quantity Calculation ---
        quantity = DEFAULT_QUANTITY
        if TRADE_AMOUNT_INR > 0:
            if order_type == "LIMIT" and entry_price > 0:
                quantity = max(1, math.floor(TRADE_AMOUNT_INR / entry_price))
            else:
                # MARKET order - fetch live price from Yahoo Finance
                try:
                    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{stock_symbol}.NS?interval=1d&range=1d"
                    headers = {"User-Agent": "Mozilla/5.0"}
                    logger.info(f"Fetching live quote from Yahoo Finance for {stock_symbol}.NS...")
                    t0 = time_mod.perf_counter()
                    resp = requests.get(url, headers=headers, timeout=5)
                    t1 = time_mod.perf_counter()
                    
                    if resp.status_code == 200:
                        data = resp.json()
                        ltp = data.get("chart", {}).get("result", [{}])[0].get("meta", {}).get("regularMarketPrice", 0)
                        if ltp and ltp > 0:
                            quantity = max(1, math.floor(TRADE_AMOUNT_INR / ltp))
                            logger.info(f"Fetched LTP: {ltp} in {(t1-t0)*1000:.1f}ms. Calculated QTY: {quantity}")
                        else:
                            quantity = max(1, DEFAULT_QUANTITY)
                            logger.warning(f"LTP not found in YF response. Using DEFAULT_QUANTITY: {quantity}")
                    else:
                        quantity = max(1, DEFAULT_QUANTITY)
                        logger.warning(f"YF returned {resp.status_code}. Using DEFAULT_QUANTITY: {quantity}")
                except Exception as e:
                    quantity = max(1, DEFAULT_QUANTITY)
                    logger.error(f"Failed to fetch live price via YF: {e}. Using DEFAULT_QUANTITY: {quantity}")

        payload = {
            "dhanClientId": self.client_id,
            "correlationId": str(uuid.uuid4())[:30],
            "transactionType": "BUY",
            "exchangeSegment": segment,
            "productType": product_type,
            "orderType": order_type,
            "validity": "DAY",
            "securityId": str(scrip_id),
            "quantity": quantity,
            "price": price,
            "disclosedQuantity": 0,
            "triggerPrice": 0.0,
            "afterMarketOrder": use_amo,
        }

        if use_amo:
            payload["amoTime"] = "OPEN"
            logger.info(f"[🌙 AMO] Placing after-market order for {stock_symbol}")
        else:
            logger.info(f"[☀️ LIVE] Placing live order for {stock_symbol}")

        logger.info(
            f"Executing → Symbol: {stock_symbol} | Segment: {segment} "
            f"| QTY: {quantity} | Type: {order_type} | Price: {price}"
        )

        return self._place_dhan_order(payload)

    def handle_signal(self, signal: dict) -> dict | None:
        """
        Handle an incoming equity trade signal.
        
        In BUY-ONLY equity mode, all signals from the server are BUY orders.
        The server-side filters already block sell/exit/index/F&O signals.
        """
        stock_symbol = signal.get("stock_symbol", "UNKNOWN")
        logger.info(f"Processing equity signal for {stock_symbol}")
        return self.place_order(signal)


def _create_broker(dry_run: bool):
    """
    Factory function: create the broker based on BROKER env var.
    Returns a broker instance with a .handle_signal(signal) method.
    """
    if BROKER == "dhan":
        if not DHAN_CLIENT_ID:
            logger.error("Dhan credentials missing. Set DHAN_CLIENT_ID.")
            sys.exit(1)
            
        if not DHAN_ACCESS_TOKEN and not (DHAN_PIN and DHAN_TOTP_SECRET):
            logger.error("Dhan token missing and no auto-login credentials (DHAN_PIN, DHAN_TOTP_SECRET) found.")
            sys.exit(1)
            
        broker = DhanBroker(
            client_id=DHAN_CLIENT_ID,
            access_token=DHAN_ACCESS_TOKEN,
            pin=DHAN_PIN,
            totp_secret=DHAN_TOTP_SECRET,
            dry_run=dry_run,
        )
        broker.connect()
        logger.info("Using Dhan HQ broker.")
        return broker
    else:
        broker = ZerodhaBroker(
            api_key=KITE_API_KEY,
            access_token=KITE_ACCESS_TOKEN,
            dry_run=dry_run,
        )
        broker.connect()
        logger.info("Using Zerodha Kite Connect broker.")
        return broker


async def connect_and_listen():
    """
    Main client loop: connect to WebSocket server, authenticate,
    and process incoming trade signals with automatic reconnection.
    """
    try:
        import websockets
    except ImportError:
        logger.error("websockets is required. Install: pip install websockets")
        return

    # Initialize broker
    dry_run = os.environ.get("DRY_RUN", "true").lower() in ("true", "1", "yes")
    broker = _create_broker(dry_run)

    if dry_run:
        logger.info("Running in DRY RUN mode. Orders will be simulated.")
    else:
        logger.warning(f"Running in LIVE mode. Orders WILL be placed on {BROKER.upper()}.")

    delay = RECONNECT_BASE_DELAY

    while True:
        try:
            # Build connection URL with auth token as query param
            connect_url = WS_SERVER_URL
            if WS_AUTH_TOKEN:
                separator = "&" if "?" in connect_url else "?"
                connect_url = f"{connect_url}{separator}token={WS_AUTH_TOKEN}"

            logger.info(f"Connecting to {WS_SERVER_URL}...")

            async with websockets.connect(
                connect_url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            ) as ws:
                logger.info("Connected to broadcaster server.")
                delay = RECONNECT_BASE_DELAY  # Reset delay on successful connect

                async for raw_message in ws:
                    try:
                        t_recv = time_mod.perf_counter()
                        signal = json.loads(raw_message)
                        # Option signals are handled by the separate fno_agent process
                        if signal.get("asset_class") == "FNO":
                            logger.info(f"Ignoring FnO signal {signal.get('signal_id')} (handled by fno_agent).")
                            continue
                        # Filter by channel if ALLOWED_CHANNELS is configured
                        channel_label = signal.get("channel_label", "").lower()
                        if ALLOWED_CHANNELS and channel_label not in ALLOWED_CHANNELS:
                            logger.info(f"Skipping signal from channel '{channel_label}' (not in ALLOWED_CHANNELS)")
                            continue

                        logger.info(
                            f"Received signal: {signal.get('stock_symbol')} "
                            f"@ {signal.get('entry_price', 'MARKET')} "
                            f"({signal.get('order_type', 'MARKET')}) "
                            f"via {signal.get('source', '?')} "
                            f"[Channel: {channel_label}]"
                        )

                        # Log server-side latency if available
                        server_latency = signal.pop("_latency_ms", None)
                        if server_latency:
                            logger.info(
                                f"[⚡ SERVER LATENCY] "
                                f"extraction={server_latency.get('extraction', '?')}ms "
                                f"({server_latency.get('source', '?')}) | "
                                f"broadcast={server_latency.get('broadcast', '?')}ms | "
                                f"total_server={server_latency.get('total_server', '?')}ms"
                            )

                        # Execute the trade
                        t_exec_start = time_mod.perf_counter()
                        result = broker.handle_signal(signal)
                        t_exec_end = time_mod.perf_counter()
                        exec_ms = (t_exec_end - t_exec_start) * 1000
                        total_client_ms = (t_exec_end - t_recv) * 1000

                        if result:
                            logger.info(f"Execution result: {result}")
                        else:
                            logger.warning("Signal handling returned no result.")

                        # Full latency summary
                        server_total = server_latency.get("total_server", 0) if server_latency else 0
                        logger.info(
                            f"[⚡ CLIENT LATENCY] "
                            f"broker_exec={exec_ms:.1f}ms | "
                            f"client_total={total_client_ms:.1f}ms | "
                            f"server+client={server_total + total_client_ms:.1f}ms"
                        )

                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse message as JSON: {e}")
                    except Exception as e:
                        logger.error(f"Error processing signal: {e}", exc_info=True)

        except asyncio.CancelledError:
            logger.info("Client shutdown requested.")
            break
        except Exception as e:
            logger.error(f"WebSocket connection error: {e}")
            logger.info(f"Reconnecting in {delay:.1f}s...")
            await asyncio.sleep(delay)
            delay = min(delay * RECONNECT_MULTIPLIER, RECONNECT_MAX_DELAY)


if __name__ == "__main__":
    logger.info("Starting Trading Signal Execution Client...")
    logger.info(f"Server: {WS_SERVER_URL}")
    logger.info(f"Broker: {BROKER.upper()}")
    logger.info(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        asyncio.run(connect_and_listen())
    except KeyboardInterrupt:
        logger.info("Client stopped by user.")
