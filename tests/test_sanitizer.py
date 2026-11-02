"""AST sanitizer tests: every forbidden family rejects, normal code passes."""

from __future__ import annotations

from mimir import sanitizer


def test_clean_code_passes():
    source = (
        "import logging\n"
        "from pathlib import Path\n"
        "import requests\n\n"
        "logger = logging.getLogger(__name__)\n\n"
        "def fetch(url: str) -> dict:\n"
        "    resp = requests.get(url, timeout=5.0)\n"
        "    logger.info('fetched %s', url)\n"
        "    return resp.json()\n"
    )
    result = sanitizer.check_source(source)
    assert result.ok
    assert result.violations == ()


def test_syntax_error_rejects():
    result = sanitizer.check_source("def broken(:\n  pass\n")
    assert not result.ok
    assert "syntax error" in result.violations[0]


def test_raw_sockets_reject():
    result = sanitizer.check_source(
        "import socket\ns = socket.socket()\n"
    )
    assert not result.ok
    assert any("socket" in v for v in result.violations)


def test_disk_wipe_rejects():
    for source in (
        "import shutil\nshutil.rmtree('/tmp/x')\n",
        "import os\nos.remove('a.txt')\n",
        "from pathlib import Path\ntarget_path.unlink()\n",
    ):
        result = sanitizer.check_source(source)
        assert not result.ok, source
    assert any(
        "rmtree" in v or "remove" in v or "unlink" in v
        for v in sanitizer.check_source(
            "import shutil\nshutil.rmtree('x')\n"
        ).violations
    )


def test_process_kill_rejects():
    result = sanitizer.check_source("import os\nos.kill(1234, 9)\n")
    assert not result.ok
    assert "os.kill" in result.violations[0]


def test_shell_execution_rejects_but_plain_subprocess_passes():
    evil = sanitizer.check_source(
        "import subprocess\nsubprocess.run('ls', shell=True)\n"
    )
    assert not evil.ok
    assert "shell=True" in evil.violations[0]
    clean = sanitizer.check_source(
        "import subprocess\n"
        "subprocess.run(['pytest', '-q'], cwd='.', timeout=10)\n"
    )
    assert clean.ok


def test_dynamic_eval_rejects():
    for call in ("eval('1')", "exec('x=1')", "__import__('os')"):
        result = sanitizer.check_source(call + "\n")
        assert not result.ok, call


def test_non_string_and_oversize_reject():
    assert not sanitizer.check_source(None).ok  # type: ignore[arg-type]
    assert not sanitizer.check_source("x = 1\n" * 100000).ok
