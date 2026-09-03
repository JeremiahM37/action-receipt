"""JavaScript evaluated inside the page.

The pieces:

* INSTALL_JS  - idempotent; installs ``window.__ar`` which counts DOM mutations, scroll
                events and layout shifts with page-clock timestamps. Registered as an init
                script (survives navigation, runs in every frame) and also evaluated directly
                on pages we attach to. It also hooks ``Element.prototype.attachShadow`` so
                mutations inside shadow roots (open *and* closed) are observed, and remembers
                the roots so the capture can walk them. It keeps a ring of recently mutated
                elements so the before-capture can mark *background* nodes - elements (and
                their subtrees) that were already mutating before the action; their later
                mutations are counted separately and do not keep settlement busy. It hooks
                ``setTimeout`` / ``setInterval`` / ``requestAnimationFrame`` so pending timers
                are visible, and each one inherits *background* from the callback that
                created it (a ticker's own re-arm is background; a timer the action scheduled
                is foreground). It records every ``invalid`` event (capture phase on window and
                on each shadow root) so a submit the browser's constraint validation refused is
                visible even though the validation bubble is not in the DOM.
* MARK_JS     - the dispatch anchor. Evaluated once, right before the action is dispatched,
                in every same-origin frame: records ``anchor`` (page clock), the set of
                animations already running (they are background from now on, finite or
                infinite) and marks every pending timer as background. Returns the status.
* STATUS_JS   - returns the counters plus running-animation counts (foreground / background /
                infinite), pending foreground timers and readyState, aggregated over the top
                frame and every same-origin child frame (child-frame clocks are converted to
                the top frame's ``performance.now()``). Polled during settlement.
* PRESETTLE_JS - re-runs background marking in every frame and reports how long ago the last
                *foreground* mutation was, so the before-capture can be delayed until the page
                is quiet or its permanent activity has shown itself (see ``settle.pre_settle``).
* PRESCROLL_JS - evaluated against the target element before the before-capture: if it is not
                fully inside its viewport, scroll it into view (instant, centred) and report
                how far the window moved. The dispatcher's own scroll-into-view then has
                nothing left to do, so the scroll never enters the delta.
* CAPTURE_JS  - the deterministic state capture: url, title, scroll geometry, focused
                element, visible modals, scrollable containers, same-origin frames' scroll
                geometry, the controls constraint validation rejected since the anchor, and a
                bounded list of (path, signature) pairs for every element -
                the DOM fingerprint input. Walks open/closed shadow roots and same-origin
                iframes. The walk is O(n): paths are built incrementally, never by re-walking
                ancestors per node.
* TARGET_JS   - evaluated against a specific element handle: disabled/readonly/value/rect,
                whether another element covers its centre, and its nearest scrollable
                ancestor.

Nothing here calls a model. Everything is a pure function of the live DOM.

Path syntax (identity of a node in the receipt): ``tag#id`` or ``tag:nth-of-type(i)`` parts
joined by ``>``; ``#shadow-root`` marks a shadow boundary and ``#document`` a same-origin
frame boundary. An id resets the path only in the top document's light tree (ids inside
shadow roots and frames are scoped, so they keep their prefix). Paths keep the last
``PATH_MAX_PARTS`` parts.
"""

PATH_MAX_PARTS = 14

