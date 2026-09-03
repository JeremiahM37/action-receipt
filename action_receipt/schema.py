"""The receipt schema, as pydantic models, and the invariants every receipt must satisfy.

``validate_receipt(d)`` accepts the dict returned by ``Receipt.to_dict()`` (which is exactly
what the MCP tools return under ``"receipt"``) and raises ``ValueError`` on any drift: unknown
keys, missing keys, wrong types, an empty evidence list, a ``no_op``/``blocked`` verdict
without a hint, a timeout without busy signals, an aborted settlement that is not
``unknown``, or a ``validation_blocked`` delta that the evidence/verdict do not reflect.
``RECEIPT_JSON_SCHEMA`` is the same contract as JSON Schema.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .settle import SETTLED_BY_VALUES
from .verdict import VERDICTS

VerdictName = Literal["changed", "no_op", "navigated", "blocked", "unknown"]
SettledBy = Literal[
    "navigation",
    "network",
    "timers",
    "animations",
    "dom",
    "layout",
    "scroll",
    "document",
    "quiet_window",
    "page_closed",
    "page_crashed",
]
assert set(get_args(VerdictName)) == set(VERDICTS)
assert set(get_args(SettledBy)) == set(SETTLED_BY_VALUES)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionModel(_Strict):
    name: str = Field(min_length=1)
    args: dict[str, Any]


class PreSettleModel(_Strict):
    elapsed_ms: float = Field(ge=0)
    polls: int = Field(ge=1)
    quiet: bool
    waited_for: Literal["dom", "document"] | None
    background_roots: int = Field(ge=0)
    max_ms: float = Field(ge=0)


class DispatchModel(_Strict):
    ok: bool | None  # None in wrap mode (the session did not dispatch)
    error: str | None
    elapsed_ms: float | None
    preflight: dict[str, Any] | None
    mode: Literal["wrap"] | None = None
    pre_settle: PreSettleModel | None = None


class ReleasedRequest(_Strict):
    url: str
    reason: str


class NetworkSignal(_Strict):
    peak_pending: int = Field(ge=0)
    started_total: int = Field(ge=0)
    reason: str | None
    busy_until_ms: float | None
    released: list[ReleasedRequest] = Field(max_length=3)


class DomSignal(_Strict):
    mutations: int = Field(ge=0)
    background_mutations: int = Field(ge=0)
    frames_observed: int = Field(ge=0)
    busy_until_ms: float | None


class ScrollSignal(_Strict):
    events: int = Field(ge=0)
    busy_until_ms: float | None


class LayoutSignal(_Strict):
    shifts: int = Field(ge=0)
    busy_until_ms: float | None


class ReasonSignal(_Strict):
    reason: str | None
    busy_until_ms: float | None


class AnimationsSignal(ReasonSignal):
    infinite: int = Field(ge=0)
    background: int = Field(ge=0)  # already running at dispatch: ignored


class TimersSignal(ReasonSignal):
    peak_pending: int = Field(ge=0)
    max_delay_ms: float | None
    long: list[float] = Field(max_length=5)  # delays > timer_wait_ms: reported, not awaited
    background: int = Field(ge=0)
    intervals_started: int = Field(ge=0)


class NavigationSignal(_Strict):
    count: int = Field(ge=0)
    last_url: str | None


class SignalsModel(_Strict):
    network: NetworkSignal
    dom: DomSignal
    scroll: ScrollSignal
    layout: LayoutSignal
    animations: AnimationsSignal
    timers: TimersSignal
    document: ReasonSignal
    navigation: NavigationSignal
    quiet_ms: float = Field(gt=0)
    timeout_ms: float = Field(gt=0)
    body_grace_ms: float = Field(ge=0)
    timer_wait_ms: float = Field(ge=0)


class SettlementModel(_Strict):
    settled: bool
    elapsed_ms: float = Field(ge=0)
    settled_by: SettledBy
    timed_out: bool
    aborted: Literal["page_closed", "page_crashed"] | None
    busy_at_timeout: list[str]
    signals: SignalsModel
    polls: int = Field(ge=1)

    @model_validator(mode="after")
    def _consistent(self):
        if self.timed_out and not self.busy_at_timeout:
            raise ValueError("timed_out without busy_at_timeout")
        if self.timed_out and self.settled:
            raise ValueError("timed_out and settled")
        if self.aborted and (self.settled or self.settled_by != self.aborted):
            raise ValueError("aborted settlement must not be settled and must name the cause in settled_by")
        if self.timed_out and self.elapsed_ms < self.signals.timeout_ms * 0.95:
            raise ValueError("timed_out before the timeout elapsed")
        if any(d <= self.signals.timer_wait_ms for d in self.signals.timers.long):
            raise ValueError("a timer listed as long is within timer_wait_ms")
        return self


class ContainerScroll(_Strict):
    path: str | None
    before: float | None
    after: float | None
    delta: int
    scrollHeight: float | None
    clientHeight: float | None


class FrameScroll(_Strict):
    path: str
    before: float
    after: float
    delta: int
    dx: int
    scrollHeight: float | None
    clientHeight: float | None


class ModifiedNode(_Strict):
    path: str
    before: str
    after: str


class DialogModel(_Strict):
    type: str
    message: str
    handled: str


class DownloadModel(_Strict):
    filename: str | None
    url: str | None


class HttpErrorModel(_Strict):
    url: str
    status: int = Field(ge=400)
    method: str | None


class Truncation(_Strict):
    fingerprinted: int = Field(ge=0)
    total: int = Field(ge=0)


class ValidationBlockedModel(_Strict):
    path: str = Field(min_length=1)
    tag: str = Field(min_length=1)
    id: str | None
    name: str | None
    type: str | None
    message: str
    flags: list[str]


class DeltaModel(_Strict):
    url_changed: bool
    url_before: str
    url_after: str
    url_only_fragment: bool
    title_changed: bool
    scroll_dx: int
    scroll_dy: int
    container_scroll: ContainerScroll | None
    frame_scrolls: list[FrameScroll]
    focus_changed: bool
    focus_before: str | None
    focus_after: str | None
    dom_changed: bool
    element_count_delta: int
    text_changed: bool
    nodes_added: list[str] = Field(max_length=12)
    nodes_removed: list[str] = Field(max_length=12)
    nodes_modified: list[ModifiedNode] = Field(max_length=12)
    nodes_changed_total: int = Field(ge=0)
    nodes_background: list[str] = Field(max_length=12)
    nodes_background_total: int = Field(ge=0)
    background_roots: list[str] = Field(max_length=50)
    fingerprint_truncated: Truncation | None
    value_before: str | None
    value_after: str | None
    value_changed: bool
    modals_opened: list[str]
    modals_closed: list[str]
    screenshot_hamming: int | None
    screenshot_changed_fraction: float | None
    screenshot_changed: bool
    screenshot_masked_regions: int = Field(ge=0)
    dialogs: list[DialogModel]
    navigations: list[str]
    cross_document_navigations: list[str]
    new_tabs: list[str]
    downloads: list[DownloadModel]
    http_errors: list[HttpErrorModel] = Field(max_length=5)
    validation_blocked: list[ValidationBlockedModel] = Field(max_length=64)
    console_errors: list[str]
    page_crashed: bool
    captures_ok: bool

    @model_validator(mode="after")
    def _consistent(self):
        if not self.dom_changed and (
            self.nodes_added or self.nodes_removed or self.nodes_modified or self.nodes_changed_total
        ):
            raise ValueError("node lists without dom_changed")
        listed = len(self.nodes_added) + len(self.nodes_removed) + len(self.nodes_modified)
        if listed > self.nodes_changed_total:
            raise ValueError("more nodes listed than nodes_changed_total")
        if self.url_changed and self.url_only_fragment:
            raise ValueError("url_changed and url_only_fragment are exclusive")
        if len(self.nodes_background) > self.nodes_background_total:
            raise ValueError("more background nodes listed than counted")
        if self.cross_document_navigations and not self.navigations:
            raise ValueError("cross-document navigation without a navigation event")
        return self


class CaptureSummary(_Strict):
    ok: bool
    url: str
    title: str
    ready_state: str
    scroll: dict[str, Any]
    focused: dict[str, Any] | None
    modals: list[str]
    element_count: int = Field(ge=0)
    element_total: int = Field(ge=0)
    truncated: bool
    frames: list[dict[str, Any]]
    background: list[str] = Field(max_length=50)
    dom_hash: str
    text_hash: int
    screenshot_dhash: str | None
    target: dict[str, Any] | None
    capture_ms: float
    error: str | None

    @model_validator(mode="after")
    def _consistent(self):
        if self.ok and self.element_total < self.element_count:
            raise ValueError("element_total < element_count")
        if self.ok and self.truncated and self.element_total <= self.element_count:
            raise ValueError("truncated but nothing beyond the cap")
        return self


class ReceiptModel(_Strict):
    id: str = Field(min_length=8)
    action: ActionModel
    verdict: VerdictName
    evidence: list[str] = Field(min_length=1)
    hint: str | None
    dispatch: DispatchModel
    settlement: SettlementModel
    delta: DeltaModel
    before: CaptureSummary
    after: CaptureSummary
    timing_ms: dict[str, float]

    @model_validator(mode="after")
    def _invariants(self):
        if self.verdict in ("no_op", "blocked") and not self.hint:
            raise ValueError(f"{self.verdict} without a hint")
        if any(not e.strip() for e in self.evidence):
            raise ValueError("empty evidence item")
        if self.settlement.aborted and self.verdict != "unknown":
            raise ValueError("aborted settlement must yield verdict unknown")
        if self.settlement.timed_out and not any(e.startswith("never_settled") for e in self.evidence):
            raise ValueError("timed-out settlement must say never_settled in the evidence")
        if self.verdict == "navigated" and not (
            self.delta.url_changed or self.delta.new_tabs or self.delta.cross_document_navigations
        ):
            raise ValueError("navigated without a navigation in the delta")
        if self.verdict == "no_op" and (
            self.delta.dom_changed
            or self.delta.scroll_dy
            or self.delta.value_changed
            or self.delta.url_changed
            or self.delta.new_tabs
            or self.delta.downloads
        ):
            raise ValueError("no_op with a substantive delta")
        has_validation_ev = any(e.startswith("form_validation_blocked") for e in self.evidence)
        if bool(self.delta.validation_blocked) != has_validation_ev:
            raise ValueError("delta.validation_blocked and the form_validation_blocked evidence must agree")
        if self.verdict == "no_op" and self.delta.validation_blocked:
            raise ValueError("no_op although form validation blocked the submit")
        if self.dispatch.mode != "wrap" and "dispatch" not in self.timing_ms:
            raise ValueError("timing_ms.dispatch missing")
        if "observed_window" not in self.timing_ms or "total" not in self.timing_ms:
            raise ValueError("timing_ms must carry observed_window and total")
        return self


RECEIPT_JSON_SCHEMA: dict[str, Any] = ReceiptModel.model_json_schema()


def validate_receipt(d: dict[str, Any]) -> ReceiptModel:
    """Validate a receipt dict; raises ValueError with the pydantic detail on failure."""
    try:
        return ReceiptModel.model_validate(d)
    except ValidationError as e:
        raise ValueError(str(e)) from None
