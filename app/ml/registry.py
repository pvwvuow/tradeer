"""The model registry (spec C10): every trained model with its version, training period,
symbols, feature list and schema hash, metrics and file hash, locally (`models/` and the
synced `model_versions` table, so the rows reach Supabase too).

A model file is JSON: LightGBM's text model, the calibrator, the feature medians, the drift
reference and the out-of-sample report. No pickle. Loading checks the file hash and the
feature schema; a model trained on another feature schema is refused. Activation is refused
when the model did not beat the baseline out of sample.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.ml import features as feature_set
from app.ml.calibration import Calibrator
from app.ml.metrics import Bucket, clean
from app.ml.model import Factory, LightGBMFactory, lightgbm_version
from app.ml.predictor import Predictor
from app.ml.trainer import TrainedModel
from app.storage.ids import new_id
from app.storage.repositories import Store

FORMAT = 1
FOLDER = "models"
TABLE = "model_versions"


@dataclass(frozen=True)
class ModelInfo:
    id: str
    version: int
    created_at: float
    strategies: tuple[str, ...]
    symbols: tuple[str, ...]
    period: str
    schema_hash: str
    file: str
    file_hash: str
    active: bool
    beats_baseline: bool
    report: Mapping[str, Any]

    @property
    def title(self) -> str:
        return f"v{self.version} ({', '.join(self.strategies)}; {', '.join(self.symbols)})"

    def activation_block(self) -> str:
        """Why this model may not be used, "" when it may."""
        if self.schema_hash != feature_set.schema_hash():
            return "its feature schema does not match this app version; train it again"
        if not self.beats_baseline:
            return "it did not beat the baseline out of sample"
        return ""


class ModelError(Exception):
    """A model file that is missing, changed or made for other features."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _day(seconds: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(seconds))


def _json(value: object, default: Any) -> Any:
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value if value is not None else default


