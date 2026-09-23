"""
FnO signal execution: validate → resolve contract → size → LIMIT entry → wait for fill → broker-side SL/targets.

Every step is written to the journal so a crash mid-trade can be resumed by reconcile().
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from fno_agent import journal as J
from fno_agent.brokers.base import TERMINAL, BrokerAuthError, BrokerError, FnOBroker, OrderState
from fno_agent.instruments import Contract, ContractNotFound, InstrumentResolver
from fno_agent.market import is_market_open, now_ist, round_to_tick
from fno_agent.notifier import Notifier
from fno_agent.settings import Settings
from fno_agent.sizer import size_position, split_tranches
from services.fno_parser import FnOSignal, _validate

logger = logging.getLogger(__name__)

ENTRY_TICKS_ABOVE_LTP = 2  # limit = min(entry_max, LTP + 2 ticks)


def describe(sig: FnOSignal) -> str:
    exp = f" {sig.expiry_day or ''}{sig.expiry_month}" if sig.expiry_month else ""
    return (f"{sig.symbol} {sig.strike:g}{sig.option_type}{exp} @ {sig.entry_min:g}-{sig.entry_max:g} "
            f"SL {sig.stop_loss:g} T {','.join(f'{t:g}' for t in sig.targets)}")


def levels(sig: FnOSignal, contract: Contract) -> Tuple[float, List[float]]:
    """Stop-loss and targets snapped to the contract's tick grid."""
    tick = contract.tick_size
    return (round_to_tick(sig.stop_loss, tick),
            [round_to_tick(t, tick) for t in sig.targets])


