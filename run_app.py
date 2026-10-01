"""PyInstaller entry point for the frozen Windows build."""

from app.main import main

if __name__ == "__main__":
    raise SystemExit(main())
