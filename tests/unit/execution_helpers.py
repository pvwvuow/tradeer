"""Shared helpers for the Phase 8 tests: an execution engine on a FakeMT5 behind the real
gateway, with the live broker, the paper broker and a temporary database (test code only)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from app.brokers.live_broker import LiveBroker
from app.brokers.market import GatewayMarket
from app.brokers.paper_broker import PaperBroker
from app.core.execution_settings import ExecutionConfig
from app.domain.modes import OperatingMode
from app.domain.signals import SignalRecord
from app.engine.execution import ExecutionEngine
from app.mt5.gateway import MT5Gateway
from app.mt5.risk_reads import GatewayRiskBroker
from app.risk.risk_manager import RiskManager
from app.risk.settings import RiskConfig
from app.storage.repositories import Store
from app.storage.risk_store import RiskRepository
from app.storage.trade_store import TradeRepository
from app.strategies.registry import MAGIC_NUMBERS, strategy_for_magic
from tests.fakes.fake_mt5 import FakeMT5
from tests.unit.risk_helpers import ACCOUNT, CLOCK, Settings
from tests.unit.signal_helpers import MORNING, make_record, make_signal, pending

NOW = float(MORNING)


@dataclass
class Config:
    """A mutable execution config, as the Trading page's saved file would be."""

    config: ExecutionConfig = field(default_factory=ExecutionConfig)

    def __call__(self) -> ExecutionConfig:
        return self.config

    def mode(self, mode: OperatingMode) -> None:
        self.config = self.config.model_copy(update={"mode": mode})


@dataclass
class Rig:
    fake: FakeMT5
    engine: ExecutionEngine
    paper: PaperBroker
    risk: RiskManager
    config: Config
    store: Store
    logs: list[tuple[str, str]]
    clock: list[float]

    def at(self, seconds: float) -> None:
        self.clock[0] = seconds


@contextmanager
def rig(
    fake: FakeMT5,
    store: Store,
    mode: OperatingMode = OperatingMode.SEMI_AUTO,
) -> Iterator[Rig]:
    gateway = MT5Gateway(lambda: fake, idle_seconds=0.05)
    gateway.start()
    logs: list[tuple[str, str]] = []
    clock = [NOW]
    config = Config()
    config.mode(mode)
    try:
        market = GatewayMarket(gateway)
        risk = RiskManager(
            Settings(RiskConfig()),
            GatewayRiskBroker(gateway, strategy_for_magic),
            store=RiskRepository(store),
            account=lambda: ACCOUNT,
            utc_now=lambda: clock[0],
        )
        risk.clock_source = lambda: CLOCK
        trades = TradeRepository(store)
        paper = PaperBroker(
            market,
            lambda: config().settings,
            store=trades,
            account=lambda: ACCOUNT,
            utc_now=lambda: clock[0],
        )
        engine = ExecutionEngine(
            config,
            market=market,
            paper=paper,
            live=LiveBroker(gateway, pause=lambda seconds: None),
            magics=MAGIC_NUMBERS,
            risk=risk,
            store=trades,
            account=lambda: ACCOUNT,
            clock=lambda: CLOCK,
            stop_trading=risk.request_stop,
            paper_step=paper.step,
            log=lambda level, message: logs.append((level, message)),
            utc_now=lambda: clock[0],
        )
        yield Rig(fake, engine, paper, risk, config, store, logs, clock)
    finally:
        gateway.stop()


def eurusd_record(**changes: object) -> SignalRecord:
    """A pending EURUSD.m buy at the fake's ask, 10 pips SL, 20 pips TP."""
    values: dict[str, object] = {
        "symbol": "EURUSD.m",
        "entry": 1.08352,
        "sl": 1.08252,
        "tp": 1.08552,
    }
    values.update(changes)
    return pending(make_record(make_signal(**values)))