INSTALL_JS = r"""
(() => {
  if (window.__ar && window.__ar.installed) return true;
  const S = {
    installed: true,
    mutations: 0, lastMutation: 0,          // foreground: not attributable to a background node
    bgMutations: 0, lastBgMutation: 0,      // inside a node that was already mutating before the action
    scrollEvents: 0, lastScroll: 0,
    shifts: 0, lastShift: 0,
    installedAt: performance.now(),
    shadow: new WeakMap(),   // host -> ShadowRoot (open and closed), filled by the attachShadow hook
    recent: [],              // ring of {el, t}: elements mutated recently (t on this frame's clock)
    bg: new Set(),           // background roots, set by markBackground() at the before-capture
    anchor: null,            // page clock at the last dispatch mark (null: no action dispatched on this document yet)
    preAnims: null,          // WeakSet of animations that were already running at the anchor
    timers: new Map(),       // id -> {delay, created, bg, interval}: pending setTimeout/setInterval callbacks
    timerBg: true,           // background flag of the callback currently running (inherited by timers it creates)
  };
  const RECENT = 256;
  const now = () => performance.now();
  // --- timers: a setTimeout the action scheduled is a settlement signal; one scheduled before the
  // anchor, or by a background callback (a ticker re-arming itself), is background and ignored.
  try {
    const oST = window.setTimeout, oCT = window.clearTimeout, oSI = window.setInterval, oCI = window.clearInterval, oRAF = window.requestAnimationFrame;
    const run = (rec, fn, self, args) => { const prev = S.timerBg; S.timerBg = rec.bg; try { return fn.apply(self, args); } finally { S.timerBg = prev; } };
    const bgNow = () => S.anchor == null || S.timerBg;
    const st = function (fn, delay, ...args) {
      if (typeof fn !== 'function') return oST.call(window, fn, delay, ...args);
      let d = Number(delay); if (!(d >= 0)) d = 0;
      const rec = { delay: d, created: now(), bg: bgNow(), interval: false };
      const id = oST.call(window, function () { S.timers.delete(id); return run(rec, fn, this, args); }, delay);
      S.timers.set(id, rec);
      return id;
    };
    const si = function (fn, delay, ...args) {
      if (typeof fn !== 'function') return oSI.call(window, fn, delay, ...args);
      let d = Number(delay); if (!(d >= 0)) d = 0;
      const rec = { delay: d, created: now(), bg: bgNow(), interval: true };
      const id = oSI.call(window, function () { return run(rec, fn, this, args); }, delay);
      S.timers.set(id, rec);
      return id;
    };
    const clr = (orig) => function (id) { S.timers.delete(id); return orig.call(window, id); };
    const raf = function (fn) {
      if (typeof fn !== 'function') return oRAF.call(window, fn);
      const bg = bgNow();
      return oRAF.call(window, function (ts) { const prev = S.timerBg; S.timerBg = bg; try { return fn.call(this, ts); } finally { S.timerBg = prev; } });
    };
    st.__ar = si.__ar = raf.__ar = true;
    window.setTimeout = st; window.setInterval = si; window.requestAnimationFrame = raf;
    window.clearTimeout = clr(oCT); window.clearInterval = clr(oCI);
  } catch (e) {}
  S.markDispatch = () => {
    S.anchor = now();
    const pre = new WeakSet();
    try { for (const a of document.getAnimations()) { if (a.playState === 'running') pre.add(a); } } catch (e) {}
    S.preAnims = pre;
    for (const rec of S.timers.values()) rec.bg = true;
    S.timerBg = false;   // the dispatch itself runs from an event, not a background callback
    S.invalid.length = 0;   // validation rejections are counted from this dispatch on
    return true;
  };
  // Foreground timers still pending (created after the anchor by a foreground callback).
  S.pendingTimers = () => {
    const out = [], t = now(); let bg = 0, intervals = 0;
    for (const rec of S.timers.values()) {
      if (rec.bg) { bg++; continue; }
      if (rec.interval) { intervals++; continue; }
      out.push({ delay: rec.delay, due: rec.created + rec.delay });
    }
    out.sort((a, b) => a.due - b.due);
    return { pending: out.slice(0, 32), background: bg, intervals: intervals, now: t };
  };
  // --- constraint validation: the browser fires `invalid` on a control it rejects (a submit click,
  // an implicit Enter submission, requestSubmit(), reportValidity() - and checkValidity(), which
  // shows no bubble). The event neither bubbles nor crosses shadow boundaries, so the listener is
  // capture-phase on window (first in the path, and registered before any page script, so a
  // page's stopImmediatePropagation cannot hide it) and every shadow root gets its own
  // (observeRoot). Cleared at the dispatch mark; the after-capture reads it.
  S.invalid = [];   // ring of {el, t, message, flags}
  const VFLAGS = ['valueMissing', 'typeMismatch', 'patternMismatch', 'tooLong', 'tooShort', 'rangeUnderflow',
                  'rangeOverflow', 'stepMismatch', 'badInput', 'customError'];
  S.onInvalid = (e) => {
    const el = e.target;
    if (!el || el.nodeType !== 1) return;
    const flags = [];
    try { const v = el.validity; if (v) for (const f of VFLAGS) if (v[f]) flags.push(f); } catch (e2) {}
    let msg = '';
    try { msg = String(el.validationMessage || ''); } catch (e2) {}
    S.invalid.push({ el: el, t: now(), message: msg.slice(0, 200), flags: flags });
    if (S.invalid.length > 64) S.invalid.splice(0, S.invalid.length - 64);
  };
  try { window.addEventListener('invalid', S.onInvalid, true); } catch (e) {}
  const targetOf = (r) => { const t = r.target; return (t && t.nodeType === 1) ? t : ((t && t.parentElement) || null); };
  const inBg = (el) => { let n = el; let i = 0; while (n && i++ < 64) { if (S.bg.has(n)) return true; n = n.parentElement || (n.parentNode && n.parentNode.host) || null; } return false; };
  let mo = null;
  try {
    mo = new MutationObserver(list => {
      const t = now();
      for (const r of list) {
        const el = targetOf(r);
        if (el && S.bg.size && inBg(el)) { S.bgMutations++; S.lastBgMutation = t; }
        else { S.mutations++; S.lastMutation = t; }
        if (el) { S.recent.push({ el: el, t: t }); if (S.recent.length > RECENT) S.recent.splice(0, S.recent.length - RECENT); }
      }
    });
    mo.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
  } catch (e) {}
  const observeRoot = (r) => {
    try { if (mo) mo.observe(r, { subtree: true, childList: true, attributes: true, characterData: true }); } catch (e) {}
    try { r.addEventListener('invalid', S.onInvalid, true); } catch (e) {}   // `invalid` is not composed: each root needs its own
  };
  // Background = an element that mutated *repeatedly* within the last windowMs (>= 3 separate
  // batches spread over >= 200 ms), after the document's load event. A single burst - the
  // parser building the page, a one-off render - is not background; a ticker or live feed is.
  S.loadedAt = document.readyState === 'complete' ? now() : 0;
  try { window.addEventListener('load', () => { S.loadedAt = now(); }); } catch (e) {}
  S.markBackground = (windowMs) => {
    const t = now();
    const floor = Math.max(t - windowMs, S.loadedAt || 0);
    const per = new Map();
    for (const r of S.recent) {
      if (r.t < floor || !r.el.isConnected) continue;
      let e = per.get(r.el);
      if (!e) per.set(r.el, { first: r.t, last: r.t, batches: 1 });
      else { if (r.t - e.last > 5) e.batches++; e.last = r.t; }
    }
    const set = new Set();
    for (const [el, e] of per) { if (e.batches >= 3 && e.last - e.first >= 200) set.add(el); }
    S.bg = set;
    return Array.from(set);
  };
  try {
    const orig = Element.prototype.attachShadow;
    if (orig && !orig.__ar) {
      const patched = function (init) {
        const r = orig.call(this, init);
        try { S.shadow.set(this, r); observeRoot(r); } catch (e) {}
        return r;
      };
      patched.__ar = true;
      Element.prototype.attachShadow = patched;
    }
  } catch (e) {}
  // Pages we attach to late (wrap mode over CDP): pick up the open shadow roots that already exist.
  try {
    for (const el of document.querySelectorAll('*')) {
      if (el.shadowRoot) { S.shadow.set(el, el.shadowRoot); observeRoot(el.shadowRoot); }
    }
  } catch (e) {}
  try {
    window.addEventListener('scroll', () => { S.scrollEvents++; S.lastScroll = now(); }, { capture: true, passive: true });
  } catch (e) {}
  try {
    const po = new PerformanceObserver(l => {
      for (const e of l.getEntries()) { if (!e.hadRecentInput) { S.shifts++; S.lastShift = now(); } }
    });
    po.observe({ type: 'layout-shift', buffered: false });
  } catch (e) {}
  window.__ar = S;
  return true;
})()
"""

