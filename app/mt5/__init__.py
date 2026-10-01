"""MetaTrader 5 connection (spec C1, D3.1, I3, I4).

Only `gateway.py` imports the `MetaTrader5` package. Everything else receives an `MT5Api`
object inside the gateway thread, so the same code runs against `tests/fakes/FakeMT5`.
"""
