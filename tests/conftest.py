"""Shared fixtures. Everything runs against a recording, never against a NIC."""

from __future__ import annotations

import os

import pytest

from nicscope.collectors import collect
from nicscope.util.context import make_context

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
SYNTHETIC = os.path.join(FIXTURE_DIR, "synthetic.capture.json")


@pytest.fixture(scope="session")
def context():
    return make_context(replay=SYNTHETIC)


@pytest.fixture(scope="session")
def report():
    """One full collection against the synthetic machine."""
    ctx = make_context(replay=SYNTHETIC)
    return collect(ctx, plan_speed_mbps=1000, jobs=1)


@pytest.fixture(scope="session")
def payload(report):
    return report.to_dict()
