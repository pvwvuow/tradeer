"""Shared test configuration."""

import os

# Headless Qt for CI and local runs; must be set before any Qt module is imported.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
