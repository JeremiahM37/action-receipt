"""Task registry for the agent-in-the-loop benches.

``LEGACY`` is the 22-task set of bench 4 v1 (imported from ``bench.agent_loop`` so the two stay
identical); ``HARD`` is the v2 set of 36 tasks whose traps a receipt can see. ``ALL`` is both.
Every task: ``id``, ``app`` (fixture path + query), ``goal`` (the text the agent sees), ``check``
(validator over ``{url: window.__state()}`` of every open tab), ``trap`` (label, or None), ``set``.
``bench/tasks/README.md`` documents every trap.
"""

from __future__ import annotations

from typing import Any

from ..agent_loop import TASKS as _LEGACY_TASKS
from ..agent_loop import _has_item, _order, _saved, _st


def _T(id, app, goal, check, trap=None):
    return {"id": id, "app": app, "goal": goal, "check": check, "trap": trap, "set": "hard"}


def _inbox(S):
    return _st(S, "inbox.html") or {}


def _profile(S):
    return _st(S, "profile.html") or {}


def _orders(S):
    return _st(S, "orders.html") or {}


def _notes(S):
    return _st(S, "notes.html") or {}


def _wiz(S):
    return _st(S, "wizard.html") or {}


def _wizard_done(S, name, plan):
    w = _wiz(S)
    return bool(w) and w.get("completed") and w.get("name") == name and w.get("plan") == plan


