# Agent-loop task sets

Two sets drive `bench/agent_loop_v2.py` (and `bench/agent_loop.py` for the legacy set alone).
Every task is a goal string on a local fixture app (`bench/fixtures/apps/`) with a programmatic
validator over `window.__state()` of every open tab; the agent never sees the validator.
`python -m bench.tasks.selftest` checks, with plain Playwright, that every hard task's scripted
solution passes its validator **and** that the naive path (the one an agent takes when it falls
for the trap) leaves the validator failing.

## Legacy set (22 tasks, bench 4 v1)

Defined in `bench/agent_loop.py`; 13 plain tasks and 9 traps (`covered_by_banner`,
`silent_validation` ×2, `confirm_modal`, `disabled_until` ×2, `enter_only`, `scroll_container`,
`new_tab`). `qwen3.6:35b-a3b` solved 95-97% of them, which is why v2 exists.

## Hard set (36 tasks, bench 4 v2)

Each trap is something the receipt can *see*: the action is delivered but has no effect
(`no_op`), cannot be delivered (`blocked`), or has an effect that is not the one the task needs
(a `changed` that the validator does not count). The last column says what the receipt reports
on the naive path, i.e. what the `receipt_enforced` gate keys on.

| task | app | trap | what happens on the naive path | receipt on the naive action |
|---|---|---|---|---|
| `inbox_star_deep` | inbox | `scroll_container_virtual` | the 40-message list is **virtualised** inside a 240 px scroll box: only rows near the viewport exist in the DOM, so `#star-30` is not there until the box is scrolled (one wheel page of the 800 px viewport lands on rows 27-35; a window scroll is a `no_op`) | `blocked` (no such element) or `no_op` if a wrong row is clicked |
| `inbox_archive_deep` | inbox | `scroll_container_virtual` | as above for row 38 (the last screen of the box), plus archiving is only via select + *Archive selected* | `blocked` |
| `inbox_menu_delete` | inbox | `hidden_menu` | *Delete* lives in a per-row *More* menu; the button does not exist until the menu is opened | `blocked` |
| `inbox_modal_star` | inbox | `modal_overlay` | a full-page "What's new" dialog covers the list; clicks on rows hit the overlay | `blocked` (another element receives the pointer) |
| `inbox_bulk_archive` | inbox | `disabled_until` | *Archive selected* is disabled until a row checkbox is ticked | `blocked` (control is disabled) |
| `inbox_confirm_delete` | inbox | `confirm_modal` | *Delete* opens an in-page confirm dialog; nothing is deleted until *Delete* in the dialog is clicked | `changed` (`modals_opened`) — the gate does not fire; the hint names the modal |
| `inbox_modal_bulk` | inbox | `modal_overlay+disabled_until` | both of the above in one task | `blocked` |
| `profile_save_phone` | profile | `silent_validation` | *Save profile* returns early when the (subtly labelled) required phone field is empty; no message, no error | `no_op` |
| `profile_native` | profile | `native_validation` | a real `<form>` with a `required` phone field: the browser's validation bubble blocks the submit and is **not in the DOM** | `no_op` (focus moved to the invalid field only) |
| `profile_rename_optimistic` | profile | `optimistic_revert` | *Rename* shows the new name and "Renamed" immediately, then the request comes back **409** ~700 ms later and the title reverts; the second attempt succeeds | `changed` + evidence `HTTP 409` and the reverted title; gate does not fire |
| `profile_2fa` | profile | `toggle_reverts` | the 2FA checkbox only takes effect after *Apply*; blurring without applying reverts it | `changed` (the checkbox did toggle) — **passes the gate**; the "Pending: click Apply" status is the only tell |
| `profile_beta` | profile | `collapsed_section` | the beta checkbox is inside a closed `<details>`; it is not listed and not clickable until the summary is opened | `blocked` (element not rendered) |
| `profile_lang` | profile | `select` | needs the `select` tool (or keyboard) and then Save | `no_op` on a click of the select |
| `profile_phone_beta` | profile | `silent_validation+collapsed_section` | both of the above | `blocked` then `no_op` |
| `notes_autosave_title` | notes | `autosave_idle` | the editor autosaves **1.5 s after the last keystroke** plus a 1.2 s request; the receipt of the typing settles in ~100 ms and the status reads "Unsaved changes" | `changed` (value) — passes the gate; timing-dependent: a slow DONE call can let the save land |
| `notes_autosave_body` | notes | `autosave_idle` | as above for the body | `changed` |
| `notes_publish_newtab` | notes | `new_tab` | *Publish* opens a new tab; the confirmation lives there | `navigated` (`new_tabs`) — passes the gate; the hint says to switch tabs |
| `orders_ship_page3` | orders | `pagination_next_only` | order #1018 is on page 3 of 4; the pager has only Prev / Next (or a page-size select) | `blocked` (no such element on page 1) |
| `orders_filter_enter` | orders | `enter_only` | the customer filter applies only on Enter; typing alone filters nothing | `changed` (value) then `blocked` on the missing row |
| `orders_export_delay` | orders | `delayed_enable` | *Export* enables 2.5 s after *Prepare export* (a `setTimeout`, invisible to settlement); an immediate click is on a disabled control | `blocked` (disabled) |
| `orders_infinite` | orders | `scroll_container_load` | order #1021 loads only after the 200 px list box has been scrolled to its bottom twice (8 rows per load, 400 ms each) | `blocked` |
| `orders_ship_two_pages` | orders | `pagination_next_only` | #1003 on page 1, #1026 on page 4 | second click `blocked` |
| `cart_consent_checkout` | cart | `covered_by_banner` | a 330 px consent banner covers the products and the Checkout button | `blocked` (intercepts pointer events) |
| `cart_stepper` | cart | `readonly_stepper` | the quantity input is `readonly`; only the +/- buttons change it | `blocked`/`no_op` on `type` |
| `cart_address_consent` | cart | `covered_by_banner+disabled_until` | banner plus a Checkout that stays disabled until the address is filled | `blocked` |
| `todo_modal_add` | todo | `modal_overlay` | a full-page "What's new" dialog must be closed first | `blocked` |
| `todo_confirm_delete` | todo | `confirm_modal` | *Delete* opens an in-page confirm dialog | `changed` (`modals_opened`); gate does not fire |
| `todo_enter_add` | todo | `enter_only` | there is no Add button; only Enter adds | `changed` (value) — passes the gate |
| `todo_banner_two` | todo | `covered_by_banner` | the v1 cookie banner, two items | `blocked` |
| `wizard_noplan` | wizard | `silent_validation` | no plan is preselected; *Next* on step 2 silently does nothing until one is picked | `no_op` |
| `wizard_delay_finish` | wizard | `delayed_enable` | *Finish* is disabled for 2 s after step 3 appears | `blocked` (disabled) |
| `wizard_terms_pro` | wizard | `silent_validation` | the v1 terms checkbox trap with the Pro plan | `no_op` |
| `settings_strict_dark` | settings | `silent_validation` | the v1 strict-email trap on a different goal (dark mode) | `no_op` |
| `settings_lang` | settings | `select` | change the language select, then Save | `no_op` on a click of the select |
| `table_gate_sort` | table | `disabled_until` | sort by score, then Approve is disabled until the row's Verified box is ticked | `blocked` (disabled) |
| `search_slow_enter` | search | `enter_only+slow_fetch` | no Search button, Enter only, results arrive after 2.5 s (settlement waits for the fetch, a fixed sleep would not) | `changed` (value) then `blocked` |

### Which traps the DONE gate can and cannot catch

The `receipt_enforced` policy refuses `done` only when the **last** action's verdict was
`no_op`, `blocked` or `unknown` (or nothing has taken effect yet). Traps whose naive final action
is `changed` or `navigated` — `confirm_modal`, `toggle_reverts`, `autosave_idle`, `new_tab`,
`optimistic_revert`, and any `enter_only` task where typing was the last action — pass the gate;
for those the receipt's *hint* is the only lever, which is what the `receipt_on` arm measures.
The v2 report's "enforcement ceiling" table counts, per arm, how many DONE-on-fail episodes ended
on a refusable verdict.

### Fixture query parameters added for v2

`todo.html`: `modal=1`, `confirm=1`, `enter=1`. `cart.html`: `banner=1`, `stepper=1`.
`wizard.html`: `noplan=1`, `delay=1`. `search.html`: `slow=<ms>`. New apps: `inbox.html`
(`modal=1`, `confirm=1`), `profile.html` (`strict=1`, `native=1`, `optimistic=1`), `notes.html`
+ `publish.html`, `orders.html` (`infinite=1`). The fixture server gained `/fail?status=S&ms=N`
for the optimistic-revert app. Legacy behaviour with no parameters is unchanged.
