"""The app's model service (spec C10): the registry, the active predictor the signal
pipeline asks for, activation and rollback, and the drift report.

`current()` is called from the analysis thread for every signal and is a cached read; the
Model page activates, rolls back or saves from the UI thread, which reloads the cache. Every
change is written to the `ml` log (audit lines included).
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable

from app.ml import features as feature_set
from app.ml.drift import DriftReport, drift_report
from app.ml.predictor import Predictor
from app.ml.registry import ModelError, ModelInfo, ModelRegistry
from app.ml.trainer import TrainedModel
from app.storage.signal_store import SignalRepository

Log = Callable[[str, str], None]
Audit = Callable[[str, str, str], None]  # action, before, after


def _quiet(level: str, message: str) -> None:
    return None


def _no_audit(action: str, before: str, after: str) -> None:
    return None


class ModelService:
    def __init__(
        self,
        registry: ModelRegistry,
        signals: SignalRepository | None = None,
        *,
        log: Log = _quiet,
        audit: Audit = _no_audit,
    ) -> None:
        self.registry = registry
        self._audit_hook = audit
        self._signals = signals
        self._log = log
        self._lock = threading.Lock()
        self._current: Predictor | None = None
        self._loaded = False

    # The pipeline ---------------------------------------------------------------------------
    def current(self) -> Predictor | None:
        with self._lock:
            if self._loaded:
                return self._current
        self.reload()
        with self._lock:
            return self._current

    def reload(self) -> None:
        predictor: Predictor | None = None
        info = None
        try:
            info = self.registry.active()
            if info is not None and not info.activation_block():
                predictor = self.registry.load(info)
        except ModelError as error:
            self._log("ERROR", f"The active model could not be loaded, using the baseline: {error}")
        except Exception as error:
            name = type(error).__name__
            self._log("ERROR", f"The model registry failed ({name}: {error}); using the baseline")
        with self._lock:
            self._current = predictor
            self._loaded = True
        if predictor is not None and info is not None:
            self._log("INFO", f"Win probability from model {info.title}")
        else:
            self._log("INFO", "Win probability from the baseline (no active model)")

    # The Model page -------------------------------------------------------------------------
    def models(self) -> list[ModelInfo]:
        return self.registry.models()

    def save(self, model: TrainedModel) -> ModelInfo:
        info = self.registry.save(model)
        self._log("INFO", f"Model {info.title} saved: {model.report.verdict()}")
        return info

    def activate(self, model_id: str) -> str:
        before = self.registry.active()
        refused = self.registry.activate(model_id)
        if refused:
            self._log("WARNING", f"Activation refused: {refused}")
            return refused
        after = self.registry.active()
        self._audit(before, after)
        self.reload()
        return ""

    def rollback(self) -> str:
        before = self.registry.active()
        refused = self.registry.rollback()
        if refused:
            return refused
        self._audit(before, self.registry.active())
        self.reload()
        return ""

    def use_baseline(self) -> None:
        before = self.registry.active()
        self.registry.deactivate()
        self._audit(before, None)
        self.reload()

    def _audit(self, before: ModelInfo | None, after: ModelInfo | None) -> None:
        old = f"v{before.version}" if before is not None else "baseline"
        new = f"v{after.version}" if after is not None else "baseline"
        self._log("INFO", f"Probability model changed ({old} \u2192 {new})")
        self._audit_hook("probability model changed", old, new)

    def drift(self) -> DriftReport | None:
        info = self.registry.active()
        if info is None or self._signals is None:
            return None
        reference = self.registry.reference(info)
        results = self._signals.model_results()
        recent = [feature_set.from_signal(f) for f in self._signals.recent_features()]
        recent = [row for row in recent if any(math.isfinite(v) for v in row.values())]
        return drift_report(results, recent, reference)
