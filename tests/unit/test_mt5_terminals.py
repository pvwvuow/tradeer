import tempfile
from pathlib import Path

from app.mt5.terminals import discover_terminals, known_servers, read_origin


def make_install(root: Path, name: str, exe: str = "terminal64.exe") -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / exe).write_bytes(b"MZ")
    return folder


def make_data_dir(root: Path, install: Path, servers: tuple[str, ...]) -> Path:
    data = root / "ABCDEF0123456789"
    (data / "bases").mkdir(parents=True)
    for server in (*servers, "Default", "signals"):
        (data / "bases" / server).mkdir()
    (data / "origin.txt").write_bytes(str(install).encode("utf-16"))
    return data


def test_terminals_are_found_with_their_servers_and_merged_across_sources() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        programs = root / "Program Files"
        demo = make_install(programs, "Demo Broker MetaTrader 5")
        make_install(programs, "Old Broker MetaTrader 4", exe="terminal.exe")
        (programs / "Unrelated").mkdir()
        data = make_data_dir(root / "Terminal", demo, ("DemoBroker-Demo", "DemoBroker-Live"))
        found = discover_terminals(
            program_dirs=[programs],
            data_root=root / "Terminal",
            install_dirs=[demo],
            running=[str(demo / "terminal64.exe")],
        )
    assert [terminal.name for terminal in found] == [
        "Demo Broker MetaTrader 5",
        "Old Broker MetaTrader 4",
    ]
    first = found[0]
    assert first.supported and first.running
    assert first.servers == ("DemoBroker-Demo", "DemoBroker-Live")
    assert first.data_dir == str(data)
    assert set(first.sources) == {"program folder", "registry", "data folder", "running process"}
    assert "running" in first.label()
    assert not found[1].supported
    assert "not supported" in found[1].label()


def test_portable_installs_keep_their_data_in_the_install_folder() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        programs = Path(tmp)
        portable = make_install(programs, "Portable MT5")
        (portable / "bases" / "Broker-Demo").mkdir(parents=True)
        found = discover_terminals(program_dirs=[programs])
    assert found[0].servers == ("Broker-Demo",)
    assert found[0].data_dir == str(portable)


def test_origin_files_and_missing_folders_are_handled() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        assert read_origin(folder) is None
        (folder / "origin.txt").write_bytes("C:\\MT5".encode("utf-8"))
        assert read_origin(folder) == "C:\\MT5"
        (folder / "origin.txt").write_bytes("D:\\Trading\\MT5".encode("utf-16"))
        assert read_origin(folder) == "D:\\Trading\\MT5"
        assert known_servers(folder / "missing") == ()
        assert discover_terminals(program_dirs=[folder / "missing"], data_root=folder) == []
