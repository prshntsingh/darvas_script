"""
Dhan v2 broker for options.

Entry + protection use a Super Order (entry LIMIT + target leg + stop-loss leg in one call),
product MARGIN so positions can be carried to expiry. Dhan Forever/OCO orders only support
CNC/MTF, so they can't protect carry-forward F&O positions.

Docs: https://dhanhq.co/docs/v2/super-order/ , https://dhanhq.co/docs/v2/market-quote/
"""

import logging
import uuid
from typing import Optional

import requests

from fno_agent.brokers.base import (
    CANCELLED, COMPLETE, OPEN, REJECTED, BrokerAuthError, BrokerError, FnOBroker, OrderState,
)
from client_agent.dhan_token import force_login, fresh_token, is_token_error
from fno_agent.instruments import Contract

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dhan.co/v2"
AUTH_URL = "https://auth.dhan.co/app/generateAccessToken"

_STATUS_MAP = {
    "TRADED": COMPLETE,
    "CLOSED": COMPLETE,  # entry filled and a target/SL leg has since executed
    "PENDING": OPEN,
    "TRANSIT": OPEN,
    "PART_TRADED": OPEN,
    "TRIGGERED": OPEN,
    "CANCELLED": CANCELLED,
    "EXPIRED": CANCELLED,
    "REJECTED": REJECTED,
}


class DhanFnOBroker(FnOBroker):
    name = "dhan"
    protects_on_entry = True

    def __init__(self, client_id: str, access_token: str = "", pin: str = "",
                 totp_secret: str = "", dry_run: bool = True):
        super().__init__(dry_run)
        self.client_id = client_id
        self.access_token = access_token
        self.pin = pin
        self.totp_secret = totp_secret

    # --- auth -------------------------------------------------------------

    def connect(self) -> None:
        if not self.access_token and self.pin and self.totp_secret:
            # Reuse the token shared with the equity bot (a new login would invalidate its token)
            self.access_token = fresh_token("", self._login)
            if not self._token_works():
                self.access_token = fresh_token(self.access_token, self._login)
        if not self.access_token and not self.dry_run:
            raise BrokerAuthError("No Dhan access token and TOTP auto-login unavailable/failed.")

    def refresh_session(self) -> None:
        """Daily 08:00 refresh: one new login, shared with the equity bot through the token file."""
        if self.pin and self.totp_secret:
            self.access_token = force_login(self._login)

    def _token_works(self) -> bool:
        try:
            resp = requests.get(BASE_URL + "/profile", headers={"access-token": self.access_token}, timeout=10)
            return resp.status_code == 200
        except Exception as e:
            logger.warning(f"Could not verify Dhan token: {e}")
            return True  # network hiccup: don't burn a login; a failed call will still self-heal

    def _login(self) -> str:
        """Generate a new access token via PIN + TOTP (same flow as client_agent DhanBroker)."""
        import pyotp

        totp = pyotp.TOTP(self.totp_secret).now()
        try:
            resp = requests.post(
                AUTH_URL,
                params={"dhanClientId": self.client_id, "pin": self.pin, "totp": totp},
                timeout=10,
            )
            data = resp.json()
        except Exception as e:
            raise BrokerAuthError(f"Dhan auto-login error: {e}") from e
        if resp.status_code != 200 or "accessToken" not in data:
            raise BrokerAuthError(f"Dhan auto-login failed (HTTP {resp.status_code}): {data}")
        logger.info("Dhan access token refreshed via TOTP.")
        return data["accessToken"]

    def _request(self, method: str, path: str, json: Optional[dict] = None, _retry: bool = True):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "access-token": self.access_token,
            "client-id": self.client_id,
        }
        resp = requests.request(method, BASE_URL + path, headers=headers, json=json, timeout=10)
        if is_token_error(resp.status_code, resp.text):
            if _retry and self.pin and self.totp_secret:
                logger.warning(f"Dhan token rejected (HTTP {resp.status_code}); refreshing and retrying.")
                self.access_token = fresh_token(self.access_token, self._login)
                return self._request(method, path, json, _retry=False)
            raise BrokerAuthError(f"Dhan unauthorized (HTTP {resp.status_code}): {resp.text[:200]}")
        try:
            data = resp.json() if resp.text else {}
        except ValueError:
            data = {"raw": resp.text}
        if resp.status_code >= 400:
            raise BrokerError(f"Dhan {method} {path} HTTP {resp.status_code}: {data}")
        return data

    # --- market data -------------------------------------------------------

    def ltp(self, contract: Contract) -> Optional[float]:
        if not self.access_token:
            return None
        try:
            data = self._request("POST", "/marketfeed/ltp", {contract.exchange_segment: [int(contract.security_id)]})
            return float(data["data"][contract.exchange_segment][str(contract.security_id)]["last_price"])
        except Exception as e:
            logger.warning(f"Dhan LTP unavailable for {contract.describe()}: {e}")
            return None

    # --- orders -------------------------------------------------------------

    def place_entry(self, contract: Contract, qty: int, price: float,
                    stop_loss: float, target: Optional[float]) -> str:
        if target is None:
            raise BrokerError("Dhan super order needs a target")
        payload = {
            "dhanClientId": self.client_id,
            "correlationId": uuid.uuid4().hex[:25],
            "transactionType": "BUY",
            "exchangeSegment": contract.exchange_segment,
            "productType": "MARGIN",
            "orderType": "LIMIT",
            "securityId": str(contract.security_id),
            "quantity": int(qty),
            "price": price,
            "targetPrice": target,
            "stopLossPrice": stop_loss,
            "trailingJump": 0,
        }
        if self.dry_run:
            return self._dry_order("super-order", payload, qty)
        data = self._request("POST", "/super/orders", payload)
        order_id = data.get("orderId")
        if not order_id or data.get("orderStatus") == "REJECTED":
            raise BrokerError(f"Dhan super order rejected: {data}")
        logger.info(f"Dhan super order placed: {order_id} ({data.get('orderStatus')})")
        return str(order_id)

    def entry_status(self, order_id: str) -> OrderState:
        if order_id.startswith("DRY-"):
            return self._dry_status(order_id)
        orders = self._request("GET", "/super/orders")
        for o in orders if isinstance(orders, list) else orders.get("data", []):
            if str(o.get("orderId")) == str(order_id):
                raw = str(o.get("orderStatus", "")).upper()
                return OrderState(
                    status=_STATUS_MAP.get(raw, OPEN),
                    filled_qty=int(o.get("filledQty") or 0),
                    avg_price=float(o["averageTradedPrice"]) if o.get("averageTradedPrice") else None,
                    message=raw,
                )
        raise BrokerError(f"Dhan super order {order_id} not found")

    def cancel_entry(self, order_id: str) -> None:
        if order_id.startswith("DRY-"):
            self._dry_orders[order_id] = 0
            return
        self._request("DELETE", f"/super/orders/{order_id}/ENTRY_LEG")
        logger.info(f"Dhan super order {order_id}: entry leg cancelled.")
