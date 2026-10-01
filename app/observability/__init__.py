"""Observability (spec E3): structured logs, trace context, masking, crash reports, watchdog.

Everything here is pure Python except `logger.py` and `runtime.py`, which wire up loguru.
The Qt pieces (Logs page, crash dialog, Qt message handler) live in `app/ui`.
"""
