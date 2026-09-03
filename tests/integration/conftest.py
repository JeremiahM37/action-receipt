"""Integration tier: one headless Chromium + ReceiptSession per test, against the fixture pages
served by the shared ``base_url`` server. Every receipt the session produced is validated
against the schema at teardown."""

from __future__ import annotations

import os

import pytest_asyncio
from playwright.async_api import async_playwright

from action_receipt.session import ReceiptSession
from action_receipt.settle import SettleConfig
from tests.conftest import validate_all


@pytest_asyncio.fixture
async def session():
    """One headless Chromium + ReceiptSession per test (fresh state, ~0.5 s).

    ``AR_HEADED=1`` launches a visible Chromium with ``slow_mo=250`` for debugging one test."""
    pw = await async_playwright().start()
    headed = os.environ.get("AR_HEADED", "") not in ("", "0")
    browser = await pw.chromium.launch(headless=not headed, slow_mo=250 if headed else 0)
    context = await browser.new_context(viewport={"width": 1000, "height": 600})
    s = ReceiptSession(settle_cfg=SettleConfig(quiet_ms=100, timeout_ms=8000), screenshots=True)
    await s.attach_context(context)
    try:
        yield s
        validate_all(s)
    finally:
        await browser.close()
        await pw.stop()
