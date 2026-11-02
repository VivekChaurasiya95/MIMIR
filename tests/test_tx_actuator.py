"""Transactional actuator tests: real files, real `pytest` subprocess.

Zero mocking: every run executes the genuine pytest binary against a
throwaway project on disk and asserts on bytes and exit codes.
"""

from __future__ import annotations

import pytest

from mimir.actuator import (
    TransactionalActuator,
    TxBoundaryError,
    run_pytest,
)


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    (root / "test_calc.py").write_text(
        "from calc import add\n\n"
        "def test_add():\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    return str(root)


def test_run_pytest_reports_genuine_exit_code(project):
    code, output = run_pytest(project, args=("-q",), timeout_s=60.0)
    assert code == 0
    assert "1 passed" in output


def test_apply_update_keeps_green_change(project):
    before = open(f"{project}/calc.py", "rb").read()
    result = TransactionalActuator(project).apply_update(
        "calc.py",
        "def add(a: int, b: int) -> int:\n    return a + b\n",
        pytest_args=("-q",),
        timeout_s=60.0,
    )
    assert result.tests_passed
    assert not result.rolled_back
    assert result.existed_before
    assert "All Tests Passed Successfully" in result.note
    after = open(f"{project}/calc.py", "rb").read()
    assert after != before
    assert b"-> int" in after


def test_apply_update_rolls_back_red_change_byte_exact(project):
    snapshot = open(f"{project}/calc.py", "rb").read()
    result = TransactionalActuator(project).apply_update(
        "calc.py",
        "def add(a, b):\n    return a - b  # breaks the suite\n",
        pytest_args=("-q",),
        timeout_s=60.0,
    )
    assert not result.tests_passed
    assert result.rolled_back
    assert result.test_returncode != 0
    assert "FAILED" in result.test_output or "failed" in result.test_output
    assert open(f"{project}/calc.py", "rb").read() == snapshot


def test_new_file_deleted_when_tests_fail(project, tmp_path):
    result = TransactionalActuator(project).apply_update(
        "test_broken.py",
        "def test_broken():\n    assert False\n",
        pytest_args=("-q",),
        timeout_s=60.0,
    )
    assert result.rolled_back
    assert not (tmp_path / "proj" / "test_broken.py").exists()


def test_path_escape_refuses(tmp_path):
    (tmp_path / "proj").mkdir(exist_ok=True)
    actuator = TransactionalActuator(str(tmp_path / "proj"))
    with pytest.raises(TxBoundaryError):
        actuator.apply_update("../escape.py", "x = 1\n")
    assert not (tmp_path / "escape.py").exists()
