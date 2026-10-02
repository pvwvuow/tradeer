"""Shared helpers for the risk tests: a connected FakeMT5 behind a real gateway."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from app.core.clock import BrokerClock
from app.mt5.gateway import MT5Gateway
from app.mt5.models import SymbolSpec
from app.mt5.risk_reads import GatewayRiskBroker
from app.risk.risk_manager import RiskManager
from app.risk.settings import RiskConfig, RiskSettings
from app.storage.repositories import Store
from app.storage.risk_store import RiskRepository
from app.strategies.registry import strategy_for_magic
from tests.fakes.fake_mt5 import FakeAccount, FakeMT5
from tests.unit.signal_helpers import MORNING

CLOCK = BrokerClock.assumed()
ACCOUNT = "account-1"


def connected(currency: str = "USD", balance: float = 10_000.0) -> FakeMT5:
    fake = FakeMT5(accounts=[FakeAccount(currency=currency, balance=balance)], now=lambda: MORNING)
    fake.initialize()
    fake.login(fake.accounts[0].login, password=fake.accounts[0].password)
    return fake


def spec_of(fake: FakeMT5, symbol: str) -> SymbolSpec:
    info = fake.symbol_info(symbol)
    assert info is not None
    return SymbolSpec.from_mt5(info)


@dataclass
class Settings:
    """A mutable config source, as the Risk page's saved file would be."""

    config: RiskConfig

    def __call__(self) -> RiskConfig:
        return self.config


@contextmanager
def risk_manager(
    fake: FakeMT5,
    store: Store | None = None,
    settings: RiskSettings | None = None,
    logs: list[tuple[str, str]] | None = None,
) -> Iterator[RiskManager]:
    gateway = MT5Gateway(lambda: fake, idle_seconds=0.05)
    gateway.start()
    found = logs if logs is not None else []
    try:
        manager = RiskManager(
            Settings(RiskConfig(settings=settings or RiskSettings())),
            GatewayRiskBroker(gateway, strategy_for_magic),
            store=RiskRepository(store) if store is not None else None,
            account=lambda: ACCOUNT,
            log=lambda level, message: found.append((level, message)),
            utc_now=lambda: float(MORNING),
        )
        manager.clock_source = lambda: CLOCK
        yield manager
    finally:
        gateway.stop()
