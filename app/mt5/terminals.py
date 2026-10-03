"""Find installed MetaTrader 5 terminals (spec I3 step 1).

Sources: program folders, the MetaQuotes data folders (`origin.txt` points back to the
install folder), the Windows uninstall registry and running processes. Each terminal lists
the trade servers it already knows (the folder names under `<data folder>/bases`), which
fill the server dropdown. MT4 and 32-bit terminals (`terminal.exe`) are listed too, marked
as unsupported, so the user sees why they cannot be used.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

TERMINAL_64 = "terminal64.exe"
TERMINAL_32 = "terminal.exe"
ORIGIN_FILE = "origin.txt"
NOT_SERVERS = frozenset({"default", "signals", "mql5"})


@dataclass(frozen=True)
class TerminalInstall:
    path: str
    name: str
    data_dir: str = ""
    servers: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    running: bool = False

    @property
    def supported(self) -> bool:
        return PureWindowsPath(self.path).name.casefold() == TERMINAL_64

    def label(self) -> str:
        text = f"{self.name} ({self.path})"
        if self.running:
            text = f"{text}, running"
        if not self.supported:
            text = f"{text}, MT4 or 32-bit: not supported"
        return text


def read_origin(data_dir: Path) -> str | None:
    """The install folder written by MT5 into `<data folder>/origin.txt` (UTF-16 or UTF-8)."""
    path = data_dir / ORIGIN_FILE
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encoding = "utf-16"
    elif b"\x00" in raw:
        encoding = "utf-16-le"
    else:
        encoding = "utf-8-sig"
    try:
        text = raw.decode(encoding).strip().strip("\x00").strip()
    except UnicodeDecodeError:
        return None
    return text or None


def known_servers(data_dir: Path) -> tuple[str, ...]:
    bases = data_dir / "bases"
    try:
        names = [entry.name for entry in bases.iterdir() if entry.is_dir()]
    except OSError:
        return ()
    return tuple(sorted(name for name in names if name.casefold() not in NOT_SERVERS))


def _key(path: str) -> str:
    return str(PureWindowsPath(path)).casefold()


def _executable_in(folder: Path) -> Path | None:
    for name in (TERMINAL_64, TERMINAL_32):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


def discover_terminals(
    *,
    program_dirs: Sequence[Path] = (),
    data_root: Path | None = None,
    install_dirs: Iterable[Path] = (),
    running: Iterable[str] = (),
) -> list[TerminalInstall]:
    found: dict[str, dict[str, object]] = {}

    def add(executable: str, source: str, data_dir: str = "") -> None:
        entry = found.setdefault(
            _key(executable),
            {"path": executable, "sources": [], "data_dir": "", "servers": ()},
        )
        sources = entry["sources"]
        if isinstance(sources, list) and source not in sources:
            sources.append(source)
        if data_dir and not entry["data_dir"]:
            entry["data_dir"] = data_dir
            entry["servers"] = known_servers(Path(data_dir))

    for folder in program_dirs:
        try:
            children = sorted(child for child in folder.iterdir() if child.is_dir())
        except OSError:
            continue
        for child in children:
            executable = _executable_in(child)
            if executable is not None:
                portable = (child / "bases").is_dir()
                add(str(executable), "program folder", str(child) if portable else "")
    for folder in install_dirs:
        executable = _executable_in(folder)
        if executable is not None:
            add(str(executable), "registry")
    if data_root is not None:
        try:
            data_dirs = sorted(child for child in data_root.iterdir() if child.is_dir())
        except OSError:
            data_dirs = []
        for data_dir in data_dirs:
            origin = read_origin(data_dir)
            if origin is None:
                continue
            executable = _executable_in(Path(origin))
            if executable is not None:
                add(str(executable), "data folder", str(data_dir))
    running_keys = {_key(path) for path in running}
    for path in running:
        add(path, "running process")

    terminals: list[TerminalInstall] = []
    for key, entry in found.items():
        path = str(entry["path"])
        servers = entry["servers"]
        sources = entry["sources"]
        terminals.append(
            TerminalInstall(
                path=path,
                name=PureWindowsPath(path).parent.name or path,
                data_dir=str(entry["data_dir"]),
                servers=tuple(servers) if isinstance(servers, tuple) else (),
                sources=tuple(sources) if isinstance(sources, list) else (),
                running=key in running_keys,
            ),
        )
    return sorted(terminals, key=lambda item: (not item.supported, not item.running, item.name))


def _program_dirs() -> list[Path]:
    names = ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "LOCALAPPDATA")
    folders = {os.environ[name] for name in names if os.environ.get(name)}
    result = [Path(folder) for folder in sorted(folders)]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        result.append(Path(local) / "Programs")
    return result


def _registry_install_dirs() -> list[Path]:
    if sys.platform != "win32":
        return []
    import winreg

    uninstall = r"Microsoft\Windows\CurrentVersion\Uninstall"
    roots = (
        (winreg.HKEY_LOCAL_MACHINE, f"SOFTWARE\\{uninstall}"),
        (winreg.HKEY_LOCAL_MACHINE, f"SOFTWARE\\WOW6432Node\\{uninstall}"),
        (winreg.HKEY_CURRENT_USER, f"SOFTWARE\\{uninstall}"),
    )
    folders: list[Path] = []
    for root, key_path in roots:
        try:
            key = winreg.OpenKey(root, key_path)
        except OSError:
            continue
        with key:
            index = 0
            while True:
                try:
                    sub_name = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                try:
                    with winreg.OpenKey(key, sub_name) as sub:
                        display = str(winreg.QueryValueEx(sub, "DisplayName")[0])
                        if "metatrader 5" not in display.casefold():
                            continue
                        location = str(winreg.QueryValueEx(sub, "InstallLocation")[0])
                except OSError:
                    continue
                if location:
                    folders.append(Path(location))
    return folders


def _running_terminals() -> list[str]:
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    psapi = ctypes.WinDLL("psapi")
    kernel32 = ctypes.WinDLL("kernel32")
    process_ids = (wintypes.DWORD * 4096)()
    needed = wintypes.DWORD()
    if not psapi.EnumProcesses(process_ids, ctypes.sizeof(process_ids), ctypes.byref(needed)):
        return []
    count = needed.value // ctypes.sizeof(wintypes.DWORD)
    paths: list[str] = []
    for process_id in process_ids[:count]:
        handle = kernel32.OpenProcess(0x1000, False, process_id)  # QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                name = PureWindowsPath(buffer.value).name.casefold()
                if name in (TERMINAL_64, TERMINAL_32):
                    paths.append(buffer.value)
        finally:
            kernel32.CloseHandle(handle)
    return paths


def system_terminals() -> list[TerminalInstall]:
    """Every terminal found on this PC. Returns an empty list on other systems."""
    if sys.platform != "win32":
        return []
    appdata = os.environ.get("APPDATA")
    return discover_terminals(
        program_dirs=_program_dirs(),
        data_root=Path(appdata) / "MetaQuotes" / "Terminal" if appdata else None,
        install_dirs=_registry_install_dirs(),
        running=_running_terminals(),
    )
