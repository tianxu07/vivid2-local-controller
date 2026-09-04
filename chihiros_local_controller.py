"""Public source and Windows packaging entry point; no automatic BLE activity."""

from gui.app import main


if __name__ == "__main__":
    raise SystemExit(main())