# Walks the top frame and same-origin child frames (depth <= 3), calling fn(w, A, off) with the
# frame's window, its __ar (or null) and its clock offset onto the top frame's performance.now().
_FRAMES_JS = r"""
  const top0 = performance.timeOrigin;
  const eachFrame = (fn) => {
    const walk = (w, depth) => {
      let A = null, off = 0;
      try { A = w.__ar || null; off = w.performance.timeOrigin - top0; } catch (e) { return; }  // cross-origin
      fn(w, A, off);
      if (depth >= 3) return;
      try { for (let i = 0; i < w.frames.length && i < 20; i++) walk(w.frames[i], depth + 1); } catch (e) {}
    };
    walk(window, 0);
  };
"""

# Aggregates the top frame and same-origin child frames. Each frame's timestamps are shifted
# onto the top frame's clock via performance.timeOrigin.
_STATUS_BODY_JS = (
    _FRAMES_JS
    + r"""
  const status = () => {
    const S = window.__ar;
    if (!S) return null;
    const out = { now: performance.now(), mutations: 0, lastMutation: 0, bgMutations: 0, lastBgMutation: 0,
                  scrollEvents: 0, lastScroll: 0, shifts: 0, lastShift: 0,
                  animations: 0, bgAnimations: 0, infiniteAnimations: 0,
                  pendingTimers: [], bgTimers: 0, intervalsStarted: 0,
                  readyState: document.readyState, frames: 0 };
    eachFrame((w, A, off) => {
      if (A) {
        out.frames++;
        out.mutations += A.mutations; out.bgMutations += (A.bgMutations || 0); out.scrollEvents += A.scrollEvents; out.shifts += A.shifts;
        if (A.lastMutation) out.lastMutation = Math.max(out.lastMutation, A.lastMutation + off);
        if (A.lastBgMutation) out.lastBgMutation = Math.max(out.lastBgMutation, A.lastBgMutation + off);
        if (A.lastScroll) out.lastScroll = Math.max(out.lastScroll, A.lastScroll + off);
        if (A.lastShift) out.lastShift = Math.max(out.lastShift, A.lastShift + off);
        try {
          const pt = A.pendingTimers ? A.pendingTimers() : null;
          if (pt) {
            out.bgTimers += pt.background; out.intervalsStarted += pt.intervals;
            for (const t of pt.pending) if (out.pendingTimers.length < 32) out.pendingTimers.push({ delay: t.delay, due: t.due + off });
          }
        } catch (e) {}
      }
      // Animations are anchored to the dispatch: one already running at the mark is background
      // (finite or infinite - it is not this action's work). One that started after the mark is
      // foreground and keeps the page busy, even if perpetual - a spinner the action put up is
      // the page saying "still working", and it is waited on until it is removed (or the hard
      // timeout). On a document with no anchor (after a cross-document navigation) there is
      // nothing to attribute to: finite animations are busy, perpetual ones are ignored.
      try {
        const pre = A && A.preAnims;
        for (const a of w.document.getAnimations()) {
          if (a.playState !== 'running') continue;
          let inf = false;
          try { inf = !!(a.effect && a.effect.getTiming().iterations === Infinity); } catch (e) {}
          if (inf) out.infiniteAnimations++;
          if (pre) { if (pre.has(a)) out.bgAnimations++; else out.animations++; }
          else if (!inf) out.animations++;
        }
      } catch (e) {}
      try { if (w.document.readyState !== 'complete' && out.readyState === 'complete') out.readyState = w.document.readyState; } catch (e) {}
    });
    out.pendingTimers.sort((a, b) => a.due - b.due);
    return out;
  };
"""
)

