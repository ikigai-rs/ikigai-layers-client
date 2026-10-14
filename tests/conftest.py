"""Shared test options."""

from __future__ import annotations


def pytest_addoption(parser):
    # CI's interop job installs the Rust host and passes this, so a missing `ikigai` there
    # is a FAILURE: a skipped interop suite would pass green while covering nothing.
    parser.addoption(
        "--require-interop",
        action="store_true",
        help="fail, not skip, the Rust-door interop tests when `ikigai` is missing",
    )
