"""Settings > Updates, the update banner and Restart to update (spec J2)."""

from pathlib import Path

from pytestqt.qtbot import QtBot

from app.core.ui_prefs import UiPrefs
from app.ui.main_window import MainWindow
from app.ui.updates_page import UpdateBanner, UpdatesContext, UpdatesPage, banner_text
from app.updates.backend import UpdateOffer
from app.updates.service import Command, UpdateService, UpdateStatus
from app.updates.settings import UpdateSettings, UpdateSettingsSource
from app.updates.state import StartInfo
from tests.fakes.fake_updates import FakeBackend


def context_for(tmp_path: Path, backend: FakeBackend) -> UpdatesContext:
    source = UpdateSettingsSource(tmp_path)
    service = UpdateService(backend, source, StartInfo("0.12.0", "", "0.11.0", False))
    return UpdatesContext(service, source)


def test_the_page_follows_the_service_from_check_to_ready(qtbot: QtBot, tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.offer = UpdateOffer("0.13.0", notes="**New**", size=2_000_000, delta=True)
    context = context_for(tmp_path, backend)
    page = UpdatesPage(context)
    qtbot.addWidget(page)
    page.show()
    assert page.version_label.text() == "Installed version: 0.12.0"
    assert not page.restart_button.isVisible()
    assert page.rollback_button.text() == "Roll back to 0.11.0"
    context.service.handle(Command.CHECK)
    qtbot.waitUntil(lambda: page.restart_button.isVisible())
    assert "Ready: restart the app to install 0.13.0" in page.status_label.text()
    assert page.notes.isVisible()


def test_settings_are_saved_and_pause_the_checks(qtbot: QtBot, tmp_path: Path) -> None:
    context = context_for(tmp_path, FakeBackend())
    page = UpdatesPage(context)
    qtbot.addWidget(page)
    page.paused.setChecked(True)
    page.hours.setValue(12)
    assert page.save()
    assert context.settings.settings == UpdateSettings(check_hours=12, paused=True)
    assert context.service.snapshot.paused
    assert page.status_label.text().startswith("Saved. Updates are paused.")


def test_without_an_install_the_page_explains_and_buttons_are_off(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    context = context_for(tmp_path, FakeBackend(reason="Updates work in the installed app only."))
    page = UpdatesPage(context)
    qtbot.addWidget(page)
    page.show()
    assert context.service.snapshot.status is UpdateStatus.UNAVAILABLE
    assert "installed app only" in page.status_label.text()
    assert not page.check_button.isEnabled()
    assert not page.rollback_button.isVisible()


def test_the_banner_shows_ready_updates_and_can_be_dismissed(
    qtbot: QtBot,
    tmp_path: Path,
) -> None:
    backend = FakeBackend()
    backend.offer = UpdateOffer("0.13.0")
    context = context_for(tmp_path, backend)
    opened: list[bool] = []
    banner = UpdateBanner(context, lambda: opened.append(True))
    qtbot.addWidget(banner)
    assert banner.isHidden()
    context.service.handle(Command.AUTO_CHECK)
    qtbot.waitUntil(lambda: not banner.isHidden())
    assert banner.text.text() == banner_text(context.service.snapshot)
    assert "0.13.0 is ready" in banner.text.text()
    banner.details_button.click()
    assert opened == [True]
    banner.dismiss()
    assert banner.isHidden()


def test_restart_to_update_asks_then_applies_and_closes(qtbot: QtBot, tmp_path: Path) -> None:
    backend = FakeBackend()
    backend.offer = UpdateOffer("0.13.0")
    context = context_for(tmp_path, backend)
    window = MainWindow(UiPrefs(), tmp_path, updates=context)
    qtbot.addWidget(window)
    window.show()
    asked: list[str] = []
    window.confirm_update = lambda text: asked.append(text) or False
    assert not window.restart_to_update()  # nothing downloaded yet
    context.service.handle(Command.AUTO_CHECK)
    assert not window.restart_to_update()  # the user said no
    assert asked and "install 0.13.0" in asked[-1]
    assert backend.applied == []
    window.confirm_update = lambda text: True
    assert window.restart_to_update()
    assert backend.applied == ["0.13.0"]
    assert not window.isVisible()
