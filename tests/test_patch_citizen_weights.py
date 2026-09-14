"""
The citizen-cognisance topic-weight patcher.

`scripts/patch_citizen_weights.py` edits a checked-in UI file in place, so the
failures worth catching are the ones that corrupt it: an anchor that has drifted,
a second run stacking a duplicate block, or an abort that leaves a half-written
file behind. CI's ruff/bandit/pytest steps target `src/` and `tests/`, so nothing
else in the suite executes this script.

Most tests run against a **copy of the real `ui/citizen-cognisance.html`**, not a
synthetic stub. That is deliberate: it means editing the UI file in a way that
breaks one of the five anchors fails here rather than at the next person to run
the patcher.

Nothing touches the network, and every write lands in `tmp_path`.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "patch_citizen_weights.py"
REAL_UI = REPO_ROOT / "ui" / "citizen-cognisance.html"


def _load_patcher():
    """Import the script by path -- `scripts/` is not a package."""
    spec = importlib.util.spec_from_file_location("patch_citizen_weights", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


patcher = _load_patcher()


@pytest.fixture
def target(tmp_path) -> Path:
    """A disposable copy of the real UI file."""
    dst = tmp_path / "citizen-cognisance.html"
    shutil.copy(REAL_UI, dst)
    return dst


def _run(target: Path, *flags: str) -> int:
    return patcher.main(["--file", str(target), *flags])


def _backups(target: Path) -> list[Path]:
    return list(target.parent.glob("*.pre-weights-*.html"))


# ── The anchors still exist ──────────────────────────────────────────────────


def test_real_ui_file_is_patchable(target, capsys):
    """Every anchor resolves against the UI file as it stands on this commit."""
    assert _run(target) == 0
    out = capsys.readouterr().out
    for step_label, _ in patcher.STEPS:
        assert step_label in out


@pytest.mark.parametrize(
    "sentinel",
    [
        patcher.CSS_SENTINEL,
        patcher.HTML_SENTINEL,
        patcher.JS_SENTINEL,
        patcher.SORT_SENTINEL,
        patcher.NEW_NOTE,
    ],
)
def test_each_block_lands(target, sentinel):
    _run(target)
    assert sentinel in target.read_text(encoding="utf-8")


def test_slider_ids_match_the_handlers_that_drive_them(target):
    """A slider whose id the reset handler cannot find would throw on Reset."""
    html = (_run(target), target.read_text(encoding="utf-8"))[1]
    for sid, vid in [
        ("housing", "v-housing"),
        ("civic", "v-civic"),
        ("safety", "v-safety"),
        ("env", "v-env"),
        ("state", "v-state"),
    ]:
        assert f'id="w-{sid}"' in html
        assert f'id="{vid}"' in html


# ── Idempotency ──────────────────────────────────────────────────────────────


def test_second_run_is_a_no_op(target, capsys):
    _run(target)
    after_first = target.read_text(encoding="utf-8")
    capsys.readouterr()

    assert _run(target) == 0
    assert "already patched" in capsys.readouterr().out
    assert target.read_text(encoding="utf-8") == after_first


def test_repeated_runs_do_not_stack_blocks(target):
    for _ in range(3):
        _run(target)
    html = target.read_text(encoding="utf-8")
    assert html.count(patcher.CSS_SENTINEL) == 1
    assert html.count(patcher.HTML_SENTINEL) == 1
    assert html.count(patcher.JS_SENTINEL) == 1
    assert html.count(patcher.SORT_SENTINEL) == 1


def test_second_run_makes_no_second_backup(target):
    _run(target)
    assert len(_backups(target)) == 1
    _run(target)
    assert len(_backups(target)) == 1


# ── --dry-run writes nothing ─────────────────────────────────────────────────


def test_dry_run_leaves_the_file_untouched(target, capsys):
    before = target.read_text(encoding="utf-8")
    assert _run(target, "--dry-run") == 0
    assert target.read_text(encoding="utf-8") == before


def test_dry_run_makes_no_backup(target):
    _run(target, "--dry-run")
    assert _backups(target) == []


def test_dry_run_prints_a_diff(target, capsys):
    _run(target, "--dry-run")
    out = capsys.readouterr().out
    assert "--- a/citizen-cognisance.html" in out
    assert "+++ b/citizen-cognisance.html" in out
    assert patcher.SORT_SENTINEL in out


# ── Failure modes leave the original intact ──────────────────────────────────


def test_missing_file_exits_1(tmp_path):
    assert _run(tmp_path / "nope.html") == 1


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("no_style", "<html><body></body></html>"),
        ("no_controls", "<html><style></style><body></body></html>"),
    ],
)
def test_missing_anchor_aborts_without_writing(tmp_path, name, content):
    bad = tmp_path / f"{name}.html"
    bad.write_text(content, encoding="utf-8")

    assert _run(bad) == 2
    assert bad.read_text(encoding="utf-8") == content
    assert _backups(bad) == []


def test_partial_patch_is_never_written(tmp_path):
    """
    A file with an early anchor but no later one must come back byte-identical --
    the CSS step succeeds in memory, so a write before the sort step would leave a
    half-patched file on disk.
    """
    partial = tmp_path / "partial.html"
    content = '<html><style></style><section class="controls" aria-label="Map filters"></section></html>'
    partial.write_text(content, encoding="utf-8")

    assert _run(partial) == 2
    assert partial.read_text(encoding="utf-8") == content


# ── The backup is a faithful copy ────────────────────────────────────────────


def test_backup_holds_the_pre_patch_content(target):
    before = target.read_text(encoding="utf-8")
    _run(target)

    backups = _backups(target)
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == before
    assert target.read_text(encoding="utf-8") != before


# ── The sort statement is replaced, not welded to its neighbour ──────────────


def test_sort_block_keeps_its_own_line(target):
    """
    Regression: a leading `\\n` in the sort regex used to be consumed, joining
    `const visible` onto the end of the preceding `const host = ...;` line.
    """
    _run(target)
    lines = target.read_text(encoding="utf-8").splitlines()
    visible = [ln for ln in lines if "const visible = NODES.filter(passesFilter)" in ln]

    assert len(visible) == 1
    assert visible[0].lstrip().startswith("const visible")


def test_freshness_ordering_survives(target):
    """The weighted branch is added ahead of the old comparator, not instead of it."""
    html = (_run(target), target.read_text(encoding="utf-8"))[1]
    assert "const ha = entryFor(a.id).hours, hb = entryFor(b.id).hours;" in html
    assert "'Ranked by signal freshness'" in html


# ── The patched page still parses ────────────────────────────────────────────


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_patched_script_block_is_valid_javascript(target, tmp_path):
    _run(target)
    html = target.read_text(encoding="utf-8")

    start = html.index("<script>", html.index("</script>")) + len("<script>")
    end = html.index("</script>", start)
    js = tmp_path / "patched.js"
    js.write_text(html[start:end], encoding="utf-8")

    result = subprocess.run(
        [shutil.which("node"), "--check", str(js)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


# ── CLI surface ──────────────────────────────────────────────────────────────


def test_default_target_is_the_repo_ui_file():
    """Resolved from the script's own location, so cwd does not matter."""
    assert patcher.DEFAULT_SRC == REAL_UI
    assert patcher.DEFAULT_SRC.exists()


def test_file_flag_targets_the_copy_and_not_the_repo(target):
    before = REAL_UI.read_text(encoding="utf-8")
    _run(target)
    assert REAL_UI.read_text(encoding="utf-8") == before


def test_short_flags_are_accepted(target):
    assert patcher.main(["-f", str(target), "-n"]) == 0
    assert _backups(target) == []


def test_help_does_not_execute(capsys):
    with pytest.raises(SystemExit) as exc:
        patcher.main(["--help"])
    assert exc.value.code == 0
    assert "--dry-run" in capsys.readouterr().out


def test_runs_as_a_subprocess(target):
    """The `if __name__ == '__main__'` path, exercised the way a user invokes it."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--file", str(target), "--dry-run"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "Nothing written" in result.stdout
