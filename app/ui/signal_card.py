"""The Signal desk's order card in the AI Lab chat (docs/SIGNAL_DESK.md 2.4, phase 21a3).

A pasted signal becomes one card: the symbol, the side and the order kind, the entry, the
stop loss with its distance in ATR, every target with its R, the lots and the money at risk
from the risk manager, the checks (Why? shows them and the decision trace) and when it
expires, the context now (the trend, the structure, the spread, the next high-impact news
and the bot's open trades on the same currencies) and the full check (phase 21b): the same
geometry's base rate on 2 years of M15 history and how pasted signals did so far, each with
its sample count. Nothing is sent until you press and hold Hold to send (1.2 s, 2 s when
the order would be real); then every leg goes to the pipeline's approval queue and the
execution engine checks the price, the spread and the limits again before it sends. The card
only shows: the desk submits, approves, skips and runs the full check.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from datetime import datetime

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from app.domain.probability import ProbabilityEstimate
from app.domain.signals import Direction, OrderType, SignalRecord, SignalState
from app.engine.signal_desk import DeskResult
from app.signals.base_rate import BaseRate
from app.signals.parse import ParsedSignal
from app.signals.plan import OrderPlan
from app.ui.lab_parts import RTL, LabCard, lab_button, lab_label, mono_label
from app.ui.signals_page import state_text
from app.ui.theme import DEFAULT, ThemeTokens

HOLD_MS = 1200  # press and hold this long to send (demo and Paper)
REAL_HOLD_MS = 2000  # and this long when the order would be real
TICK_MS = 30
FILL_ALPHA = 150
Words = Callable[[str], str]
QUICK_NOTE = (
    "Checked: the price, the stops, the filters and the risk limits. The full check's numbers "
    "come from the app's own history. Nothing is sent until you hold the button."
)
CONTEXT_HEAD = "Context now:"
NO_HISTORY = "The full check needs the price history: connect to MT5."
FULL_HEAD = "Same geometry on {days} days of M15 history (a base rate, not a forecast):"
FULL_RATE = "TP{number} ({atr:.1f} ATR): {rate:.0f}% of {count} similar trades hit it first"
FULL_FEW = "TP{number} ({atr:.1f} ATR): too little data ({count} similar trades)"
SOURCE_RATE = "Pasted signals so far: {rate:.0f}% wins of {count} closed"
SOURCE_FEW = "Pasted signals so far: too little data ({count} closed)"
NO_ANALYSIS = (
    "no live analysis yet: connect to MT5, wait for the first closed bar and paste the signal "
    "again."
)
STATE_TAGS: dict[str, tuple[str, str]] = {
    "checking": ("CHECKING", "neutral"),
    "refused": ("REFUSED", "loss"),
    "waiting": ("WAITS FOR YOU", "warning"),
    "sending": ("SENDING", "warning"),
    "sent": ("SENT", "profit"),
    "filled": ("FILLED", "profit"),
    "closed": ("CLOSED", "neutral"),
    "failed": ("FAILED", "loss"),
    "expired": ("EXPIRED", "neutral"),
    "skipped": ("SKIPPED", "neutral"),
}
REASON_STATES = (
    SignalState.FAILED,
    SignalState.EXPIRED,
    SignalState.RISK_REJECTED,
    SignalState.FILTERED_OUT,
)
ORDER_FA: dict[str, str] = {
    "from: {source}": "منبع: {source}",
    "pasted": "متن چسبانده‌شده",
    "Why?": "چرا؟",
    "Hide": "بستن",
    "Edit": "ویرایش",
    "Skip": "رد کردن",
    "Hold to send": "نگه دارید تا ارسال شود",
    "Hold to send a REAL order": "نگه دارید تا سفارش واقعی ارسال شود",
    "Press and hold for {seconds:g} s to send. Letting go early cancels.": (
        "برای ارسال {seconds:g} ثانیه نگه دارید. رها کردن زودتر لغو می‌کند."
    ),
    "Checks: {passed}/{total} passed": "بررسی‌ها: {passed} از {total} قبول",
    "{count} warning": "{count} هشدار",
    "Expires {time} ({minutes} min)": "انقضا {time} ({minutes} دقیقه)",
    "Checking it against the live price, the filters and the risk limits...": (
        "در حال بررسی با قیمت زنده، فیلترها و حدود ریسک..."
    ),
    "Not possible: {why}": "ممکن نیست: {why}",
    "Sending is off: {why}": "ارسال خاموش است: {why}",
    "Sent for the final check: the engine checks the price, the spread and the limits again, "
    "then places the order.": (
        "برای بررسی نهایی رفت: موتور قیمت، اسپرد و حدود را دوباره می‌سنجد و بعد سفارش را می‌گذارد."
    ),
    "Skipped: nothing was sent.": "رد شد: چیزی ارسال نشد.",
    "Full check": "بررسی کامل",
    CONTEXT_HEAD: "وضعیت الان:",
    "Checking the same geometry in the history...": "در حال بررسی همین هندسه در تاریخچه...",
    "Full check failed: {why}": "بررسی کامل نشد: {why}",
    NO_HISTORY: "بررسی کامل تاریخچه‌ی قیمت را لازم دارد: به MT5 وصل شوید.",
    FULL_HEAD: "همین هندسه در {days} روز تاریخچه‌ی M15 (نرخ پایه، نه پیش‌بینی):",
    FULL_RATE: "TP{number} ({atr:.1f} ATR): {rate:.0f}% از {count} معامله‌ی مشابه اول به آن رسید",
    FULL_FEW: "TP{number} ({atr:.1f} ATR): داده کم است ({count} معامله‌ی مشابه)",
    SOURCE_RATE: "سیگنال‌های چسبانده‌شده تا حالا: {rate:.0f}% برد از {count} بسته‌شده",
    SOURCE_FEW: "سیگنال‌های چسبانده‌شده تا حالا: داده کم است ({count} بسته‌شده)",
    NO_ANALYSIS: (
        "هنوز تحلیل زنده‌ای نیست: به MT5 وصل شوید، صبر کنید اولین کندل بسته شود و سیگنال را "
        "دوباره بچسبانید."
    ),
    "This would be a REAL order now: hold again ({seconds:g} s).": (
        "این الان یک سفارش واقعی است: دوباره نگه دارید ({seconds:g} ثانیه)."
    ),
    QUICK_NOTE: (
        "بررسی شد: قیمت، حد ضرر و سود، فیلترها و حدود ریسک. عددهای بررسی کامل از تاریخچه‌ی "
        "خود برنامه می‌آیند. تا دکمه را نگه ندارید چیزی ارسال نمی‌شود."
    ),
    "waiting for approval": "منتظر تأیید شما",
    "filtered out": "فیلتر شد",
    "rejected by risk": "رد شده توسط ریسک",
    "expired": "منقضی شد",
    "dismissed": "رد شد",
    "sent (order placed)": "ارسال شد (سفارش ثبت شد)",
    "filled": "پر شد",
    "open (managed)": "باز (مدیریت می‌شود)",
    "closed": "بسته شد",
    "failed": "ناموفق",
}


def order_words(fa: bool) -> Words:
    def word(english: str) -> str:
        return ORDER_FA.get(english, english) if fa else english

    return word


def side_text(direction: Direction | None, order: OrderType | None) -> str:
    """BUY LIMIT, SELL MARKET, or just the side while the order kind is not known yet."""
    side = "?" if direction is None else "BUY" if direction is Direction.LONG else "SELL"
    return side if order is None else f"{side} {order.value.upper()}"


def price_text(value: float | None, digits: int | None) -> str:
    if value is None or not math.isfinite(value):
        return "?"
    return f"{value:.{digits}f}" if digits is not None else f"{value:g}"


def parsed_lines(parsed: ParsedSignal) -> tuple[str, str]:
    """The prices as they were pasted, before the desk has planned them."""
    entry = "-".join(f"{value:g}" for value in parsed.entry) or "MARKET"
    first = f"ENTRY {entry}   SL {price_text(parsed.sl, None)}"
    targets = [
        f"TP{index} {'open' if tp is None else f'{tp:g}'}"
        for index, tp in enumerate(parsed.tps, start=1)
    ]
    return first, "   ".join(targets) or "TP ?"


def plan_lines(plan: OrderPlan, digits: int | None, atr: float) -> tuple[str, str]:
    """The planned entry, the stop loss with its distance (in ATR when known), the targets."""
    distance = abs(plan.entry - plan.sl)
    gap = price_text(distance, digits)
    if math.isfinite(atr) and atr > 0:
        gap = f"{gap} = {distance / atr:.1f} ATR"
    first = f"ENTRY {price_text(plan.entry, digits)}   SL {price_text(plan.sl, digits)} ({gap})"
    targets = [
        f"TP{index} {price_text(tp, digits)}  R {plan.rr(tp):.1f}"
        for index, tp in enumerate(plan.tps, start=1)
    ]
    return first, "   ".join(targets)


def size_line(legs: Sequence[SignalRecord], currency: str = "") -> str:
    """The lots of every leg and the money at risk, from the risk manager; "" without."""
    volumes = [leg.volume for leg in legs]
    if not legs or any(volume is None for volume in volumes):
        return ""
    lots = [float(volume) for volume in volumes if volume is not None]
    total = round(sum(lots), 8)
    text = f"LOTS {total:g}"
    if len(lots) > 1:
        same = all(math.isclose(lot, lots[0]) for lot in lots)
        parts = f"{len(lots)} x {lots[0]:g}" if same else " + ".join(f"{lot:g}" for lot in lots)
        text += f" = {parts}"
    risks = [leg.risk_money for leg in legs]
    if all(risk is not None for risk in risks):
        money = sum(float(risk) for risk in risks if risk is not None)
        text += f"   RISK {money:,.2f} {currency}".rstrip()
    return text


def card_state(
    result: DeskResult | None,
    legs: Sequence[SignalRecord],
    *,
    sent: bool = False,
    skipped: bool = False,
) -> str:
    """Where the card is: checking, refused, waiting, sending, sent, filled, closed,
    failed, expired or skipped."""
    states = [leg.signal.state for leg in legs]
    moving = any(state.open_position or state is SignalState.APPROVED for state in states)
    if skipped and not moving:
        return "skipped"
    if SignalState.FAILED in states:
        return "failed"
    if any(state in (SignalState.FILLED, SignalState.MANAGED) for state in states):
        return "filled"
    if SignalState.SENT in states:
        return "sent"
    waiting = SignalState.PENDING_APPROVAL in states
    if sent and (waiting or SignalState.APPROVED in states):
        return "sending"
    if waiting:
        return "waiting"
    if SignalState.CLOSED in states:
        return "closed"
    ended = (SignalState.EXPIRED, SignalState.USER_REJECTED)
    if states and all(state in ended for state in states):
        return "skipped" if SignalState.USER_REJECTED in states else "expired"
    return "checking" if result is None else "refused"


def leg_lines(legs: Sequence[SignalRecord], word: Words) -> str:
    """One line per leg: its number, its state and, when it stopped, why."""
    lines: list[str] = []
    for index, leg in enumerate(legs, start=1):
        signal = leg.signal
        number = str(signal.features.get("leg", f"{index}/{len(legs)}"))
        text = f"{number}: {word(state_text(signal.state))}"
        reason = leg.reject_reason or (signal.history[-1].reason if signal.history else "")
        if signal.state in REASON_STATES and reason:
            text += f" ({reason})"
        lines.append(text)
    return "\n".join(lines)


def full_lines(
    found: BaseRate | None,
    source: ProbabilityEstimate | None,
    word: Words,
) -> list[str]:
    """The full check's lines: the base rate per target and this source's record, each with
    its sample count (no percent under the minimum)."""
    lines: list[str] = []
    if found is not None:
        lines.append(word(FULL_HEAD).format(days=found.days))
        for number, target in enumerate(found.targets, start=1):
            rate = target.rate
            values = {"number": number, "atr": target.tp_atr, "count": target.samples}
            if rate is None:
                lines.append(word(FULL_FEW).format(**values))
            else:
                lines.append(word(FULL_RATE).format(rate=rate * 100, **values))
    if source is not None:
        if source.value is None:
            lines.append(word(SOURCE_FEW).format(count=source.samples))
        else:
            rate = source.value * 100
            lines.append(word(SOURCE_RATE).format(rate=rate, count=source.samples))
    return lines


def why_text(plan: OrderPlan | None, legs: Sequence[SignalRecord]) -> str:
    """The plan's checks, then the first leg's decision trace (like the Signals page)."""
    lines: list[str] = []
    if plan is not None:
        for check in plan.checks:
            mark = "\u2713" if check.passed else "!" if check.warning else "\u2717"
            lines.append(f"{mark} {check.name}: {check.detail}".rstrip(": "))
        lines.extend(plan.notes)
    if legs:
        if lines:
            lines.append("")
        lines.extend(legs[0].trace.lines())
    return "\n".join(lines)


class HoldButton(QPushButton):
    """Press and hold to confirm: the fill grows from the reading start, letting go early
    cancels. The space bar works like the mouse (Qt's pressed and released)."""

    held = Signal()

    def __init__(self, text: str, hold_ms: int = HOLD_MS) -> None:
        super().__init__(text)
        self.setProperty("lab", "primary")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoRepeat(False)
        self.hold_ms = hold_ms
        self.danger = False  # the real order fill (the loss color)
        self.progress = 0.0
        self.tokens: ThemeTokens = DEFAULT
        self._started = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)
        self.pressed.connect(self._start)
        self.released.connect(self.cancel)

    @property
    def holding(self) -> bool:
        return self._timer.isActive()

    def apply_tokens(self, tokens: ThemeTokens) -> None:
        self.tokens = tokens
        self.update()

    def _start(self) -> None:
        self._started = time.monotonic()
        self.progress = 0.0
        self._timer.start()
        self.update()

    def _tick(self) -> None:
        spent = (time.monotonic() - self._started) * 1000.0
        self.progress = min(1.0, spent / max(self.hold_ms, 1))
        if self.progress >= 1.0:
            self._timer.stop()
            self.progress = 0.0
            self.update()
            self.held.emit()
            return
        self.update()

    def cancel(self) -> None:
        self._timer.stop()
        self.progress = 0.0
        self.update()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 (Qt name)
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            self.cancel()
        super().changeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt name)
        super().paintEvent(event)
        if self.progress <= 0:
            return
        color = QColor(self.tokens.loss if self.danger else self.tokens.profit)
        color.setAlpha(FILL_ALPHA)
        width = self.width() * self.progress
        start = self.width() - width if self.layoutDirection() == RTL else 0.0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(start, 0.0, width, float(self.height())), 6.0, 6.0)
        painter.end()


