"""
Package import surface and the module entry points.

`python -m pdx1.pipeline` is the command the README leads with, so it has to be clean.
It is not, if importing the package eagerly imports the submodule: runpy imports
`pdx1`, finds `pdx1.pipeline` already in `sys.modules`, and then executes the same file
again as `__main__` -- warning, and running the module body twice under two names.
"""

from __future__ import annotations

import subprocess
import sys


def test_importing_the_package_does_not_pull_in_the_pipeline():
    """
    The invariant behind the entry-point fix.

    Checked in a subprocess because another test importing `pdx1.pipeline` first would
    otherwise mask it -- sys.modules is process-wide.
    """
    result = subprocess.run(
        [sys.executable, "-c", "import sys, pdx1; print('pdx1.pipeline' in sys.modules)"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", (
        "importing pdx1 must not eagerly import pdx1.pipeline -- "
        "python -m pdx1.pipeline warns and double-executes when it does"
    )


def test_running_the_pipeline_module_emits_no_runtime_warning():
    """`python -m pdx1.pipeline` must be warning-free; -W error makes one fatal."""
    result = subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-m", "pdx1.pipeline", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "RuntimeWarning" not in result.stderr


def test_package_is_runnable_as_a_module():
    """`python -m pdx1` is a working entry point alongside the console script."""
    result = subprocess.run(
        [sys.executable, "-m", "pdx1", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_lazy_names_still_resolve():
    """Making the pipeline re-exports lazy must not change the public surface."""
    import pdx1

    assert pdx1.Pipeline.__name__ == "Pipeline"
    assert pdx1.run_cycle.__name__ == "run_cycle"
    assert pdx1.CycleResult.__name__ == "CycleResult"


def test_every_advertised_name_resolves():
    """`__all__` is the contract; nothing in it may be unreachable."""
    import pdx1

    for name in pdx1.__all__:
        assert getattr(pdx1, name) is not None, f"{name} is advertised but does not resolve"


def test_dir_lists_the_lazy_names():
    import pdx1

    listed = dir(pdx1)
    assert "Pipeline" in listed and "Signal" in listed


def test_unknown_attribute_still_raises_attribute_error():
    """The lazy hook must not swallow genuine typos."""
    import pdx1
    import pytest

    with pytest.raises(AttributeError, match="no attribute 'NotAThing'"):
        pdx1.NotAThing
