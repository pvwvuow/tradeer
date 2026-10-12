"""The order card with the fixes of docs/AI_AUTOPILOT.md 23a (0.50.2).

`DeskCard` is the Signal desk's `OrderCard` with `app.engine.desk_cards` deciding its state:

- a desk answer without legs is final: the old legs do not keep the card holdable (B11);
- a card refused after it was shown says why (the risk manager's or the filter's reason, not
  the old "waits for your confirmation") and what can be done about it (B11);
- one failed leg does not make a card with an open leg "failed" (B15);
- a card refreshed with a time past its legs' expiry turns expired and its hold goes off
  (B14; the pipeline expires the legs on its next cycle and the engine checks again).
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.signals import SignalRecord, SignalState
from app.engine.desk_cards import EXPIRED_TEXT, desk_state, desk_words, fix_hint, refusal
from app.engine.signal_desk import DeskResult
from app.ui.signal_card import STATE_TAGS, OrderCard, Words


def persian(word: Words) -> bool:
    """The card's words are Persian (its word for Skip is not the English one)."""
    return word("Skip") != "Skip"


class DeskCard(OrderCard):
    @property
    def fixes(self) -> Words:
        """The words of the fixes, in the card's language."""
        return desk_words(persian(self.word))

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
        if result is not None and not result.signal_ids and not legs:
            self.leg_ids = ()
            self.legs = ()
        super().show_result(result, legs, block, currency=currency, note=note, now=now)

    def refresh(
        self,
        *,
        currency: str | None = None,
        note: str = "",
        now: float | None = None,
    ) -> None:
        super().refresh(currency=currency, note=note, now=now)
        state = desk_state(
            self.result,
            self.legs,
            sent=self.sent,
            skipped=self.skipped,
            now=now,
        )
        if state != self.state:
            self.state = state
            self.state_tag.set(*STATE_TAGS[state])
            self.status.setText(self._status(note))
            self.expires.setText(self._expiry(now))
            self.expires.setVisible(bool(self.expires.text()))
            waiting = state == "waiting"
            self.hold_button.setEnabled(waiting and not self.block)
            self.skip_button.setEnabled(waiting)
            self.edit_button.setEnabled(state not in ("sending", "sent", "filled"))
            result = self.result
            planned = result is not None and result.plan is not None
            shown = planned and state not in ("checking", "refused")
            self.full_button.setVisible(shown and self.full_state != "running")
        if state == "refused":
            self.status.setText(self.refused_text(note))
        elif state == "expired" and any(
            leg.signal.state is SignalState.PENDING_APPROVAL for leg in self.legs
        ):
            self.status.setText(self.fixes(EXPIRED_TEXT))

    def refused_text(self, note: str = "") -> str:
        """Not possible: the reason, and on the next line what can be done about it."""
        why = note or refusal(self.result, self.legs, self.fixes)
        text = self.word("Not possible: {why}").format(why=why)
        hint = fix_hint(why)
        return f"{text}\n{self.fixes(hint)}" if hint else text
