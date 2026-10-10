"""Channel signals become order cards in the AI Lab (docs/SIGNAL_DESK.md 3.3 to 3.5, phase
21d): only a Live channel with a budget gets cards, booked to its own magic and sized with
its budget; a Paper trial only counts; noise and updates cost nothing."""

from pathlib import Path

from pytestqt.qtbot import QtBot

from app.channels.folder import ChannelMessage, Peer, PeerKind
from app.channels.policy import Action, ChannelMode, ChannelPolicy
from app.domain.signals import SignalState
from app.storage.channel_store import ChannelRepository
from app.storage.repositories import Store
from app.ui.channel_cards import FollowUpCard, SettingsCard
from app.ui.channel_feed import ChannelFeed, day_start
from app.ui.signal_card import OrderCard
from tests.ui.test_signal_card import desk_page
from tests.unit.storage_helpers import temporary_store
from tests.unit.test_signal_desk import buy_text

GOLD = Peer(-1001, "Gold Room", PeerKind.CHANNEL)
LIVE = ChannelPolicy(mode=ChannelMode.LIVE, budget=100.0, max_open=1)


def channel(store: Store, policy: ChannelPolicy) -> tuple[ChannelRepository, int]:
    repository = ChannelRepository(store)
    (source,) = repository.sync_folder([GOLD], 100.0)
    repository.set_enabled(GOLD.id, True, 100.0)
    repository.set_policy(GOLD.id, policy, 100.0)
    return repository, source.magic


def test_a_live_channel_signal_becomes_a_quick_order_card(qtbot: QtBot, tmp_path: Path) -> None:
    page, desk, signals, now, price = desk_page(qtbot, tmp_path)
    with temporary_store() as store:
        repository, magic = channel(store, LIVE)
        feed = ChannelFeed(desk, repository, utc_now=lambda: now)
        found = feed.handle(ChannelMessage(GOLD.id, 7, now, buy_text(price)))
        assert found is not None and found.action is Action.CARD, found
        card = page.chat.extras[-1]
        assert isinstance(card, OrderCard)
        assert card.request_id in desk.quick  # the full check waits for its button
        signals.on_cycle(now + 1)
        qtbot.waitUntil(lambda: card.state == "waiting")
        assert card.request_id not in desk.checked
        legs = [r.signal for r in signals.snapshot.signals if r.id in card.leg_ids]
        assert legs and {leg.strategy for leg in legs} == {f"channel:{magic}"}
        assert all(leg.features["budget_risk"] == 2.0 for leg in legs)  # 2% of 100
        assert all(leg.features["confirm"] == "user" for leg in legs)
        assert feed.count(GOLD.id).cards == 1
        assert feed.seen[0].channel == "Gold Room" and feed.seen[0].action is Action.CARD


def test_paper_trials_noise_and_off_channels_make_no_card(qtbot: QtBot, tmp_path: Path) -> None:
    page, desk, _signals, now, price = desk_page(qtbot, tmp_path)
    with temporary_store() as store:
        repository, _magic = channel(store, ChannelPolicy())
        feed = ChannelFeed(desk, repository, utc_now=lambda: now)
        before = len(page.chat.extras)
        paper = feed.handle(ChannelMessage(GOLD.id, 1, now, buy_text(price)))
        assert paper is not None and paper.action is Action.PAPER
        noise = feed.handle(ChannelMessage(GOLD.id, 2, now, "Join our VIP group today"))
        assert noise is not None and noise.action is Action.SKIP
        counts = feed.count(GOLD.id)
        assert (counts.cards, counts.paper, counts.skipped) == (0, 1, 1)
        repository.set_policy(GOLD.id, ChannelPolicy(mode=ChannelMode.LIVE), now)
        unfunded = feed.handle(ChannelMessage(GOLD.id, 3, now, buy_text(price)))
        assert unfunded is not None and "needs a budget" in unfunded.reason
        repository.set_enabled(GOLD.id, False, now)
        assert feed.handle(ChannelMessage(GOLD.id, 4, now, buy_text(price))) is None
        assert feed.handle("not a message") is None
        assert len(page.chat.extras) == before
        assert day_start(86_400.0 * 3 + 5.0) == 86_400.0 * 3


