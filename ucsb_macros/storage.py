"""Persistent per-user data location shared by the CLI and web app."""
import os
import sys
from pathlib import Path


def data_dir():
    override = os.environ.get("GAUCHO_DATA_DIR") or os.environ.get("UCSB_MACROS_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return base / "Gaucho"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Gaucho"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "gaucho"
