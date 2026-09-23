"""Broker interface used by the FnO executor. All methods are synchronous (run via asyncio.to_thread)."""

import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Optional

from fno_agent.instruments import Contract

logger = logging.getLogger(__name__)

# Normalised order states
OPEN, COMPLETE, CANCELLED, REJECTED = "OPEN", "COMPLETE", "CANCELLED", "REJECTED"
TERMINAL = {COMPLETE, CANCELLED, REJECTED}


class BrokerError(Exception):
    pass


class BrokerAuthError(BrokerError):
    pass


@dataclass
class OrderState:
    status: str  # OPEN | COMPLETE | CANCELLED | REJECTED
    filled_qty: int = 0
    avg_price: Optional[float] = None
    message: str = ""


class FnOBroker(ABC):
    name = "base"
    # True when place_entry() already attaches SL + target at the broker (Dhan Super Order).
    # False when protection is placed separately after the fill (Kite GTT OCO).
    protects_on_entry = False

    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self._dry_orders: Dict[str, int] = {}

    @abstractmethod
    def connect(self) -> None:
        """Authenticate. Raises BrokerAuthError if credentials are unusable."""

    def refresh_session(self) -> None:
        """Proactive daily session refresh. Default: reconnect."""
        self.connect()

    @abstractmethod
    def ltp(self, contract: Contract) -> Optional[float]:
        """Last traded price, or None if unavailable."""

    @abstractmethod
    def place_entry(self, contract: Contract, qty: int, price: float,
                    stop_loss: float, target: Optional[float]) -> str:
        """Place a BUY LIMIT entry. Returns the broker order id."""

    @abstractmethod
    def entry_status(self, order_id: str) -> OrderState:
        ...

    @abstractmethod
    def cancel_entry(self, order_id: str) -> None:
        ...

    def place_protection(self, contract: Contract, qty: int, stop_loss: float,
                         target: float, last_price: float) -> str:
        """Place a broker-side OCO (SL + target) SELL for qty. Returns its id."""
        raise NotImplementedError(f"{self.name} protects on entry")

    # --- dry-run helpers -------------------------------------------------

    def _dry_order(self, kind: str, payload: dict, qty: int) -> str:
        order_id = f"DRY-{uuid.uuid4().hex[:10]}"
        self._dry_orders[order_id] = qty
        logger.info(f"[DRY RUN] {self.name} {kind} {order_id}: {payload}")
        return order_id

    def _dry_status(self, order_id: str) -> OrderState:
        qty = self._dry_orders.get(order_id, 0)
        return OrderState(COMPLETE if qty else CANCELLED, filled_qty=qty, message="simulated fill")