STATUS_JS = (
    "(() => {"
    + _STATUS_BODY_JS
    + r"""
  return status();
})()
"""
)

# The dispatch anchor: mark every same-origin frame, then return the status (so the caller's
# "since dispatch" baselines come from the same aggregate the settle loop polls).
MARK_JS = (
    "(() => {"
    + _STATUS_BODY_JS
    + r"""
  eachFrame((w, A) => { try { if (A && A.markDispatch) A.markDispatch(); } catch (e) {} });
  return status();
})()
"""
)

# Re-mark background in every frame; report ``ref`` = the later of the last *foreground*
# mutation and the load event (top clock). The page counts as quiet only once quiet_ms has been
# *observed* since ref: a page that loaded 10 ms ago has not been watched long enough to know
# whether a ticker is about to start.
PRESETTLE_JS = (
    "(windowMs) => {"
    + _FRAMES_JS
    + r"""
  let roots = 0, ref = 0, found = false, loading = false;
  eachFrame((w, A, off) => {
    if (!A) return;
    found = true;
    try { if (A.markBackground) roots += A.markBackground(windowMs).length; } catch (e) {}
    if (!A.loadedAt) loading = true; else ref = Math.max(ref, A.loadedAt + off);
    if (A.lastMutation) ref = Math.max(ref, A.lastMutation + off);
  });
  if (!found) return null;
  return { now: performance.now(), ref: ref, loading: loading, roots: roots };
}
"""
)