class OrderCard(LabCard):
    """One pasted signal: what the desk planned and sized, and Hold to send."""

    held = Signal()  # the hold is complete: the page approves every waiting leg
    skip = Signal()
    edit = Signal()
    full = Signal()  # run the full check (the base rate on the history)

    def __init__(
        self,
        parsed: ParsedSignal,
        word: Words,
        *,
        real: bool = False,
        source: str = "pasted",
    ) -> None:
        title = f"{parsed.symbol or '?'}  {side_text(parsed.direction, parsed.order)}"
        super().__init__("paste", title, word("from: {source}").format(source=word(source)))
        self.setObjectName("AiOrderCard")
        self.word = word
        self.parsed = parsed
        self.request_id = ""
        self.result: DeskResult | None = None
        self.legs: tuple[SignalRecord, ...] = ()
        self.leg_ids: tuple[str, ...] = ()
        self.block = ""
        self.currency = ""
        self.real = False
        self.sent = False
        self.skipped = False
        self.state = "checking"
        self.full_state = ""  # "", running, done or failed
        self.state_tag = self.add_tag("CHECKING")
        self.real_tag = self.add_tag("REAL ORDER", "loss")
        body = QWidget()
        lines = QVBoxLayout(body)
        lines.setContentsMargins(16, 12, 16, 12)
        lines.setSpacing(6)
        self.prices = mono_label("")
        self.prices.setObjectName("AiOrderPrices")
        self.targets = mono_label("")
        self.targets.setObjectName("AiOrderTargets")
        self.size_label = mono_label("")
        self.size_label.setObjectName("AiOrderSize")
        for label in (self.prices, self.targets, self.size_label):
            lines.addWidget(label)
        checks = QHBoxLayout()
        checks.setSpacing(10)
        self.checks_label = lab_label("", "text")
        self.why_button = lab_button(word("Why?"), "link")
        self.why_button.setObjectName("AiOrderWhy")
        self.why_button.clicked.connect(self.toggle_why)
        self.full_button = lab_button(word("Full check"), "link")
        self.full_button.setObjectName("AiOrderFullCheck")
        self.full_button.clicked.connect(self.full.emit)
        checks.addWidget(self.checks_label)
        checks.addWidget(self.why_button)
        checks.addWidget(self.full_button)
        checks.addStretch(1)
        lines.addLayout(checks)
        self.why = lab_label("", "note", wrap=True)
        self.why.setObjectName("AiOrderTrace")
        self.why.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.why.hide()
        lines.addWidget(self.why)
        self.context_label = lab_label("", "note", wrap=True)
        self.context_label.setObjectName("AiOrderContext")
        self.context_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.context_label.hide()
        lines.addWidget(self.context_label)
        self.full_label = lab_label("", "note", wrap=True)
        self.full_label.setObjectName("AiOrderFull")
        self.full_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.full_label.hide()
        lines.addWidget(self.full_label)
        self.status = lab_label("", "text", wrap=True)
        self.status.setObjectName("AiOrderStatus")
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lines.addWidget(self.status)
        self.expires = lab_label("", "note")
        self.expires.setObjectName("AiOrderExpires")
        lines.addWidget(self.expires)
        self.add_section(body)
        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.edit_button = lab_button(word("Edit"), "ghost")
        self.edit_button.setObjectName("AiOrderEdit")
        self.edit_button.clicked.connect(self.edit.emit)
        self.skip_button = lab_button(word("Skip"), "ghost")
        self.skip_button.setObjectName("AiOrderSkip")
        self.skip_button.clicked.connect(self.skip.emit)
        self.hold_button = HoldButton(word("Hold to send"))
        self.hold_button.setObjectName("AiOrderHold")
        self.hold_button.setAccessibleName("Hold to send")
        self.hold_button.held.connect(self.held.emit)
        row.addWidget(self.edit_button)
        row.addWidget(self.skip_button)
        row.addStretch(1)
        row.addWidget(self.hold_button)
        self.add_section(actions, (16, 12, 16, 12))
        self.add_footer(word(QUICK_NOTE))
        self.set_real(real)
        self.refresh()

    # What the page tells the card ------------------------------------------------------
    def set_real(self, real: bool) -> None:
        """A real order (Semi-auto or Auto on a real account): REAL ORDER and a 2 s hold."""
        self.real = real
        self.real_tag.setVisible(real)
        button = self.hold_button
        button.hold_ms = REAL_HOLD_MS if real else HOLD_MS
        button.danger = real
        button.setText(self.word("Hold to send a REAL order" if real else "Hold to send"))
        tip = "Press and hold for {seconds:g} s to send. Letting go early cancels."
        button.setToolTip(self.word(tip).format(seconds=button.hold_ms / 1000))

    def show_result(
        self,
        result: DeskResult | None,
        legs: Sequence[SignalRecord],
        block: str = "",
        *,
        currency: str | None = None,
        note: str = "",
        now: float | None = None,
    ) -> None:
        """The desk's answer and the legs' newest records (from a signals snapshot)."""
        if result is not None:
            self.result = result
            if result.signal_ids:
                self.leg_ids = result.signal_ids
        if legs or not self.leg_ids:
            self.legs = tuple(legs)
        self.block = block
        self.refresh(currency=currency, note=note, now=now)

    def show_context(self, lines: Sequence[str]) -> None:
        """What the app sees around the signal now (`app.engine.desk_context`)."""
        text = "\n".join([self.word(CONTEXT_HEAD), *lines]) if lines else ""
        self.context_label.setText(text)
        self.context_label.setVisible(bool(text))

    def full_running(self) -> None:
        self.full_state = "running"
        self._full_text(self.word("Checking the same geometry in the history..."))

    def show_full(self, outcome: BaseRate | str) -> None:
        """The full check's answer, or why it could not run."""
        if isinstance(outcome, str):
            self.full_state = "failed"
            self._full_text(self.word("Full check failed: {why}").format(why=outcome))
            return
        self.full_state = "done"
        source = self.legs[0].probability if self.legs else None
        self._full_text("\n".join(full_lines(outcome, source, self.word)))

    def full_note(self, text: str) -> None:
        self.full_state = "failed"
        self._full_text(text)

    def _full_text(self, text: str) -> None:
        self.full_label.setText(text)
        self.full_label.setVisible(bool(text))
        self.refresh()

    def mark_sent(self) -> None:
        self.sent = True
        self.refresh()

    def mark_skipped(self) -> None:
        self.skipped = True
        self.refresh()

    def warn_real(self) -> None:
        """The mode or the account changed to real since the card was drawn."""
        text = "This would be a REAL order now: hold again ({seconds:g} s)."
        self.status.setText(self.word(text).format(seconds=REAL_HOLD_MS / 1000))

    def toggle_why(self) -> None:
        shown = self.why.isHidden()
        self.why.setVisible(shown)
        self.why_button.setText(self.word("Hide" if shown else "Why?"))

    # Drawing ---------------------------------------------------------------------------
    def refresh(
        self,
        *,
        currency: str | None = None,
        note: str = "",
        now: float | None = None,
    ) -> None:
        word = self.word
        if currency is not None:
            self.currency = currency
        self.state = card_state(self.result, self.legs, sent=self.sent, skipped=self.skipped)
        self.state_tag.set(*STATE_TAGS[self.state])
        result = self.result
        plan = result.plan if result is not None else None
        first = self.legs[0] if self.legs else None
        digits = first.signal.digits if first is not None else None
        if plan is not None and plan.direction is not None:
            self.title.setText(f"{plan.symbol}  {side_text(plan.direction, plan.order)}")
            atr = first.atr if first is not None else math.nan
            prices, targets = plan_lines(plan, digits, atr)
            passed = sum(1 for check in plan.checks if check.passed)
            warnings = sum(1 for check in plan.checks if check.warning and not check.passed)
            checks = word("Checks: {passed}/{total} passed").format(
                passed=passed,
                total=len(plan.checks),
            )
            if warnings:
                checks += ", " + word("{count} warning").format(count=warnings)
        else:
            prices, targets = parsed_lines(self.parsed)
            checks = ""
        self.prices.setText(prices)
        self.targets.setText(targets)
        size = size_line(self.legs, self.currency)
        self.size_label.setText(size)
        self.size_label.setVisible(bool(size))
        self.checks_label.setText(checks)
        trace = why_text(plan, self.legs)
        self.why.setText(trace)
        self.why_button.setVisible(bool(trace))
        planned = plan is not None and self.state not in ("checking", "refused")
        self.full_button.setVisible(planned and self.full_state != "running")
        self.checks_label.setVisible(bool(checks))
        self.status.setText(self._status(note))
        self.expires.setText(self._expiry(now))
        self.expires.setVisible(bool(self.expires.text()))
        waiting = self.state == "waiting"
        self.hold_button.setEnabled(waiting and not self.block)
        self.skip_button.setEnabled(waiting)
        self.edit_button.setEnabled(self.state not in ("sending", "sent", "filled"))

    def _status(self, note: str) -> str:
        word = self.word
        result = self.result
        state = self.state
        if state == "checking":
            return word("Checking it against the live price, the filters and the risk limits...")
        if state == "refused":
            why = note or (result.message if result is not None else "")
            return word("Not possible: {why}").format(why=why)
        if state == "waiting":
            text = result.message if result is not None else ""
            if self.block:
                text = f"{text}\n{word('Sending is off: {why}').format(why=self.block)}".strip()
            return text
        if state == "sending":
            return word(
                "Sent for the final check: the engine checks the price, the spread and the "
                "limits again, then places the order.",
            )
        if state == "skipped":
            return word("Skipped: nothing was sent.")
        return leg_lines(self.legs, word)

    def _expiry(self, now: float | None) -> str:
        if self.state not in ("waiting", "sending") or not self.legs:
            return ""
        ends = self.legs[0].signal.expires_at
        moment = time.time() if now is None else now
        minutes = max(0, math.ceil((ends - moment) / 60.0))
        clock = datetime.fromtimestamp(ends).strftime("%H:%M")
        return self.word("Expires {time} ({minutes} min)").format(time=clock, minutes=minutes)
