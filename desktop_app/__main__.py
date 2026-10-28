"""Entry point: python -m desktop_app [--store PATH].

When Qt bindings are missing for the current interpreter, the launcher
automatically re-executes itself under the project-local ``.venv`` (when
one ships Qt), so bare ``python -m desktop_app`` just works.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

__all__ = ["find_venv_python", "main"]


def find_venv_python(root: str | Path) -> str | None:
    """Locate a project-local venv interpreter shipping Qt bindings.

    Returns the venv python path, or None when there is no usable venv
    (missing interpreter, same as the current one, or no Qt installed).
    """
    root = Path(root)
    current = Path(sys.executable).resolve()
    if os.name == "nt":
        candidates = [(root / ".venv" / "Scripts" / "python.exe",
                       root / ".venv" / "Lib" / "site-packages")]
    else:
        libs = sorted((root / ".venv" / "lib").glob("python*/site-packages"))
        candidates = [(root / ".venv" / "bin" / "python",
                       libs[0] if libs else root)]
    for interpreter, site_packages in candidates:
        if not interpreter.is_file():
            continue
        try:
            if interpreter.resolve() == current:
                continue  # already inside this venv; no hop to make
        except OSError:
            continue
        if ((site_packages / "PyQt6").is_dir()
                or (site_packages / "PySide6").is_dir()):
            return str(interpreter)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mimir-desktop",
                                     description="MIMIR desktop companion")
    parser.add_argument("--store", default="mimir_memory.db",
                        help="Principle store path (default: mimir_memory.db)")
    args = parser.parse_args(argv)
    try:
        from .tabs._qt import load_qt
        load_qt()
    except ImportError:
        repo_root = Path(__file__).resolve().parent.parent
        venv_python = find_venv_python(repo_root)
        if venv_python is not None:
            print(f"mimir-desktop: switching to project env ({venv_python})",
                  flush=True)  # flush: execv below never returns to flush us
            forward = list(argv) if argv is not None else sys.argv[1:]
            os.execv(venv_python, [venv_python, "-m", "desktop_app", *forward])
        print(f"mimir-desktop: PyQt6 (or PySide6) is required for "
              f"{sys.executable} "
              f"(activate .venv or run: pip install mimir[cobot])")
        return 2
    from .app import run
    return run(store=args.store)


if __name__ == "__main__":
    raise SystemExit(main())