# Evaluated on the target's handle (in its own frame). Scrolls the element into view - instantly,
# centred - only if some part of it (or of an ancestor frame) is outside its viewport. Reports the
# top window's scroll delta and the element's own frame's, so the receipt can state it.
PRESCROLL_JS = r"""
(el) => {
  const rect = el.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) return { scrolled: false, reason: 'no box' };
  let w = window, r = rect, needed = false;
  for (let i = 0; i < 8 && w; i++) {
    if (r.top < 0 || r.left < 0 || r.bottom > w.innerHeight || r.right > w.innerWidth) { needed = true; break; }
    let fe = null; try { fe = w.frameElement; } catch (e) { fe = null; }
    if (!fe) break;
    r = fe.getBoundingClientRect(); w = fe.ownerDocument.defaultView;
  }
  if (!needed) return { scrolled: false };
  let T = null; try { T = window.top; T.scrollX; } catch (e) { T = null; }
  const tx0 = T ? T.scrollX : null, ty0 = T ? T.scrollY : null, fx0 = window.scrollX, fy0 = window.scrollY;
  el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
  return { scrolled: true, dx: T ? T.scrollX - tx0 : 0, dy: T ? T.scrollY - ty0 : 0,
           frame_dx: window.scrollX - fx0, frame_dy: window.scrollY - fy0 };
}
"""

# Shared helper source, prepended to CAPTURE_JS and TARGET_JS.
_HELPERS_JS = r"""
  const MAXP = __MAXP__;
  const SHADOW = '#shadow-root', FRAMEDOC = '#document';
  const shadowOf = (el) => {
    if (el.shadowRoot) return el.shadowRoot;
    try { const S = window.__ar; if (S && S.shadow) return S.shadow.get(el) || null; } catch (e) {}
    return null;
  };
  const frameElementOf = (n) => { try { const w = n.ownerDocument.defaultView; return w ? w.frameElement : null; } catch (e) { return null; } };
  const inFrame = (n) => { try { const w = n.ownerDocument.defaultView; return !!w && w !== w.top; } catch (e) { return true; } };
  // Path parts for one element, walking up. Must agree with the incremental walk in CAPTURE_JS.
  const pathParts = (el) => {
    const parts = [];
    let n = el;
    while (n && n.nodeType === 1 && parts.length < MAXP) {
      const tag = n.tagName.toLowerCase();
      const root = n.getRootNode();
      const inShadow = !!(root && root.nodeType === 11 && root.host);
      const p = n.parentElement;
      const topLight = !inShadow && !inFrame(n);
      let part;
      if (n.id) {
        part = tag + '#' + CSS.escape(n.id);
        if (topLight) { parts.unshift(part); break; }
      } else if (p || inShadow) {
        const sibs = p ? p.children : root.children;
        let i = 1;
        for (const s of sibs) { if (s === n) break; if (s.tagName === n.tagName) i++; }
        part = tag + ':nth-of-type(' + i + ')';
      } else {
        part = tag;  // documentElement
      }
      parts.unshift(part);
      if (p) { n = p; continue; }
      if (inShadow) { parts.unshift(SHADOW); n = root.host; continue; }
      const fe = frameElementOf(n);
      if (fe) { parts.unshift(FRAMEDOC); n = fe; continue; }
      break;
    }
    return parts.length > MAXP ? parts.slice(-MAXP) : parts;
  };
  const cssPath = (el) => pathParts(el).join('>');
  const visible = (el) => {
    if (!el || !el.getClientRects) return false;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return false;
    const cs = getComputedStyle(el);
    return cs.visibility !== 'hidden' && cs.display !== 'none';
  };
  const elValue = (el) => {
    if (!el) return null;
    const t = el.tagName;
    if (t === 'INPUT' || t === 'TEXTAREA' || t === 'SELECT') {
      const v = el.value == null ? '' : String(el.value);
      return el.type === 'password' ? '*'.repeat(v.length) : v;
    }
    if (el.isContentEditable) return (el.innerText || '').slice(0, 2000);
    return null;
  };
  const describe = (el) => el ? {
    path: cssPath(el), tag: el.tagName.toLowerCase(), id: el.id || null, name: el.getAttribute('name'),
    type: el.getAttribute('type'), role: el.getAttribute('role'), value: elValue(el),
    text: (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120) || null,
  } : null;
  const scrollableAncestor = (el) => {
    let n = el && el.parentElement;
    while (n && n !== document.body && n !== document.documentElement) {
      const cs = getComputedStyle(n);
      const oy = cs.overflowY;
      if ((oy === 'auto' || oy === 'scroll') && n.scrollHeight > n.clientHeight + 1) {
        return { path: cssPath(n), scrollTop: n.scrollTop, scrollHeight: n.scrollHeight, clientHeight: n.clientHeight,
                 atBottom: Math.ceil(n.scrollTop + n.clientHeight) >= n.scrollHeight, atTop: n.scrollTop <= 0 };
      }
      n = n.parentElement;
    }
    return null;
  };
""".replace("__MAXP__", str(PATH_MAX_PARTS))

