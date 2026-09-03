"""action-receipt: a deterministic effect oracle for browser agents (dispatch -> settlement -> delta -> verdict)."""

from .delta import Delta
from .schema import RECEIPT_JSON_SCHEMA, ReceiptModel, validate_receipt
from .session import Receipt, ReceiptSession
from .settle import SettleConfig, SettleReport
from .verdict import VERDICTS, Verdict

__all__ = [
    "RECEIPT_JSON_SCHEMA",
    "VERDICTS",
    "Delta",
    "Receipt",
    "ReceiptModel",
    "ReceiptSession",
    "SettleConfig",
    "SettleReport",
    "Verdict",
    "validate_receipt",
]
__version__ = "0.2.0"
