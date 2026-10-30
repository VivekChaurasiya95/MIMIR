"""OS login-item management for the desktop companion (Qt-free).

Settings → General → "Start on system launch" persists a preference; these
helpers give that switch its real effect:

- Windows: ``HKCU\\...\\Run`` value (per-user, no elevation needed).
- Linux: ``~/.config/autostart/habitous.desktop`` entry.
- Other platforms: honest ``(False, reason)`` — the caller keeps the
  preference but tells the user it has no OS effect here.

Only :func:`set_autostart` and :func:`autostart_enabled` touch the OS;
:func:`login_command` and :func:`desktop_entry_text` are pure and covered
by headless tests.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = [
    "APP_ID",
    "RUN_VALUE_NAME",
    "autostart_enabled",
    "desktop_entry_text",
    "login_command",
    "set_autostart",
]

#: Stable identifiers for the login item on every platform.
APP_ID = "habitous"
RUN_VALUE_NAME = "Habitous"


def login_command() -> str:
    """Quoted command that launches the desktop companion at login."""
    exe = sys.executable or "python"
    return f'"{exe}" -m desktop_app'


def desktop_entry_text() -> str:
    """Linux ``.desktop`` autostart file content."""
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={RUN_VALUE_NAME}\n"
        f"Exec={login_command()}\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        f"X-GNOME-Autostart-enabled=true\n"
    )


def _autostart_file() -> Path:
    return (Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
            / "autostart" / f"{APP_ID}.desktop")


def autostart_enabled() -> bool | None:
    """True/False when the platform state is readable, else None."""
    if os.name == "nt":
        try:
            import winreg
        except ImportError:
            return None
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
            ) as key:
                winreg.QueryValueEx(key, RUN_VALUE_NAME)
        except OSError:
            return False
        return True
    if sys.platform.startswith("linux"):
        try:
            return _autostart_file().exists()
        except OSError:
            return None
    return None


def set_autostart(enabled: bool) -> tuple[bool, str]:
    """Create or remove the OS login item. Returns (ok, message)."""
    if os.name == "nt":
        try:
            import winreg
        except ImportError:
            return (False, "Windows registry unavailable.")
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            if enabled:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, key_path, 0,
                    winreg.KEY_SET_VALUE,
                ) as key:
                    winreg.SetValueEx(key, RUN_VALUE_NAME, 0,
                                      winreg.REG_SZ, login_command())
                return (True, "Habitous will start when you sign in.")
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, key_path, 0,
                winreg.KEY_SET_VALUE,
            ) as key:
                try:
                    winreg.DeleteValue(key, RUN_VALUE_NAME)
                except FileNotFoundError:
                    pass
            return (True, "Start on system launch turned off.")
        except OSError as exc:
            return (False, f"Could not update login items: {exc}")
    if sys.platform.startswith("linux"):
        try:
            target = _autostart_file()
            if enabled:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(desktop_entry_text(), encoding="utf-8")
                return (True, "Habitous will start when you sign in.")
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            return (True, "Start on system launch turned off.")
        except OSError as exc:
            return (False, f"Could not update autostart entry: {exc}")
    return (False, "Start on system launch is not supported on this OS — "
                   "the preference is saved but has no effect here.")