CAPTURE_JS = (
    r"""
(opts) => {
"""
    + _HELPERS_JS
    + r"""
  const MAXN = (opts && opts.maxNodes) || 20000;
  const MAXFRAMES = 10;
  const ATTRS = ['class','name','type','href','src','role','aria-label','aria-expanded','aria-selected',
                 'aria-checked','aria-hidden','aria-modal','aria-disabled','disabled','hidden','open',
                 'placeholder','selected','readonly','style','data-state'];
  const ATTRSET = new Set(ATTRS);
  const SKIP = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEMPLATE: 1 };
  const sig = (el) => {
    let s = el.tagName.toLowerCase();
    const at = el.attributes;
    for (let i = 0; i < at.length; i++) { const a = at[i]; if (ATTRSET.has(a.name)) s += '|' + a.name + '=' + a.value; }
    const v = elValue(el); if (v !== null) s += '|val=' + v;
    if (el.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) s += '|chk=' + el.checked;
    try { if (el.checkVisibility && !el.checkVisibility()) s += '|hidden'; } catch (e) {}
    let t = '';
    for (const c of el.childNodes) if (c.nodeType === 3) t += c.nodeValue;
    t = t.replace(/\s+/g, ' ').trim();
    if (t) s += '|t=' + t.slice(0, 200);
    return s;
  };
  // Background marking (before-capture only): elements mutated within the window before the
  // action, in this frame and every same-origin child frame. Their subtrees are flagged below.
  const background = [];
  const forEachAr = (w, depth, fn) => {
    let A = null; try { A = w.__ar; } catch (e) { return; }
    if (A) fn(A);
    if (depth >= 3) return;
    try { for (let i = 0; i < w.frames.length && i < 20; i++) forEachAr(w.frames[i], depth + 1, fn); } catch (e) {}
  };
  if (opts && opts.markBackground) {
    forEachAr(window, 0, (A) => { try { if (A.markBackground) for (const el of A.markBackground(opts.bgWindowMs || 1500)) { if (background.length < 50) background.push(cssPath(el)); } } catch (e) {} });
  }
  const bgOf = (el) => { try { const A = el.ownerDocument.defaultView.__ar; return !!(A && A.bg && A.bg.has(el)); } catch (e) { return false; } };
  // Controls the browser's constraint validation rejected since the dispatch anchor, in this
  // frame and every same-origin child frame (shadow roots feed their frame's ring). One entry per
  // control (its latest message/flags), in first-seen order; a frame with no anchor has nothing
  // to attribute to and contributes none.
  const validation = [];
  forEachAr(window, 0, (A) => { try {
    if (A.anchor == null || !A.invalid || !A.invalid.length) return;
    const seen = new Map();
    for (const r of A.invalid) {
      if (r.t < A.anchor) continue;
      const prev = seen.get(r.el);
      if (prev) { prev.message = r.message; prev.flags = r.flags; } else seen.set(r.el, { message: r.message, flags: r.flags });
    }
    for (const [el, r] of seen) {
      if (validation.length >= 64) break;
      validation.push({ path: cssPath(el), tag: el.tagName.toLowerCase(), id: el.id || null, name: el.getAttribute('name'),
                        type: el.getAttribute('type'), message: r.message, flags: r.flags });
    }
  } catch (e) {} });
  // Viewport rectangles of the top document's background roots (both captures), so the
  // screenshot measure can set their pixels aside the way the DOM delta sets their nodes aside.
  const bgRects = [];
  try {
    const A = window.__ar;
    if (A && A.bg) for (const el of A.bg) {
      if (!el.isConnected || bgRects.length >= 50) continue;
      const r = el.getBoundingClientRect();
      const x0 = Math.max(0, Math.floor(r.left)), y0 = Math.max(0, Math.floor(r.top));
      const x1 = Math.min(window.innerWidth, Math.ceil(r.right)), y1 = Math.min(window.innerHeight, Math.ceil(r.bottom));
      if (x1 > x0 && y1 > y0) bgRects.push([x0, y0, x1, y1]);
    }
  } catch (e) {}
  const nodes = [];
  const frames = [];
  let total = 0, truncated = false;
  // Explicit DFS stack; entries carry their already-built path parts (O(1) per child).
  const stack = [];
  const pushChildren = (children, parentParts, top, marker, parentBg) => {
    const counts = {};
    const entries = [];
    for (const c of children) {
      if (!c.tagName || SKIP[c.tagName]) continue;
      const tag = c.tagName.toLowerCase();
      let part;
      if (c.id) part = tag + '#' + CSS.escape(c.id);
      else { counts[tag] = (counts[tag] || 0) + 1; part = tag + ':nth-of-type(' + counts[tag] + ')'; }
      let parts;
      if (c.id && top) parts = [part];
      else {
        parts = marker ? parentParts.concat([marker, part]) : parentParts.concat([part]);
        if (parts.length > MAXP) parts = parts.slice(-MAXP);
      }
      entries.push({ el: c, parts: parts, top: top, bg: parentBg || bgOf(c) });
    }
    for (let i = entries.length - 1; i >= 0; i--) stack.push(entries[i]);
  };
  const root = document.body || document.documentElement;
  if (root) stack.push({ el: root, parts: pathParts(root), top: true, bg: bgOf(root) });
  while (stack.length) {
    const it = stack.pop();
    const el = it.el;
    total++;
    if (nodes.length < MAXN) nodes.push(it.parts.join('>') + '\t' + sig(el) + (it.bg ? '\t1' : ''));
    else truncated = true;
    const sr = shadowOf(el);
    if (sr) pushChildren(sr.children, it.parts, false, SHADOW, it.bg);
    if ((el.tagName === 'IFRAME' || el.tagName === 'FRAME') && frames.length < MAXFRAMES) {
      let d = null, w = null;
      try { d = el.contentDocument; w = el.contentWindow; } catch (e) {}
      const fpath = it.parts.join('>');
      if (d && d.documentElement && w) {
        const fse = d.scrollingElement || d.documentElement;
        frames.push({ path: fpath, url: (() => { try { return d.location.href; } catch (e) { return null; } })(),
                      x: w.scrollX, y: w.scrollY, scrollHeight: fse.scrollHeight, clientHeight: fse.clientHeight,
                      atBottom: Math.ceil(w.scrollY + fse.clientHeight) >= fse.scrollHeight, atTop: w.scrollY <= 0,
                      scrollable: fse.scrollHeight > fse.clientHeight + 1 });
        const froot = d.body || d.documentElement;
        if (froot) stack.push({ el: froot, parts: pathParts(froot), top: false, bg: it.bg || bgOf(froot) });
      } else {
        frames.push({ path: fpath, crossOrigin: true });
      }
    }
    pushChildren(el.children, it.parts, it.top, null, it.bg);
  }
  // FNV-1a over the visible text so we can report "text changed" without shipping the text.
  let text = root ? (root.innerText || '') : '';
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  const se = document.scrollingElement || document.documentElement;
  const ovHidden = (() => { try {
    const a = getComputedStyle(document.documentElement).overflowY, b = document.body ? getComputedStyle(document.body).overflowY : '';
    return a === 'hidden' || b === 'hidden' || a === 'clip' || b === 'clip';
  } catch (e) { return false; } })();
  const modals = [];
  for (const el of document.querySelectorAll('dialog[open],[role=dialog],[role=alertdialog],[aria-modal=true]')) {
    if (visible(el) && modals.length < 10) modals.push(cssPath(el));
  }
  const containers = [];
  let scanned = 0;
  for (const el of document.querySelectorAll('*')) {
    if (++scanned > 1500 || containers.length >= 5) break;
    if (el === se || el === document.body) continue;
    const cs = getComputedStyle(el);
    const oy = cs.overflowY;
    if ((oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 1 && visible(el)) {
      containers.push({ path: cssPath(el), scrollTop: el.scrollTop, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight });
    }
  }
  return {
    url: location.href, title: document.title, readyState: document.readyState,
    viewport: { width: window.innerWidth, height: window.innerHeight },
    scroll: {
      x: window.scrollX, y: window.scrollY,
      scrollWidth: se.scrollWidth, scrollHeight: se.scrollHeight,
      clientWidth: se.clientWidth, clientHeight: se.clientHeight,
      pageScrollable: se.scrollHeight > se.clientHeight + 1 && !ovHidden,
      overflowHidden: ovHidden,
      atTop: window.scrollY <= 0,
      atBottom: Math.ceil(window.scrollY + se.clientHeight) >= se.scrollHeight,
    },
    focused: describe(document.activeElement && document.activeElement !== document.body ? document.activeElement : null),
    modals: modals,
    scrollableContainers: containers,
    frames: frames,
    background: background,
    backgroundRects: bgRects,
    validation: validation,
    elementCount: nodes.length,
    elementTotal: total,
    truncated: truncated,
    textHash: h >>> 0,
    textLength: text.length,
    // One string, not 10k small arrays: the protocol serialisation was 80% of the capture cost.
    nodes: nodes.join('\n'),
  };
}
"""
)