class FnOExecutor:
    def __init__(
        self,
        settings: Settings,
        broker: FnOBroker,
        resolver: InstrumentResolver,
        journal: J.Journal,
        notifier: Notifier,
        holidays: Iterable = (),
        clock: Callable[[], datetime] = now_ist,
        sleep: Callable = asyncio.sleep,
    ):
        self.s = settings
        self.broker = broker
        self.resolver = resolver
        self.journal = journal
        self.notifier = notifier
        self.holidays = set(holidays)
        self.clock = clock
        self.sleep = sleep

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def handle(self, signal: dict) -> str:
        """Process one broadcast FnO signal. Returns the final journal status (or IGNORED/DUPLICATE)."""
        sid = signal.get("signal_id") or f"nosid:{abs(hash(signal.get('raw_text', '')))}"
        label = str(signal.get("channel_label", "")).lower()
        if self.s.allowed_channels and label not in self.s.allowed_channels:
            logger.info(f"Ignoring {sid}: channel '{label}' not in ALLOWED_CHANNELS")
            return "IGNORED"
        if not self.journal.try_claim(sid, signal):
            logger.info(f"Duplicate signal {sid} ignored.")
            return "DUPLICATE"

        try:
            return await self._execute(sid, signal)
        except ContractNotFound as e:
            return await self._reject(sid, f"contract not found: {e}")
        except BrokerAuthError as e:
            return await self._fail(sid, f"BROKER AUTH FAILED: {e}")
        except Exception as e:
            logger.exception(f"Signal {sid} failed")
            return await self._fail(sid, f"error: {e}")

    async def _reject(self, sid: str, reason: str) -> str:
        self.journal.update_signal(sid, J.REJECTED, reason)
        await self.notifier.send(f"⛔ Skipped {sid}: {reason}")
        return J.REJECTED

    async def _fail(self, sid: str, reason: str) -> str:
        row = self.journal.get_signal(sid)
        if row and row["status"] in J.PENDING_STATES:
            # Orders may be live at the broker: keep it pending so reconcile() picks it up.
            self.journal.update_signal(sid, reason=reason)
            await self.notifier.send(f"🚨 {sid} needs attention (orders may be live): {reason}")
            return row["status"]
        self.journal.update_signal(sid, J.ERROR, reason)
        await self.notifier.send(f"🚨 {sid} failed: {reason}")
        return J.ERROR

    # ------------------------------------------------------------------
    # Validate → resolve → size → enter
    # ------------------------------------------------------------------

    async def _execute(self, sid: str, signal: dict) -> str:
        try:
            sig = _validate(FnOSignal.model_validate(signal))
        except Exception as e:
            sig = None
            logger.warning(f"Bad FnO payload {sid}: {e}")
        if sig is None:
            return await self._reject(sid, "invalid signal payload")
        desc = describe(sig)
        logger.info(f"Signal {sid}: {desc}")

        now = self.clock()
        if self.s.enforce_market_hours and not self.s.dry_run and not is_market_open(now, self.holidays):
            return await self._reject(sid, f"market closed ({now:%a %H:%M} IST): {desc}")

        contract = self.resolver.resolve(sig.symbol, sig.strike, sig.option_type,
                                         sig.expiry_month, sig.expiry_day, today=now.date())
        size = size_position(self.s.capital_per_trade, contract.lot_size, sig.entry_max, self.s.max_lots)
        if size.lots == 0:
            return await self._reject(
                sid, f"1 lot of {contract.describe()} costs ₹{size.cost_per_lot:,.0f} "
                     f"> budget ₹{self.s.capital_per_trade:,.0f}")

        tick = contract.tick_size
        ltp = await asyncio.to_thread(self.broker.ltp, contract)
        if ltp is not None:
            if ltp > sig.entry_max * (1 + self.s.chase_pct / 100):
                return await self._reject(sid, f"missed entry: LTP {ltp} > {sig.entry_max} +{self.s.chase_pct}% ({desc})")
            if ltp <= sig.stop_loss:
                return await self._reject(sid, f"LTP {ltp} already at/below SL {sig.stop_loss} ({desc})")
            limit = min(sig.entry_max, ltp + ENTRY_TICKS_ABOVE_LTP * tick)
        else:
            logger.warning(f"No LTP for {contract.describe()}; using entry_max as limit.")
            limit = sig.entry_max
        limit = round_to_tick(limit, tick, "down")
        sl, targets = levels(sig, contract)

        deadline = datetime.now(timezone.utc) + timedelta(seconds=self.s.entry_timeout_sec)
        self.journal.update_signal(sid, contract=json.dumps(contract.to_dict()), planned_qty=size.qty,
                                   entry_deadline=deadline.isoformat())

        # Dhan: one super order per target tranche (SL/target attached). Kite: one entry, GTTs after fill.
        if self.broker.protects_on_entry:
            plan = [(lots * contract.lot_size, tgt) for lots, tgt in split_tranches(size.lots, targets)]
        else:
            plan = [(size.qty, None)]

        placed, errors = 0, []
        for qty, tgt in plan:
            try:
                oid = await asyncio.to_thread(self.broker.place_entry, contract, qty, limit, sl, tgt)
            except BrokerAuthError:
                if placed:
                    errors.append("auth failure mid-placement")
                    break
                raise
            except Exception as e:
                errors.append(str(e))
                continue
            self.journal.add_order(sid, J.ENTRY, oid, qty, limit, sl, tgt, "OPEN")
            if not placed:
                self.journal.update_signal(sid, J.ENTRY_PLACED)
            placed += 1

        if not placed:
            return await self._reject(sid, f"entry rejected by broker: {'; '.join(errors)}")
        await self.notifier.send(
            f"🟢 BUY {size.lots} lot(s) = {size.qty} x {contract.describe()} LIMIT {limit} "
            f"(LTP {ltp if ltp is not None else 'n/a'}) SL {sl} T {targets} [{sid}]"
            + (f"\n⚠️ some tranches failed: {'; '.join(errors)}" if errors else "")
            + ("\nℹ️ signal says go slow/limited qty" if sig.caution else "")
        )
        return await self.await_fill_and_protect(sid)

    # ------------------------------------------------------------------
    # Fill watch → protection (also used by reconcile after a restart)
    # ------------------------------------------------------------------

    async def await_fill_and_protect(self, sid: str) -> str:
        row = self.journal.get_signal(sid)
        contract = Contract.from_dict(json.loads(row["contract"]))
        sig = FnOSignal.model_validate(json.loads(row["payload"]))
        deadline = datetime.fromisoformat(row["entry_deadline"])
        entries = self.journal.orders_for(sid, J.ENTRY)

        states: Dict[int, OrderState] = await self._poll_until(entries, deadline)
        filled = sum(st.filled_qty for st in states.values())
        still_open = [e["broker_order_id"] for e in entries if states[e["id"]].status not in TERMINAL]

        if still_open:
            await self.notifier.send(f"🚨 {sid}: could not cancel entry order(s) {still_open}; check broker!")

        if filled == 0:
            self.journal.update_signal(sid, J.CANCELLED, "no fill before timeout", filled_qty=0)
            await self.notifier.send(f"⌛ No fill for {contract.describe()} within "
                                     f"{self.s.entry_timeout_sec}s; entry cancelled [{sid}]")
            return J.CANCELLED

        self.journal.update_signal(sid, J.FILLED, filled_qty=filled)
        avg = next((st.avg_price for st in states.values() if st.avg_price), None)

        if self.broker.protects_on_entry:
            self.journal.update_signal(sid, J.PROTECTED)
            await self.notifier.send(f"✅ Filled {filled} x {contract.describe()} avg {avg or '?'}; "
                                     f"SL/target legs live at {self.broker.name} [{sid}]")
            return J.PROTECTED

        return await self._protect(sid, sig, contract, filled, avg)

    async def _poll_until(self, entries, deadline: datetime) -> Dict[int, OrderState]:
        states: Dict[int, OrderState] = {e["id"]: OrderState("OPEN") for e in entries}
        cancelled = False
        while True:
            for e in entries:
                if states[e["id"]].status in TERMINAL:
                    continue
                try:
                    st = await asyncio.to_thread(self.broker.entry_status, e["broker_order_id"])
                except BrokerError as ex:
                    logger.warning(f"Status check failed for {e['broker_order_id']}: {ex}")
                    continue
                states[e["id"]] = st
                self.journal.update_order(e["id"], st.status, st.filled_qty)

            if all(st.status in TERMINAL for st in states.values()) or cancelled:
                return states

            if datetime.now(timezone.utc) >= deadline:
                for e in entries:
                    if states[e["id"]].status not in TERMINAL:
                        try:
                            await asyncio.to_thread(self.broker.cancel_entry, e["broker_order_id"])
                        except Exception as ex:
                            logger.error(f"Cancel failed for {e['broker_order_id']}: {ex}")
                cancelled = True  # one final status poll to pick up partial fills
                await self.sleep(1)
                continue

            await self.sleep(self.s.poll_interval_sec)

    async def _protect(self, sid: str, sig: FnOSignal, contract: Contract, filled: int,
                       avg: Optional[float]) -> str:
        sl, targets = levels(sig, contract)
        tranches = split_tranches(filled // contract.lot_size, targets)
        done = self.journal.orders_for(sid, J.PROTECTION)  # already placed before a crash
        last_price = await asyncio.to_thread(self.broker.ltp, contract) or avg or sig.entry_max

        errors = []
        for lots, tgt in tranches[len(done):]:
            qty = lots * contract.lot_size
            try:
                pid = await asyncio.to_thread(self.broker.place_protection, contract, qty, sl, tgt, last_price)
                self.journal.add_order(sid, J.PROTECTION, pid, qty, None, sl, tgt, "ACTIVE")
            except Exception as e:
                errors.append(f"{qty}@T{tgt}: {e}")

        if errors:
            self.journal.update_signal(sid, J.UNPROTECTED, "; ".join(errors))
            await self.notifier.send(f"🚨 UNPROTECTED position {filled} x {contract.describe()} — "
                                     f"place SL {sl} manually! {errors} [{sid}]")
            return J.UNPROTECTED

        self.journal.update_signal(sid, J.PROTECTED)
        await self.notifier.send(
            f"✅ Filled {filled} x {contract.describe()} avg {avg or '?'}; OCO SL {sl} / targets "
            f"{[f'{l} lot@{t}' for l, t in tranches]} [{sid}]")
        return J.PROTECTED

    # ------------------------------------------------------------------
    # Crash recovery
    # ------------------------------------------------------------------

    async def reconcile(self):
        """Resume trades interrupted by a restart."""
        for row in self.journal.signals_with_status(J.RECEIVED):
            # Crashed before any entry was recorded; an order may still have reached the broker.
            self.journal.update_signal(row["signal_id"], J.ERROR, "interrupted before entry was recorded")
            await self.notifier.send(f"🚨 {row['signal_id']} was interrupted before entry was recorded; "
                                     f"verify no stray order at the broker.")
        for row in self.journal.pending_signals():
            sid = row["signal_id"]
            logger.info(f"Reconciling {sid} ({row['status']})")
            await self.notifier.send(f"♻️ Resuming {sid} after restart ({row['status']})")
            try:
                await self.await_fill_and_protect(sid)
            except Exception as e:
                logger.exception(f"Reconcile failed for {sid}")
                await self._fail(sid, f"reconcile error: {e}")