class ModelRegistry:
    def __init__(
        self,
        store: Store,
        folder: Path,
        *,
        account: Callable[[], str | None] | None = None,
        factory: Factory | None = None,
    ) -> None:
        self.store = store
        self.folder = folder
        self._account = account or (lambda: None)
        self.factory = factory or LightGBMFactory()

    # Reads ------------------------------------------------------------------------------
    def models(self) -> list[ModelInfo]:
        rows = self.store.db.query(f"SELECT * FROM {TABLE} ORDER BY created_at DESC, rowid DESC")
        found = [self._info(row) for row in rows]
        return sorted(found, key=lambda info: -info.version)

    def active(self) -> ModelInfo | None:
        return next((info for info in self.models() if info.active), None)

    def _info(self, row: Mapping[str, Any]) -> ModelInfo:
        meta = _json(row.get("metrics_json"), {})
        meta = meta if isinstance(meta, dict) else {}
        symbols = _json(row.get("symbols"), [])
        return ModelInfo(
            id=str(row["id"]),
            version=int(meta.get("version", 0)),
            created_at=float(meta.get("created_at", 0.0)),
            strategies=tuple(str(s) for s in meta.get("strategies", [])),
            symbols=tuple(str(s) for s in symbols) if isinstance(symbols, list) else (),
            period=str(row.get("train_period") or ""),
            schema_hash=str(row.get("schema_hash") or ""),
            file=str(meta.get("file", "")),
            file_hash=str(row.get("file_hash") or ""),
            active=bool(row.get("is_active")),
            beats_baseline=bool(meta.get("beats_baseline", False)),
            report=meta.get("report", {}) if isinstance(meta.get("report"), dict) else {},
        )

    # Writes -----------------------------------------------------------------------------
    def save(self, model: TrainedModel, *, now: float | None = None) -> ModelInfo:
        moment = time.time() if now is None else now
        version = max((info.version for info in self.models()), default=0) + 1
        report = model.report
        payload = {
            "format": FORMAT,
            "version": version,
            "created_at": moment,
            "library": self.factory.name,
            "library_version": lightgbm_version() if self.factory.name == "lightgbm" else "",
            "schema_hash": feature_set.schema_hash(model.names),
            "names": list(model.names),
            "model": model.fitted.dump(),
            "calibrator": model.calibrator.to_json(),
            "medians": clean(list(model.medians)),
            "reference": [[list(edges), list(shares)] for edges, shares in model.reference],
            "baseline_rates": dict(model.baseline_rates),
            "report": report.to_json(),
        }
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.folder.mkdir(parents=True, exist_ok=True)
        name = f"model_v{version}.json"
        temporary = self.folder / (name + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(self.folder / name)
        period = f"{_day(report.period_start)} to {_day(report.period_end)}"
        row_id = new_id()
        self.store.upsert(
            TABLE,
            {
                "id": row_id,
                "account_id": self._account(),
                "strategy": ",".join(report.strategies),
                "features_json": list(model.names),
                "schema_hash": payload["schema_hash"],
                "train_period": period,
                "symbols": list(report.symbols),
                "metrics_json": {
                    "version": version,
                    "created_at": moment,
                    "file": name,
                    "strategies": list(report.strategies),
                    "beats_baseline": report.beats_baseline,
                    "report": report.to_json(),
                },
                "file_hash": _sha256(data),
                "is_active": False,
            },
        )
        saved = next(info for info in self.models() if info.id == row_id)
        return saved

    def _set_active(self, chosen: str | None) -> None:
        for info in self.models():
            want = info.id == chosen
            if info.active != want:
                self.store.upsert(TABLE, {"id": info.id, "is_active": want})

    def activate(self, model_id: str) -> str:
        """Make `model_id` the active model. Returns why it was refused, or "" when done."""
        info = next((item for item in self.models() if item.id == model_id), None)
        if info is None:
            return "unknown model"
        block = info.activation_block()
        if block:
            return f"v{info.version} cannot be activated: {block}"
        try:
            self.load(info)
        except ModelError as error:
            return f"v{info.version} cannot be activated: {error}"
        self._set_active(info.id)
        return ""

    def deactivate(self) -> None:
        """Back to the baseline."""
        self._set_active(None)

    def rollback(self) -> str:
        """Activate the newest usable model older than the active one ("" when done)."""
        current = self.active()
        if current is None:
            return "no model is active"
        older = [
            info
            for info in self.models()
            if info.version < current.version and not info.activation_block()
        ]
        if not older:
            self.deactivate()
            return ""
        return self.activate(older[0].id)

    # Loading ----------------------------------------------------------------------------
    def load(self, info: ModelInfo) -> Predictor:
        path = self.folder / info.file
        try:
            data = path.read_bytes()
        except OSError as error:
            raise ModelError(f"the model file {info.file} is missing") from error
        if _sha256(data) != info.file_hash:
            raise ModelError(f"the model file {info.file} was changed (hash mismatch)")
        payload = json.loads(data.decode("utf-8"))
        if payload.get("format") != FORMAT:
            raise ModelError("unknown model file format")
        names = tuple(str(name) for name in payload.get("names", []))
        current = feature_set.schema_hash()
        if names != feature_set.FEATURE_NAMES or payload.get("schema_hash") != current:
            raise ModelError("its feature schema does not match this app version")
        try:
            fitted = self.factory.load(str(payload["model"]))
        except RuntimeError as error:
            raise ModelError(str(error)) from error
        report = payload.get("report", {})
        buckets = tuple(Bucket(**item) for item in report.get("buckets", []))
        model_scores = report.get("model") or {}
        medians = [float(m) if m is not None else float("nan") for m in payload["medians"]]
        return Predictor(
            version=int(payload["version"]),
            fitted=fitted,
            calibrator=Calibrator.from_json(payload.get("calibrator", {})),
            names=names,
            medians=medians,
            buckets=buckets,
            strategies=tuple(report.get("strategies", [])),
            evaluated=int(model_scores.get("count") or 0),
        )

    def reference(self, info: ModelInfo) -> list[tuple[list[float], list[float]]]:
        """The drift reference (per feature: bin edges and training shares)."""
        try:
            payload = json.loads((self.folder / info.file).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [(list(edges), list(shares)) for edges, shares in payload.get("reference", [])]

    def load_active(self) -> Predictor | None:
        info = self.active()
        if info is None or info.activation_block():
            return None
        try:
            return self.load(info)
        except ModelError:
            return None


def bucket_json(bucket: Bucket) -> dict[str, Any]:
    found: dict[str, Any] = clean(asdict(bucket))
    return found