TARGET_JS = (
    r"""
(el) => {
"""
    + _HELPERS_JS
    + r"""
  if (!el) return null;
  const r = el.getBoundingClientRect();
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  const inViewport = r.width > 0 && r.height > 0 && cx >= 0 && cy >= 0 && cx <= window.innerWidth && cy <= window.innerHeight;
  let coveredBy = null;
  if (inViewport) {
    // elementFromPoint at the target's own root (document or shadow root) so shadow children resolve.
    const root = el.getRootNode();
    const src = (root && root.elementFromPoint) ? root : document;
    let hit = src.elementFromPoint(cx, cy);
    // Descend through open shadow hosts to the real hit target.
    for (let i = 0; hit && i < 5; i++) {
      const sr = shadowOf(hit);
      if (!sr || !sr.elementFromPoint) break;
      const inner = sr.elementFromPoint(cx, cy);
      if (!inner || inner === hit) break;
      hit = inner;
    }
    if (hit && hit !== el && !el.contains(hit) && !hit.contains(el)) coveredBy = describe(hit);
  }
  const cs = getComputedStyle(el);
  const w = el.ownerDocument.defaultView;
  const fse = el.ownerDocument.scrollingElement || el.ownerDocument.documentElement;
  // Off the document entirely (e.g. left:-9999px): no scroll can bring it into view.
  const offscreen = r.width > 0 && r.height > 0 && (
    r.right + w.scrollX <= 0 || r.bottom + w.scrollY <= 0 ||
    r.left + w.scrollX >= fse.scrollWidth || r.top + w.scrollY >= fse.scrollHeight);
  return {
    ...describe(el),
    inFrame: inFrame(el),
    frameScroll: inFrame(el) && w ? { x: w.scrollX, y: w.scrollY, scrollHeight: fse.scrollHeight, clientHeight: fse.clientHeight,
                                      atBottom: Math.ceil(w.scrollY + fse.clientHeight) >= fse.scrollHeight, atTop: w.scrollY <= 0 } : null,
    disabled: !!(el.disabled || el.getAttribute('aria-disabled') === 'true' || (el.closest && el.closest('fieldset[disabled]'))),
    readonly: !!(el.readOnly || el.getAttribute('aria-readonly') === 'true'),
    editable: !!(el.isContentEditable || ((el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') && !el.readOnly && !el.disabled)),
    visible: visible(el),
    pointerEvents: cs.pointerEvents,
    rect: { x: r.left, y: r.top, width: r.width, height: r.height },
    inViewport: inViewport,
    offscreen: offscreen,
    coveredBy: coveredBy,
    scrollableAncestor: scrollableAncestor(el),
    // The form a control belongs to and its constraint-validation state - context only: the verdict
    // keys on the `invalid` events, never on this (matches(':invalid') fires no events; checkValidity() would).
    form: (() => { try {
      const f = el.form || (el.closest ? el.closest('form') : null);
      return f ? { path: cssPath(f), noValidate: !!f.noValidate, invalid: f.matches(':invalid') } : null;
    } catch (e) { return null; } })(),
    selfScrollable: (() => { const oy = cs.overflowY; return (oy === 'auto' || oy === 'scroll') && el.scrollHeight > el.clientHeight + 1; })(),
    scrollTop: el.scrollTop, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight,
  };
}
"""
)
