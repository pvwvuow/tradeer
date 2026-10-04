"""The README, the user guide and the progress page stay true to the app (Phase 16c)."""

import re
from pathlib import Path

from app.cli import build_parser
from app.ui.navigation import ADVANCED_PAGES

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
GUIDE = ROOT / "docs" / "USER_GUIDE.md"
PROGRESS = ROOT / "docs" / "PROGRESS.md"
LINK = re.compile(r"\]\(([^)\s]+)\)")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def command_line_options() -> list[str]:
    options = [
        option
        for action in build_parser()._actions
        for option in action.option_strings
        if option.startswith("--")
    ]
    return [option for option in options if option != "--help"]


def test_every_command_line_option_is_in_the_user_guide() -> None:
    guide = read(GUIDE)
    options = command_line_options()
    assert "--self-check" in options and "--profile" in options
    missing = [o for o in options if not re.search(rf"`{re.escape(o)}[ `]", guide)]
    assert missing == []


def test_every_sidebar_page_has_a_section_in_the_user_guide() -> None:
    headings = [line for line in read(GUIDE).splitlines() if line.startswith("## ")]
    missing = [p.title for p in ADVANCED_PAGES if not any(p.title in h for h in headings)]
    assert missing == []
    assert any("Home" in heading for heading in headings)


def test_relative_links_point_to_files_that_exist() -> None:
    broken = []
    for document in (README, GUIDE, PROGRESS):
        for target in LINK.findall(read(document)):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path = (document.parent / target.split("#", 1)[0]).resolve()
            if not path.exists():
                broken.append(f"{document.name}: {target}")
    assert broken == []


def test_the_readme_points_to_the_installer_and_the_guide() -> None:
    readme = read(README)
    assert "MT5TradingWorkstation-win-Setup.exe" in readme
    assert "releases/latest" in readme
    assert "docs/USER_GUIDE.md" in readme
