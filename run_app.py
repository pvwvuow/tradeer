"""PyInstaller entry point for the frozen Windows build."""

import multiprocessing

if __name__ == "__main__":
    # The MT5 helper process starts this same exe again; freeze_support() runs the helper
    # there and exits, so the window never opens twice (ADR 46).
    multiprocessing.freeze_support()
    # Velopack's install, update and uninstall hooks come next and may exit (ADR 113).
    from app.updates.bootstrap import run_velopack_hooks

    run_velopack_hooks()
    from app.main import main

    raise SystemExit(main())