FX = Peer(-1002, "FX Room", PeerKind.CHANNEL)


def test_the_same_signal_from_a_second_channel_joins_the_first_card(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    page, desk, signals, now, price = desk_page(qtbot, tmp_path)
    with temporary_store() as store:
        repository, _magic = channel(store, LIVE)
        repository.sync_folder([GOLD, FX], 100.0)
        repository.set_enabled(FX.id, True, 100.0)
        repository.set_policy(FX.id, LIVE, 100.0)
        feed = ChannelFeed(desk, repository, utc_now=lambda: now)
        feed.handle(ChannelMessage(GOLD.id, 1, now, buy_text(price)))
        card = page.chat.extras[-1]
        assert isinstance(card, OrderCard)
        signals.on_cycle(now + 1)
        qtbot.waitUntil(lambda: card.state == "waiting")
        again = feed.handle(ChannelMessage(FX.id, 9, now, buy_text(price)))
        assert again is not None and again.action is Action.PAPER
        assert "the same signal as Gold Room's card" in again.reason
        assert page.chat.extras[-1] is card and card.note.text() == "Gold Room + FX Room"
        saved = {signal.channel_id: signal for signal in repository.signals()}
        assert saved[FX.id].request_id == saved[GOLD.id].request_id == card.request_id


def test_settings_asked_in_the_chat_wait_for_the_hold(qtbot: QtBot, tmp_path: Path) -> None:
    page, desk, _signals, now, _price = desk_page(qtbot, tmp_path)
    with temporary_store() as store:
        repository, _magic = channel(store, ChannelPolicy())
        feed = ChannelFeed(desk, repository, utc_now=lambda: now)
        page.chat.set_text("Give Gold Room 250 dollars")
        assert page.chat.send()
        card = page.chat.extras[-1]
        assert isinstance(card, SettingsCard) and card in feed.extras
        assert repository.policy(GOLD.id).budget == 0.0  # nothing before the hold
        card.held.emit()
        assert repository.policy(GOLD.id).budget == 250.0 and card.done
        page.chat.set_text("Gold Room live")
        assert page.chat.send()
        live = page.chat.extras[-1]
        assert isinstance(live, SettingsCard)
        live.held.emit()
        assert repository.policy(GOLD.id).mode is ChannelMode.LIVE


def test_a_follow_up_of_a_taken_signal_needs_the_hold(qtbot: QtBot, tmp_path: Path) -> None:
    page, desk, signals, now, price = desk_page(qtbot, tmp_path)
    with temporary_store() as store:
        repository, _magic = channel(store, LIVE)
        feed = ChannelFeed(desk, repository, utc_now=lambda: now)
        feed.handle(ChannelMessage(GOLD.id, 7, now, buy_text(price)))
        card = page.chat.extras[-1]
        assert isinstance(card, OrderCard)
        signals.on_cycle(now + 1)
        qtbot.waitUntil(lambda: card.state == "waiting")
        update = ChannelMessage(GOLD.id, 8, now + 60, "close now", reply_to=7)
        feed.handle(update)
        follow = page.chat.extras[-1]
        assert isinstance(follow, FollowUpCard)
        assert repository.signals(GOLD.id)[0].close_at is not None  # the shadow closes too
        legs = [r for r in signals.snapshot.signals if r.id in card.leg_ids]
        assert all(leg.signal.state is SignalState.PENDING_APPROVAL for leg in legs)
        follow.held.emit()
        assert follow.done
        signals.on_cycle(now + 120)
        legs = [r for r in signals.snapshot.signals if r.id in card.leg_ids]
        assert legs and all(leg.signal.state is SignalState.USER_REJECTED for leg in legs)