HARD = [
    # ---- inbox: virtualised inner scroll list, hidden row menu, modal overlay, bulk action, confirm dialog
    _T(
        "inbox_star_deep",
        "apps/inbox.html",
        "Star the message with the subject 'Contract draft v3'.",
        lambda S: 30 in _inbox(S).get("starred", []),
        trap="scroll_container_virtual",
    ),
    _T(
        "inbox_archive_deep",
        "apps/inbox.html",
        "Archive the message with the subject 'Board minutes'.",
        lambda S: 38 in _inbox(S).get("archived", []),
        trap="scroll_container_virtual",
    ),
    _T(
        "inbox_menu_delete",
        "apps/inbox.html",
        "Delete the message with the subject 'Invoice 2201'.",
        lambda S: 5 in _inbox(S).get("deleted", []),
        trap="hidden_menu",
    ),
    _T(
        "inbox_modal_star",
        "apps/inbox.html?modal=1",
        "Star the message with the subject 'Welcome to Inbox'.",
        lambda S: 3 in _inbox(S).get("starred", []),
        trap="modal_overlay",
    ),
    _T(
        "inbox_bulk_archive",
        "apps/inbox.html",
        "Archive the two messages 'Weekly digest' and 'Promo: 20% off'.",
        lambda S: 6 in _inbox(S).get("archived", []) and 7 in _inbox(S).get("archived", []),
        trap="disabled_until",
    ),
    _T(
        "inbox_confirm_delete",
        "apps/inbox.html?confirm=1",
        "Delete the message with the subject 'Spam offer'.",
        lambda S: 8 in _inbox(S).get("deleted", []),
        trap="confirm_modal",
    ),
    _T(
        "inbox_modal_bulk",
        "apps/inbox.html?modal=1",
        "Archive the message with the subject 'Weekly digest'.",
        lambda S: 6 in _inbox(S).get("archived", []),
        trap="modal_overlay+disabled_until",
    ),
    # ---- profile: silent validation, native validation bubble, optimistic revert, toggle needs Apply, collapsed section, select
    _T(
        "profile_save_phone",
        "apps/profile.html?strict=1",
        "Change the display name to 'Ada' and save the profile.",
        lambda S: _profile(S).get("saved", {}).get("display") == "Ada",
        trap="silent_validation",
    ),
    _T(
        "profile_native",
        "apps/profile.html?native=1",
        "Change the display name to 'Ada' and save the profile.",
        lambda S: _profile(S).get("saved", {}).get("display") == "Ada",
        trap="native_validation",
    ),
    _T(
        "profile_rename_optimistic",
        "apps/profile.html?optimistic=1",
        "Rename the project to 'Phoenix'.",
        lambda S: _profile(S).get("project") == "Phoenix",
        trap="optimistic_revert",
    ),
    _T(
        "profile_2fa",
        "apps/profile.html",
        "Enable two-factor authentication on the profile.",
        lambda S: _profile(S).get("applied2fa") is True,
        trap="toggle_reverts",
    ),
    _T(
        "profile_beta",
        "apps/profile.html",
        "Enable beta features and save the profile.",
        lambda S: _profile(S).get("saved", {}).get("beta") is True,
        trap="collapsed_section",
    ),
    _T(
        "profile_lang",
        "apps/profile.html",
        "Set the profile language to Deutsch and save the profile.",
        lambda S: _profile(S).get("saved", {}).get("lang") == "de",
        trap="select",
    ),
    _T(
        "profile_phone_beta",
        "apps/profile.html?strict=1",
        "Enable beta features and save the profile.",
        lambda S: _profile(S).get("saved", {}).get("beta") is True,
        trap="silent_validation+collapsed_section",
    ),
    # ---- notes: autosave after idle, publish in a new tab
    _T(
        "notes_autosave_title",
        "apps/notes.html",
        "Change the note title to 'Q3 plan' and make sure the change is saved.",
        lambda S: _notes(S).get("saved", {}).get("title") == "Q3 plan",
        trap="autosave_idle",
    ),
    _T(
        "notes_autosave_body",
        "apps/notes.html",
        "Type 'Budget approved' into the note body and make sure the change is saved.",
        lambda S: "budget approved" in (_notes(S).get("saved", {}).get("body") or "").lower(),
        trap="autosave_idle",
    ),
    _T(
        "notes_publish_newtab",
        "apps/notes.html",
        "Publish the note and confirm the publication on the page that opens.",
        lambda S: bool(_st(S, "publish.html")) and _st(S, "publish.html").get("published") is True,
        trap="new_tab",
    ),
    # ---- orders: Next-only pager, Enter-only filter, delayed enable, load-on-scroll container, two pages
    _T(
        "orders_ship_page3",
        "apps/orders.html",
        "Mark order #1018 as shipped.",
        lambda S: 1018 in _orders(S).get("shipped", []),
        trap="pagination_next_only",
    ),
    _T(
        "orders_filter_enter",
        "apps/orders.html",
        "Find the order for the customer Okafor and mark it as shipped.",
        lambda S: 1019 in _orders(S).get("shipped", []),
        trap="enter_only",
    ),
    _T(
        "orders_export_delay",
        "apps/orders.html",
        "Prepare the orders export and then download it with the Export button.",
        lambda S: _orders(S).get("exported") is True,
        trap="delayed_enable",
    ),
    _T(
        "orders_infinite",
        "apps/orders.html?infinite=1",
        "Mark order #1021 as shipped.",
        lambda S: 1021 in _orders(S).get("shipped", []),
        trap="scroll_container_load",
    ),
    _T(
        "orders_ship_two_pages",
        "apps/orders.html",
        "Mark orders #1003 and #1026 as shipped.",
        lambda S: 1003 in _orders(S).get("shipped", []) and 1026 in _orders(S).get("shipped", []),
        trap="pagination_next_only",
    ),
    # ---- cart variants: consent banner covering the shop, readonly stepper, banner + gated checkout
    _T(
        "cart_consent_checkout",
        "apps/cart.html?banner=1",
        "Add a Gadget to the cart and check out.",
        lambda S: bool(_order(S)) and "gadget" in _order(S)["items"],
        trap="covered_by_banner",
    ),
    _T(
        "cart_stepper",
        "apps/cart.html?stepper=1",
        "Add a Widget to the cart, set its quantity to 3, and check out.",
        lambda S: bool(_order(S)) and _order(S)["items"].get("widget") == 3,
        trap="readonly_stepper",
    ),
    _T(
        "cart_address_consent",
        "apps/cart.html?address=1&banner=1",
        "Add a Widget to the cart and check out, shipping to '1 Main St'.",
        lambda S: (
            bool(_order(S)) and "widget" in _order(S)["items"] and "main" in _order(S)["address"].lower()
        ),
        trap="covered_by_banner+disabled_until",
    ),
    # ---- todo variants: modal overlay, confirm dialog, Enter-only add, banner + two items
    _T(
        "todo_modal_add",
        "apps/todo.html?modal=1",
        "Add a todo item with the text 'milk'.",
        lambda S: _has_item(S, "milk"),
        trap="modal_overlay",
    ),
    _T(
        "todo_confirm_delete",
        "apps/todo.html?confirm=1",
        "Delete the todo 'Call mom'.",
        lambda S: not _has_item(S, "call mom") and _has_item(S, "buy bread"),
        trap="confirm_modal",
    ),
    _T(
        "todo_enter_add",
        "apps/todo.html?enter=1",
        "Add a todo item with the text 'eggs'.",
        lambda S: _has_item(S, "eggs"),
        trap="enter_only",
    ),
    _T(
        "todo_banner_two",
        "apps/todo.html?banner=1",
        "Add two todo items: 'milk' and 'eggs'.",
        lambda S: _has_item(S, "milk") and _has_item(S, "eggs"),
        trap="covered_by_banner",
    ),
    # ---- wizard variants: no plan preselected, delayed Finish, terms + Pro
    _T(
        "wizard_noplan",
        "apps/wizard.html?noplan=1",
        "Complete the signup wizard with the name 'Ada' and the Pro plan.",
        lambda S: _wizard_done(S, "Ada", "pro"),
        trap="silent_validation",
    ),
    _T(
        "wizard_delay_finish",
        "apps/wizard.html?delay=1",
        "Complete the signup wizard with the name 'Ada' and the Basic plan.",
        lambda S: _wizard_done(S, "Ada", "basic"),
        trap="delayed_enable",
    ),
    _T(
        "wizard_terms_pro",
        "apps/wizard.html?terms=1",
        "Complete the signup wizard with the name 'Grace' and the Pro plan.",
        lambda S: _wizard_done(S, "Grace", "pro"),
        trap="silent_validation",
    ),
    # ---- settings / table / search variants
    _T(
        "settings_strict_dark",
        "apps/settings.html?strict=1",
        "Enable dark mode and save the settings.",
        lambda S: _saved(S, "dark", True),
        trap="silent_validation",
    ),
    _T(
        "settings_lang",
        "apps/settings.html",
        "Set the language to Français and save the settings.",
        lambda S: _saved(S, "lang", "fr"),
        trap="select",
    ),
    _T(
        "table_gate_sort",
        "apps/table.html?gate=1",
        "Sort the applicants by score so the highest score is first, then approve the applicant with the highest score.",
        lambda S: (
            bool(_st(S, "table.html"))
            and _st(S, "table.html")["topScoreName"] in _st(S, "table.html")["approved"]
        ),
        trap="disabled_until",
    ),
    _T(
        "search_slow_enter",
        "apps/search.html?enter=1&slow=2500",
        "Search for 'banana' and open the result titled 'Banana handbook'.",
        lambda S: (_st(S, "search.html") or {}).get("opened") == "banana",
        trap="enter_only+slow_fetch",
    ),
]

LEGACY = [dict(t, set="legacy") for t in _LEGACY_TASKS]
ALL = LEGACY + HARD
BY_ID: dict[str, dict[str, Any]] = {t["id"]: t for t in ALL}
assert len(BY_ID) == len(ALL), "duplicate task id"


def select(which: str, ids: list[str] | None = None) -> list[dict[str, Any]]:
    base = {"legacy": LEGACY, "hard": HARD, "all": ALL}[which]
    return [t for t in base if not ids or t["id"] in ids]
