"""Schema conformance. The session fixture already validates every receipt of every test at
teardown; here the schema itself is exercised: a real receipt of each verdict validates, and the
invariants reject what they are supposed to reject."""

from __future__ import annotations

import copy
import json

import pytest

from action_receipt.schema import RECEIPT_JSON_SCHEMA, ReceiptModel, validate_receipt
from action_receipt.verdict import VERDICTS


async def _one_of_each(session, base_url):
    """One real receipt per verdict."""
    out = {}
    await session.new_page(f"{base_url}/buttons.html")
    out["changed"] = (await session.click("#counter-btn"))[1]
    out["no_op"] = (await session.click("#noop-btn"))[1]
    out["blocked"] = (await session.click("#disabled-btn"))[1]
    await session.new_page(f"{base_url}/nav.html")
    out["navigated"] = (await session.click("#go"))[1]
    page = await session.new_page(f"{base_url}/short.html")
    await page.close()
    out["unknown"] = (await session.scroll(page=page))[1]
    return out


async def test_every_verdict_validates_with_evidence_and_hint_rules(session, base_url):
    rs = await _one_of_each(session, base_url)
    assert set(rs) == set(VERDICTS)
    for v, r in rs.items():
        d = r.to_dict()
        assert d["verdict"] == v, (v, d["verdict"], d["evidence"])
        m = validate_receipt(d)
        assert isinstance(m, ReceiptModel)
        assert len(d["evidence"]) >= 1
        if v in ("no_op", "blocked"):
            assert d["hint"]
        json.dumps(d)


async def test_schema_rejects_drift(session, base_url):
    rs = await _one_of_each(session, base_url)
    good = rs["no_op"].to_dict()
    validate_receipt(good)

    def broken(mutate):
        d = copy.deepcopy(good)
        mutate(d)
        with pytest.raises(ValueError):
            validate_receipt(d)

    broken(lambda d: d["evidence"].clear())  # no evidence
    broken(lambda d: d.update(hint=None))  # no_op without hint
    broken(lambda d: d.update(verdict="succeeded"))  # unknown verdict
    broken(lambda d: d.update(extra_field=1))  # unknown top-level key
    broken(lambda d: d["settlement"].update(settled_by="magic"))  # unknown signal
    broken(lambda d: d["settlement"].update(timed_out=True))  # timeout without busy list
    broken(lambda d: d["settlement"].update(aborted="page_closed"))  # aborted but not unknown
    broken(
        lambda d: d["delta"].update(nodes_modified=[{"path": "x", "before": "a", "after": "b"}])
    )  # nodes without dom_changed
    broken(lambda d: d["delta"].update(url_changed=True, url_only_fragment=True))
    broken(
        lambda d: (
            d["delta"]["nodes_added"].extend(["p"] * 13)
            or d["delta"].update(dom_changed=True, nodes_changed_total=13)
        )
    )
    broken(lambda d: d["timing_ms"].pop("observed_window"))
    broken(lambda d: d["before"].update(truncated=True))  # truncated with nothing beyond the cap
    broken(lambda d: d["settlement"]["signals"]["network"].pop("released"))
    broken(lambda d: d["delta"].update(nodes_background=["a"], nodes_background_total=0))
    broken(lambda d: d["delta"].update(cross_document_navigations=["x"], navigations=[]))

    blk = rs["blocked"].to_dict()
    validate_receipt(blk)
    d = copy.deepcopy(blk)
    d["hint"] = ""
    with pytest.raises(ValueError):
        validate_receipt(d)


def test_json_schema_export_is_self_contained():
    s = RECEIPT_JSON_SCHEMA
    assert s["title"] == "ReceiptModel" and s.get("additionalProperties") is False
    for k in (
        "id",
        "action",
        "verdict",
        "evidence",
        "hint",
        "dispatch",
        "settlement",
        "delta",
        "before",
        "after",
        "timing_ms",
    ):
        assert k in s["properties"], k
    assert set(s["properties"]["verdict"]["enum"]) == set(VERDICTS)
    assert set(s["required"]) == set(s["properties"])
    json.dumps(s)  # serialisable as-is (what the receipt_schema tool returns)
