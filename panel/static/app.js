/* IBM Storage Plugin Proxmox - UI (vanilla JS on top of IBM Carbon v11 CSS + @carbon/icons) */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const main = $("#main");
let ME = null;
let ROUTE_SEQ = 0;

/* ======================================================================
   Theme: light (g10) by default, dark (g100) optional - saved in the browser
   ====================================================================== */
function currentTheme() { return document.documentElement.dataset.theme === "dark" ? "dark" : "light"; }
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  document.documentElement.className = t === "dark" ? "cds--g100" : "cds--g10";
  try { localStorage.setItem("swtheme", t); } catch (e) { }
  const b = $("#theme-btn");
  if (b) { b.innerHTML = icon(t === "dark" ? "light" : "asleep", 20); b.title = b.ariaLabel = t === "dark" ? "Use light theme" : "Use dark theme"; }
  if (window.__charts) window.__charts.forEach(f => f());
}

/* ======================================================================
   Utilities
   ====================================================================== */
function esc(v) { return String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function bytes(n, dec) {
  n = Number(n) || 0; const u = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]; let i = 0;
  while (Math.abs(n) >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  const d = dec ?? (n >= 100 || i === 0 ? 0 : n >= 10 ? 1 : 2);
  return `${LANG === "en" ? n.toFixed(d) : n.toFixed(d).replace(".", ",")} ${u[i]}`;
}
function num(n, d = 0) { return Number(n || 0).toLocaleString(LOCALE, { maximumFractionDigits: d, minimumFractionDigits: d }); }
function pct(a, b) { return b ? Math.round(100 * a / b) : 0; }
function ago(ts) { const s = Math.max(0, Math.round(Date.now() / 1000 - ts)); return s < 60 ? `${s} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`; }
function dt(ts) { return ts ? new Date(ts * 1000).toLocaleString(LOCALE, { dateStyle: "short", timeStyle: "short" }) : ""; }
function fcTime(t) { const m = String(t || "").match(/^(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)/); return m ? `${m[3]}/${m[2]}/20${m[1]} ${m[4]}:${m[5]}` : t || ""; }
function debounce(f, ms = 150) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => f(...a), ms); }; }
function uid() { return "u" + Math.random().toString(36).slice(2, 9); }

/* ---- Carbon components -------------------------------------------- */
const TAG_COLORS = { ok: "green", warn: "warm-gray", err: "red", info: "blue", neutral: "gray", purple: "purple", teal: "teal", cyan: "cyan" };
function tag(text, kind = "neutral", title = "") { return `<span class="cds--tag cds--tag--sm cds--tag--${TAG_COLORS[kind] || kind}" ${title ? `title="${esc(title)}"` : ""}><span class="cds--tag__label">${esc(text)}</span></span>`; }
const ST_ICON = { ok: "checkmark--filled", warn: "warning--alt--filled", err: "error--filled", info: "information--filled", neutral: "circle-dash", run: "in-progress" };
function status(kind, label) { return `<span class="st ${kind}">${icon(ST_ICON[kind] || "circle-dash", 16)}<span>${esc(label)}</span></span>`; }
function btn(label, kind = "primary", attrs = "", ico = "", size = "") {
  const only = !label && ico;
  return `<button type="button" class="cds--btn cds--btn--${kind}${size ? " cds--btn--" + size : ""}${only ? " cds--btn--icon-only" : ""}" ${attrs}>${esc(label)}${ico ? icon(ico, 16, "cds--btn__icon") : ""}</button>`;
}
function link(href, text) { return `<a class="cds--link" href="${href}">${esc(text)}</a>`; }
function breadcrumb(items) {
  return `<nav aria-label="Breadcrumb"><ol class="cds--breadcrumb cds--breadcrumb--no-trailing-slash">${items.map(([t, h]) =>
    `<li class="cds--breadcrumb-item">${h ? `<a class="cds--link" href="${h}">${esc(t)}</a>` : `<span>${esc(t)}</span>`}</li>`).join("")}</ol></nav>`;
}
function pageHeader({ crumbs, title, sub, actions = "", meta = "" }) {
  return `<div class="ph"><div style="min-width:0">${crumbs ? breadcrumb(crumbs) : ""}<h1>${title}</h1>${sub ? `<p>${sub}</p>` : ""}${meta ? `<div class="ph-meta">${meta}</div>` : ""}</div>
    <div class="ph-actions">${actions}</div></div>`;
}
function notification(kind, title, sub = "", lowContrast = true) {
  const k = { ok: "success", warn: "warning", err: "error", info: "info" }[kind] || kind;
  return `<div class="cds--inline-notification cds--inline-notification--${k} ${lowContrast ? "cds--inline-notification--low-contrast" : ""}" role="${k === "error" ? "alert" : "status"}">
    <div class="cds--inline-notification__details">${icon(ST_ICON[kind] || "information--filled", 20, "cds--inline-notification__icon")}
    <div class="cds--inline-notification__text-wrapper"><div class="cds--inline-notification__title">${title}</div>${sub ? `<div class="cds--inline-notification__subtitle">${sub}</div>` : ""}</div></div></div>`;
}
function textInput(id, label, attrs = "", helper = "") {
  return `<div class="cds--form-item cds--text-input-wrapper"><label class="cds--label" for="${id}">${esc(label)}</label>
    <div class="cds--text-input__field-outer-wrapper"><div class="cds--text-input__field-wrapper"><input id="${id}" class="cds--text-input" ${attrs}></div></div>
    ${helper ? `<div class="cds--form__helper-text">${helper}</div>` : ""}</div>`;
}
function selectInput(id, label, options, helper = "", inline = false) {
  return `<div class="cds--form-item"><div class="cds--select ${inline ? "cds--select--inline" : ""}"><label class="cds--label" for="${id}">${esc(label)}</label>
    <div class="cds--select-input__wrapper"><select id="${id}" class="cds--select-input">${options.map(o => `<option value="${esc(o.v)}" ${o.sel ? "selected" : ""}>${esc(o.t)}</option>`).join("")}</select>
    ${icon("chevron--down", 16, "cds--select__arrow")}</div>${helper ? `<div class="cds--form__helper-text">${helper}</div>` : ""}</div></div>`;
}
function checkbox(id, label, checked = false, attrs = "") {
  return `<div class="cds--form-item cds--checkbox-wrapper"><input type="checkbox" class="cds--checkbox" id="${id}" ${checked ? "checked" : ""} ${attrs}>
    <label for="${id}" class="cds--checkbox-label"><span class="cds--checkbox-label-text">${label}</span></label></div>`;
}
function tabs(items, sel, attr = "data-tab") {
  return `<div class="cds--tabs"><div class="cds--tab--list" role="tablist">${items.map(([k, t, n]) =>
    `<button type="button" role="tab" aria-selected="${k === sel}" class="cds--tabs__nav-item cds--tabs__nav-link ${k === sel ? "cds--tabs__nav-item--selected" : ""}" ${attr}="${k}">
      <div class="cds--tabs__nav-item-label-wrapper"><span class="cds--tabs__nav-item-label">${esc(t)}${n != null ? ` <span class="muted">(${n})</span>` : ""}</span></div></button>`).join("")}</div></div>`;
}
function contentSwitcher(id, items, sel) {
  return `<div class="cds--content-switcher cds--content-switcher--sm" id="${id}" role="tablist">${items.map(([k, t]) =>
    `<button type="button" role="tab" aria-selected="${k === sel}" class="cds--content-switcher-btn ${k === sel ? "cds--content-switcher--selected" : ""}" data-v="${k}"><span class="cds--content-switcher__label">${esc(t)}</span></button>`).join("")}</div>`;
}
function sl(rows) { return `<table class="sl"><tbody>${rows.filter(Boolean).map(([k, v]) => `<tr><th scope="row">${esc(k)}</th><td>${v}</td></tr>`).join("")}</tbody></table>`; }
function meter(used, total, { label = "", prov = null } = {}) {
  const p = pct(used, total), c = p >= 90 ? "crit" : p >= 80 ? "warn" : "";
  return `<div class="meter"><div class="mt"><span>${label}<b>${bytes(used)}</b> of ${bytes(total)}</span><span><b>${p}%</b></span></div>
    <div class="bar" role="meter" aria-valuenow="${p}" aria-valuemin="0" aria-valuemax="100">${prov ? `<span class="prov" style="width:${Math.min(100, pct(prov, total))}%"></span>` : ""}<span class="${c}" style="width:${p}%"></span></div></div>`;
}
function emptyState(ico, title, text, action = "") {
  return `<div class="empty-state">${icon(ico, 64)}<h3>${esc(title)}</h3><p>${text}</p>${action}</div>`;
}
function skeleton(blocks = 3) {
  return `<div class="page sk-page"><div class="cds--skeleton__text" style="width:12rem;height:.75rem"></div><div class="cds--skeleton__text" style="width:20rem;height:2rem"></div>
    <div class="g g-4" style="margin-top:2rem">${"<div class='cds--skeleton sk-block'></div>".repeat(4)}</div>
    <div class="g g-2" style="margin-top:1rem">${"<div class='cds--skeleton sk-block' style='height:14rem'></div>".repeat(Math.min(blocks, 4))}</div></div>`;
}

/* ---- toasts ---------------------------------------------------------- */
function toast(kind, title, sub = "") {
  const k = { ok: "success", warn: "warning", err: "error", info: "info" }[kind] || "info";
  const t = document.createElement("div");
  t.className = `cds--toast-notification cds--toast-notification--${k}`; t.setAttribute("role", k === "error" ? "alert" : "status");
  t.innerHTML = `${icon(ST_ICON[kind] || "information--filled", 20, "cds--toast-notification__icon")}
    <div class="cds--toast-notification__details"><div class="cds--toast-notification__title">${esc(title)}</div>${sub ? `<div class="cds--toast-notification__subtitle">${esc(sub)}</div>` : ""}
    <div class="cds--toast-notification__caption">${new Date().toLocaleTimeString(LOCALE, { timeStyle: "short" })}</div></div>
    <button class="cds--toast-notification__close-button" type="button" aria-label="Close">${icon("close", 20, "cds--toast-notification__close-icon")}</button>`;
  $(".cds--toast-notification__close-button", t).onclick = () => t.remove();
  $("#toasts").prepend(t);
  setTimeout(() => t.remove(), kind === "err" ? 12000 : 6000);
}

/* ---- API ------------------------------------------------------------- */
async function api(method, path, body) {
  const opt = { method, headers: { "x-requested-with": "swpanel" }, credentials: "same-origin" };
  if (body !== undefined) { opt.headers["content-type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch("/api" + path, opt);
  let data = null; try { data = await r.json(); } catch (e) { }
  if (r.status === 401 && path !== "/login") { ME = null; if (path !== "/me" && !location.hash.startsWith("#/login")) location.hash = "#/login"; throw new Error("Session expired. Sign in again."); }
  if (!r.ok) { const e = new Error((data && (data.detail || data.error)) || r.statusText); e.data = data; e.status = r.status; throw e; }
  return data;
}

/* ---- modal ----------------------------------------------------------- */
function modal({ label = "", title, body, buttons = [{ id: "ok", label: "OK", kind: "primary" }], size = "sm", danger = false, onButton, onOpen }) {
  return new Promise(resolve => {
    const root = $("#modal-root"), prev = document.activeElement, id = uid();
    root.innerHTML = `<div class="cds--modal is-visible ${danger ? "cds--modal--danger" : ""}" role="presentation">
      <div class="cds--modal-container cds--modal-container--${size}" role="dialog" aria-modal="true" aria-labelledby="${id}">
        <div class="cds--modal-header">${label ? `<h2 class="cds--modal-header__label">${esc(label)}</h2>` : ""}<h3 class="cds--modal-header__heading" id="${id}">${esc(title)}</h3>
          <div class="cds--modal-close-button"><button class="cds--modal-close" type="button" data-b="__close" aria-label="Close">${icon("close", 20, "cds--modal-close__icon")}</button></div></div>
        <div class="cds--modal-content">${body}</div>
        <div class="cds--modal-footer cds--btn-set ${buttons.length === 3 ? "cds--modal-footer--three-button" : ""}">${buttons.map(b => `<button type="button" class="cds--btn cds--btn--${b.kind || "secondary"}" data-b="${b.id}">${esc(b.label)}</button>`).join("")}</div>
      </div></div>`;
    const close = v => { root.innerHTML = ""; document.removeEventListener("keydown", onKey); if (prev && prev.focus) prev.focus(); resolve(v); };
    const click = async bid => {
      if (bid === "__close") return close(null);
      if (onButton) { const keep = await onButton(bid, root); if (keep) return; }
      close(bid);
    };
    const onKey = e => {
      if (e.key === "Escape") return close(null);
      if (e.key !== "Tab") return;
      const f = $$("button:not([disabled]), input, select, textarea, a[href], summary", root).filter(x => x.offsetParent !== null);
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      else if (!root.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    $$("[data-b]", root).forEach(b => b.onclick = () => click(b.dataset.b));
    $(".cds--modal", root).addEventListener("mousedown", e => { if (e.target.classList.contains("cds--modal")) close(null); });
    if (onOpen) onOpen(root);
    const f = $(".cds--modal-content input, .cds--modal-content select, .cds--modal-footer .cds--btn--primary, .cds--modal-footer .cds--btn--danger", root); if (f) f.focus();
  });
}
async function confirmDialog({ title, body, ok = "Confirm", danger = false, label = "" }) {
  return (await modal({ label, title, body, danger, buttons: [{ id: "no", label: "Cancel", kind: "secondary" }, { id: "yes", label: ok, kind: danger ? "danger" : "primary" }] })) === "yes";
}
async function busy(el, fn) {
  if (!el) return fn();
  const old = el.innerHTML, w = el.offsetWidth; el.disabled = true; el.style.minWidth = w + "px"; el.innerHTML = `<span class="busy-ico"></span>Please wait…`;
  try { return await fn(); } finally { el.disabled = false; el.innerHTML = old; }
}

/* ---- floating menu (overflow menu) ---------------------------------- */
function openMenu(anchor, items) {
  closeMenu();
  const root = $("#menu-root"), r = anchor.getBoundingClientRect();
  root.innerHTML = `<ul class="sw-menu" role="menu">${items.map((it, i) => it === "-" ? "<hr>" : it.header ? `<li class="mh">${it.header}</li>` :
    `<li><button type="button" role="menuitem" class="${it.danger ? "danger" : ""}" data-i="${i}" ${it.disabled ? "disabled" : ""} title="${esc(it.title || "")}" ${it.noi18n ? "data-noi18n" : ""}>${it.svg || (it.icon ? icon(it.icon, 16) : "")}${esc(it.label)}</button></li>`).join("")}</ul>`;
  const m = $(".sw-menu", root);
  const left = Math.min(window.innerWidth - m.offsetWidth - 8, Math.max(8, r.right - m.offsetWidth));
  const top = r.bottom + m.offsetHeight > window.innerHeight - 8 ? r.top - m.offsetHeight : r.bottom;
  m.style.left = left + "px"; m.style.top = top + "px";
  anchor.setAttribute("aria-expanded", "true"); root._anchor = anchor;
  $$("button[data-i]", m).forEach(b => b.onclick = () => { closeMenu(); items[+b.dataset.i].run(); });
  const first = $("button[data-i]:not([disabled])", m); if (first) first.focus();
}
function closeMenu() { const root = $("#menu-root"); if (root._anchor) root._anchor.setAttribute("aria-expanded", "false"); root._anchor = null; root.innerHTML = ""; }
document.addEventListener("mousedown", e => { if (!e.target.closest(".sw-menu") && !e.target.closest("[aria-haspopup]")) closeMenu(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeMenu(); });
window.addEventListener("resize", closeMenu);
window.addEventListener("scroll", closeMenu, true);
function ovfButton(attrs, label = "Actions") { return `<button type="button" class="ovf-btn" aria-haspopup="true" aria-expanded="false" aria-label="${esc(label)}" title="${esc(label)}" ${attrs}>${icon("overflow-menu--vertical", 16)}</button>`; }

/* ---- data table (search + sorting + expandable rows) -------------- */
const DT_STATE = {}; let DT_SEQ = 0;
function dataTable({ id, title, desc, columns, rows, search = true, actions = "", empty = "Nothing to show.", expand, rowAttrs }) {
  id = id || uid();
  const skey = location.hash + "|" + (DT_SEQ++);
  const st = DT_STATE[skey] || (DT_STATE[skey] = { q: "", sort: null, dir: 1, open: new Set() });
  const wrap = document.createElement("div"); wrap.className = "dt"; wrap.id = id;
  const draw = () => {
    let rs = rows.slice();
    if (st.q) { const q = st.q.toLowerCase(); rs = rs.filter(r => columns.some(c => String(c.text ? c.text(r) : (c.key ? r[c.key] : "")).toLowerCase().includes(q))); }
    if (st.sort != null) {
      const c = columns[st.sort], val = c.sortVal || (r => c.key ? r[c.key] : (c.text ? c.text(r) : ""));
      rs.sort((a, b) => { const x = val(a), y = val(b); return (typeof x === "number" && typeof y === "number" ? x - y : String(x ?? "").localeCompare(String(y ?? ""), LOCALE, { numeric: true })) * st.dir; });
    }
    const ncol = columns.length + (expand ? 1 : 0);
    const head = `<tr>${expand ? `<th class="cds--table-expand" scope="col"><span class="cds--table-header-label"></span></th>` : ""}${columns.map((c, i) => {
      const cls = [c.num ? "num" : "", c.act ? "act" : ""].join(" ");
      if (c.sortable === false || c.act) return `<th class="${cls}" scope="col"><div class="cds--table-header-label">${esc(c.t || "")}</div></th>`;
      const active = st.sort === i, aria = active ? (st.dir > 0 ? "ascending" : "descending") : "none";
      return `<th class="${cls}" scope="col" aria-sort="${aria}"><button type="button" class="cds--table-sort ${active ? "cds--table-sort--active" : ""} ${active && st.dir < 0 ? "cds--table-sort--descending" : ""}" data-sort="${i}">
        <span class="cds--table-sort__flex"><div class="cds--table-header-label">${esc(c.t || "")}</div>${icon("arrow--down", 16, "cds--table-sort__icon")}${icon("caret--sort", 16, "cds--table-sort__icon-unsorted")}</span></button></th>`;
    }).join("")}</tr>`;
    const body = rs.length ? rs.map((r, ri) => {
      const key = r.__key ?? ri, open = st.open.has(key);
      const tds = columns.map(c => `<td class="${c.num ? "num" : ""} ${c.act ? "act" : ""}">${c.render ? c.render(r) : esc(c.key ? r[c.key] : "")}</td>`).join("");
      const exp = expand ? `<td class="cds--table-expand" data-previous-value="${open ? "expanded" : "collapsed"}"><button type="button" class="cds--table-expand__button" data-exp="${esc(key)}" aria-label="${open ? "Collapse" : "Expand"}" aria-expanded="${open}">${icon("chevron--right", 16, "cds--table-expand__svg")}</button></td>` : "";
      return `<tr class="${expand ? "cds--parent-row" : ""} ${open ? "cds--expandable-row" : ""}" ${expand ? 'data-parent-row="true"' : ""} ${rowAttrs ? rowAttrs(r) : ""}>${exp}${tds}</tr>` +
        (expand && open ? `<tr class="child" data-child-row="true"><td colspan="${ncol}"><div class="child-inner">${expand(r)}</div></td></tr>` : "");
    }).join("") : `<tr><td colspan="${ncol}"><div class="empty muted">${esc(st.q ? `No results for “${st.q}”.` : empty)}</div></td></tr>`;
    $("table", wrap).innerHTML = `<thead>${head}</thead><tbody>${body}</tbody>`;
    $(".dt-foot", wrap).textContent = `${rs.length} of ${rows.length} item(s)`;
    $$("[data-sort]", wrap).forEach(b => b.onclick = () => { const i = +b.dataset.sort; if (st.sort === i) { if (st.dir > 0) st.dir = -1; else { st.sort = null; st.dir = 1; } } else { st.sort = i; st.dir = 1; } draw(); });
    $$("[data-exp]", wrap).forEach(b => b.onclick = () => { const k = isNaN(b.dataset.exp) ? b.dataset.exp : +b.dataset.exp; st.open.has(k) ? st.open.delete(k) : st.open.add(k); draw(); });
    wrap.dispatchEvent(new CustomEvent("dt:draw"));
  };
  wrap.innerHTML = `${title ? `<div class="dt-head"><h3>${esc(title)}</h3>${desc ? `<p>${desc}</p>` : ""}</div>` : ""}
    ${search || actions ? `<section class="cds--table-toolbar" aria-label="Toolbar"><div class="cds--toolbar-content">
      ${search ? `<div class="cds--search cds--search--lg cds--toolbar-search-container-persistent" role="search">
        <div class="cds--search-magnifier" aria-hidden="true">${icon("search", 16, "cds--search-magnifier-icon")}</div>
        <label class="cds--label cds--visually-hidden" for="${id}-q">Search</label>
        <input class="cds--search-input" id="${id}-q" type="search" placeholder="Search" autocomplete="off"></div>` : "<span style='flex:1'></span>"}
      ${actions}</div></section>` : ""}
    <div class="dt-scroll"><table class="cds--data-table cds--data-table--md ${expand ? "cds--data-table--expandable" : ""}"></table></div><div class="dt-foot"></div>`;
  if (search) $(`#${id}-q`, wrap).value = st.q;
  if (search) $(`#${id}-q`, wrap).oninput = debounce(e => { st.q = e.target.value.trim(); draw(); });
  draw();
  wrap.redraw = draw;
  return wrap;
}
function mount(el, node) { el.innerHTML = ""; el.appendChild(node); return node; }

/* ---- charts (Carbon Charts style) --------------------------------- */
window.__charts = [];
function chartColors() { const cs = getComputedStyle(document.documentElement); return ["--sw-c1", "--sw-c2", "--sw-c3", "--sw-c4", "--sw-c5"].map(v => cs.getPropertyValue(v).trim()); }
function niceMax(v) { if (v <= 0) return 1; const m = Math.pow(10, Math.floor(Math.log10(v))); const n = v / m; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * m; }
// latency arrives in milliseconds (array and host); flash answers in fractions of a millisecond,
// so it is always shown in microseconds
function scaleUnit(v, unit) { return unit === "ms" ? [v * 1000, "µs"] : [v, unit]; }
function fmtVal(v, unit) { v = Number(v) || 0; [v, unit] = scaleUnit(v, unit); return `${num(v, v < 10 && v % 1 ? 2 : v < 100 && v % 1 ? 1 : 0)}${unit ? " " + unit : ""}`; }
function lineChart(el, { title, unit = "", series, hours }) {
  const W = 640, H = 200, L = 44, R = 12, T = 20, B = 26;
  const draw = () => {
    const colors = chartColors();
    const all = series.flatMap(s => s.pts);
    const last = s => s.pts.length ? s.pts[s.pts.length - 1][1] : null;
    const head = `<div class="chart-h"><h4>${esc(title)}</h4><span class="now">${series.map(s => last(s) != null ? `${esc(s.label)}: <b>${fmtVal(last(s), unit)}</b>` : "").filter(Boolean).join(" · ")}</span></div>`;
    if (all.length < 2) { el.innerHTML = `${head}<div class="empty">${icon("time", 16)} Collecting data — the panel records one point per minute.</div>`; return; }
    const now = Math.floor(Date.now() / 1000), t0 = hours ? now - hours * 3600 : Math.min(...all.map(p => p[0])), t1 = hours ? now : Math.max(...all.map(p => p[0]));
    const ymax = niceMax(Math.max(...all.map(p => p[1]), 0) * 1.1);
    const x = t => L + (W - L - R) * (t - t0) / Math.max(t1 - t0, 1), y = v => T + (H - T - B) * (1 - v / ymax);
    const ticks = [0, .25, .5, .75, 1].map(f => ymax * f);
    const axU = unit === "ms" ? "µs" : unit, axF = axU === "µs" ? 1000 : 1;
    const xt = 5, xticks = Array.from({ length: xt }, (_, i) => t0 + (t1 - t0) * i / (xt - 1));
    const tf = t => { const d = new Date(t * 1000); return hours > 24 ? d.toLocaleDateString(LOCALE, { day: "2-digit", month: "2-digit" }) + " " + d.toLocaleTimeString(LOCALE, { hour: "2-digit" }) + "h" : d.toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit" }); };
    // break the line only on collection gaps: interval much longer than the normal pace of the series
    const segs = pts => {
      const gaps = pts.slice(1).map((p, i) => p[0] - pts[i][0]).sort((a, b) => a - b);
      const typical = gaps.length ? gaps[Math.floor(gaps.length / 2)] : 60, limit = Math.max(300, typical * 3);
      const out = []; let cur = [];
      pts.forEach((p, i) => { if (i && p[0] - pts[i - 1][0] > limit) { out.push(cur); cur = []; } cur.push(p); });
      if (cur.length) out.push(cur); return out;
    };
    const paths = series.map((s, i) => segs(s.pts).map(sg => {
      const d = sg.map((p, j) => `${j ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
      const a = `${d}L${x(sg[sg.length - 1][0]).toFixed(1)},${y(0)}L${x(sg[0][0]).toFixed(1)},${y(0)}Z`;
      if (sg.length === 1) return `<circle cx="${x(sg[0][0]).toFixed(1)}" cy="${y(sg[0][1]).toFixed(1)}" r="2.5" fill="${colors[i % colors.length]}"/>`;
      return `<path class="area" d="${a}" fill="${colors[i % colors.length]}"/><path class="line" d="${d}" stroke="${colors[i % colors.length]}"/>`;
    }).join("")).join("");
    el.innerHTML = `${head}<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(title)}">
      <g class="grid">${ticks.map(v => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>`).join("")}</g>
      <g class="axis">${axU ? `<text x="${L - 8}" y="10" text-anchor="end">${esc(axU)}</text>` : ""}${ticks.map(v => `<text x="${L - 8}" y="${y(v) + 4}" text-anchor="end">${fmtVal(v * axF, "")}</text>`).join("")}
      ${xticks.map((t, i) => `<text x="${x(t)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === xt - 1 ? "end" : "middle"}">${tf(t)}</text>`).join("")}</g>
      ${paths}<line class="rule" y1="${T}" y2="${H - B}" x1="-10" x2="-10"/>${series.map((s, i) => `<circle class="dot" r="4" cx="-10" cy="-10" fill="${colors[i % colors.length]}"/>`).join("")}</svg>
      <div class="legend">${series.map((s, i) => `<span><i style="background:${colors[i % colors.length]}"></i>${esc(s.label)}</span>`).join("")}</div>`;
    const svg = $("svg", el), rule = $(".rule", el), dots = $$(".dot", el);
    let tip = null;
    svg.onmousemove = e => {
      svg.classList.add("hover");
      const r = svg.getBoundingClientRect(), sx = (e.clientX - r.left) / r.width * W;
      const t = t0 + (sx - L) / (W - L - R) * (t1 - t0);
      const near = series.map(s => s.pts.reduce((b, p) => !b || Math.abs(p[0] - t) < Math.abs(b[0] - t) ? p : b, null));
      const ref = near.find(Boolean); if (!ref || Math.abs(ref[0] - t) > 600) { svg.onmouseleave(); return; }
      const px = x(ref[0]); rule.setAttribute("x1", px); rule.setAttribute("x2", px);
      near.forEach((p, i) => { if (p) { dots[i].setAttribute("cx", x(p[0])); dots[i].setAttribute("cy", y(p[1])); } });
      if (!tip) { tip = document.createElement("div"); tip.className = "chart-tip"; el.appendChild(tip); }
      tip.innerHTML = `<b>${new Date(ref[0] * 1000).toLocaleString(LOCALE, { dateStyle: "short", timeStyle: "short" })}</b>${near.map((p, i) => p ? `<div><i style="background:${colors[i % colors.length]}"></i>${esc(series[i].label)}: ${fmtVal(p[1], unit)}</div>` : "").join("")}`;
      tip.hidden = false;
      const er = el.getBoundingClientRect(), left = e.clientX - er.left + 14;
      tip.style.left = Math.max(8, Math.min(left, er.width - tip.offsetWidth - 8)) + "px"; tip.style.top = Math.max(0, e.clientY - er.top - tip.offsetHeight - 12) + "px";
    };
    svg.onmouseleave = () => { svg.classList.remove("hover"); rule.setAttribute("x1", -10); rule.setAttribute("x2", -10); dots.forEach(d => d.setAttribute("cx", -10)); if (tip) tip.hidden = true; };
  };
  draw(); window.__charts.push(draw);
}
function sparkline(pts) {
  if (!pts || pts.length < 2) return `<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true"></svg>`;
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0], m = Math.max(...pts.map(p => p[1]), 0.0001);
  const x = t => 100 * (t - t0) / Math.max(t1 - t0, 1), y = v => 28 - 26 * v / m;
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(2)},${y(p[1]).toFixed(2)}`).join("");
  return `<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true"><path class="a" d="${d}L100,30L0,30Z"/><path class="l" d="${d}"/></svg>`;
}
function donut(parts, centerBig, centerSmall) {
  const total = parts.reduce((a, p) => a + p.v, 0) || 1, r = 52, c = 2 * Math.PI * r; let off = 0;
  const arcs = parts.map(p => { const len = c * p.v / total; const s = `<circle r="${r}" cx="70" cy="70" stroke="${p.color}" stroke-dasharray="${len} ${c - len}" stroke-dashoffset="${-off}" transform="rotate(-90 70 70)"/>`; off += len; return s; }).join("");
  return `<svg class="donut" viewBox="0 0 140 140" role="img" aria-label="${esc(centerSmall)} ${esc(centerBig)}"><circle class="bg" r="${r}" cx="70" cy="70"/>${arcs}
    <text class="big" x="70" y="72" text-anchor="middle">${esc(centerBig)}</text><text class="sm" x="70" y="90" text-anchor="middle">${esc(centerSmall)}</text></svg>`;
}

/* ======================================================================
   Shell: header, navigation, routing
   ====================================================================== */
const NAV = [
  { h: "#/", t: "Dashboard", i: "dashboard" },
  { h: "#/storages", t: "Storages", i: "data--base", match: ["#/storage", "#/add"] },
  { h: "#/vms", t: "VMs and volumes", i: "virtual-machine", match: ["#/diskperf"] },
  { h: "#/backups", t: "Backups", i: "ibm-cloud--vpc-block-storage-snapshots" },
  { h: "#/reconcile", t: "Reconciliation", i: "compare" },
  { h: "#/performance", t: "Performance", i: "chart--line" },
  { h: "#/dr", t: "DR", i: "connect", dr: true },
  { section: "Infrastructure" },
  { h: "#/nodes", t: "Proxmox nodes", i: "server--proxy" },
  { h: "#/https", t: "Domain and certificate", i: "locked" },
];
function buildNav() {
  $("#nav-items").innerHTML = NAV.filter(n => !n.dr || (ME && ME.dr)).map(n => n.section ? `<li class="nav-section">${esc(n.section)}</li>` :
    `<li class="cds--side-nav__item"><a class="cds--side-nav__link" href="${n.h}"><div class="cds--side-nav__icon">${icon(n.i, 16)}</div><span class="cds--side-nav__link-text">${esc(n.t)}</span></a></li>`).join("");
  $("#menu-btn").innerHTML = icon("menu", 20);
}
function markNav() {
  const h = "#/" + (location.hash.replace(/^#\/?/, "").split("/")[0] || "");
  $$(".cds--side-nav__link").forEach(a => {
    const n = NAV.find(x => x.h === a.getAttribute("href")) || {};
    const cur = a.getAttribute("href") === h || (n.match || []).includes(h);
    a.classList.toggle("cds--side-nav__link--current", cur);
    if (cur) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
}
function shell(on) {
  $("#sidenav").hidden = !on; $("#menu-btn").hidden = !on; document.body.classList.toggle("nonav", !on);
  $("#header-right").innerHTML =
    (on ? `<button class="hdr-btn" id="refresh-btn" title="Refresh array data" aria-label="Refresh">${icon("renew", 20)}</button>` : "") +
    `<button class="hdr-btn" id="lang-btn" aria-haspopup="true" aria-expanded="false" data-noi18n title="${LANGS[LANG].name}" aria-label="Language">${FLAGS[LANG]}</button>` +
    `<button class="hdr-btn" id="theme-btn"></button>` +
    (on && ME ? `<button class="hdr-btn" id="user-btn" aria-haspopup="true" aria-expanded="false" title="${esc(ME.user)}" aria-label="User">${icon("user--avatar", 20)}</button>` : "");
  setTheme(currentTheme());
  $("#theme-btn").onclick = () => setTheme(currentTheme() === "dark" ? "light" : "dark");
  $("#lang-btn").onclick = e => openMenu(e.currentTarget, Object.keys(LANGS).map(l => ({ label: LANGS[l].name, svg: FLAGS[l], noi18n: true, run: () => setLang(l) })));
  if (on) {
    $("#refresh-btn").onclick = async e => { const b = e.currentTarget; $("svg", b).classList.add("spin-ico"); try { await api("POST", "/refresh"); main.dataset.route = ""; await route(); } finally { $("svg", b)?.classList.remove("spin-ico"); } };
    $("#user-btn").onclick = e => openMenu(e.currentTarget, [
      { header: `<b>${esc(ME.user)}</b><span>${ME.admin ? "Administrator (Sys.Modify)" : "Read only"}</span>` },
      { label: currentTheme() === "dark" ? "Light theme" : "Dark theme", icon: currentTheme() === "dark" ? "light" : "asleep", run: () => setTheme(currentTheme() === "dark" ? "light" : "dark") },
      ...(ME.admin ? [{ label: "Proxmox connection", icon: "settings", run: () => location.hash = "#/setup" }] : []),
      { label: "Open Proxmox VE", icon: "launch", run: () => window.open(`https://${ME.pve_host}:8006/`, "_blank", "noopener") }, "-",
      { label: "Sign out", icon: "logout", run: async () => { await api("POST", "/logout").catch(() => { }); ME = null; location.hash = "#/login"; } },
    ]);
    $("#nav-foot").innerHTML = `<b>IBM Storage Plugin Proxmox</b><br>Version ${esc(ME.version)} ${esc(ME.stage || "")}<br>Proxmox VE at ${esc(ME.pve_host)}`;
    markNav();
  }
}
$("#menu-btn").onclick = () => { const o = !$("#sidenav").classList.contains("open"); $("#sidenav").classList.toggle("open", o); $("#nav-scrim").hidden = !o; };
$("#nav-scrim").onclick = () => { $("#sidenav").classList.remove("open"); $("#nav-scrim").hidden = true; };
document.addEventListener("click", e => { if (e.target.closest(".cds--side-nav__link")) { $("#sidenav").classList.remove("open"); $("#nav-scrim").hidden = true; } });

const routes = { setup: pageSetup, "": pageDashboard, storages: pageStorages, storage: pageStorage, add: pageWizard, vms: pageVMs, diskperf: pageDiskPerf, reconcile: pageReconcile, performance: pagePerformance, nodes: pageNodes, dr: pageDR, https: pageHttps, backups: pageBackups };
async function route() {
  const seq = ++ROUTE_SEQ;
  window.__charts = []; closeMenu(); DT_SEQ = 0;
  const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  if (parts[0] === "login") { shell(false); return pageLogin(); }
  if (parts[0] === "setup" && !ME) { ME = await api("GET", "/me").catch(() => null); if (!ME) { shell(false); return pageSetup(); } }
  if (!ME) { try { ME = await api("GET", "/me"); } catch (e) { location.hash = "#/login"; return; } }
  shell(true);
  if (!!ME.dr !== !!$('#nav-items a[href="#/dr"]')) { buildNav(); markNav(); }
  const fn = routes[parts[0]] || pageDashboard;
  if (main.dataset.route !== location.hash) main.innerHTML = skeleton();
  main.dataset.route = location.hash;
  try { await fn(...parts.slice(1)); }
  catch (e) { if (seq === ROUTE_SEQ) main.innerHTML = `<div class="page">${notification("err", "Could not load this page", esc(e.message))}</div>`; }
}
const PAGE_TITLES = { "": "Dashboard", storages: "Storages", storage: "Storage", add: "Add storage", setup: "Proxmox connection", vms: "VMs and volumes",
  diskperf: "Disk performance", reconcile: "Reconciliation", performance: "Performance", nodes: "Proxmox nodes", login: "Sign in", dr: "DR", https: "Domain and certificate", backups: "Backups" };
function setTitle() {
  const p = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  const extra = p[0] === "storage" && p[1] ? ` ${p[1]}` : p[0] === "diskperf" && p[1] ? ` · VM ${p[1]} ${p[2] || ""}` : "";
  document.title = `${tr(PAGE_TITLES[p[0]] ?? "Dashboard")}${extra} · IBM Storage Plugin Proxmox`;
}
setInterval(() => {
  if (!ME || document.hidden || /^#\/(login|add|setup|https|dr)/.test(location.hash) || /^#\/storage\/[^/]+\/(fc|hosts|protecao)/.test(location.hash)) return;
  if ($("#modal-root").innerHTML || $("#menu-root").innerHTML) return;
  const a = document.activeElement; if (a && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName)) return;
  route();
}, 60000);
window.addEventListener("hashchange", () => { setTitle(); main.dataset.route = ""; route(); main.focus({ preventScroll: true }); window.scrollTo(0, 0); });

/* attribute-driven navigation (no inline handlers: the CSP blocks inline script) */
document.addEventListener("click", e => { const g = e.target.closest("[data-go]"); if (g) location.hash = g.dataset.go; });
document.addEventListener("change", e => { if (e.target.id === "r-tr" && $("#r-po-w")) $("#r-po-w").hidden = e.target.value !== "nvme-tcp"; });

/* copy */
document.addEventListener("click", e => {
  const b = e.target.closest("[data-copy]"); if (!b) return;
  const src = document.getElementById(b.dataset.copy), t = src?.textContent || "";
  (navigator.clipboard ? navigator.clipboard.writeText(t) : Promise.reject()).then(() => toast("ok", "Copied to the clipboard"), () => {
    const r = document.createRange(); r.selectNodeContents(src); const s = getSelection(); s.removeAllRanges(); s.addRange(r); toast("info", "Text selected — press Ctrl+C");
  });
});

/* ======================================================================
   Login
   ====================================================================== */
async function pageLogin() {
  main.innerHTML = `<div class="login">
    <form class="login-form" id="lf" novalidate>
      <h1>Sign in</h1>
      <p class="lead">Use your <b>Proxmox VE</b> user. Proxmox permissions apply here too.</p>
      <div class="form-stack">
        ${textInput("u", "User", 'autocomplete="username" required value="root" autocapitalize="off" spellcheck="false"')}
        ${textInput("p", "Password", 'type="password" autocomplete="current-password" required')}
        <div id="realm-box">${selectInput("r", "Realm", [{ v: "pam", t: "Linux PAM (pam)" }])}</div>
      </div>
      <div id="lerr" style="margin-top:1.5rem"></div>
      <button class="cds--btn cds--btn--primary" type="submit">Sign in${icon("arrow--right", 16, "cds--btn__icon")}</button>
      <p class="fine">The password is sent only to Proxmox VE to get a ticket; the panel does not store it.</p>
    </form>
    <div class="login-art">
      <img class="art-photo" src="/static/cover-flashsystem.jpg" alt="IBM FlashSystem" width="1024" height="937">
      <div class="art-text"><h2>IBM Storage Plugin<br>Proxmox</h2>
      <p id="art-ver">Beta version</p></div></div>
  </div>`;
  $("#p").focus();
  api("GET", "/info").then(i => { if (!i.configured) { location.hash = "#/setup"; return; } $("#art-ver").innerHTML = `Version ${esc(i.version)} <span class="beta-tag">${esc(i.stage)}</span>`; }).catch(() => { });
  api("GET", "/realms").then(rs => { $("#realm-box").innerHTML = selectInput("r", "Realm", rs.map(r => ({ v: r.realm, t: `${r.comment || r.realm} (${r.realm})`, sel: r.realm === "pam" }))); }).catch(() => { });
  $("#lf").onsubmit = async ev => {
    ev.preventDefault(); $("#lerr").innerHTML = "";
    if (!$("#u").value || !$("#p").value) { $("#lerr").innerHTML = notification("warn", "Enter user and password."); return; }
    try {
      await busy($("#lf button[type=submit]"), () => api("POST", "/login", { username: $("#u").value.trim(), password: $("#p").value, realm: $("#r").value }));
      ME = null; location.hash = "#/";
    } catch (e) { $("#lerr").innerHTML = notification("err", "Could not sign in", esc(e.message)); $("#p").select(); }
  };
}

/* ======================================================================
   Setup: Proxmox connection (first run, or a change made by an administrator)
   ====================================================================== */
async function pageSetup() {
  const first = !ME;
  let probed = null;
  const form = `<div class="form-stack">
      ${first ? textInput("s-code", "Installation code", 'autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="XXXX-XXXX"',
        "Shown on the panel container console. Valid only until the first setup.") : ""}
      <div class="form-row">${textInput("s-host", "Proxmox VE address", `placeholder="192.168.15.16" value="${esc(first ? "" : ME.pve_host || "")}" autocapitalize="off" spellcheck="false"`, "IP or name of a cluster node.")}
      ${textInput("s-port", "API port", 'type="number" min="1" max="65535" value="8006"')}</div>
      <div id="s-fp"></div><div id="s-err"></div>
      <div>${btn("Fetch certificate", "primary", 'id="s-probe"', "connect")}</div></div>`;
  if (first) {
    main.innerHTML = `<div class="login"><form class="login-form" id="sf" novalidate><h1>Initial setup</h1>
        <p class="lead">Enter the Proxmox VE this panel will use. After that, sign-in uses the Proxmox users themselves.</p>${form}</form>
      <div class="login-art"><img class="art-photo" src="/static/cover-flashsystem.jpg" alt="IBM FlashSystem" width="1024" height="937">
        <div class="art-text"><h2>IBM Storage Plugin<br>Proxmox</h2><p id="art-ver"></p></div></div></div>`;
    api("GET", "/info").then(i => { if (i.configured) location.hash = "#/login"; $("#art-ver").innerHTML = `Version ${esc(i.version)} <span class="beta-tag">${esc(i.stage)}</span>`; }).catch(() => { });
    $("#sf").onsubmit = e => e.preventDefault();
  } else {
    if (!ME.admin) { main.innerHTML = `<div class="page">${notification("warn", "Changing the connection requires the Sys.Modify privilege in Proxmox.")}</div>`; return; }
    main.innerHTML = `<div class="page">${pageHeader({ title: "Proxmox connection", sub: `The panel currently uses <span class="mono">${esc(ME.pve_host)}</span>. Changing it ends all sessions.` })}
      <div class="wiz-body">${form}</div></div>`;
  }
  const body = () => ({ code: $("#s-code")?.value || "", host: $("#s-host").value.trim(), port: $("#s-port").value });
  const fail = e => { $("#s-err").innerHTML = notification("err", "Could not continue", esc(e.message)); };
  $("#s-host").oninput = $("#s-port").oninput = () => { probed = null; $("#s-fp").innerHTML = ""; };
  $("#s-probe").onclick = async () => {
    $("#s-err").innerHTML = "";
    try { probed = await busy($("#s-probe"), () => api("POST", "/setup/probe", body())); } catch (e) { return fail(e); }
    $("#s-fp").innerHTML = `<p class="cds--label">Certificate of ${esc(probed.host)}:${esc(probed.port)} (SHA-256)</p>
      <div class="codebox" style="padding-right:1rem">${esc(probed.fingerprint)}</div>
      <p class="helper" style="margin:.5rem 0 1rem">Check it in Proxmox: node › System › Certificates, or in the node shell with
        <span class="mono">openssl x509 -in /etc/pve/local/pve-ssl.pem -noout -fingerprint -sha256</span>.</p>
      ${checkbox("s-ok", "Checked: this is my Proxmox fingerprint")}
      <div style="margin-top:1rem">${btn("Save and continue", "primary", 'id="s-save"', "checkmark--outline")}</div>`;
    $("#s-save").onclick = async () => {
      if (!$("#s-ok").checked) return fail(new Error("Confirm the certificate fingerprint."));
      try { await busy($("#s-save"), () => api("POST", "/setup/save", { ...body(), fingerprint: probed.fingerprint })); } catch (e) { return fail(e); }
      ME = null; toast("ok", "Connection configured", `${probed.host}:${probed.port}`); location.hash = "#/login";
    };
  };
}

/* ======================================================================
   Dashboard - styled after the Storage Virtualize 8.x GUI
   ====================================================================== */
function healthKind(h) { if (!h) return "neutral"; const [a, b] = h; return b === 0 ? "neutral" : a === b ? "ok" : a === 0 ? "err" : "warn"; }
async function pageDashboard() {
  const [sts, issues, inv] = await Promise.all([api("GET", "/storages"), api("GET", "/reconcile").catch(() => []), api("GET", "/inventory").catch(() => ({ vms: [], disks: [] }))]);
  if (!sts.length) {
    main.innerHTML = `<div class="page">${pageHeader({ title: "Dashboard", sub: "IBM Storage Virtualize storages on Proxmox VE" })}
      ${emptyState("data--base", "No storage registered", "Register the first array to get started.",
      ME.admin ? btn("Add storage", "primary", 'data-go="#/add"', "add") : "")}</div>`;
    return;
  }
  const perf = {}; await Promise.all(sts.filter(s => s.array_ok).map(async s => { perf[s.config.storage] = await api("GET", `/perf/${encodeURIComponent(s.config.storage)}?hours=1`).catch(() => ({})); }));
  const errs = issues.filter(i => i.severity === "error").length, warns = issues.length - errs;
  let cap = 0, free = 0, virt = 0; sts.forEach(s => { if (s.pool) { cap += +s.pool.capacity || 0; free += +s.pool.free_capacity || 0; virt += +s.pool.virtual_capacity || 0; } });
  const used = cap - free;
  const sum = k => sts.reduce((a, s) => { const h = s.health?.[k]; return h ? [a[0] + h[0], a[1] + h[1]] : a; }, [0, 0]);
  const hw = sum("canisters"), hosts = sum("hosts"), vols = sum("volumes"), fcs = sum("fcmaps");
  const alerts = sts.reduce((a, s) => a + (s.health?.alerts || 0), 0);
  const allOk = sts.every(s => s.array_ok);
  const firstOk = sts.find(s => s.array_ok);
  const narr = Math.max(1, Object.keys(perf).length);
  const series = (k, avg) => { const out = {}; Object.values(perf).forEach(p => (p[k] || []).forEach(([t, v]) => out[t] = (out[t] || 0) + v)); return Object.entries(out).map(([t, v]) => [+t, avg ? v / narr : v]).sort((a, b) => a[0] - b[0]); };
  const lastOf = pts => pts.length ? pts[pts.length - 1][1] : null;
  const nowSum = (k, avg) => { let a = 0, n = 0; sts.forEach(s => { if (s.stats && s.stats[k] != null && s.stats[k] !== "") { a += +s.stats[k]; n++; } }); return n ? (avg ? a / n : a) : null; };
  const spark = (label, k, unit, avg) => { const pts = series(k, avg), v = pts.length ? lastOf(pts) : nowSum(k, avg);
    return `<div class="kpi"><div class="k">${esc(label)}</div><div class="v">${v == null ? "–" : fmtVal(scaleUnit(v, unit)[0], "")}<small>${esc(v == null ? unit : scaleUnit(v, unit)[1])}</small></div>${sparkline(pts)}<div class="s">${pts.length > 1 ? "last hour" : "collecting history…"}</div></div>`; };
  const overall = !allOk || errs ? "err" : alerts || warns ? "warn" : "ok";

  main.innerHTML = `<div class="page">
    ${pageHeader({ title: "Dashboard", sub: `${sts.length} IBM Storage Virtualize storage(s) · ${inv.vms.length} VM(s)/CT(s) with disks on the array`,
      actions: ME.admin ? btn("Add storage", "primary", 'data-go="#/add"', "add") : "",
      meta: firstOk ? `<span>${icon("time", 16)} Data age: <b>${ago(firstOk.fetched || Date.now() / 1000)}</b></span>` : "" })}
    ${overall === "err" ? notification("err", errs ? `${errs} problem(s) need attention` : "Some storages cannot be reached by the panel", link("#/reconcile", "View reconciliation")) : ""}
    <div class="g g-3">
      <section class="tile span-2 fill"><h3 class="tile-title">System health ${status(overall, overall === "ok" ? "All good" : overall === "err" ? "Attention" : "Warnings")}</h3>
        <div class="health">
          <div><span class="hl">Hardware</span>${status(healthKind(hw), `${hw[0]}/${hw[1]} canisters`)}</div>
          <div><span class="hl">Connectivity</span>${status(healthKind(hosts), `${hosts[0]}/${hosts[1]} hosts online`)}</div>
          <div><span class="hl">Proxmox volumes</span>${status(healthKind(vols), `${vols[0]}/${vols[1]} online`)}</div>
          <div><span class="hl">FlashCopy</span>${status(fcs[1] ? healthKind(fcs) : "ok", fcs[1] ? `${fcs[0]}/${fcs[1]} normal` : "none active")}</div>
          <div><span class="hl">Array alerts</span>${status(alerts ? "warn" : "ok", alerts ? `${alerts} pending` : "none")}</div>
          <div><span class="hl">Reconciliation</span>${status(errs ? "err" : warns ? "warn" : "ok", errs || warns ? `${errs} error(s) · ${warns} warning(s)` : "consistent")}</div>
        </div></section>
      <section class="tile"><h3 class="tile-title">Usable capacity ${link("#/storages", "Details")}</h3>
        <div class="cap">${donut([{ v: used, color: "var(--cds-interactive)" }, { v: free, color: "transparent" }], `${pct(used, cap)}%`, "in use")}
        <div class="legend-list"><div><i style="background:var(--cds-interactive)"></i><span>Used</span><span>${bytes(used)}</span></div>
          <div><i style="background:var(--cds-layer-accent-01)"></i><span>Free</span><span>${bytes(free)}</span></div>
          <div><i style="outline:1px dashed var(--cds-border-strong-01);outline-offset:-1px"></i><span>Provisioned</span><span>${bytes(virt)}</span></div>
          ${virt > used ? `<div class="helper">Thin: ${bytes(virt - used)} promised beyond what is used</div>` : ""}</div></div></section>
    </div>
    <h2 class="sec">Performance <span class="helper" style="font-size:.75rem">${link(`#/performance/${encodeURIComponent(firstOk?.config.storage || "")}`, "View history")}</span></h2>
    <div class="kpi-row">${spark("Latency", "vdisk_ms", "ms", true)}${spark("Throughput", "vdisk_mb", "MB/s")}${spark("IOPS", "vdisk_io", "")}${spark("Array CPU", "cpu_pc", "%", true)}</div>
    ${topDisks(inv)}
    <h2 class="sec">Storages</h2>
    <div class="g g-auto">${sts.map(storageCard).join("")}</div>
  </div>`;
}
function topDisks(inv) {
  const act = d => d.perf_now ? d.perf_now.r_iops + d.perf_now.w_iops : 0;
  const rows = inv.disks.filter(d => d.perf_now && !d.key.startsWith("unused")).sort((a, b) => act(b) - act(a)).slice(0, 5);
  if (!rows.length) return "";
  const max = Math.max(...rows.map(act), 1);
  return `<h2 class="sec">Busiest disks right now <span class="helper" style="font-size:.75rem">${link("#/vms", "View all")}</span></h2>
    <div class="tile flush"><table class="mini"><thead><tr><th>VM</th><th>Disk</th><th style="width:30%">IOPS</th><th class="num">Throughput</th><th>Latency</th><th></th></tr></thead><tbody>
    ${rows.map(d => { const p = d.perf_now, lat = Math.max(p.r_ms || 0, p.w_ms || 0);
      return `<tr><td><b>${d.vmid}</b> ${esc(d.name || "")}</td><td class="mono">${esc(d.key)}</td>
        <td><div class="hbar"><span style="width:${Math.max(2, 100 * act(d) / max)}%"></span><b>${num(act(d), 0)}</b></div></td>
        <td class="num">${num(p.r_mbs + p.w_mbs, 1)} MB/s</td><td>${status(lat > 20 ? "err" : lat > 5 ? "warn" : "ok", `${num(lat, 1)} ms`)}</td>
        <td class="act">${btn("", "ghost", `data-go="#/diskperf/${d.vmid}/${esc(d.key)}" title="View charts" aria-label="Charts for ${esc(d.key)}"`, "chart--line", "sm")}</td></tr>`; }).join("")}
    </tbody></table></div>`;
}
function storageCard(s) {
  const c = s.config, p = s.pool || {}, active = s.status.some(x => x.active);
  const st = !s.array_ok ? (s.panel_credential ? status("err", "No access to the array") : status("warn", "No panel credential")) : active ? status("ok", "Active") : status("err", "Inactive in Proxmox");
  return `<a class="tile clickable" href="#/storage/${encodeURIComponent(c.storage)}">
    <h3 class="tile-title"><span style="display:flex;gap:.5rem;align-items:center">${icon("data--base", 20)} ${esc(c.storage)}</span>${st}</h3>
    <p class="muted" style="margin:-.5rem 0 1rem">${s.system ? `${esc(s.system.name)} · ${esc(s.system.product_name)} · ${esc((s.system.code_level || "").split(" ")[0])}` : `<span class="mono">${esc(c["sw-address"])}</span>`}</p>
    ${s.pool ? meter(+p.capacity - +p.free_capacity, +p.capacity, { label: `${esc(p.name)} · `, prov: +p.virtual_capacity }) : ""}
    ${sl([["Volumes", `${s.volumes ?? "–"}`], ["Snapshots", `${s.snapshots ?? "–"}`],
      ["Hardware", s.health ? status(healthKind(s.health.canisters), `${s.health.canisters[0]}/${s.health.canisters[1]} canisters`) : "–"],
      ["Alerts", s.health ? (s.health.alerts ? status("warn", String(s.health.alerts)) : status("ok", "none")) : "–"]])}
  </a>`;
}

/* ======================================================================
   Storages
   ====================================================================== */
async function pageStorages() {
  const sts = await api("GET", "/storages");
  main.innerHTML = `<div class="page">${pageHeader({ title: "Storages", sub: "IBM Storage Virtualize arrays registered in Proxmox VE. Each entry has its own credential, pool and prefix." })}<div id="t"></div></div>`;
  const rows = sts.map(s => ({ ...s, __key: s.config.storage }));
  const t = mount($("#t"), dataTable({
    columns: [
      { t: "Storage", text: r => r.config.storage, render: r => `<a class="cds--link" href="#/storage/${encodeURIComponent(r.config.storage)}">${esc(r.config.storage)}</a>` },
      { t: "State", text: r => r.array_ok ? "active" : "no access", render: r => !r.array_ok ? (r.panel_credential ? status("err", "No access") : status("warn", "No credential")) : r.status.some(x => x.active) ? status("ok", "Active") : status("err", "Inactive") },
      { t: "System", text: r => r.system?.name || "", render: r => r.system ? `${esc(r.system.name)}<div class="helper">${esc(r.system.product_name)}</div>` : "–" },
      { t: "Firmware", text: r => (r.system?.code_level || "").split(" ")[0] },
      { t: "Address", text: r => r.config["sw-address"], render: r => `<span class="mono">${esc(r.config["sw-address"])}</span>` },
      { t: "Pool", text: r => r.config["sw-pool"] },
      { t: "Usage", num: 1, sortVal: r => r.pool ? (r.pool.capacity - r.pool.free_capacity) / r.pool.capacity : -1, render: r => r.pool ? `${pct(r.pool.capacity - r.pool.free_capacity, r.pool.capacity)}%<div class="helper">${bytes(r.pool.capacity - r.pool.free_capacity)} / ${bytes(r.pool.capacity)}</div>` : "–" },
      { t: "Prefix", text: r => r.config["sw-prefix"] || "pve", render: r => tag(r.config["sw-prefix"] || "pve", "neutral") },
      { t: "Volumes", num: 1, sortVal: r => r.volumes ?? -1, render: r => esc(r.volumes ?? "–") },
      { t: "", act: 1, render: r => ovfButton(`data-st="${esc(r.config.storage)}"`) },
    ], rows, empty: "No storage registered.",
    actions: ME.admin ? btn("Add storage", "primary", 'data-go="#/add"', "add") : "",
  }));
  const bind = () => $$("[data-st]").forEach(b => b.onclick = () => {
    const id = b.dataset.st, s = sts.find(x => x.config.storage === id);
    openMenu(b, [
      { label: "Open", icon: "launch", run: () => location.hash = `#/storage/${encodeURIComponent(id)}` },
      { label: "Performance", icon: "chart--line", run: () => location.hash = `#/performance/${encodeURIComponent(id)}` },
      ...(ME.admin ? ["-", { label: s.panel_credential ? "Renew panel credential" : "Set up panel credential", icon: "password", run: () => credentialFlow(id) },
        { label: "Remove from Proxmox", icon: "trash-can", danger: true, run: () => removeStorage(id) }] : []),
    ]);
  });
  bind(); t.addEventListener("dt:draw", bind);
}
async function removeStorage(id) {
  if (!await confirmDialog({ label: "Storage", title: `Remove ${id} from Proxmox?`, danger: true, ok: "Remove",
    body: `<p>The storage definition is removed from Proxmox and the panel credential is deleted.</p><p>The volumes on the array are <b>not</b> deleted. The removal is refused if any VM or CT still uses this storage.</p>` })) return;
  try { await api("DELETE", `/storages/${encodeURIComponent(id)}`); toast("ok", "Storage removed", id); location.hash = "#/storages"; }
  catch (e) { toast("err", "Could not remove", e.message); }
}

function clockCell(ck) {
  if (!ck) return "–";
  const off = ck.offset_s, ok = ck.ntp && off != null && Math.abs(off) <= 60;
  const diff = off == null ? "" : Math.abs(off) < 1 ? "same as the panel" : `${off > 0 ? "+" : ""}${num(off, 0)} s relative to the panel`;
  return `${status(ok ? "ok" : "warn", ck.ntp ? `NTP ${ck.ntp}` : "no NTP")}<div class="helper">${esc(diff)}</div>`;
}
const CAP_LABEL = { flashcopy: "FlashCopy", child_pool: "Child pool", host_cluster: "Host cluster", rest_api: "REST API", volume_group: "Volume group", vg_snapshot: "Volume group snapshot", pbr: "PBR replication", partition: "Storage partition", safeguarded: "Safeguarded snapshot", recovery_test: "Recovery test" };
const AUTO_IMPORT = new Set();
async function pageStorage(id, tab = "resumo") {
  let d = await api("GET", `/storages/${encodeURIComponent(id)}`);
  // no panel credential yet: take the plugin key from Proxmox first, once per visit
  if (!d.panel_credential && ME.admin && !AUTO_IMPORT.has(id)) {
    AUTO_IMPORT.add(id);
    try { await api("POST", `/storages/${encodeURIComponent(id)}/credential/import`); toast("ok", "Credential imported", "The panel now uses the plugin key"); d = await api("GET", `/storages/${encodeURIComponent(id)}`); } catch (e) { }
  }
  const c = d.config, a = d.array, pfx = (c["sw-prefix"] || "pve") + "-";
  const active = d.status.some(x => x.active);
  const isTrash = v => v.name.startsWith(pfx + "trash");
  const managed = a.ok ? a.vdisks.filter(v => v.name.startsWith(pfx) && !isTrash(v)) : [];
  const head = pageHeader({
    crumbs: [["Storages", "#/storages"], [id]],
    title: `${icon("data--base", 24)} ${esc(id)} ${a.ok ? (active ? status("ok", "Active") : status("err", "Inactive")) : status("warn", "No access from the panel")}`,
    sub: a.ok ? `${esc(a.system.name)} · ${esc(a.system.product_name)} · firmware ${esc((a.system.code_level || "").split(" ")[0])}` : `<span class="mono">${esc(c["sw-address"])}</span>`,
    meta: `<span>Address <b class="mono">${esc(c["sw-address"])}</b></span><span>Pool <b>${esc(c["sw-pool"])}</b></span><span>Type <b data-noi18n>${c.type === "flashsystem" ? "IBM FlashSystem" : "IBM Storwize"}</b></span><span>Prefix <b>${esc(c["sw-prefix"] || "pve")}</b></span><span>Backend <b>${esc(c["sw-api"] || "auto")}</b></span>${a.ok ? `<span>Data age: <b>${ago(a.fetched)}</b></span>` : ""}`,
    actions: (a.ok ? btn("Performance", "tertiary", `data-go="#/performance/${encodeURIComponent(id)}"`, "chart--line") : "") + (ME.admin ? ovfButton('id="st-ovf"', "More actions") : ""),
  });
  if (!a.ok) {
    main.innerHTML = `<div class="page">${head}${notification("warn", "The panel cannot read this array", esc(a.error || ""))}
      ${d.panel_credential ? "" : emptyState("password", "Set up the panel credential", "The Proxmox plugin can reach the array, but the panel does not have the key for this storage yet. Generate a new key: the panel updates the plugin automatically.", ME.admin ? btn("Use the plugin key", "primary", 'id="cred-imp"', "download") + btn("Generate new key", "tertiary", 'id="cred"', "password") : "")}</div>`;
    $("#cred")?.addEventListener("click", () => credentialFlow(id));
    $("#cred-imp")?.addEventListener("click", async e => {
      try { await busy(e.currentTarget, () => api("POST", `/storages/${encodeURIComponent(id)}/credential/import`)); toast("ok", "Credential imported", "The panel now uses the plugin key"); main.dataset.route = ""; route(); }
      catch (err) { toast("err", "Could not import", err.message); }
    }); bindStorageOvf(id, d); return;
  }
  const T = [["resumo", "Summary"], ["volumes", "Volumes", a.vdisks.length], ["flashcopy", "FlashCopy", a.fcmaps.length], ["hosts", "Hosts", a.hosts.length], ["fc", "Fibre Channel"], ["eventos", "Alerts", a.events.length], ["lixeira", "Trash", a.vdisks.filter(isTrash).length], ["config", "Configuration"]];
  if (a.capabilities.vg_snapshot) T.splice(3, 0, ["protecao", "Protection"]);
  main.innerHTML = `<div class="page">${head}${tabs(T, tab)}<div id="tab"></div></div>`;
  $$("[data-tab]").forEach(b => b.onclick = () => location.hash = `#/storage/${encodeURIComponent(id)}/${b.dataset.tab}`);
  bindStorageOvf(id, d);
  const el = $("#tab");
  if (tab === "resumo") {
    const pool = a.pools.find(p => p.name === c["sw-pool"]) || {}, s = a.system, st = a.stats;
    const parent = a.pools.find(p => p.name === pool.parent_mdisk_grp_name && p.name !== pool.name);
    el.innerHTML = `<div class="g g-3">
      <section class="tile"><h3 class="tile-title">System</h3>${sl([["Name", esc(s.name)], ["Product", esc(s.product_name)], ["Firmware", esc(s.code_level)], ["Management IP", `<span class="mono">${esc(s.console_IP || c["sw-address"])}</span>`],
        ["Clock", clockCell(a.clock)], ["Time zone", esc((s.time_zone || "").replace(/^\d+\s+/, ""))],
        ["Statistics", `${esc(s.statistics_status)} · every ${esc(s.statistics_frequency)} min`]])}</section>
      <section class="tile"><h3 class="tile-title">Pool ${esc(pool.name || c["sw-pool"])} ${tag(pool.type === "child_thick" ? "child pool" : pool.type || "", "info")}</h3>
        ${meter(+pool.capacity - +pool.free_capacity, +pool.capacity, { label: "Used ", prov: +pool.virtual_capacity })}
        ${sl([["Free", bytes(pool.free_capacity)], ["Provisioned", bytes(pool.virtual_capacity)], ["Real allocated", bytes(pool.real_capacity)], ["Overallocation", `${esc(pool.overallocation)}%`],
          parent ? ["Parent pool", `${esc(parent.name)} · ${bytes(parent.free_capacity)} free`] : null, ["Easy Tier", esc(pool.easy_tier || "–")]])}</section>
      <section class="tile"><h3 class="tile-title">Hardware</h3>${sl(a.nodes.map(n => [n.name, `${status(n.status === "online" ? "ok" : "err", n.status)}${n.config_node === "yes" ? ` <span class="helper">· configuration node</span>` : ""}`])
        .concat(a.enclosures.map(e => [`Enclosure ${e.id}`, `${status(e.status === "online" ? "ok" : "err", e.status)} <span class="helper">${esc(e.product_MTM || e.type || "")}</span>`])))}</section>
      <section class="tile"><h3 class="tile-title">Now</h3>${sl([["Latency", fmtVal(st.vdisk_ms, "ms")], ["Throughput", fmtVal(st.vdisk_mb, "MB/s")], ["IOPS", fmtVal(st.vdisk_io, "")],
        ["CPU", `${esc(st.cpu_pc)}%`], ["Fibre Channel", fmtVal(st.fc_mb, "MB/s")], ["Write cache", `${esc(st.write_cache_pc)}%`]])}</section>
      <section class="tile"><h3 class="tile-title">Proxmox</h3>${sl([["Managed volumes", String(managed.filter(v => !v.name.includes("_s_")).length)], ["Snapshots (FlashCopy)", String(managed.filter(v => v.name.includes("_s_")).length)],
        ["Mapping", esc(c["sw-hostcluster"] ? "host cluster " + c["sw-hostcluster"] : "hosts " + (c["sw-hosts"] || ""))], ["Content", esc(c.content)],
        ["Snapshots", (c["sw-snapmode"] === "volumegroup" ? "volume group" : "FlashCopy") + ((c["sw-consistency"] ?? 1) != 0 ? ` · ${tag("consistent across disks", "info")}` : " · per disk")], ...d.status.map(x => [`Node ${x.node}`, status(x.active ? "ok" : "err", x.active ? "active" : "inactive")])])}</section>
      <section class="tile"><h3 class="tile-title">Pool QoS ${ME.admin && a.throttle_supported ? btn("Edit", "ghost", 'id="pool-qos"', "edit", "sm") : ""}</h3>
        ${!a.throttle_supported ? `<p class="muted">This firmware has no QoS (mkthrottle needs 7.7+).</p>` : sl([["Maximum IOPS", a.pool_limit?.iops ? num(a.pool_limit.iops) : "no limit"], ["Maximum throughput", a.pool_limit?.mbps ? `${num(a.pool_limit.mbps)} MB/s` : "no limit"],
          ["Volumes with a limit", String(a.throttles.filter(t => t.throttle_type === "vdisk").length)]])}
        <p class="helper" style="margin-top:1rem">Per-disk limits are in ${link("#/vms", "VMs and volumes")}.</p></section>
      <section class="tile"><h3 class="tile-title">Firmware features</h3><div class="tags">${Object.entries(a.capabilities).map(([k, v]) => tag(CAP_LABEL[k] || k, v ? "info" : "neutral", v ? "supported" : "not supported on this firmware")).join("")}</div>
        <p class="helper" style="margin-top:1rem">Greyed-out features need newer firmware (for example, volume group from 8.3 and PBR from 8.5.2).</p></section>
    </div>`;
    $("#pool-qos")?.addEventListener("click", () => poolLimitsFlow(id, c["sw-pool"], a.pool_limit));
  } else if (tab === "volumes") {
    const thrOf = n => { const t = a.throttles.find(x => x.throttle_type === "vdisk" && x.object_name === n); return t ? { iops: +t.IOPs_limit || 0, mbps: +t.bandwidth_limit_MB || 0 } : null; };
    mount(el, dataTable({
      columns: [
        { t: "Name", key: "name", render: v => `<span class="mono">${esc(v.name)}</span>` },
        { t: "Type", text: v => isTrash(v) ? "trash" : v.name.startsWith(pfx) ? (v.name.includes("_s_") ? "snapshot" : "proxmox") : "external", render: v => isTrash(v) ? tag("Trash", "warm-gray") : v.name.startsWith(pfx) ? (v.name.includes("_s_") ? tag("Snapshot", "purple") : tag("Proxmox", "info")) : tag("External", "neutral", "not managed by Proxmox") },
        { t: "State", key: "status", render: v => status(v.status === "online" ? "ok" : "err", v.status) },
        { t: "Pool", key: "mdisk_grp_name" },
        { t: "Capacity", num: 1, sortVal: v => +v.capacity, render: v => bytes(v.capacity) },
        { t: "UID", key: "vdisk_UID", render: v => `<span class="mono">${esc(v.vdisk_UID)}</span>` },
        { t: "FlashCopy", num: 1, sortVal: v => +v.fc_map_count, key: "fc_map_count" },
        { t: "QoS", text: v => limitText(thrOf(v.name)), render: v => limitText(thrOf(v.name)) ? esc(limitText(thrOf(v.name))) : `<span class="muted">–</span>` },
        { t: "Replication", key: "RC_name", render: v => v.RC_name ? tag(v.RC_name, "teal") : `<span class="muted">–</span>` },
      ], rows: a.vdisks, empty: "No volumes on this array.",
    }));
  } else if (tab === "flashcopy") {
    mount(el, dataTable({
      columns: [
        { t: "ID", key: "id", num: 1, sortVal: m => +m.id },
        { t: "Source", key: "source_vdisk_name", render: m => `<span class="mono">${esc(m.source_vdisk_name)}</span>` },
        { t: "Target", key: "target_vdisk_name", render: m => `<span class="mono">${esc(m.target_vdisk_name)}</span>${m.target_vdisk_name.includes("_s_") ? `<div class="helper">snapshot “${esc(m.target_vdisk_name.split("_s_")[1])}”</div>` : ""}` },
        { t: "Consistency group", key: "group_name", render: m => m.group_name ? tag(m.group_name, "info") : `<span class="muted">–</span>` },
        { t: "State", key: "status", render: m => m.status === "copying" ? status("info", m.copy_rate === "0" ? "Active (snapshot)" : "Copying") : m.status === "idle_or_copied" ? status("ok", "Copied") : status("warn", m.status) },
        { t: "Progress", num: 1, sortVal: m => +m.progress, render: m => `${esc(m.progress)}%` },
        { t: "Copy rate", num: 1, key: "copy_rate" },
        { t: "Start", key: "start_time", render: m => esc(fcTime(m.start_time)) },
      ], rows: a.fcmaps, empty: "No FlashCopy on this array.",
    }));
  } else if (tab === "hosts") {
    await hostsTab(el, id, c);
  } else if (tab === "fc") {
    await fcTab(el, id);
  } else if (tab === "protecao") {
    await protectionTab(el, id);
  } else if (tab === "eventos") {
    if (!a.events.length) el.innerHTML = emptyState("checkmark--outline", "No pending alerts", "The array event log has no open alerts. Informational messages are not shown here.");
    else mount(el, dataTable({
      columns: [
        { t: "Date", key: "last_timestamp", render: e => esc(fcTime(e.last_timestamp)) },
        { t: "Object", text: e => `${e.object_type} ${e.object_name || e.object_id}`, render: e => `${esc(e.object_type)} <span class="mono">${esc(e.object_name || e.object_id)}</span>` },
        { t: "Code", text: e => e.error_code || e.event_id, render: e => `<span class="mono">${esc(e.error_code || e.event_id)}</span>` },
        { t: "Description", key: "description" },
        { t: "Severity", render: e => e.error_code ? status("err", "Error") : status("warn", "Warning") },
      ], rows: a.events, title: "Open alerts", desc: "Handle the alert and mark it as fixed in the array GUI (Monitoring › Events).",
    }));
  } else if (tab === "lixeira") {
    await trashTab(el, id);
  } else if (tab === "config") {
    const keys = Object.keys(c).filter(k => k !== "digest").sort();
    el.innerHTML = `<div class="g g-2"><section class="tile"><h3 class="tile-title">Definition in Proxmox <span class="helper">storage.cfg</span></h3>${sl(keys.map(k => [k, `<span class="mono">${esc(c[k])}</span>`]))}</section>
      <section class="tile"><h3 class="tile-title">State per node</h3>${sl(d.status.map(x => [x.node, `${status(x.active ? "ok" : "err", x.active ? "active" : "inactive")} <span class="helper">${x.total ? `${bytes(x.avail)} free of ${bytes(x.total)}` : esc(x.error || "")}</span>`]))}
        <h3 class="tile-title" style="margin-top:2rem">Panel credential</h3><p class="muted">${d.panel_credential ? "The panel uses the same key as the Proxmox plugin." : "Not configured."}</p>
        ${ME.admin ? `<div style="margin-top:1rem">${btn(d.panel_credential ? "Renew credential" : "Set up credential", "tertiary", 'id="cred"', "password")}</div>` : ""}</section></div>`;
    $("#cred")?.addEventListener("click", () => credentialFlow(id));
  }
}
/* ---- Trash: disks removed in Proxmox, kept unmapped on the array ---------- */
async function trashTab(el, id) {
  const sid = encodeURIComponent(id);
  const r = await api("GET", `/storages/${sid}/trash`);
  const keep = r.days > 0
    ? `Disks removed in Proxmox stay here for <b>${r.days} day(s)</b>, unmapped from the hosts, and are then deleted from the array. They still use space in the pool.`
    : `The trash is off: disks removed in Proxmox are deleted from the array at once.`;
  const head = `<div class="page-actions" style="display:flex;gap:1rem;align-items:center;justify-content:space-between;flex-wrap:wrap;margin-bottom:1rem"><p class="muted" style="max-width:48rem;margin:0">${keep}</p>${ME.admin ? btn("Retention", "tertiary", 'id="tr-days"', "time") : ""}</div>`;
  el.innerHTML = head + `<div id="tr-t"></div>`;
  mount($("#tr-t", el), dataTable({
    columns: [
      { t: "Disk", key: "volname", render: x => `<span class="mono">${esc(x.volname)}</span><div class="helper mono">${esc(x.name)}</div>` },
      { t: "VM/CT", num: 1, key: "vmid", render: x => `${x.vmid}${x.guest ? ` <span class="helper">${esc(x.guest)}</span>` : ` <span class="helper">does not exist</span>`}` },
      { t: "Size", num: 1, sortVal: x => x.size, render: x => bytes(x.size) },
      { t: "Removed", sortVal: x => x.deleted, render: x => esc(dt(x.deleted)) },
      { t: "Deleted on", sortVal: x => x.expires, render: x => x.expires * 1000 <= Date.now() ? status("warn", "next check") : esc(dt(x.expires)) },
      ...(ME.admin ? [{ t: "", render: x => `<div style="display:flex;gap:.5rem;justify-content:flex-end">${btn("Restore", "tertiary", `data-trr="${esc(x.name)}" data-vm="${x.vmid}"`, "undo", "sm")}${btn("Delete now", "danger--ghost", `data-trd="${esc(x.name)}"`, "trash-can", "sm")}</div>` }] : []),
    ], rows: r.items, empty: "The trash is empty.",
  }));
  const reload = () => { main.dataset.route = ""; route(); };
  el.onclick = async e => {
    const rs = e.target.closest("[data-trr]"), dl = e.target.closest("[data-trd]");
    if (e.target.closest("#tr-days")) {
      await modal({ label: id, title: "Trash retention", size: "sm",
        body: `${textInput("tr-n", "Days to keep removed disks", `type="number" min="0" max="3650" value="${r.days}"`, "0 turns the trash off: removed disks are deleted at once. A shorter value also applies to the disks already in the trash, at the next hourly check.")}`,
        buttons: [{ id: "no", label: "Cancel" }, { id: "go", label: "Save", kind: "primary" }],
        onButton: async (b, root) => {
          if (b !== "go") return false;
          try { await busy($("[data-b=go]", root), () => api("POST", `/storages/${sid}/trash/days`, { days: +$("#tr-n").value })); toast("ok", "Retention saved", id); reload(); return false; }
          catch (err) { toast("err", "Could not save", err.message); return true; }
        } });
    } else if (rs) {
      const name = rs.dataset.trr;
      await modal({ label: id, title: "Restore disk", size: "sm",
        body: `<p>The disk is mapped to the hosts again and shows up in the VM/CT as an <b>unused disk</b>: attach it in Proxmox under Hardware › Unused Disk › Edit.</p>
          ${textInput("tr-vm", "VM/CT", `type="number" min="100" value="${esc(rs.dataset.vm)}"`, "The original VMID by default. With another VMID the disk takes the next free disk name of that VM/CT.")}`,
        buttons: [{ id: "no", label: "Cancel" }, { id: "go", label: "Restore", kind: "primary" }],
        onButton: async (b, root) => {
          if (b !== "go") return false;
          try { const o = await busy($("[data-b=go]", root), () => api("POST", `/storages/${sid}/trash/restore`, { name, vmid: $("#tr-vm").value }));
            toast("ok", "Disk restored", `${o.volid || ""} · ${o.rescan || ""}`); reload(); return false; }
          catch (err) { toast("err", "Could not restore", err.message); return true; }
        } });
    } else if (dl) {
      const name = dl.dataset.trd;
      if (!await confirmDialog({ label: id, title: "Delete this disk now?", danger: true, ok: "Delete",
        body: `<p>The volume <span class="mono">${esc(name)}</span> is deleted from the array. This cannot be undone.</p>` })) return;
      try { await api("POST", `/storages/${sid}/trash/delete`, { name }); toast("ok", "Disk deleted", name); reload(); }
      catch (err) { toast("err", "Could not delete", err.message); }
    }
  };
}

/* ---- Fibre Channel: light (SFP DDM) and errors on both sides -------------- */
function optKind(v, sfp, dir) {
  if (v == null || !sfp || !sfp.ddm) return "neutral";
  const lo_a = sfp[`${dir}_alarm_low`], lo_w = sfp[`${dir}_warn_low`], hi_w = sfp[`${dir}_warn_high`], hi_a = sfp[`${dir}_alarm_high`];
  if (lo_a == null) return "neutral";        // module limits not reported (Emulex)
  if (v <= lo_a || (hi_a > -40 && v >= hi_a)) return "err";
  if (v <= lo_w || (hi_w > -40 && v >= hi_w)) return "warn";
  return "ok";
}
function optCell(sfp, dir) {
  if (!sfp) return `<span class="muted">no data</span>`;
  if (!sfp.ddm) return `<span class="muted">SFP without DDM</span>`;
  const v = sfp[`${dir}_dbm`], k = optKind(v, sfp, dir);
  const tip = sfp[dir + "_alarm_low"] == null ? "the HBA driver does not report the module limits" : `warning below ${num(sfp[dir + "_warn_low"], 1)} dBm · alarm below ${num(sfp[dir + "_alarm_low"], 1)} dBm`;
  return `<span title="${esc(tip)}">${status(k, `${num(v, 2)} dBm`)}</span>`;
}
function errCell(deltas, labels) {
  if (!deltas || !Object.keys(deltas).length) return `<span class="muted" title="the panel starts measuring from now">measuring…</span>`;
  const bad = Object.entries(deltas).filter(([, x]) => x.delta > 0);
  const since = Math.min(...Object.values(deltas).map(x => x.since));
  if (!bad.length) return `<span title="since ${esc(dt(since))}">${status("ok", "no errors")}</span>`;
  const crit = bad.some(([k]) => /crc|error_frames|icrc/.test(k));
  return `<span title="since ${esc(dt(since))}">${status(crit ? "err" : "warn", bad.map(([k, x]) => `${x.delta} ${labels[k] || k}`).join(", "))}</span>`;
}
const HOST_CNT_LABEL = { link_failure_count: "link failures", loss_of_sync_count: "sync losses", loss_of_signal_count: "signal losses",
  invalid_crc_count: "CRC errors", invalid_tx_word_count: "invalid words", prim_seq_protocol_err_count: "protocol errors", error_frames: "error frames" };

async function fcTab(el, id) {
  el.innerHTML = `<div class="tab-loading"><span class="busy-ico"></span><span>Loading…</span></div>`;
  const F = await api("GET", `/storages/${encodeURIComponent(id)}/fc`);
  const nodePort = {}; F.nodes.forEach(n => n.ports.forEach(p => nodePort[p.wwpn] = { node: n.node, ...p }));
  const active = F.array.ports.filter(p => p.status === "active"), idle = F.array.ports.filter(p => p.status !== "active");
  const links = active.map(p => {
    const peer = p.peer && p.peer.direct ? nodePort[p.peer.wwpn] : null;
    return `<tr><td><b>${esc(p.node_name)}</b> · port ${esc(p.port_id)}<div class="helper mono">${esc(p.WWPN)}</div></td>
      <td>${status("ok", p.port_speed)}</td>
      <td>${peer ? `${icon("server--proxy", 16)} <b>${esc(peer.node)}</b> · ${esc(peer.host)} ${tag("direct link", "neutral")}<div class="helper mono">${esc(peer.wwpn)}</div>` : p.peer ? `switch <span class="mono">${esc(p.peer.switch)}</span>` : "–"}</td>
      <td>${peer ? optCell(peer.sfp, "rx") : `<span class="muted">–</span>`}</td>
      <td>${peer ? optCell(peer.sfp, "tx") : `<span class="muted">–</span>`}</td>
      <td>${errCell(p.deltas, F.counter_labels)}</td>
      <td>${peer ? errCell(peer.deltas, HOST_CNT_LABEL) : `<span class="muted">–</span>`}</td></tr>`;
  }).join("");
  const nodesHtml = F.nodes.map(n => n.error ? `<tr><td colspan="9">${esc(n.node)}: ${esc(n.error)}</td></tr>` : n.ports.map(p => `<tr>
      <td><b>${esc(n.node)}</b> · ${esc(p.host)}<div class="helper">${esc(p.model || "")}</div></td><td class="mono">${esc(p.wwpn)}</td>
      <td>${status(p.state === "Online" ? "ok" : "err", p.state === "Online" ? p.speed : p.state)}</td>
      <td>${p.sfp && p.sfp.ddm ? `${esc(p.sfp.vendor)} ${esc(p.sfp.pn)}<div class="helper">${esc(p.sfp.wavelength_nm)} nm · SN ${esc(p.sfp.sn)}</div>` : "–"}</td>
      <td>${optCell(p.sfp, "tx")}</td><td>${optCell(p.sfp, "rx")}</td>
      <td class="num">${p.sfp && p.sfp.ddm ? `${num(p.sfp.temp_c, 1)} °C` : "–"}</td><td class="num">${p.sfp && p.sfp.ddm ? `${num(p.sfp.bias_ma, 2)} mA` : "–"}</td>
      <td>${errCell(p.deltas, HOST_CNT_LABEL)}</td></tr>`).join("")).join("");
  el.innerHTML = `${F.array.note ? notification("warn", "Array counters", esc(F.array.note)) : ""}
    <section class="tile flush" style="margin-bottom:1.5rem"><div class="dt-head"><h3>Links ${esc(id)} ↔ Proxmox</h3>
      <p>Light measured at the SFP of the Proxmox HBA. On a direct link, the node Rx is the light leaving the array and the Tx is the light reaching it. Errors = how much the counters rose in the last 24 h, not the total since boot.</p></div>
      <div class="dt-scroll"><table class="mini"><thead><tr><th>Array port</th><th>State</th><th>Connected to</th><th>Rx at node (array light)</th><th>Node Tx</th><th>Array errors (24 h)</th><th>Node errors (24 h)</th></tr></thead>
      <tbody>${links || `<tr><td colspan="7" class="muted">No active FC port.</td></tr>`}</tbody></table></div>
      ${idle.length ? `<div class="dt-foot">${idle.length} unused port(s) on the array: ${idle.map(p => `${esc(p.node_name)} p${esc(p.port_id)} (${esc(p.status.replace(/_/g, " "))})`).join(", ")}</div>` : ""}</section>
    <section class="tile flush"><div class="dt-head"><h3>FC ports of the Proxmox nodes</h3><p>SFP diagnostics (DDM, SFF-8472): transmit and receive power, temperature and laser current, with the module maker's own limits.</p></div>
      <div class="dt-scroll"><table class="mini"><thead><tr><th>Node / HBA</th><th>WWPN</th><th>State</th><th>SFP</th><th>Tx</th><th>Rx</th><th class="num">Temp.</th><th class="num">Current</th><th>Errors (24 h)</th></tr></thead>
      <tbody>${nodesHtml || `<tr><td colspan="9" class="muted">No managed node — authorize one in ${link("#/nodes", "Proxmox nodes")}.</td></tr>`}</tbody></table></div>
      <div class="dt-foot">Updated ${ago(F.array.fetched)} ago · reference for 8G short-wave SFP: healthy Rx between about −1 and −8 dBm.</div></section>`;
}

/* ---- Hosts tab: array hosts and who receives the volumes of this storage ---- */
async function hostsTab(el, id, c) {
  el.innerHTML = `<div class="tab-loading"><span class="busy-ico"></span><span>Loading…</span></div>`;
  const H = await api("GET", `/storages/${encodeURIComponent(id)}/hosts`);
  const owner = {}; H.hosts.forEach(h => h.wwpns.forEach(p => owner[p.wwpn] = h.name));
  const modeTxt = H.mode === "hostcluster" ? `The volumes of this storage are mapped to the host cluster <b>${esc(H.hostcluster)}</b>.`
    : `The volumes of this storage are mapped to each linked host (<span class="mono">${esc(c["sw-hosts"] || "")}</span>).`;
  el.innerHTML = `${notification("info", H.mode === "hostcluster" ? "Host cluster mode" : "Individual hosts mode", modeTxt)}
    <h3 class="tile-title" style="margin-top:1.5rem">Proxmox nodes</h3>
    <div class="dt" style="margin-bottom:2rem"><table class="mini"><thead><tr><th>Node</th><th>FC ports</th><th>iSCSI IQN</th><th>Host on the array</th><th></th></tr></thead><tbody>
    ${H.pve_nodes.map(n => {
      const ws = n.wwpns || [], hosts = [...new Set(ws.map(w => owner[w]).filter(Boolean))], free = ws.filter(w => !owner[w]);
      const hostCell = hosts.length ? hosts.map(h => { const ho = H.hosts.find(x => x.name === h); return `${esc(h)} ${ho?.attached ? tag("linked", "green") : tag("not linked", "warm-gray")}`; }).join("<br>")
        : n.wwpns === null ? `<span class="helper">authorize the node in ${link("#/nodes", "Proxmox nodes")}</span>` : `<span class="muted">none</span>`;
      const act = n.wwpns === null ? "" : !hosts.length ? status("warn", "no host on the array") : free.length ? status("warn", "ports without a host")
        : hosts.every(h => H.hosts.find(x => x.name === h)?.attached) ? status("ok", "ready") : status("warn", "host not linked");
      return `<tr><td><b>${esc(n.node)}</b> ${n.online ? "" : tag("offline", "red")}</td><td>${ws.length ? ws.map(w => `<span class="mono">${esc(w)}</span>${owner[w] ? "" : ` ${tag("no host", "warm-gray")}`}`).join("<br>") : `<span class="muted">${n.wwpns === null ? "–" : "no FC"}</span>`}</td>
        <td class="mono helper">${esc(n.iqn || "–")}</td><td>${hostCell}</td><td class="act" style="width:auto;white-space:nowrap">${act}</td></tr>`;
    }).join("")}</tbody></table></div>
    <div id="hosts-t"></div>
    <p class="helper" style="margin-top:1rem">Hosts, ports and host mapping are configured on the storage itself; the panel only shows their state.</p>`;
  mount($("#hosts-t"), dataTable({
    title: "Hosts on the array", desc: "Host objects of IBM Storage Virtualize. Volumes of this storage = how many of the Proxmox volumes are mapped to the host.",
    columns: [
      { t: "Host", key: "name", render: h => `<b>${esc(h.name)}</b>` },
      { t: "State", key: "status", render: h => status(h.status === "online" ? "ok" : h.status === "degraded" ? "warn" : h.status === "offline" ? "err" : "neutral", h.status) },
      { t: "Ports", text: h => h.wwpns.map(p => p.wwpn).concat(h.iqns).join(" "), render: h => h.wwpns.map(p => `<span class="mono">${esc(p.wwpn)}</span> <span class="helper">${esc(p.state || "")}</span>`).concat(h.iqns.map(q => `<span class="mono helper">${esc(q)}</span>`)).join("<br>") || "–" },
      { t: "Host cluster", key: "host_cluster_name", render: h => h.host_cluster_name ? tag(h.host_cluster_name, "info") : `<span class="muted">–</span>` },
      { t: "Volumes of this storage", num: 1, sortVal: h => h.volumes_mapped, render: h => h.volumes_total ? `${h.volumes_mapped}/${h.volumes_total}` : "0" },
      { t: "Link", text: h => h.attached ? "linked" : "", render: h => h.attached ? (h.volumes_mapped < h.volumes_total ? status("warn", "linked, mappings missing") : status("ok", "linked")) : `<span class="muted">not linked</span>` },
    ], rows: H.hosts.map(h => ({ ...h, __key: h.name })), empty: "No hosts on the array.",
  }));
}

function bindStorageOvf(id, d) {
  const b = $("#st-ovf"); if (!b) return;
  b.onclick = () => openMenu(b, [
    { label: d.panel_credential ? "Renew panel credential" : "Set up panel credential", icon: "password", run: () => credentialFlow(id) },
    { label: "Remove from Proxmox", icon: "trash-can", danger: true, run: () => removeStorage(id) },
  ]);
}
async function credentialFlow(id) {
  let r; try { r = await api("POST", `/storages/${encodeURIComponent(id)}/credential`); } catch (e) { return toast("err", "Error", e.message); }
  await modal({ label: id, title: "Panel credential", size: "md",
    body: `${notification("warn", "Replacing the key stops the Proxmox plugin until the last step", "Use a new key only when the plugin key does not open the array. From the moment the key changes on the array until you click Test and apply, Proxmox cannot reach the array. A key generated here is kept by the panel until it is applied, even if the panel restarts.")}
      <ol class="steps-list"><li><span>Copy the public key below.</span></li>
      <li><span>In the array GUI, open <b>Access › Users › ${esc(r.user)}</b> and replace the <b>SSH Public Key</b>. On firmware 7.x each user has a single key.</span></li>
      <li><span>Click <b>Test and apply</b>: the panel tests the key and updates the Proxmox plugin.</span></li></ol>
      <div class="codebox"><span id="pk">${esc(r.pubkey)}</span><span class="copy">${btn("", "ghost", 'data-copy="pk" title="Copy" aria-label="Copy"', "copy", "sm")}</span></div>
      <p class="helper">Array fingerprints: ${r.fingerprints.map(f => `<span class="mono">${esc(f)}</span>`).join(" · ")}</p>`,
    buttons: [{ id: "no", label: "Cancel", kind: "secondary" }, { id: "go", label: "Test and apply", kind: "primary" }],
    onButton: async (bid, root) => {
      if (bid !== "go") return false;
      try { await busy($("[data-b=go]", root), () => api("POST", `/storages/${encodeURIComponent(id)}/credential/apply`)); toast("ok", "Credential applied", "Panel and plugin now use the new key"); main.dataset.route = ""; route(); return false; }
      catch (e) { toast("err", "The key does not work yet", e.message); return true; }
    } });
}

/* ======================================================================
   Wizard: add storage
   ====================================================================== */
async function pageWizard() {
  if (!ME.admin) { main.innerHTML = `<div class="page">${notification("warn", "Registering storages requires the Sys.Modify privilege in Proxmox.")}</div>`; return; }
  const W = { step: 0 };
  const STEPS = ["Connection", "SSH key", "Verification", "Pool", "Mapping", "Registration"];
  const progress = () => `<ul class="cds--progress" aria-label="Steps">${STEPS.map((s, i) => {
    const cls = i < W.step ? "complete" : i === W.step ? "current" : "incomplete";
    const svg = i < W.step ? icon("checkmark--outline", 16) : i === W.step
      ? `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1a7 7 0 1 0 7 7 7 7 0 0 0-7-7zm0 13a6 6 0 1 1 6-6 6 6 0 0 1-6 6z" fill="currentColor"/><circle cx="8" cy="8" r="4" fill="currentColor"/></svg>`
      : `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1a7 7 0 1 0 7 7 7 7 0 0 0-7-7zm0 13a6 6 0 1 1 6-6 6 6 0 0 1-6 6z" fill="currentColor"/></svg>`;
    return `<li class="cds--progress-step cds--progress-step--${cls}"><div class="cds--progress-step-button cds--progress-step-button--unclickable" role="listitem" aria-current="${i === W.step}">${svg}
      <div class="cds--progress-text"><p class="cds--progress-label">${esc(s)}</p></div><span class="cds--progress-line"></span></div></li>`;
  }).join("")}</ul>`;
  const render = (html, { back, next = "Next", nextIcon = "arrow--right" } = {}) => {
    main.innerHTML = `<div class="page wiz">${pageHeader({ crumbs: [["Storages", "#/storages"], ["Add storage"]], title: "Add storage",
      sub: "SSH key, pool, hosts and storage registration in Proxmox." })}
      ${progress()}<div class="wiz-body">${html}<div id="w-err" style="margin-top:1.5rem"></div>
      <div class="wiz-foot"><div>${btn("Cancel", "ghost", 'id="w-cancel"')}</div><div>${back ? btn("Back", "secondary", 'id="w-back"') : ""}${next ? btn(next, "primary", 'id="w-next"', nextIcon) : ""}</div></div></div></div>`;
    $("#w-cancel").onclick = cancel; if (back) $("#w-back").onclick = back;
  };
  const fail = e => { $("#w-err").innerHTML = notification("err", "Could not continue", esc(e.message)); };
  const cancel = async () => { if (W.wid) await api("DELETE", `/wizard/${W.wid}`).catch(() => { }); location.hash = "#/storages"; };

  const s1 = () => {
    W.step = 0;
    render(`<h2>Array address</h2><p class="lead">Enter the management IP of the IBM Storwize, FlashSystem or SVC and the user Proxmox will use.</p>
      <div class="form-stack" style="max-width:32rem">${textInput("w-addr", "Management IP or name", `placeholder="192.168.15.20" value="${esc(W.address || "")}" autocapitalize="off" spellcheck="false"`)}
      ${textInput("w-user", "User on the array", `value="${esc(W.user || "pveplugin")}" autocapitalize="off" spellcheck="false"`, "User in the <b>Administrator</b> group (creates and deletes volumes). Create it in the array GUI under Access › Users if it does not exist yet.")}</div>`, { next: "Generate key", nextIcon: "password" });
    $("#w-addr").focus();
    $("#w-next").onclick = async () => {
      const address = $("#w-addr").value.trim(), user = $("#w-user").value.trim();
      if (!address) return fail(new Error("Enter the array address."));
      try { const r = await busy($("#w-next"), () => api("POST", "/wizard/start", { address, user })); Object.assign(W, r, { address, user }); s2(); } catch (e) { fail(e); }
    };
  };
  const s2 = () => {
    W.step = 1;
    render(`<h2>Register the key on the array</h2><p class="lead">The panel generated an RSA 4096 key just for this storage. The private key stays only on the panel and in Proxmox.</p>
      <ol class="steps-list"><li><span>Copy the public key:</span></li></ol>
      <div class="codebox" style="margin:-.75rem 0 1.5rem"><span id="pk">${esc(W.pubkey)}</span><span class="copy">${btn("", "ghost", 'data-copy="pk" title="Copy" aria-label="Copy"', "copy", "sm")}</span></div>
      <ol class="steps-list" style="counter-reset:s 1"><li><span>In the array GUI: <b>Access › Users</b> › <b>${esc(W.user)}</b> (or <i>Create User</i> in the Administrator group) › <b>SSH Public Key</b> › paste and save.</span></li>
      <li><span>Check that the array is really yours: the fingerprints below must match the array's.</span></li></ol>
      <div class="codebox" style="margin:-.75rem 0 1.5rem">${W.fingerprints.map(esc).join("<br>")}</div>
      ${checkbox("w-trust", "I checked the fingerprints and registered the public key on the array")}`, { back: s1, next: "Test connection", nextIcon: "connect" });
    $("#w-next").onclick = async () => {
      if (!$("#w-trust").checked) { $("#w-err").innerHTML = notification("warn", "Confirm the fingerprints and the key registration."); return; }
      try { await api("POST", `/wizard/${W.wid}/trust`); W.info = await busy($("#w-next"), () => api("POST", `/wizard/${W.wid}/test`)); s3(); }
      catch (e) { fail(new Error(`${e.message}. If you just registered the key, wait a few seconds and try again.`)); }
    };
  };
  const s3 = () => {
    W.step = 2; const i = W.info, lic = i.license || {};
    render(`<h2>Connected to the array</h2>${notification("ok", `${esc(i.system.name)} · ${esc(i.system.product_name)}`, `Firmware ${esc(i.system.code_level)}`)}
      <div class="g g-2" style="margin-top:1rem"><div>${sl([["Pools", String(i.pools.length)], ["Hosts", String(i.hosts.length)], ["Host clusters", String(i.hostclusters.length)],
        ["FC ports without a host", i.candidates.length ? i.candidates.map(x => `<span class="mono">${esc(x)}</span>`).join("<br>") : "none"],
        ["FlashCopy license", `${esc(lic.license_flash ?? "?")} <span class="helper">(on the V7000 Gen1, 0 usually means “included in the base license”)</span>`]])}</div>
      <div><p class="cds--label">Firmware features</p><div class="tags">${Object.entries(i.capabilities).map(([k, v]) => tag(CAP_LABEL[k] || k, v ? "info" : "neutral")).join("")}</div>
        ${i.capabilities.rest_api ? "" : `<p class="helper" style="margin-top:1rem">No REST API on this firmware: Proxmox will use the CLI over SSH.</p>`}</div></div>
      ${i.used_prefixes.length ? notification("warn", "This array already has a storage in Proxmox", `Prefixes in use: <b>${i.used_prefixes.map(esc).join(", ")}</b>. The new storage needs a different prefix.`) : ""}`, { back: s2 });
    $("#w-next").onclick = s4;
  };
  const s4 = () => {
    W.step = 3; const i = W.info;
    const parents = i.pools.filter(p => !p.type || p.type === "parent" || p.parent_mdisk_grp_name === p.name);
    render(`<h2>Volume pool</h2><p class="lead">Recommended: a <b>dedicated child pool</b>. Proxmox only sees its space and the other volumes on the array stay protected.</p>
      <div class="choice-list" role="radiogroup">${i.pools.map(p => `<label class="radio-tile ${W.pool === p.name ? "sel" : ""}"><input type="radio" name="pool" value="${esc(p.name)}" ${W.pool === p.name ? "checked" : ""}>
        <div style="flex:1"><b>${esc(p.name)} ${tag(p.type === "child_thick" ? "child pool" : p.type || "pool", p.type?.startsWith("child") ? "info" : "neutral")}</b>
        <span>${bytes(p.free_capacity)} free of ${bytes(p.capacity)} · ${esc(p.vdisk_count)} volumes${p.parent_mdisk_grp_name && p.parent_mdisk_grp_name !== p.name ? ` · child of ${esc(p.parent_mdisk_grp_name)}` : ""}</span></div></label>`).join("")}</div>
      ${i.capabilities.child_pool && parents.length ? `<h2 style="margin-top:2.5rem">Or create a child pool</h2><div class="form-row" style="margin-top:1rem">
        ${textInput("cp-name", "Name", 'value="PVE_Pool"')}${selectInput("cp-parent", "Parent pool", parents.map(p => ({ v: p.name, t: `${p.name} (${bytes(p.free_capacity)} free)` })))}${textInput("cp-size", "Size (GB)", 'type="number" min="1" value="1024"')}</div>
        <div style="margin-top:1rem">${btn("Create child pool", "tertiary", 'id="cp-go"', "add")}</div>` : ""}`, { back: s3 });
    $$("input[name=pool]").forEach(r => r.onchange = () => { $$(".radio-tile").forEach(t => t.classList.toggle("sel", $("input", t).checked)); });
    $("#cp-go")?.addEventListener("click", async e => {
      const b = e.currentTarget, name = $("#cp-name").value.trim(), parent = $("#cp-parent").value, size = +$("#cp-size").value;
      if (!await confirmDialog({ label: "Array", title: "Create child pool?", ok: "Create", body: `<p>The panel will run on the array:</p><pre class="term">svctask mkmdiskgrp -name ${esc(name)} -parentmdiskgrp ${esc(parent)} -size ${size} -unit gb</pre>` })) return;
      try { await busy(b, () => api("POST", `/wizard/${W.wid}/childpool`, { name, parent, size_gb: size })); W.pool = name; W.info = await api("POST", `/wizard/${W.wid}/test`); toast("ok", "Child pool created", name); s4(); } catch (err) { fail(err); }
    });
    $("#w-next").onclick = () => { const r = $("input[name=pool]:checked"); if (!r) return fail(new Error("Choose the pool.")); W.pool = r.value; s5(); };
  };
  const s5 = () => {
    W.step = 4; const i = W.info;
    const owner = {}; Object.entries(i.host_ports).forEach(([h, ps]) => ps.forEach(p => owner[(p || "").toUpperCase()] = h));
    const cand = i.candidates.map(c => (c || "").toUpperCase());
    const nodeRows = i.pve_nodes.map(n => {
      const ws = n.wwpns || [], hosts = [...new Set(ws.map(w => owner[w]).filter(Boolean))], free = ws.filter(w => !owner[w] && cand.includes(w));
      const right = hosts.length ? hosts.map(h => status("ok", `host ${h}`)).join(" ") : free.length ? btn(`Create host “${n.node}”`, "tertiary", `data-mk="${esc(n.node)}" data-w="${free.join(",")}"`, "add", "sm")
        : n.wwpns === null ? `<span class="helper">Authorize the node in ${link("#/nodes", "Proxmox nodes")} to detect the ports.</span>` : status("warn", "WWPNs not logged in to the array (zoning?)");
      return `<tr><td><b>${esc(n.node)}</b></td><td>${ws.length ? ws.map(w => `<span class="mono">${esc(w)}</span>`).join("<br>") : `<span class="muted">${n.wwpns === null ? "unmanaged node" : "no FC"}</span>`}</td><td>${right}</td></tr>`;
    }).join("");
    const pick = W.hosts || i.hosts.filter(h => i.pve_nodes.some(n => (n.wwpns || []).some(w => owner[w] === h.name))).map(h => h.name);
    render(`<h2>Mapping</h2><p class="lead">Volumes created by Proxmox are mapped to these array hosts. Each Proxmox node must exist as a host on the array.</p>
      <div class="dt" style="margin-bottom:2rem"><table class="cds--data-table cds--data-table--md"><thead><tr><th><div class="cds--table-header-label">Proxmox node</div></th><th><div class="cds--table-header-label">WWPNs</div></th><th><div class="cds--table-header-label">Host on the array</div></th></tr></thead><tbody>${nodeRows}</tbody></table></div>
      <div class="choice-list" role="radiogroup">
        <label class="radio-tile ${!W.hostcluster ? "sel" : ""}"><input type="radio" name="mode" value="hosts" ${!W.hostcluster ? "checked" : ""}><div><b>Individual hosts</b><span>One mapping per host. Good for a single node.</span></div></label>
        ${i.capabilities.host_cluster ? `<label class="radio-tile ${W.hostcluster ? "sel" : ""}"><input type="radio" name="mode" value="hc" ${W.hostcluster ? "checked" : ""}><div><b>Host cluster</b><span>One mapping shared by all nodes. Recommended for a Proxmox cluster.</span></div></label>` : ""}
      </div>
      <div id="m-hosts" style="margin-top:1.5rem"><p class="cds--label">Hosts that receive the volumes</p>${i.hosts.length ? i.hosts.map(h => checkbox("h-" + h.name, `${esc(h.name)} <span class="helper">· ${esc(h.status)} · ${esc(h.port_count)} ports</span>`, pick.includes(h.name), `data-h="${esc(h.name)}"`)).join("") : `<p class="muted">No hosts on the array yet — create them from the WWPNs above.</p>`}</div>
      <div id="m-hc" style="margin-top:1.5rem;max-width:32rem">${selectInput("hc", "Host cluster", [...i.hostclusters.map(h => ({ v: h.name, t: h.name, sel: h.name === W.hostcluster })), { v: "__new", t: "+ Create new host cluster" }])}
        <div id="hc-new" style="margin-top:1rem">${textInput("hc-name", "Name of the new host cluster", 'value="pve-cluster"', "The hosts of the Proxmox nodes will be added to it.")}</div></div>`, { back: s4 });
    const sync = () => { const hc = $("input[name=mode]:checked")?.value === "hc"; $("#m-hosts").hidden = hc; $("#m-hc").hidden = !hc; $("#hc-new").hidden = $("#hc").value !== "__new"; $$(".radio-tile").forEach(t => t.classList.toggle("sel", $("input", t).checked)); };
    $$("input[name=mode]").forEach(r => r.onchange = sync); $("#hc").onchange = sync; sync();
    $$("[data-mk]").forEach(b => b.onclick = async () => {
      const name = b.dataset.mk.replace(/[^A-Za-z0-9_.-]/g, "_"), w = b.dataset.w.split(",");
      if (!await confirmDialog({ label: "Array", title: "Create host on the array?", ok: "Create", body: `<pre class="term">svctask mkhost -name ${esc(name)} -fcwwpn ${esc(w.join(":"))} -type generic</pre>` })) return;
      try { await busy(b, () => api("POST", `/wizard/${W.wid}/host`, { name, wwpns: w })); W.info = await api("POST", `/wizard/${W.wid}/test`); W.hosts = [...(W.hosts || pick), name]; toast("ok", "Host created", name); s5(); } catch (e) { fail(e); }
    });
    $("#w-next").onclick = async () => {
      if ($("input[name=mode]:checked").value === "hc") {
        let hc = $("#hc").value;
        if (hc === "__new") {
          hc = $("#hc-name").value.trim();
          const members = i.hosts.map(h => h.name).filter(n => i.pve_nodes.some(p => (p.wwpns || []).some(w => owner[w] === n)) || (W.hosts || []).includes(n));
          if (!await confirmDialog({ label: "Array", title: "Create host cluster?", ok: "Create", body: `<pre class="term">svctask mkhostcluster -name ${esc(hc)}\n${members.map(m => `svctask addhostclustermember -host ${esc(m)} ${esc(hc)}`).join("\n")}</pre>` })) return;
          try { await busy($("#w-next"), () => api("POST", `/wizard/${W.wid}/hostcluster`, { name: hc, members })); } catch (e) { return fail(e); }
        }
        W.hostcluster = hc; W.hosts = null;
      } else {
        W.hosts = $$("[data-h]").filter(x => x.checked).map(x => x.dataset.h); W.hostcluster = null;
        if (!W.hosts.length) return fail(new Error("Choose at least one host."));
      }
      s6();
    };
  };
  const s6 = () => {
    W.step = 5; const i = W.info;
    const sugg = W.prefix || (i.used_prefixes.includes("pve") ? `pve${i.used_prefixes.length + 1}` : "pve");
    const sid = W.storage || (i.system.name || "ibm").toLowerCase().replace(/[^a-z0-9-]/g, "").replace(/^[^a-z]+/, "").slice(0, 20) || "ibm";
    render(`<h2>Register in Proxmox</h2><p class="lead">Review and register. The storage appears on every cluster node.</p>
      <div class="g g-2"><div class="form-stack">${textInput("r-id", "Storage ID in Proxmox", `value="${esc(sid)}" autocapitalize="off" spellcheck="false"`, "Shown in the Proxmox GUI, for example in <i>v7k:vm-100-disk-0</i>")}
        ${textInput("r-prefix", "Volume prefix", `value="${esc(sugg)}" maxlength="16"`, "Every Proxmox volume starts with this prefix. The plugin only touches volumes that have it.")}
        ${selectInput("r-snap", "Snapshot pool", [{ v: "", t: `Same as the volumes (${W.pool})` }, ...i.pools.filter(p => p.name !== W.pool).map(p => ({ v: p.name, t: p.name }))])}
        ${selectInput("r-tr", "Transport", [{ v: "", t: "Fibre Channel / iSCSI (multipath)" }, { v: "nvme-tcp", t: "NVMe over TCP — experimental" }])}
        <div id="r-po-w" hidden>${textInput("r-po", "Array NVMe/TCP ports", 'placeholder="10.0.0.11,10.0.0.12" autocapitalize="off" spellcheck="false"', "IP (or IP:port) of the array data ports, comma separated. The nodes need the NVMe over TCP step in Proxmox nodes, and the host on the array must be created with the node's NQN.")}</div>
        ${checkbox("r-thin", "Thin-provisioned volumes", true)}${checkbox("r-ct", "Allow container disks (rootdir)", false)}
        ${checkbox("r-cg", "Snapshots consistent across the VM disks (FlashCopy consistency group)", true)}
        ${i.capabilities.vg_snapshot ? selectInput("r-sm", "Snapshot engine", [{ v: "flashcopy", t: "FlashCopy (all versions) — recommended" }, { v: "volumegroup", t: "Volume group + addsnapshot (8.5.1+) — experimental" }], "Volume group mode creates one volume group per VM on the array. Not yet validated on 8.x hardware.") : ""}
        ${(i.partitions || []).length ? selectInput("r-pt", "Storage partition", [{ v: "", t: "None" }, ...i.partitions.map(p => ({ v: p.name, t: p.replication_policy ? `${p.name} (${p.replication_policy})` : p.name }))], "Inside a partition, the Proxmox hosts must belong to it and the snapshot engine becomes volume group. For high availability (PBHA), register the storage using the partition management IP.") : ""}</div>
      <div class="tile" style="background:var(--cds-layer-02)"><h3 class="tile-title">Summary</h3>${sl([["Array", `${esc(i.system.name)} <span class="helper mono">${esc(W.address)}</span>`], ["Firmware", esc((i.system.code_level || "").split(" ")[0])], ["Type in Proxmox", `<span data-noi18n>${i.plugin_type === "flashsystem" ? "IBM FlashSystem" : "IBM Storwize"}</span> <span class="helper mono">${esc(i.plugin_type || "storwize")}</span>`], ["Backend", "SSH (CLI)"],
        ["User", esc(W.user)], ["Pool", esc(W.pool)], ["Mapping", esc(W.hostcluster ? `host cluster ${W.hostcluster}` : `hosts ${W.hosts.join(", ")}`)]])}
        <p class="helper" style="margin-top:1rem">The private key goes to <span class="mono">/etc/pve/priv/storwize/</span> through the Proxmox API and is never stored in storage.cfg. The plugin must be installed on every node — see ${link("#/nodes", "Proxmox nodes")}.</p></div></div>`,
      { back: s5, next: "Register storage", nextIcon: "checkmark--outline" });
    $("#w-next").onclick = async () => {
      const body = { storage: $("#r-id").value.trim(), prefix: $("#r-prefix").value.trim(), pool: W.pool, snappool: $("#r-snap").value || null, thin: $("#r-thin").checked,
        content: $("#r-ct").checked ? "images,rootdir" : "images", hostcluster: W.hostcluster, hosts: W.hosts,
        consistency: $("#r-cg").checked, snapmode: $("#r-sm") ? $("#r-sm").value : "flashcopy", partition: $("#r-pt") ? $("#r-pt").value : "", transport: $("#r-tr").value, portals: $("#r-po").value.trim() };
      W.storage = body.storage; W.prefix = body.prefix;
      try { await busy($("#w-next"), () => api("POST", `/wizard/${W.wid}/register`, body)); toast("ok", "Storage registered", `${body.storage} now appears in Proxmox`); location.hash = `#/storage/${encodeURIComponent(body.storage)}`; } catch (e) { fail(e); }
    };
  };
  s1();
}

/* ======================================================================
   VMs and volumes
   ====================================================================== */
const hasAgent = vm => /^(1|enabled=1)/.test(String(vm.agent || ""));
async function pageVMs() {
  const inv = await api("GET", "/inventory");
  const disks = {}; inv.disks.forEach(d => (disks[d.vmid] = disks[d.vmid] || []).push(d));
  main.innerHTML = `<div class="page">${pageHeader({ title: "VMs and volumes", sub: "Each disk is a volume on the array. Actions go through the Proxmox API, with your permissions." })}<div id="t"></div></div>`;
  if (!inv.vms.length) { $("#t").innerHTML = emptyState("virtual-machine", "No VM uses the storages yet", "Create a disk on a Storwize storage in the Proxmox GUI (VM › Hardware › Add › Hard Disk) and it shows up here."); return; }
  const rows = inv.vms.map(v => ({ ...v, __key: v.vmid, d: disks[v.vmid] || [] }));
  const t = mount($("#t"), dataTable({
    columns: [
      { t: "VMID", num: 1, key: "vmid", sortVal: v => v.vmid, render: v => `<b>${v.vmid}</b>` },
      { t: "Name", key: "name", render: v => `${esc(v.name || "")} <span class="helper">${v.type === "qemu" ? "VM" : "CT"}</span>${v.d.some(d => limitText(d.array_limit) || limitText(qemuLimit(d.qemu_limits))) ? " " + tag("I/O limited", "info") : ""}` },
      { t: "State", key: "status", render: v => v.status === "running" ? status("ok", "Running") : status("neutral", v.status === "stopped" ? "Stopped" : v.status) },
      { t: "Node", key: "node" },
      { t: "Capacity", num: 1, sortVal: v => v.d.reduce((a, d) => a + (d.capacity || 0), 0), render: v => bytes(v.d.reduce((a, d) => a + (d.capacity || 0), 0)) },
      { t: "Snapshots", num: 1, sortVal: v => v.snapshots.length + (v.array_snapshots || []).length,
        render: v => { const a = v.array_snapshots || [], sg = a.filter(x => x.safeguarded).length;
          return `${v.snapshots.length}${a.length ? ` <span class="helper">+ ${a.length} ${esc(tr("on the array"))}</span>` : ""}${sg ? " " + tag(`${sg} Safeguarded`, "purple") : ""}`; } },
      { t: "IOPS now", num: 1, sortVal: v => v.d.reduce((a, d) => a + (d.perf_now ? d.perf_now.r_iops + d.perf_now.w_iops : 0), 0),
        render: v => { const n = v.d.reduce((a, d) => a + (d.perf_now ? d.perf_now.r_iops + d.perf_now.w_iops : 0), 0); return v.d.some(d => d.perf_now) ? num(n, 0) : `<span class="muted">–</span>`; } },
      { t: "Snapshot", text: v => hasAgent(v) ? "agent" : "", render: v => v.type !== "qemu" ? "<span class='muted'>–</span>" : hasAgent(v) ? status("ok", "guest agent") : `<span title="no qemu-guest-agent: snapshot without fs-freeze">${status("warn", "crash-consistent")}</span>` },
      { t: "", act: 1, render: v => ovfButton(`data-vm="${v.vmid}"`, `Actions for VM ${v.vmid}`) },
    ], rows,
    expand: v => `
      <section><div class="sub-h"><span class="cds--label">Disks on the array</span></div>
        <table class="mini"><thead><tr><th>Disk</th><th>Volume / UID</th><th class="num">Size</th><th>Now (as seen by the host)</th><th>I/O limits</th><th></th></tr></thead><tbody>
        ${v.d.map(d => `<tr><td>${d.vstatus === "online" ? status("ok", d.key) : status("err", `${d.key} · ${d.vstatus || "missing"}`)}</td><td><span class="mono">${esc(d.vdisk)}</span><div class="mono helper">${esc(d.uid || "–")}</div></td>
          
          <td class="num">${d.capacity ? bytes(d.capacity) : esc(d.size)}</td><td>${perfNow(d.perf_now)}</td><td>${limitsCell(d)}</td>
          <td class="act">${d.key.startsWith("unused") ? "" : ovfButton(`data-diskm="${v.vmid}" data-disk="${esc(d.key)}"`, `Actions for disk ${d.key}`)}</td></tr>`).join("")}</tbody></table></section>
      <section><div class="sub-h"><span class="cds--label">Snapshots (${v.snapshots.length})</span>${btn("Take snapshot", "ghost", `data-newsnap="${v.vmid}"`, "camera", "sm")}</div>
        ${v.snapshots.length ? `<table class="mini"><thead><tr><th>Name</th><th>Created</th><th>Description</th><th></th></tr></thead><tbody>
        ${v.snapshots.slice().sort((a, b) => (b.snaptime || 0) - (a.snaptime || 0)).map(s => `<tr><td><b>${esc(s.name)}</b>${s.vmstate ? " " + tag("with RAM", "purple") : ""}</td><td>${esc(dt(s.snaptime))}</td><td class="muted">${esc(s.description || "")}</td>
          <td class="act">${ovfButton(`data-snapm="${v.vmid}" data-snap="${esc(s.name)}"`, `Actions for snapshot ${s.name}`)}</td></tr>`).join("")}</tbody></table>` : `<div class="mini-empty">No snapshots for this VM.</div>`}</section>
      ${(v.array_snapshots || []).length || (v.array_policies || []).length ? `<section><div class="sub-h"><span class="cds--label">${esc(tr("Array snapshots (volume group)"))} (${(v.array_snapshots || []).length})</span>
        ${(v.array_policies || []).map(p => `<span class="helper">${esc(tr("Policy"))} ${tag(p.policy, "info")}${p.safeguarded ? " " + tag("Safeguarded", "purple") : ""}${p.next ? ` · ${esc(tr("next"))}: ${esc(fcTime(p.next))}` : ""}</span>`).join(" ")}</div>
        ${(v.array_snapshots || []).length ? `<table class="mini"><thead><tr><th>Name</th><th>Created</th><th>Expires</th><th>Type</th><th></th></tr></thead><tbody>
        ${v.array_snapshots.map((s, i) => `<tr><td class="mono">${esc(s.name)}</td><td>${esc(fcTime(s.time))}</td><td>${s.expires ? esc(fcTime(s.expires)) : `<span class="muted">–</span>`}</td>
          <td>${s.safeguarded ? tag("Safeguarded", "purple", "Immutable until it expires") : tag("Snapshot", "neutral")} ${s.auto ? tag("policy", "info") : ""}${s.state && s.state !== "active" ? " " + tag(s.state, "warn") : ""}${s.anomaly && !/^(none|no|normal)$/i.test(s.anomaly) ? " " + tag(s.anomaly, "red", "Anomaly detection of the array (possible ransomware)") : ""}</td>
          <td class="act">${ME.admin && v.type === "qemu" ? ovfButton(`data-asnap="${v.vmid}" data-i="${i}"`, `Actions for snapshot ${s.name}`) : ""}</td></tr>`).join("")}</tbody></table>` : `<div class="mini-empty">${esc(tr("No snapshots on the array yet."))}</div>`}</section>` : ""}`,
  }));
  const bind = () => {
    $$("[data-vm]").forEach(b => b.onclick = () => {
      const v = rows.find(x => x.vmid === +b.dataset.vm);
      openMenu(b, [
        { label: "Take snapshot", icon: "camera", run: () => snapCreate(v) },
        ...v.d.filter(d => !d.key.startsWith("unused")).map(d => ({ label: `Expand ${d.key}`, icon: "maximize", run: () => resizeFlow(v.vmid, d.key, d.fc_count || 0, d.capacity || 0) })),
        ...v.d.filter(d => !d.key.startsWith("unused")).map(d => ({ label: `I/O limits for ${d.key}`, icon: "flash", run: () => limitsFlow(v, d) })),
        "-", { label: "Backup from array snapshot", icon: "data--base", disabled: !ME.admin || v.type !== "qemu", title: v.type !== "qemu" ? "VMs only" : "", run: () => cbtBackupFlow(v) },
        { label: "Snapshot backup history", icon: "time", disabled: v.type !== "qemu", run: () => cbtHistory(v) },
        "-", { label: "Open in Proxmox", icon: "launch", run: () => window.open(`https://${ME.pve_host}:8006/#v1:0:=${v.type === "qemu" ? "qemu" : "lxc"}%2F${v.vmid}`, "_blank", "noopener") },
      ]);
    });
    $$("[data-diskm]").forEach(b => b.onclick = () => {
      const v = rows.find(x => x.vmid === +b.dataset.diskm), d = v.d.find(x => x.key === b.dataset.disk);
      openMenu(b, [
        { label: "Performance charts", icon: "chart--line", run: () => location.hash = `#/diskperf/${v.vmid}/${d.key}` },
        { label: "I/O limits", icon: "flash", run: () => limitsFlow(v, d) },
        { label: "Expand disk", icon: "maximize", run: () => resizeFlow(v.vmid, d.key, d.fc_count || 0, d.capacity || 0) },
      ]);
    });
    $$("[data-newsnap]").forEach(b => b.onclick = () => snapCreate(rows.find(x => x.vmid === +b.dataset.newsnap)));
    $$("[data-resize]").forEach(b => b.onclick = () => resizeFlow(+b.dataset.resize, b.dataset.disk, +b.dataset.fc, +b.dataset.cap));
    $$("[data-limits]").forEach(b => b.onclick = () => { const v = rows.find(x => x.vmid === +b.dataset.limits); limitsFlow(v, v.d.find(d => d.key === b.dataset.disk)); });
    $$("[data-asnap]").forEach(b => b.onclick = () => {
      const v = rows.find(x => x.vmid === +b.dataset.asnap), s = v.array_snapshots[+b.dataset.i];
      const base = `/storages/${encodeURIComponent(s.storage)}/vg/${encodeURIComponent(s.vg)}/snapshots/${encodeURIComponent(s.name)}`;
      openMenu(b, [
        { label: "New VM from this snapshot…", icon: "copy", run: () => vgNewVmFlow(`${base}/newvm`, { name: s.vg, vmid: v.vmid, vm_name: v.name, vm_node: v.node }, s.name) },
        { label: "Restore over this VM…", icon: "undo", run: async () => {
          if (v.status !== "stopped") return toast("warn", "Shut down the VM first", "Restoring overwrites all its disks with the snapshot content.");
          if (await confirmDialog({ label: `VM ${v.vmid}`, title: `Restore “${s.name}”?`, danger: true, ok: "Restore",
            body: `<p>All disks of VM ${v.vmid} on ${esc(s.storage)} go back to ${esc(fcTime(s.time))}. Everything written after that is lost.</p><p class="helper">${esc(tr("To keep the current state, use New VM from this snapshot instead."))}</p>` })) {
            try { await api("POST", `${base}/restore`); toast("ok", "Snapshot restored", `VM ${v.vmid}`); main.dataset.route = ""; route(); }
            catch (e) { toast("err", "The operation failed", e.message); }
          } } },
      ]);
    });
    $$("[data-snapm]").forEach(b => b.onclick = () => {
      const vmid = +b.dataset.snapm, n = b.dataset.snap;
      openMenu(b, [{ label: "Restore (rollback)", icon: "undo", run: () => snapRollback(vmid, n, (rows.find(x => x.vmid === vmid) || {}).status === "running") }, "-", { label: "Delete snapshot", icon: "trash-can", danger: true, run: () => snapDelete(vmid, n) }]);
    });
  };
  bind(); t.addEventListener("dt:draw", bind);
}
async function snapCreate(vm) {
  const def = "snap" + new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
  await modal({ label: `VM ${vm.vmid} · ${vm.name || ""}`, title: "Take snapshot", size: "sm",
    body: `<div class="form-stack">${textInput("sn", "Name", `value="${def}" maxlength="40" autocapitalize="off" spellcheck="false"`, "Letters, digits, _ and -; starts with a letter.")}${textInput("sd", "Description (optional)")}</div>
      ${vm.type === "qemu" && vm.status === "running" && !hasAgent(vm) ? notification("warn", "No qemu-guest-agent", "The snapshot will be <i>crash-consistent</i> (no file system freeze). Install the agent in the VM for consistent snapshots.") : ""}
      <p class="helper">${(vm.d || []).length > 1 ? `All ${(vm.d || []).length} disks are captured at the same instant (FlashCopy consistency group, if consistency is on for the storage).` : "One FlashCopy of the disk."} On the array: thin volume + mkfcmap + start with -prep. Fast, no data copy.</p>`,
    buttons: [{ id: "no", label: "Cancel" }, { id: "go", label: "Take snapshot", kind: "primary" }],
    onButton: async (id, root) => {
      if (id !== "go") return false;
      const name = $("#sn").value.trim();
      try { await busy($("[data-b=go]", root), () => api("POST", `/vms/${vm.vmid}/snapshot`, { name, description: $("#sd").value })); toast("ok", "Snapshot created", `VM ${vm.vmid} · ${name}`); main.dataset.route = ""; route(); return false; }
      catch (e) { toast("err", "Could not create the snapshot", e.message); return true; }
    } });
}
async function snapRollback(vmid, n, running) {
  let start = false;
  const r = await modal({ label: `VM ${vmid}`, title: `Restore “${n}”?`, danger: true, size: "sm",
    body: `<p>The VM will be <b>stopped</b> and the disks will return to the state of snapshot <b>${esc(n)}</b>. Everything written after it will be lost.</p>
      <div style="margin-top:1rem">${checkbox("rb-start", "Start the VM after restoring", !!running)}</div>
      <p class="helper" style="margin-top:1rem">On the array: reverse FlashCopy (-restore). The content is available immediately; the copy finishes in the background.</p>`,
    buttons: [{ id: "no", label: "Cancel", kind: "secondary" }, { id: "yes", label: "Restore", kind: "danger" }],
    onButton: id => { if (id === "yes") start = $("#rb-start").checked; return false; } });
  if (r !== "yes") return;
  try {
    toast("info", "Restoring…", `VM ${vmid} → ${n}`);
    const res = await api("POST", `/vms/${vmid}/snapshot/${encodeURIComponent(n)}/rollback`, { start });
    if (res.vm_status === "running") toast("ok", "Snapshot restored", `VM ${vmid} started`);
    else toast("warn", "Snapshot restored; the VM is powered off", `VM ${vmid} → ${n}`);
    main.dataset.route = ""; route();
  } catch (e) { toast("err", "Could not restore", e.message); }
}
async function snapDelete(vmid, n) {
  if (!await confirmDialog({ label: `VM ${vmid}`, title: `Delete “${n}”?`, danger: true, ok: "Delete", body: `<p>The snapshot and its FlashCopy maps on the array will be deleted. Other snapshots are not affected.</p>` })) return;
  try { toast("info", "Deleting snapshot…", n); await api("DELETE", `/vms/${vmid}/snapshot/${encodeURIComponent(n)}`); toast("ok", "Snapshot deleted", n); main.dataset.route = ""; route(); }
  catch (e) { toast("err", "Could not delete", e.message); }
}
async function resizeFlow(vmid, disk, fc, cap) {
  await modal({ label: `VM ${vmid} · ${disk}`, title: "Expand disk", size: "sm",
    body: `${fc ? notification("warn", `This disk has ${fc} FlashCopy`, "The array does not expand a volume that has a snapshot. Delete the VM snapshots first.") : ""}
      ${sl([["Current size", `<b>${bytes(cap)}</b>`]])}
      <div class="form-row">${textInput("rs", "Increase by (GiB)", 'type="number" min="1" step="1" value="10"')}<div class="cds--form-item"><span class="cds--label">New size</span><div id="rs-new" style="padding:.6875rem 0">${bytes(cap + 10 * 1024 ** 3)}</div></div></div>
      <p class="helper">Works with the VM running: expandvdisksize on the array, rescan on the node and block_resize in QEMU. Then grow the partition inside the guest.</p>`,
    buttons: [{ id: "no", label: "Cancel" }, { id: "go", label: "Expand", kind: "primary" }],
    onOpen: root => { $("#rs", root).oninput = e => { $("#rs-new", root).textContent = bytes(cap + (+e.target.value || 0) * 1024 ** 3); }; },
    onButton: async (id, root) => {
      if (id !== "go") return false;
      const g = Math.floor(+$("#rs").value); if (!(g > 0)) { toast("warn", "Enter how many GiB to add"); return true; }
      try { await busy($("[data-b=go]", root), () => api("POST", `/vms/${vmid}/resize`, { disk, size: `+${g}G` })); toast("ok", "Disk expanded", `${disk}: +${g} GiB`); main.dataset.route = ""; route(); return false; }
      catch (e) { toast("err", "Could not expand", e.message); return true; }
    } });
}

/* ---- I/O limits -------------------------------------------------------- */
function limitText(l) {
  if (!l || (!l.iops && !l.mbps)) return "";
  return [l.iops ? `${num(l.iops)} IOPS` : "", l.mbps ? `${num(l.mbps)} MB/s` : ""].filter(Boolean).join(" · ");
}
function qemuLimit(q) { q = q || {}; return { iops: +q.iops || 0, mbps: +q.mbps || 0, other: Object.keys(q).filter(k => !["iops", "mbps"].includes(k)) }; }
function limitsCell(d) {
  const a = limitText(d.array_limit), q = limitText(qemuLimit(d.qemu_limits));
  if (!a && !q) return `<span class="muted">no limit</span>`;
  return [a ? `${tag("array", "info")} ${esc(a)}` : "", q ? `${tag("proxmox", "purple")} ${esc(q)}` : ""].filter(Boolean).join("<br>");
}
async function limitsFlow(vm, d) {
  const q = qemuLimit(d.qemu_limits), a = d.array_limit || {};
  let level = d.throttle_supported && ME.admin ? "array" : "proxmox";
  const body = () => `
    ${contentSwitcher("lv", [["array", "On the array (IBM QoS)"], ["proxmox", "In Proxmox (QEMU)"]], level)}
    <div id="lv-help" style="margin-top:1rem"></div>
    <div class="form-row">${textInput("li", "Maximum IOPS", 'type="number" min="1" step="1" placeholder="no limit"')}${textInput("lm", "Maximum throughput (MB/s)", 'type="number" min="1" step="1" placeholder="no limit"')}</div>
    <p class="helper">Leave blank for no limit. Limits take effect immediately, with the VM running.</p>`;
  const sync = root => {
    const isA = level === "array";
    $("#li", root).value = (isA ? a.iops : q.iops) || ""; $("#lm", root).value = (isA ? a.mbps : q.mbps) || "";
    $("#lv-help", root).innerHTML = isA
      ? (!d.throttle_supported ? notification("warn", "This firmware does not support per-volume QoS.") : !ME.admin ? notification("warn", "Array limits require the Sys.Modify privilege.") :
        `<p class="helper" style="margin-bottom:1rem">Applied by the array to volume <span class="mono">${esc(d.vdisk)}</span> (<span class="mono">mkthrottle</span>). Applies to any access to the volume and protects the other VMs on the array.</p>`)
      : (vm.type !== "qemu" ? notification("warn", "QEMU limits apply to VMs only.") :
        `<p class="helper" style="margin-bottom:1rem">Applied by QEMU to disk <span class="mono">${esc(d.key)}</span> (options <span class="mono">iops</span>/<span class="mono">mbps</span> in Proxmox).${q.other.length ? ` Other existing options (${esc(q.other.join(", "))}) are kept only if they are not limit options.` : ""}</p>`);
    $$("#lv button", root).forEach(b => b.classList.toggle("cds--content-switcher--selected", b.dataset.v === level));
  };
  await modal({ label: `VM ${vm.vmid} · ${d.key}`, title: "I/O limits", size: "md", body: body(),
    buttons: [{ id: "no", label: "Cancel" }, { id: "clear", label: "Remove limit", kind: "secondary" }, { id: "go", label: "Apply", kind: "primary" }],
    onOpen: root => { sync(root); $$("#lv button", root).forEach(b => b.onclick = () => { level = b.dataset.v; sync(root); }); },
    onButton: async (id, root) => {
      if (id === "no") return false;
      const payload = id === "clear" ? { level, iops: null, mbps: null } : { level, iops: $("#li", root).value || null, mbps: $("#lm", root).value || null };
      try {
        await busy($(`[data-b=${id}]`, root), () => api("POST", `/vms/${vm.vmid}/disks/${encodeURIComponent(d.key)}/limits`, payload));
        toast("ok", id === "clear" ? "Limit removed" : "Limit applied", `${d.key} · ${level === "array" ? "array" : "Proxmox"}${id === "clear" ? "" : " · " + (limitText(payload) || "no limit")}`);
        await api("POST", "/refresh"); main.dataset.route = ""; route(); return false;
      } catch (e) { toast("err", "Could not apply", e.message); return true; }
    } });
}
async function poolLimitsFlow(id, pool, cur) {
  cur = cur || {};
  await modal({ label: id, title: `QoS for pool ${pool}`, size: "sm",
    body: `<p>Total limit for the pool on the array (<span class="mono">mkthrottle -type mdiskgrp</span>). Applies to the sum of all volumes in the pool — protects the rest of the array.</p>
      <div class="form-row">${textInput("pi", "Maximum IOPS", `type="number" min="1" value="${cur.iops || ""}" placeholder="no limit"`)}${textInput("pm", "Maximum throughput (MB/s)", `type="number" min="1" value="${cur.mbps || ""}" placeholder="no limit"`)}</div>
      <p class="helper">Leave blank for no limit.</p>`,
    buttons: [{ id: "no", label: "Cancel" }, { id: "clear", label: "Remove limit", kind: "secondary" }, { id: "go", label: "Apply", kind: "primary" }],
    onButton: async (bid, root) => {
      if (bid === "no") return false;
      const p = bid === "clear" ? { iops: null, mbps: null } : { iops: $("#pi", root).value || null, mbps: $("#pm", root).value || null };
      try { await busy($(`[data-b=${bid}]`, root), () => api("POST", `/storages/${encodeURIComponent(id)}/limits`, p)); toast("ok", bid === "clear" ? "Pool limit removed" : "Pool limit applied", limitText(p)); main.dataset.route = ""; route(); return false; }
      catch (e) { toast("err", "Could not apply", e.message); return true; }
    } });
}

/* ---- per-disk (LUN) performance: host vs array ------------------------------ */
function perfNow(p) {
  if (!p) return `<span class="muted" title="the panel collects every minute">collecting…</span>`;
  const lat = Math.max(p.r_ms || 0, p.w_ms || 0);
  return `<span class="perf-now" title="read ${num(p.r_iops, 0)} IOPS · ${num(p.r_mbs, 1)} MB/s · ${num(p.r_ms, 2)} ms&#10;write ${num(p.w_iops, 0)} IOPS · ${num(p.w_mbs, 1)} MB/s · ${num(p.w_ms, 2)} ms">
    ${num(p.r_iops + p.w_iops, 0)} IOPS · ${num(p.r_mbs + p.w_mbs, 1)} MB/s · ${status(lat > 20 ? "err" : lat > 5 ? "warn" : "ok", `${num(lat, 1)} ms`)}</span>`;
}
async function pageDiskPerf(vmid, disk, hours = "6") {
  const h = +hours || 6;
  const P = await api("GET", `/vms/${vmid}/disks/${encodeURIComponent(disk)}/perf?hours=${h}`);
  const d = P.disk;
  main.innerHTML = `<div class="page">${pageHeader({ crumbs: [["VMs and volumes", "#/vms"], [`VM ${vmid}`], [disk]],
      title: `${icon("chart--line", 24)} ${esc(d.name || "")} · ${esc(disk)}`,
      sub: `Volume <span class="mono">${esc(d.vdisk)}</span> · ${d.capacity ? bytes(d.capacity) : esc(d.size)} · UID <span class="mono">${esc(d.uid || "")}</span>`,
      meta: `<span>Host: collected every minute on the node (multipath LUN statistics)</span><span>Array: the array's statistics interval</span>` })}
    <div style="display:flex;gap:1.5rem;align-items:flex-end;flex-wrap:wrap;margin-bottom:1.5rem">
      <div><span class="cds--label" style="display:block">Period</span>${contentSwitcher("dp-h", [["1", "1 hour"], ["6", "6 hours"], ["24", "24 hours"], ["168", "7 days"]], String(h))}</div></div>
    ${notification("info", "How to read latency", "High on the <b>array</b>: the bottleneck is the storage. High only on the <b>host</b>: the bottleneck is in the path (FC, multipath, host queue). Host latency is what the VM feels.")}
    <div class="g g-2" id="dp-charts"></div></div>`;
  const H = P.host, A = P.array, box = $("#dp-charts");
  const C = [
    ["Latency", "ms", [["Read (host)", H.r_ms], ["Write (host)", H.w_ms], ["Read (array)", A.r_ms], ["Write (array)", A.w_ms]]],
    ["IOPS", "", [["Read (host)", H.r_iops], ["Write (host)", H.w_iops], ["Read (array)", A.r_iops], ["Write (array)", A.w_iops]]],
    ["Throughput", "MB/s", [["Read (host)", H.r_mbs], ["Write (host)", H.w_mbs], ["Read (array)", A.r_mbs], ["Write (array)", A.w_mbs]]],
    ["LUN busy time on the host", "%", [["Busy time", H.util]]],
  ];
  C.forEach(([title, unit, ser]) => { const el = document.createElement("div"); el.className = "chart"; box.appendChild(el);
    lineChart(el, { title, unit, hours: h, series: ser.map(([label, pts]) => ({ label, pts: pts || [] })) }); });
  $$("#dp-h button").forEach(b => b.onclick = () => location.hash = `#/diskperf/${vmid}/${encodeURIComponent(disk)}/${b.dataset.v}`);
}

/* ---- backup from array snapshot ("Backup from Storage Snapshots" style) ---- */
async function cbtBackupFlow(vm) {
  let pbs = []; try { pbs = await api("GET", "/pbs-storages"); } catch (e) { return toast("err", "Error", e.message); }
  if (!pbs.length) return toast("warn", "No PBS storage in Proxmox", "Add a Proxmox Backup Server in Datacenter › Storage.");
  await modal({ label: `VM ${vm.vmid} · ${vm.name || ""}`, title: "Backup from array snapshot", size: "md",
    body: `<ol class="steps-list">
        <li><span>The array prepares a FlashCopy of all the VM disks (consistency group).</span></li>
        <li><span>With the guest agent, the file system freezes only while the snapshot is triggered (about 2 s). <b>The VM does not stop.</b></span></li>
        <li><span>The node reads <b>from the array snapshot</b> — only the blocks changed since the last backup (QEMU CBT) — and sends them to PBS in the VM backup format.</span></li>
        <li><span>The snapshot is deleted from the array. Restore is done as usual from the Proxmox GUI.</span></li></ol>
      ${vm.status !== "running" ? notification("info", "The VM is stopped", "The snapshot backup needs the VM running. With the fallback checked, the job runs the regular Proxmox backup.") : ""}
      ${!hasAgent(vm) ? notification("warn", "No qemu-guest-agent", "The snapshot will be crash-consistent (no file system freeze).") : ""}
      <div class="form-row">${selectInput("cbt-pbs", "Proxmox Backup Server", pbs.map(p => ({ v: p.storage, t: `${p.storage} · ${p.datastore} @ ${p.server}`, sel: p.storage === "pbs-cbt" })))}</div>
      ${checkbox("cbt-full", "Force a full backup (normally automatic: full on the first run and after the VM restarts)", false)}
      ${checkbox("cbt-fb", "If the snapshot backup fails, run the <b>regular Proxmox backup</b> to the same PBS", true)}
      ${checkbox("cbt-prot", "Mark the backup as <b>protected</b> on the PBS", false)}
      <p class="helper">Prune jobs and a plain remove skip protected backups; they stay until someone clears the mark in Proxmox, so they keep using space. The mark guards against mistakes, not against someone with rights on the datastore.</p>
      <p class="helper">Use a datastore just for these backups: the incremental depends on the last backup of the group being from this same method.</p>`,
    buttons: [{ id: "no", label: "Cancel" }, { id: "go", label: "Start backup", kind: "primary" }],
    onButton: async (id, root) => {
      if (id !== "go") return false;
      try { await busy($("[data-b=go]", root), () => api("POST", `/vms/${vm.vmid}/cbt-backup`, { pbs: $("#cbt-pbs", root).value, full: $("#cbt-full", root).checked, fallback: $("#cbt-fb", root).checked, protected: $("#cbt-prot", root).checked }));
        toast("ok", "Backup started", `VM ${vm.vmid} · follow it in the history`); setTimeout(() => cbtHistory(vm), 400); return false; }
      catch (e) { toast("err", "Could not start", e.message); return true; }
    } });
}
async function cbtHistory(vm) {
  let timer = null;
  const render = async root => {
    let H; try { H = await api("GET", `/vms/${vm.vmid}/cbt-backups`); } catch (e) { $("#cbt-h", root).innerHTML = notification("err", "Error", esc(e.message)); return; }
    const rows = H.items.map(it => {
      const r = it.result || {}, t = r.t || {};
      const st = it.running ? status("run", "running") : r.backup ? status("ok", "done") : status("err", "failed");
      return `<tr><td>${esc(dt(it.id))}</td><td>${st}</td><td>${r.fallback ? `<span title="${esc(r.reason || "")}">${tag("regular (fallback)", "warn")}</span>` : r.mode ? tag(r.mode === "full" ? "full" : "incremental", r.mode === "full" ? "purple" : "info") : "–"}</td>
        <td class="num">${r.read_MiB != null ? bytes(r.read_MiB * 1048576) : "–"}</td><td class="num">${t.total != null ? `${num(t.total, 0)} s` : "–"}</td>
        <td class="num">${r.read_MiB_s ? `${num(r.read_MiB_s, 0)} MiB/s` : "–"}</td>
        <td class="num">${t.fs_freeze != null ? `${num(t.fs_freeze, 1)} s` : t.snapshot_trigger != null ? `0 s <span class="helper">(no agent)</span>` : "–"}</td>
        <td class="helper wrap">${r.backup ? `<span class="mono">${esc(r.backup)}</span>${r.protected ? " " + tag("protected", "green") : r.protect_error ? `<div>not protected: ${esc(r.protect_error)}</div>` : ""}${r.fallback ? `<div>snapshot failed: ${esc(r.reason || "")}</div>` : ""}` : r.error ? esc(r.error) : it.log ? `<span class="mono">${esc(it.log.split("\n").slice(-2).join(" "))}</span>` : ""}</td></tr>`;
    }).join("");
    $("#cbt-h", root).innerHTML = !H.installed ? notification("warn", "Tool not installed on this node", `Use ${link("#/nodes", "Proxmox nodes")} › Prepare node.`) :
      rows ? `<div class="dt-scroll"><table class="mini"><thead><tr><th>Start</th><th>State</th><th>Type</th><th class="num">Read</th><th class="num">Time</th><th class="num">Speed</th><th class="num">FS frozen</th><th>Backup</th></tr></thead><tbody>${rows}</tbody></table></div>`
        : `<p class="muted">No snapshot backups for this VM yet.</p>`;
    clearTimeout(timer); if (H.items.some(i => i.running) && document.body.contains(root.firstChild)) timer = setTimeout(() => render(root), 4000);
  };
  await modal({ label: `VM ${vm.vmid} · ${vm.name || ""}`, title: "Backups from array snapshot", size: "lg", body: `<div id="cbt-h"><div class="cds--skeleton sk-block"></div></div>`,
    buttons: [{ id: "ok", label: "Close", kind: "primary" }], onOpen: root => render(root) });
  clearTimeout(timer);
}

/* ======================================================================
   Reconciliation
   ====================================================================== */
async function pageReconcile(filter = "all") {
  const issues = await api("GET", "/reconcile");
  const errs = issues.filter(i => i.severity === "error"), warns = issues.filter(i => i.severity !== "error");
  const shown = filter === "error" ? errs : filter === "warning" ? warns : issues;
  main.innerHTML = `<div class="page">${pageHeader({ title: "Reconciliation", sub: "Compares what Proxmox records with what exists on the arrays: volumes, mappings, snapshots, capacity and alerts." })}
    <div class="kpi-row" style="grid-template-columns:repeat(3,minmax(0,1fr))">
      <div class="kpi"><div class="k">${status("err", "Errors")}</div><div class="v">${errs.length}</div></div>
      <div class="kpi"><div class="k">${status("warn", "Warnings")}</div><div class="v">${warns.length}</div></div>
      <div class="kpi"><div class="k">${status(issues.length ? (errs.length ? "err" : "warn") : "ok", "Status")}</div><div class="v" style="font-size:1.25rem">${issues.length ? "Needs attention" : "Consistent"}</div></div></div>
    <div id="t"></div></div>`;
  if (!issues.length) { $("#t").innerHTML = emptyState("checkmark--outline", "Everything is consistent", "Volumes, mappings and snapshots match between Proxmox and the arrays. Nothing to do."); return; }
  mount($("#t"), dataTable({
    actions: `<div style="display:flex;align-items:center;padding:0 1rem">${contentSwitcher("sevsw", [["all", `All (${issues.length})`], ["error", `Errors (${errs.length})`], ["warning", `Warnings (${warns.length})`]], filter)}</div>`,
    columns: [
      { t: "Severity", text: i => i.severity, sortVal: i => i.severity === "error" ? 0 : 1, render: i => i.severity === "error" ? status("err", "Error") : status("warn", "Warning") },
      { t: "Storage", key: "storage" },
      { t: "Type", key: "kind" },
      { t: "Object", key: "object", render: i => `<span class="mono">${esc(i.object)}</span>` },
      { t: "Detail", key: "message" },
    ], rows: shown.slice().sort((a, b) => (a.severity === "error" ? 0 : 1) - (b.severity === "error" ? 0 : 1)),
  }));
  $$("#sevsw button").forEach(b => b.onclick = () => location.hash = `#/reconcile/${b.dataset.v}`);
}

/* ======================================================================
   Performance
   ====================================================================== */
async function pagePerformance(id, hours = "6") {
  const sts = (await api("GET", "/storages")).filter(s => s.array_ok);
  if (!sts.length) { main.innerHTML = `<div class="page">${pageHeader({ title: "Performance" })}${emptyState("chart--line", "No data", "No storage reachable by the panel.")}</div>`; return; }
  if (!id || !sts.some(s => s.config.storage === id)) id = sts[0].config.storage;
  const h = +hours || 6;
  const data = await api("GET", `/perf/${encodeURIComponent(id)}?hours=${h}`);
  main.innerHTML = `<div class="page">${pageHeader({ title: "Performance", sub: "Array statistics (lssystemstats) recorded by the panel every minute. 7 days of history." })}
    <div style="display:flex;gap:1.5rem;align-items:flex-end;flex-wrap:wrap;margin-bottom:1.5rem">
      <div style="min-width:16rem">${selectInput("p-st", "Storage", sts.map(s => ({ v: s.config.storage, t: `${s.config.storage} · ${s.system?.name || ""}`, sel: s.config.storage === id })))}</div>
      <div><span class="cds--label" style="display:block">Period</span>${contentSwitcher("p-h", [["1", "1 hour"], ["6", "6 hours"], ["24", "24 hours"], ["168", "7 days"]], String(h))}</div></div>
    <div class="g g-2" id="charts"></div></div>`;
  const C = [
    ["Volume latency", "ms", [["vdisk_r_ms", "Read"], ["vdisk_w_ms", "Write"]]],
    ["Volume throughput", "MB/s", [["vdisk_r_mb", "Read"], ["vdisk_w_mb", "Write"]]],
    ["Volume IOPS", "", [["vdisk_r_io", "Read"], ["vdisk_w_io", "Write"]]],
    ["Array CPU", "%", [["cpu_pc", "CPU"]]],
    ["Fibre Channel", "MB/s", [["fc_mb", "Total"]]],
    ["Cache", "%", [["write_cache_pc", "Write"], ["total_cache_pc", "Total"]]],
  ];
  const box = $("#charts");
  C.forEach(([title, unit, s]) => { const el = document.createElement("div"); el.className = "chart"; box.appendChild(el); lineChart(el, { title, unit, hours: h, series: s.map(([k, label]) => ({ label, pts: data[k] || [] })) }); });
  $("#p-st").onchange = () => location.hash = `#/performance/${encodeURIComponent($("#p-st").value)}/${h}`;
  $$("#p-h button").forEach(b => b.onclick = () => location.hash = `#/performance/${encodeURIComponent(id)}/${b.dataset.v}`);
}

/* ======================================================================
   Proxmox nodes
   ====================================================================== */
async function pageNodes() {
  const nodes = await api("GET", "/nodes");
  main.innerHTML = `<div class="page">${pageHeader({ title: "Proxmox nodes", sub: "Packages, multipath, plugin and snapshot backup on each node." })}
    <div class="g" style="grid-template-columns:1fr">${nodes.map(nodeCard).join("")}</div></div>`;
  $$("[data-trust]").forEach(b => b.onclick = () => trustNode(b.dataset.trust));
  $$("[data-prep]").forEach(b => b.onclick = () => prepareNode(nodes.find(n => n.node === b.dataset.prep)));
}
function nodeCard(n) {
  const online = n.online ? status("ok", "Online") : status("err", "Offline");
  const title = `<h3 class="tile-title"><span style="display:flex;gap:.5rem;align-items:center">${icon("server--proxy", 20)} ${esc(n.node)}</span>${online}</h3>`;
  if (!n.managed) return `<section class="tile node-card">${title}<p class="muted node-sub"><span class="mono">${esc(n.ip)}</span></p>
    ${emptyState("locked", "Node not authorized yet", "To install packages, multipath and the plugin, the panel needs SSH access to the node. The root password is used once to install the panel key and is not stored.",
      ME.admin ? btn("Authorize the panel", "primary", `data-trust="${esc(n.node)}"`, "password") : "")}</section>`;
  const pk = n.packages || {}, plugOk = n.plugin && n.plugin === n.plugin_current;
  const ready = plugOk && n.multipath_ok && n.lvm_filter_ok && pk["multipath-tools"] && pk["sg3-utils"] && n.multipathd === "active";
  return `<section class="tile node-card">${title}<p class="muted node-sub"><span class="mono">${esc(n.ip)}</span> · ${esc((String(n.pveversion).match(/pve-manager\/([\d.]+)/) || [])[1] ? "Proxmox VE " + String(n.pveversion).match(/pve-manager\/([\d.]+)/)[1] : n.pveversion)} ${esc((String(n.pveversion).match(/kernel: (\S+)\)/) || [])[1] ? "· kernel " + String(n.pveversion).match(/kernel: (\S+)\)/)[1] : "")}</p>
    ${ready ? notification("ok", "Ready for IBM storages") : notification("warn", "Preparation pending", "Use <b>Prepare node</b> to finish.")}
    ${n.guest_vgs && !n.lvm_filter_ok ? notification("err", "The host can see the LVM inside the VMs", "Apply the <b>LVM filter</b> in Prepare node. Without it, the host can activate VM volumes and corrupt data.") : ""}
    <div class="node-grid">
      <div><h4 class="node-h">Preparation</h4>${sl([
        ["Packages", status(pk["multipath-tools"] && pk["sg3-utils"] ? "ok" : "warn", pk["multipath-tools"] && pk["sg3-utils"] ? "multipath-tools, sg3-utils" : "pending")],
        ["Multipath", status(n.multipathd === "active" && n.multipath_ok ? "ok" : n.multipathd === "active" ? "warn" : "err", n.multipathd !== "active" ? (n.multipathd || "stopped") : n.multipath_ok ? "active, IBM 2145 rule" : "active, no IBM rule")],
        ["LVM filter", status(n.lvm_filter_ok ? "ok" : "err", n.lvm_filter_ok ? "IBM volumes ignored" : "host reads the VMs' LVM")],
        ["Plugin", n.plugin ? status(plugOk ? "ok" : "warn", plugOk ? `v${n.plugin}` : `v${n.plugin} → v${n.plugin_current}`) : status("warn", "not installed")],
        ["Snapshot backup", n.cbt ? status(n.cbt === ME.version ? "ok" : "warn", n.cbt === ME.version ? `v${n.cbt}` : `v${n.cbt} → v${ME.version}`) : status("warn", "not installed")],
        ["Storage API", esc(String(n.apiver || "").replace(/^(\d+) (\d+)$/, "$1 (age $2)"))]])}</div>
      <div><h4 class="node-h">Fibre Channel</h4>${(n.fc || []).length ? `<table class="mini node-fc"><thead><tr><th>Port</th><th>WWPN</th><th>State</th><th class="num">Tx</th><th class="num">Rx</th><th class="num">Temp.</th></tr></thead><tbody>
        ${n.fc.map(f => `<tr><td>${esc(f.host)}</td><td class="mono">${esc(f.wwpn)}</td><td>${status(f.state === "Online" ? "ok" : "err", f.state === "Online" ? f.speed || "online" : f.state)}</td>
          <td class="num">${f.sfp && f.sfp.ddm ? `${num(f.sfp.tx_dbm, 1)} dBm` : "–"}</td><td class="num">${f.sfp && f.sfp.ddm ? `${num(f.sfp.rx_dbm, 1)} dBm` : "–"}</td><td class="num">${f.sfp && f.sfp.ddm ? `${num(f.sfp.temp_c, 0)} °C` : "–"}</td></tr>`).join("")}</tbody></table>` : `<p class="muted">No FC ports.</p>`}
        <h4 class="node-h" style="margin-top:1.25rem">Identity and paths</h4>${sl([
        ["IBM LUNs", `${esc(n.mpaths)} multipath`],
        ["iSCSI IQN", `<span class="mono iqn">${esc(n.iqn || "–")}</span>`],
        ["NVMe/TCP", n.nvme && n.nvme.cli ? `${status(n.nvme.tcp_module ? "ok" : "warn", n.nvme.tcp_module ? `${n.nvme.controllers.length} IBM controllers` : "nvme-tcp module not loaded")} <span class="mono iqn">${esc(n.nvme.nqn || "no NQN")}</span>` : `<span class="muted">not prepared</span>`]])}</div></div>
    ${ME.admin ? `<div style="margin-top:1rem">${btn(ready ? "Update node" : "Prepare node", ready ? "tertiary" : "primary", `data-prep="${esc(n.node)}"`, "settings")}</div>` : ""}</section>`;
}
async function trustNode(node) {
  let hk; try { hk = await api("GET", `/nodes/${encodeURIComponent(node)}/hostkey`); } catch (e) { return toast("err", "Could not connect", e.message); }
  const kt = hk.type.replace("ssh-", "").replace(/ecdsa.*/, "ecdsa");
  await modal({ label: node, title: "Authorize the panel on this node", size: "md",
    body: `<p>The panel will install its SSH key on the node. The root password is used only now, to install the key, and is not stored.</p>
      ${sl([["Node fingerprint", `<span class="mono">${esc(hk.type)} ${esc(hk.fingerprint)}</span>`], ["Check on the node", `<span class="mono">ssh-keygen -lf /etc/ssh/ssh_host_${esc(kt)}_key.pub</span>`]])}
      ${textInput("np", "Node root password", 'type="password" autocomplete="off"')}`,
    buttons: [{ id: "no", label: "Cancel" }, { id: "go", label: "Authorize", kind: "primary" }],
    onButton: async (id, root) => {
      if (id !== "go") return false;
      try { const rt = await busy($("[data-b=go]", root), () => api("POST", `/nodes/${encodeURIComponent(node)}/trust`, { password: $("#np").value, fingerprint: hk.fingerprint })); toast("ok", "Node authorized", node); for (const sid of (rt && rt.imported) || []) toast("ok", "Credential imported", sid); main.dataset.route = ""; route(); return false; }
      catch (e) { toast("err", "Could not authorize", e.message); return true; }
    } });
}
async function prepareNode(n) {
  const pk = n.packages || {};
  const diff = (n.multipath_diff || "").split("\n").map(l => `<span class="${l.startsWith("+++") || l.startsWith("---") || l.startsWith("@@") ? "hd" : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : ""}">${esc(l)}</span>`).join("\n");
  await modal({ label: n.node, title: "Prepare node", size: "lg",
    body: `<div class="form-stack">
      ${checkbox("pp-pk", "Install the packages <b>multipath-tools</b> and <b>sg3-utils</b>", !(pk["multipath-tools"] && pk["sg3-utils"]))}
      ${checkbox("pp-mp", "Write the <b>/etc/multipath.conf</b> recommended for IBM 2145, backing up the current one", !n.multipath_ok)}
      <details><summary class="cds--link" style="cursor:pointer">View the multipath.conf differences</summary><pre class="term" style="margin-top:.75rem">${diff || "No differences."}</pre></details>
      ${checkbox("pp-lvm", "Apply the <b>LVM filter</b>: the host stops reading LVM inside the IBM volumes (VM disks), backing up lvm.conf", !n.lvm_filter_ok)}
      ${checkbox("pp-nv", "Prepare <b>NVMe over TCP</b>: nvme-cli, the nvme-tcp module, the node NQN and a round-robin path policy (native multipath)", false)}
      ${checkbox("pp-cbt", "Install or update the <b>array snapshot backup</b> (QEMU CBT + FlashCopy → PBS) and the python3-libnbd dependency", n.cbt !== ME.version)}
      ${checkbox("pp-pl", `Install or update the plugin (v${esc(n.plugin_current)}) and restart the Proxmox services`, n.plugin !== n.plugin_current)}</div>
      ${notification("info", "Running VMs are not affected.", "The Proxmox GUI reconnects by itself a few seconds after pveproxy restarts.")}<div id="pp-log"></div>`,
    buttons: [{ id: "no", label: "Close" }, { id: "go", label: "Run", kind: "primary" }],
    onButton: async (id, root) => {
      if (id !== "go") { main.dataset.route = ""; route(); return false; }
      const body = { packages: $("#pp-pk").checked, multipath: $("#pp-mp").checked, lvmfilter: $("#pp-lvm").checked, cbt: $("#pp-cbt").checked, plugin: $("#pp-pl").checked, nvme: $("#pp-nv").checked };
      if (!body.packages && !body.multipath && !body.lvmfilter && !body.cbt && !body.plugin && !body.nvme) { toast("warn", "Check at least one step"); return true; }
      let r; try { r = await busy($("[data-b=go]", root), () => api("POST", `/nodes/${encodeURIComponent(n.node)}/prepare`, body)); } catch (e) { r = e.data || { ok: false, error: e.message, log: [] }; }
      $("#pp-log").innerHTML = (r.ok ? notification("ok", "Done") : notification("err", "Failed", esc(r.error))) +
        `<pre class="term">${(r.log || []).map(s => `<span class="hd">## ${esc(s.step)} (rc=${s.rc})</span>\n${esc(s.out)}`).join("\n")}</pre>`;
      $("#pp-log").scrollIntoView({ block: "nearest" });
      return true;
    } });
}

/* ====================================================================== */
/* ---- 8.5.2+: Protection tab (volume group snapshots, Safeguarded, replication) ---- */
function replCell(r) {
  if (!r) return `<span class="muted">–</span>`;
  if (r.error) return status("warn", "Query failed");
  const m = r.local_mode, healthy = r.local_status === "healthy" || !r.local_status;
  const st = r.recovery_test ? status("info", "DR test in progress") : m === "production" ? status(healthy && r.link !== "disconnected" ? "ok" : "warn", "Production")
    : m === "recovery" ? status(healthy ? "ok" : "warn", "Recovery copy") : m === "independent" ? status("warn", "Independent") : status("warn", m || "?");
  return `${st}<div class="helper"><span>Peer:</span> <b>${esc(r.remote_system || "?")}</b> · <span>${r.within_rpo === "yes" ? "within RPO" : r.within_rpo === "no" ? "outside RPO" : "RPO not reported"}</span></div>`;
}
async function protectionTab(el, id) {
  el.innerHTML = `<div class="tab-loading"><span class="busy-ico"></span><span>Loading…</span></div>`;
  const sid = encodeURIComponent(id);
  const P = await api("GET", `/storages/${sid}/protection`), C = P.capabilities;
  const reload = () => { main.dataset.route = ""; route(); };
  const act = async (msg, fn) => { try { await fn(); toast("ok", msg); reload(); } catch (e) { toast("err", "The operation failed", e.message); } };
  const snapRows = g => g.snapshots.length ? `<table class="mini"><thead><tr><th>Snapshot</th><th>Created</th><th>Expires</th><th>Type</th><th></th></tr></thead><tbody>${g.snapshots.map(s => `<tr>
      <td class="mono">${esc(s.name)}</td><td>${esc(fcTime(s.time))}</td><td>${s.expires ? esc(fcTime(s.expires)) : `<span class="muted">–</span>`}</td>
      <td>${s.safeguarded ? tag("Safeguarded", "purple", "Immutable until it expires") : tag("Snapshot", "neutral")} ${s.auto ? tag("policy", "info") : ""}${s.state && s.state !== "active" ? " " + tag(s.state, "warn") : ""}${s.anomaly && !/^(none|no|normal)$/i.test(s.anomaly) ? " " + tag(s.anomaly, "red", "Anomaly detection of the array (possible ransomware)") : ""}</td>
      <td class="act nowrap">${ME.admin ? (P.snapmode === "volumegroup" && C.vg_clone && g.vm_name != null ? btn("New VM", "ghost", `data-sn="${esc(g.name)}|${esc(s.name)}"`, "copy", "sm") : "") + btn("Restore", "ghost", `data-sr="${esc(g.name)}|${esc(s.name)}"`, "undo", "sm") + (s.safeguarded ? "" : btn("Delete", "ghost", `data-sd="${esc(g.name)}|${esc(s.name)}"`, "trash-can", "sm")) : ""}</td></tr>`).join("")}</tbody></table>`
    : `<p class="muted">No snapshots in this volume group.</p>`;
  el.innerHTML = `${P.partition ? `<section class="tile" style="margin-bottom:1rem"><h3 class="tile-title">Storage partition <span class="mono">${esc(P.partition.name)}</span></h3>${P.partition.missing ? notification("err", "The partition was not found on the array") : sl([
        ["Partition replication", P.partition.replication_policy ? `${tag(P.partition.replication_policy, "info")} <span class="helper">${esc(P.partition.ha_status)} ${esc(P.partition.link_status)}</span>` : `<span class="muted">none</span>`],
        P.partition.active_management ? ["Active management system", `<b data-noi18n>${esc(P.partition.active_management)}</b>`] : null,
        ["Hosts / volume groups", `${esc(P.partition.hosts ?? "–")} / ${esc(P.partition.volume_groups ?? "–")}`]])}
      ${C.readonly_recovery ? `<p class="helper" style="margin-top:.75rem">Firmware 9.1+: inside a partition, the recovery copy stays online and read-only; it accepts writes after a failover or during a DR test.</p>` : ""}</section>` : ""}
    ${P.snapmode !== "volumegroup" ? notification("warn", "This storage uses FlashCopy as its snapshot engine", "Per-VM volume groups are only created when the storage is registered with the “volume group” engine. Safeguarded snapshots and policy-based replication depend on them.") : ""}<div id="pp" style="margin-bottom:1.5rem"></div><div id="pt"></div>`;
  const every = s => `${tr("every")} ${s.interval} ${tr(s.unit + (+s.interval > 1 ? "s" : ""))}`;
  mount($("#pp", el), dataTable({
    title: "Snapshot policies", desc: `Schedules kept by the array itself: it takes the snapshots and deletes them when the retention ends. Times follow the array clock (${esc(P.time_zone || "UTC")}).`,
    actions: ME.admin && C.vg_snapshot ? btn("Create policy", "primary", `data-pnew`, "add", "sm") : "",
    columns: [
      { t: "Policy", key: "name", render: p => `<b>${esc(p.name)}</b> ${p.predefined ? tag("array default", "neutral") : ""}` },
      { t: "Frequency", text: p => p.schedules.filter(x => x.type === "localsnapshot").map(every).join(", "), render: p => esc(p.schedules.filter(x => x.type === "localsnapshot").map(every).join(" · ") || "–") },
      { t: "Kept for", text: p => p.schedules.map(x => x.retention_days).join(","), render: p => esc(p.schedules.filter(x => x.type === "localsnapshot").map(x => `${x.retention_days} ${tr(+x.retention_days > 1 ? "days" : "day")}`).join(" · ") || "–") },
      { t: "Volume groups", num: 1, key: "volume_groups" },
      { act: 1, render: p => ME.admin && !p.predefined ? btn("", "ghost", `data-pdel="${esc(p.name)}" title="${esc(tr("Delete policy"))}"`, "trash-can", "sm") : "" },
    ], rows: (P.snapshot_policies || []).map(p => ({ ...p, __key: p.name })), search: false, empty: "No snapshot policies on the array.",
  }));
  mount($("#pt", el), dataTable({
    title: "VM volume groups", desc: "One volume group per VM: snapshots (including Safeguarded) and replication apply to all of the VM's disks at once.",
    columns: [
      { t: "VM", sortVal: g => g.vmid, text: g => `${g.vmid} ${g.vm_name || ""}`, render: g => `<b>${g.vmid}</b> ${esc(g.vm_name || "")}${g.vm_name == null ? ` ${tag("no VM at this site", "warn")}` : ""}` },
      { t: "Volume group", key: "name", render: g => `<span class="mono">${esc(g.name)}</span>` },
      { t: "Volumes", num: 1, key: "volumes" },
      { t: "Snapshot policy", text: g => g.snapshot_policy, render: g => g.snapshot_policy ? `${tag(g.snapshot_policy, "info")} ${g.policy_safeguarded ? tag("Safeguarded", "purple") : ""} ${g.policy_suspended ? tag("suspended", "warn") : ""}${g.policy_next ? `<div class="helper">${esc(tr("next"))}: ${esc(fcTime(g.policy_next))}</div>` : ""}` : `<span class="muted">–</span>` },
      { t: "Snapshots", num: 1, sortVal: g => g.snapshots.length, render: g => `${g.snapshots.length}${g.snapshots.some(s => s.safeguarded) ? ` <span class="helper">(${g.snapshots.filter(s => s.safeguarded).length} Safeguarded)</span>` : ""}` },
      { t: "Replication", text: g => g.replication ? g.replication.local_mode : "", render: g => replCell(g.replication) },
      { act: 1, render: g => ME.admin ? ovfButton(`data-pg="${esc(g.name)}"`, `Actions for ${g.name}`) : "" },
    ], rows: P.groups.map(g => ({ ...g, __key: g.name })), expand: snapRows, empty: "No VM volume groups on this storage.",
  }));
  const G = n => P.groups.find(g => g.name === n);
  el.addEventListener("click", async e => {
    const pg = e.target.closest("[data-pg]"), sr = e.target.closest("[data-sr]"), sd = e.target.closest("[data-sd]"), sn = e.target.closest("[data-sn]");
    const pnew = e.target.closest("[data-pnew]"), pdel = e.target.closest("[data-pdel]");
    if (pnew) {
      policyCreateFlow(`/storages/${sid}`, P, reload);
    } else if (pdel) {
      const name = pdel.dataset.pdel, pol = (P.snapshot_policies || []).find(p => p.name === name);
      if (await confirmDialog({ label: name, title: `Delete policy “${name}”?`, danger: true, ok: "Delete", body: `<p>The array stops taking snapshots with this policy${pol && pol.volume_groups ? ` and removes it from ${pol.volume_groups} volume group(s)` : ""}. Snapshots already taken stay until they expire; Safeguarded ones cannot be deleted before that.</p>` }))
        act("Policy deleted", () => api("DELETE", `/storages/${sid}/snapshot-policies/${encodeURIComponent(name)}`));
    } else if (sn) {
      const [vg, snap] = sn.dataset.sn.split("|");
      vgNewVmFlow(`/storages/${sid}/vg/${encodeURIComponent(vg)}/snapshots/${encodeURIComponent(snap)}/newvm`, G(vg), snap);
    } else if (sr) {
      const [vg, sn] = sr.dataset.sr.split("|");
      if (await confirmDialog({ label: vg, title: `Restore “${sn}”?`, danger: true, ok: "Restore", body: `<p>All disks in the volume group go back to the state of this snapshot. The VM must be powered off.</p><p class="helper">On the array: restorefromsnapshot.</p>` }))
        act("Snapshot restored", () => api("POST", `/storages/${sid}/vg/${encodeURIComponent(vg)}/snapshots/${encodeURIComponent(sn)}/restore`));
    } else if (sd) {
      const [vg, sn] = sd.dataset.sd.split("|");
      if (await confirmDialog({ label: vg, title: `Delete “${sn}”?`, danger: true, ok: "Delete", body: `<p>The snapshot will be removed from the array.</p>` }))
        act("Snapshot deleted", () => api("DELETE", `/storages/${sid}/vg/${encodeURIComponent(vg)}/snapshots/${encodeURIComponent(sn)}`));
    } else if (pg) {
      const g = G(pg.dataset.pg), r = g.replication && !g.replication.error ? g.replication : null, vgp = `/storages/${sid}/vg/${encodeURIComponent(g.name)}`;
      const repl = (action, title, body, ok) => async () => { if (await confirmDialog({ label: g.name, title, body, ok, danger: true })) act("Command accepted by the array", () => api("POST", `${vgp}/replication`, { action })); };
      openMenu(pg, [
        { label: "Take snapshot…", icon: "camera", run: () => vgSnapFlow(vgp, g, C, reload) },
        { label: "Snapshot policy…", icon: "time", run: () => vgPolicyFlow(vgp, g, P, "snapshot", reload) },
        C.pbr ? { label: "Replication policy…", icon: "connect", run: () => vgPolicyFlow(vgp, g, P, "replication", reload) } : null,
        r ? "-" : null,
        r && r.local_mode === "recovery" && C.recovery_test && !r.recovery_test ? { label: "Start DR test", icon: "launch", run: repl("test-start", "Start DR test?", "<p>The recovery volumes become accessible for testing while replication continues. Anything written during the test is discarded when it ends.</p>", "Start test") } : null,
        r && r.recovery_test ? { label: "End DR test", icon: "undo", run: repl("test-stop", "End DR test?", "<p>Changes made during the test are discarded and the volumes become a recovery copy again.</p>", "End test") } : null,
        r && r.local_mode === "recovery" ? { label: "Enable access (failover)…", icon: "power", danger: true, run: repl("failover", "Fail over this volume group?", "<p>Replication is suspended and this copy starts accepting reads and writes (independent mode). Use it when production is down.</p><p class=\"helper\">To recreate the VM at this site, use the DR page.</p>", "Fail over") } : null,
        r && r.local_mode === "independent" ? { label: "Make production and resume replication…", icon: "renew", danger: true, run: repl("restart", "Make this site production?", "<p>Replication restarts from this copy. The content at the other site is overwritten.</p>", "Resume replication") } : null,
      ].filter(Boolean));
    }
  });
}
async function vgNewVmFlow(url, g, snap) {
  const [nid, nodes] = await Promise.all([api("GET", "/nextid"), api("GET", "/nodes")]);
  const online = nodes.filter(n => n.online), here = online.find(n => n.node === g.vm_node) || online[0];
  const helpT = {
    thinclone: "Ready at once and takes no space up front: the new VM shares the unchanged data with the snapshot. The array keeps the snapshot while this VM exists, even if it is deleted. Good for tests, DR drills and quick copies.",
    clone: "Ready at once: the array copies the data in the background, without going through the network or the nodes. When the copy ends the VM no longer depends on the snapshot. Good for a permanent copy.",
  };
  let kind = "thinclone";
  await modal({ label: `${g.name} · ${snap}`, title: "New VM from snapshot", size: "md",
    body: `<div class="form-stack">
      <div><span class="cds--label" style="display:block">Type</span>${contentSwitcher("nv-t", [["thinclone", "Thinclone"], ["clone", "Clone"]], kind)}
        <p class="helper" id="nv-h" style="margin-top:.5rem">${esc(helpT[kind])}</p></div>
      ${textInput("nv-id", "VMID", `type="number" min="100" value="${esc(nid.vmid)}"`)}
      ${textInput("nv-n", "Name", `value="${esc(((g.vm_name || "vm" + g.vmid) + "-" + snap).replace(/[^A-Za-z0-9.-]/g, "-").slice(0, 63))}" maxlength="63" autocapitalize="off" spellcheck="false"`)}
      ${selectInput("nv-node", "Node", online.map(n => ({ v: n.node, t: n.node, sel: here && n.node === here.node })))}
      ${checkbox("nv-s", "Start the VM when it is ready")}</div>
      ${notification("info", "Network addresses", "The new VM gets new MAC addresses, but keeps the same IP configuration inside the guest and the same cloud-init settings. Change them before starting it next to the original.", true)}`,
    onOpen: root => $$("#nv-t button", root).forEach(b => b.onclick = () => {
      kind = b.dataset.v;
      $$("#nv-t button", root).forEach(x => { const on = x === b; x.classList.toggle("cds--content-switcher--selected", on); x.setAttribute("aria-selected", on); });
      $("#nv-h", root).textContent = tr(helpT[kind]);
    }),
    buttons: [{ id: "no", label: "Cancel" }, { id: "go", label: "Create VM", kind: "primary" }],
    onButton: async (id, root) => {
      if (id !== "go") return false;
      const body = { type: kind, vmid: +$("#nv-id").value, name: $("#nv-n").value.trim(), node: $("#nv-node").value, start: $("#nv-s").checked };
      try {
        const r = await busy($("[data-b=go]", root), () => api("POST", url, body));
        toast("ok", `VM ${r.vmid} created`, r.skipped.length ? `${tr("Left out (other storages):")} ${r.skipped.join(", ")}` : "");
        main.dataset.route = ""; route(); return false;
      } catch (e) { toast("err", "Could not create the VM", e.message); return true; }
    } });
}
async function vgSnapFlow(vgp, g, C, done) {
  const def = "snap" + new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
  await modal({ label: g.name, title: "Take snapshot", size: "sm",
    body: `<div class="form-stack">${textInput("vs-n", "Name", `value="${def}" maxlength="40" autocapitalize="off" spellcheck="false"`)}
      ${C.safeguarded ? checkbox("vs-s", "Safeguarded (immutable until it expires)") + textInput("vs-d", "Retention (days)", `type="number" min="1" max="3650" value="7"`, "Before that, only an array Security Administrator can delete it.") : ""}</div>`,
    buttons: [{ id: "no", label: "Cancel" }, { id: "go", label: "Take snapshot", kind: "primary" }],
    onButton: async (id, root) => {
      if (id !== "go") return false;
      const safe = !!($("#vs-s") && $("#vs-s").checked);
      try { await busy($("[data-b=go]", root), () => api("POST", `${vgp}/snapshots`, { name: $("#vs-n").value.trim(), safeguarded: safe, days: safe ? +$("#vs-d").value : null })); toast("ok", "Snapshot created"); done(); return false; }
      catch (e) { toast("err", "Could not create the snapshot", e.message); return true; }
    } });
}
async function policyCreateFlow(base, P, done) {
  const groups = P.groups.filter(g => !g.snapshot_policy);
  await modal({ label: "Snapshot policy", title: "Create policy", size: "md",
    body: `<div class="form-stack">${textInput("pc-n", "Name", `maxlength="63" placeholder="vm-hourly-7d" autocapitalize="off" spellcheck="false"`)}
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem">${textInput("pc-i", "Every", `type="number" min="1" value="4"`)}
        ${selectInput("pc-u", "Unit", [["hour", "hours"], ["day", "days"], ["week", "weeks"], ["month", "months"], ["minute", "minutes (60 or more)"]].map(([v, t]) => ({ v, t, sel: v === "hour" })))}</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem">${textInput("pc-r", "Keep for (days)", `type="number" min="1" max="3650" value="7"`)}
        ${textInput("pc-s", "First snapshot at (HH:MM, optional)", `placeholder="22:00" maxlength="5"`, `${esc(tr("Array clock"))}: ${esc(P.time_zone || "UTC")}`)}</div>
      ${P.capabilities.safeguarded ? checkbox("pc-sg", "Safeguarded: the snapshots are immutable until they expire", true) : ""}
      <div><span class="cds--label" style="display:block">${esc(tr("Apply to the VMs"))}</span>${groups.length ? `<div class="pc-vms" style="max-height:12rem;overflow:auto">${groups.map(g => checkbox(`pc-g-${g.vmid}`, `<b>${g.vmid}</b> ${esc(g.vm_name || "")} <span class="helper mono">${esc(g.name)}</span>`, false, `data-vg="${esc(g.name)}"`)).join("")}</div>` : `<p class="helper">${esc(tr("Every VM volume group already has a policy."))}</p>`}</div></div>
      ${notification("warn", "Safeguarded snapshots", "Only an array Security Administrator can delete them before they expire, and they count against the pool capacity. Size the retention with that in mind.", true)}`,
    buttons: [{ id: "no", label: "Cancel" }, { id: "go", label: "Create", kind: "primary" }],
    onButton: async (id, root) => {
      if (id !== "go") return false;
      const body = { name: $("#pc-n").value.trim(), interval: +$("#pc-i").value, unit: $("#pc-u").value, retention_days: +$("#pc-r").value,
        start: $("#pc-s").value.trim() || null, safeguarded: !!($("#pc-sg") && $("#pc-sg").checked),
        volume_groups: $$("[data-vg]", root).filter(x => x.checked).map(x => x.dataset.vg) };
      try {
        const r = await busy($("[data-b=go]", root), () => api("POST", `${base}/snapshot-policies`, body));
        toast(r.failed.length ? "warn" : "ok", "Policy created", r.assigned.length ? `${tr("Applied to")} ${r.assigned.join(", ")}` : "");
        r.failed.forEach(f => toast("err", f.vg, f.error));
        done(); return false;
      } catch (e) { toast("err", "Could not create the policy", e.message); return true; }
    } });
}
async function vgPolicyFlow(vgp, g, P, kind, done) {
  const snap = kind === "snapshot", list = snap ? P.snapshot_policies : P.replication_policies, cur = snap ? g.snapshot_policy : (g.replication && g.replication.policy) || "";
  const opts = [{ v: "", t: "None" }].concat(list.map(p => ({ v: p.name, t: p.name, sel: p.name === cur })));
  await modal({ label: g.name, title: snap ? "Snapshot policy" : "Replication policy", size: "sm",
    body: `<div class="form-stack">${selectInput("vp-p", "Policy", opts, list.length ? "" : "No policy exists on the array. Create the policy in the array GUI and come back.")}
      ${snap && P.capabilities.safeguarded ? checkbox("vp-s", "Create the snapshots as Safeguarded", g.policy_safeguarded) : ""}
      ${snap ? textInput("vp-t", "First snapshot at (HH:MM, optional)", `placeholder="22:00" maxlength="5"`, `${esc(tr("Array clock"))}: ${esc(P.time_zone || "UTC")}`) : ""}</div>
      ${snap ? "" : `<p class="helper">When the policy is assigned, the array creates and synchronizes the copy on the other system. When it is removed, the recovery copy is deleted.</p>`}`,
    buttons: [{ id: "no", label: "Cancel" }, { id: "go", label: "Apply", kind: "primary" }],
    onButton: async (id, root) => {
      if (id !== "go") return false;
      const body = snap ? { snapshot_policy: $("#vp-p").value, safeguarded: !!($("#vp-s") && $("#vp-s").checked), start: ($("#vp-t") && $("#vp-t").value.trim()) || null } : { replication_policy: $("#vp-p").value };
      try { await busy($("[data-b=go]", root), () => api("POST", `${vgp}/policy`, body)); toast("ok", "Policy applied"); done(); return false; }
      catch (e) { toast("err", "Could not apply the policy", e.message); return true; }
    } });
}

/* ---- DR page: cross-site inventory, DR test and failover -------------------- */
async function pageDR() {
  const D = await api("GET", "/dr"), reload = () => { main.dataset.route = ""; route(); };
  const ex = D.export, p = D.peer;
  const head = pageHeader({ crumbs: [["DR"]], title: `${icon("connect", 24)} Disaster recovery`,
    sub: "Replication is done by the array (policy-based replication, firmware 8.5.2+). The panel keeps the VM configuration and recreates each VM at the other site." });
  main.innerHTML = `<div class="page">${head}${notification("info", "Feature not yet validated on hardware", "Run a DR test with a lab VM first.")}
    <div class="g g-2" style="margin-bottom:1.5rem"><section class="tile"><h3 class="tile-title">This site as production</h3>
      ${sl([["VM inventory", ex.generated ? `<span>${ex.vms} VMs · updated ${ago(ex.generated)} ago</span>` : `<span class="muted">not collected yet</span>`],
        ["Export token", ex.token_set ? status("ok", "Created") : status("neutral", "Not created")]])}
      <p class="helper" style="margin-top:1rem">The DR site panel uses this token to copy the VM configuration every 10 minutes.</p>
      ${ME.admin ? `<div style="margin-top:1rem">${btn(ex.token_set ? "Generate new token" : "Generate token", "tertiary", 'id="dr-tok"', "password")}</div>` : ""}</section>
    <section class="tile"><h3 class="tile-title">This site as DR</h3>
      ${p ? sl([["Production panel", `<span class="mono">${esc(p.url)}</span>`], ["Last copy", p.fetched ? `<span>${ago(p.fetched)} ago</span>` : `<span class="muted">none</span>`],
        p.error ? ["Last error", `<span class="st err">${esc(p.error)}</span>`] : null]) : `<p class="muted">No production panel configured.</p>`}
      ${ME.admin ? `<div style="margin-top:1rem">${btn(p ? "Change" : "Set up production panel", "tertiary", 'id="dr-peer"', "connect")}${p ? btn("Remove", "ghost", 'id="dr-peer-rm"', "trash-can") : ""}</div>` : ""}</section></div><div id="dr-vms"></div></div>`;
  $("#dr-tok")?.addEventListener("click", async e => {
    if (ex.token_set && !await confirmDialog({ title: "Generate a new token?", body: "<p>The current token stops working; the DR site panel must be reconfigured.</p>", ok: "Generate" })) return;
    try {
      const r = await busy(e.currentTarget, () => api("POST", "/dr/token"));
      await modal({ title: "Export token", size: "sm", body: `<p>Copy it now: it is not shown again. In the DR site panel, enter this address and the token.</p>
        <div class="form-stack" style="margin-top:1rem">${textInput("tk-u", "Address of this panel", `readonly value="${esc(location.origin)}"`)}${textInput("tk-t", "Token", `readonly value="${esc(r.token)}"`)}</div>` });
      reload();
    } catch (err) { toast("err", "Could not generate the token", err.message); }
  });
  $("#dr-peer-rm")?.addEventListener("click", async () => {
    if (!await confirmDialog({ title: "Remove the production panel?", body: "<p>The copy of the VM inventory kept at this site is deleted too.</p>", danger: true, ok: "Remove" })) return;
    try { await api("DELETE", "/dr/peer"); reload(); } catch (err) { toast("err", "The operation failed", err.message); }
  });
  $("#dr-peer")?.addEventListener("click", () => drPeerFlow(p, reload));
  if (!p) return;
  const isTest = v => v.local && String(v.local.name || "").endsWith("-drtest");
  mount($("#dr-vms"), dataTable({
    title: "Production site VMs", desc: "DR test: starts an isolated copy (network disconnected) without stopping replication. Failover: suspends replication and recreates the VM here.",
    columns: [
      { t: "VM", sortVal: v => v.vmid, text: v => `${v.vmid} ${v.name}`, render: v => `<b>${v.vmid}</b> ${esc(v.name)}` },
      { t: "Source node", key: "node" },
      { t: "Disks", num: 1, sortVal: v => v.disks.length, render: v => `${v.disks.length}` },
      { t: "Source storage", text: v => v.source_storages.join(" "), render: v => v.source_storages.map(x => tag(x, "neutral")).join(" ") },
      { t: "At this site", text: v => v.local ? v.local.status : "", render: v => !v.local ? `<span class="muted">does not exist</span>` : isTest(v) ? status("info", "DR test VM") : status("ok", "Recovered") },
      { act: 1, render: v => !ME.admin || v.template ? "" : isTest(v) ? btn("End test", "ghost", `data-dts="${v.vmid}"`, "undo", "sm") : v.local ? "" : btn("DR test", "ghost", `data-dt="${v.vmid}"`, "launch", "sm") + btn("Failover", "ghost", `data-df="${v.vmid}"`, "power", "sm") },
    ], rows: D.vms, empty: "The production site inventory has no VMs on plugin storage.",
  }));
  $("#dr-vms").addEventListener("click", async e => {
    const t = e.target.closest("[data-dt]"), f = e.target.closest("[data-df]"), ts = e.target.closest("[data-dts]");
    const vm = D.vms.find(v => String(v.vmid) === (t || f || ts)?.dataset[t ? "dt" : f ? "df" : "dts"]);
    if (!vm) return;
    if (ts) {
      if (!await confirmDialog({ label: `VM ${vm.vmid}`, title: "End DR test?", ok: "End test", danger: true, body: "<p>The test VM is removed from this site and everything it wrote is discarded. Replication is not affected.</p>" })) return;
      try { toast("info", "Ending test…"); await api("POST", "/dr/test-stop", { vmid: vm.vmid, storage_map: vm.suggested }); toast("ok", "DR test ended"); reload(); }
      catch (err) { toast("err", "The operation failed", err.message); }
    } else drRecoverFlow(vm, D, !!t, reload);
  });
}
async function drPeerFlow(p, done) {
  await modal({ title: "Production site panel", size: "sm",
    body: `<div class="form-stack">${textInput("dp-u", "Address", `placeholder="https://production-panel" value="${esc(p ? p.url : "")}" autocapitalize="off" spellcheck="false"`)}
      ${textInput("dp-t", "Export token", `type="password" autocomplete="off"`, "Generated on the DR page of the production panel.")}<div id="dp-fp"></div></div>`,
    buttons: [{ id: "no", label: "Cancel" }, { id: "go", label: "Check certificate", kind: "primary" }],
    onButton: async (id, root) => {
      if (id !== "go") return false;
      const b = $("[data-b=go]", root), url = $("#dp-u").value.trim();
      try {
        if (!root.dataset.fp) {
          const r = await busy(b, () => api("POST", "/dr/peer/probe", { url }));
          root.dataset.fp = r.fingerprint; b.textContent = "Trust and save";
          $("#dp-fp").innerHTML = `<p class="cds--label">Panel certificate (SHA-256)</p><p class="mono" style="word-break:break-all">${esc(r.fingerprint)}</p><p class="helper">Compare it with the certificate your browser shows when opening the production panel.</p>`;
          return true;
        }
        const r = await busy(b, () => api("POST", "/dr/peer", { url, token: $("#dp-t").value.trim(), fingerprint: root.dataset.fp }));
        toast("ok", "Production panel connected"); done(); return false;
      } catch (e) { toast("err", "Could not connect", e.message); return true; }
    } });
}
async function drRecoverFlow(vm, D, test, done) {
  const maps = vm.source_storages.map((s, i) => selectInput(`dm-${i}`, `Local storage for “${s}”`, [{ v: "", t: "Do not recover" }].concat(D.local_storages.map(l => ({ v: l, t: l, sel: vm.suggested[s] === l })))));
  await modal({ label: `VM ${vm.vmid} · ${vm.name}`, title: test ? "DR test" : "Failover", size: "sm", danger: !test,
    body: `${test ? `<p>A copy of the VM is created at this site with the network disconnected, on top of the recovery volumes. Replication continues.</p>`
      : `<p>Replication of this VM is suspended and the VM is recreated at this site on the replicated copy. Do this when production is down, or in a planned switchover with the source VM powered off.</p>`}
      <div class="form-stack" style="margin-top:1rem">${selectInput("dm-n", "Proxmox node", D.nodes.map(n => ({ v: n, t: n })))}${maps.join("")}${checkbox("dm-s", "Start the VM after creating it")}</div>`,
    buttons: [{ id: "no", label: "Cancel" }, { id: "go", label: test ? "Start test" : "Fail over", kind: test ? "primary" : "danger" }],
    onButton: async (id, root) => {
      if (id !== "go") return false;
      const storage_map = {}; vm.source_storages.forEach((s, i) => { const v = $(`#dm-${i}`).value; if (v) storage_map[s] = v; });
      try {
        const r = await busy($("[data-b=go]", root), () => api("POST", "/dr/recover", { vmid: vm.vmid, test, node: $("#dm-n").value, storage_map, start: $("#dm-s").checked }));
        toast("ok", test ? "Test VM created" : "VM recovered at this site", r.skipped.length ? `Disks outside replication: ${r.skipped.join(", ")}` : ""); done(); return false;
      } catch (e) { toast("err", "Recovery failed", e.message); return true; }
    } });
}

/* ---- panel domain and HTTPS certificate ------------------------------------- */
async function pageHttps(tab = "le") {
  const S = await api("GET", "/tls"), c = S.cert;
  const MODE = { selfsigned: ["Self-signed", "neutral"], custom: ["Own certificate", "info"], letsencrypt: ["Let's Encrypt", "ok"], generated: ["Self-signed", "neutral"] }[S.mode] || [S.mode, "neutral"];
  const exp = c ? (c.days_left < 0 ? status("err", "Expired") : c.days_left <= 15 ? status("warn", dt(c.not_after)) : status("ok", dt(c.not_after))) : "–";
  const head = pageHeader({ crumbs: [["Domain and certificate"]], title: `${icon("locked", 24)} Domain and certificate`,
    sub: "Address and HTTPS certificate used to open this panel." });
  const le = `<div class="form-stack">${textInput("le-d", "Domain", `placeholder="storage.example.com" value="${esc(S.domain)}" autocapitalize="off" spellcheck="false"`)}
      ${textInput("le-e", "E-mail for expiry notices (optional)", `type="email" autocomplete="off"`, "Sent only to Let's Encrypt.")}
      ${selectInput("le-m", "Validation", [{ v: "http", t: "HTTP on port 80", sel: S.method !== "cloudflare" }, { v: "cloudflare", t: "DNS at Cloudflare", sel: S.method === "cloudflare" }])}
      <div id="le-http" class="helper">The domain must point to this panel and port 80 must reach it from the internet.</div>
      <div id="le-cf" hidden>${textInput("le-t", "Cloudflare API token", `type="password" autocomplete="off"`, "Permission Zone › DNS › Edit on the domain's zone. Works for a panel with no access from the internet.")}</div>
      ${checkbox("le-s", "Let's Encrypt test environment (untrusted certificate, no issuance limit)", S.staging)}
      <div>${btn("Issue certificate", "primary", 'id="le-go"', "locked")}</div></div>`;
  const own = `<div class="form-stack">${textInput("cu-d", "Domain (optional)", `placeholder="storage.example.com" value="${esc(S.domain)}" autocapitalize="off" spellcheck="false"`, "If given, the panel checks that the certificate is valid for it.")}
      <div class="cds--form-item"><label class="cds--label" for="cu-c">Certificate and chain (PEM)</label><textarea id="cu-c" class="cds--text-area mono" rows="7" spellcheck="false" placeholder="-----BEGIN CERTIFICATE-----"></textarea></div>
      <div class="cds--form-item"><label class="cds--label" for="cu-k">Private key (PEM, no passphrase)</label><textarea id="cu-k" class="cds--text-area mono" rows="7" spellcheck="false" placeholder="-----BEGIN PRIVATE KEY-----"></textarea></div>
      <div>${btn("Install certificate", "primary", 'id="cu-go"', "locked")}</div></div>`;
  const self = `<div class="form-stack">${textInput("ss-d", "Domain", `placeholder="storage.example.lan" value="${esc(S.domain)}" autocapitalize="off" spellcheck="false"`, "The certificate is also valid for the panel's current IP.")}
      ${selectInput("ss-y", "Validity", [{ v: "1", t: "1 year" }, { v: "2", t: "2 years" }, { v: "5", t: "5 years" }, { v: "10", t: "10 years", sel: true }])}
      <p class="helper">The browser shows an untrusted certificate warning until you accept or import the certificate. There is no automatic renewal.</p>
      <div>${btn("Generate certificate", "primary", 'id="ss-go"', "locked")}</div></div>`;
  main.innerHTML = `<div class="page">${head}${S.last_error ? notification("warn", "Last error", esc(S.last_error)) : ""}
    <div class="g g-2"><section class="tile"><h3 class="tile-title">Certificate in use ${tag(MODE[0], MODE[1])}${S.mode === "letsencrypt" && S.staging ? " " + tag("test", "warn") : ""}</h3>
      ${c ? sl([["Issued to", `<span class="mono">${esc(c.subject)}</span>`], ["Names", c.names.length ? c.names.map(n => `<span class="mono">${esc(n)}</span>`).join(", ") : `<span class="muted">–</span>`],
        ["Issued by", `<span data-noi18n>${esc(c.issuer)}</span>`], ["Valid until", exp],
        S.mode === "letsencrypt" ? ["Renewal", `<span>Automatic, ${S.renew_days} days before expiry</span>`] : null,
        ["Fingerprint SHA-256", `<span class="mono" style="word-break:break-all;font-size:.75rem">${esc(c.fingerprint)}</span>`]]) : `<p class="muted">Could not read the certificate.</p>`}
      ${ME.admin && S.mode !== "selfsigned" ? `<div style="margin-top:1rem">${btn("Back to the installation certificate", "ghost", 'id="tls-reset"', "undo")}</div>` : ""}</section>
    <section class="tile"><h3 class="tile-title">Change certificate</h3>${ME.admin ? `${tabs([["le", "Let's Encrypt"], ["own", "Own certificate"], ["self", "Self-signed"]], tab, "data-ht")}<div style="margin-top:1rem">${tab === "own" ? own : tab === "self" ? self : (S.certbot ? le : notification("err", "certbot not installed", "Run the panel deploy again to install certbot."))}</div>` : `<p class="muted">Only administrators can change the certificate.</p>`}</section></div></div>`;
  $$("[data-ht]").forEach(b => b.onclick = () => location.hash = `#/https/${b.dataset.ht}`);
  const done = (r, domain) => modal({ title: "Certificate installed", size: "sm", body: `<p>The panel restarts in a few seconds to use the new certificate. This session will end.</p>
      ${domain ? `<p style="margin-top:1rem">Open <a class="cds--link" href="https://${esc(domain)}/">https://${esc(domain)}/</a></p>` : ""}
      <p class="helper" style="margin-top:1rem">If this panel is the production site of a DR setup using a self-signed or own certificate, redo the connection in the DR site panel.</p>` });
  const m = $("#le-m"); if (m) { const sync = () => { $("#le-cf").hidden = m.value !== "cloudflare"; $("#le-http").hidden = m.value === "cloudflare"; }; m.onchange = sync; sync(); }
  $("#le-go")?.addEventListener("click", async e => {
    const domain = $("#le-d").value.trim();
    try { toast("info", "Requesting the certificate…", "It can take up to two minutes");
      const r = await busy(e.currentTarget, () => api("POST", "/tls/letsencrypt", { domain, email: $("#le-e").value.trim(), method: m.value, cf_token: $("#le-t").value.trim(), staging: $("#le-s").checked })); await done(r, domain); }
    catch (err) { toast("err", "Could not issue the certificate", err.message); }
  });
  $("#cu-go")?.addEventListener("click", async e => {
    const domain = $("#cu-d").value.trim();
    try { const r = await busy(e.currentTarget, () => api("POST", "/tls/custom", { domain, cert: $("#cu-c").value, key: $("#cu-k").value })); await done(r, domain); }
    catch (err) { toast("err", "Certificate rejected", err.message); }
  });
  $("#ss-go")?.addEventListener("click", async e => {
    const domain = $("#ss-d").value.trim();
    try { const r = await busy(e.currentTarget, () => api("POST", "/tls/selfsigned", { domain, years: +$("#ss-y").value })); await done(r, domain); }
    catch (err) { toast("err", "Could not generate the certificate", err.message); }
  });
  $("#tls-reset")?.addEventListener("click", async () => {
    if (!await confirmDialog({ title: "Go back to the installation certificate?", body: "<p>The panel goes back to the certificate created at installation and restarts. Automatic renewal is turned off.</p>", ok: "Back", danger: true })) return;
    try { await api("POST", "/tls/reset"); await done({}, ""); } catch (err) { toast("err", "The operation failed", err.message); }
  });
}

/* ---- Backups: in progress, mounted snapshots, schedules and latest runs ---- */
const DAY_LABEL = { Mon: "Mon", Tue: "Tue", Wed: "Wed", Thu: "Thu", Fri: "Fri", Sat: "Sat", Sun: "Sun" };
function dur(sec) { sec = Math.max(0, Math.round(sec)); return sec < 60 ? `${sec} s` : sec < 3600 ? `${Math.floor(sec / 60)} min ${sec % 60} s` : `${Math.floor(sec / 3600)} h ${Math.floor(sec % 3600 / 60)} min`; }
function schedWhen(c) { return `${c.days.length && c.days.length < 7 ? c.days.map(d => tr(DAY_LABEL[d])).join(", ") : tr("every day")} · ${c.time}`; }
let BK_TIMER = null;
async function pageBackups(tab = "andamento") {
  clearTimeout(BK_TIMER);
  const B = await api("GET", "/backup"), now = Date.now() / 1000;
  const reload = () => { main.dataset.route = ""; route(); };
  const vmCell = r => r.vmid ? `<b>${r.vmid}</b> ${esc(r.name || "")}` : `<span class="muted">–</span>`;
  const badNodes = Object.entries(B.nodes).filter(([, n]) => n.error || !n.installed);
  const head = pageHeader({ crumbs: [["Backups"]], title: `${icon("ibm-cloud--vpc-block-storage-snapshots", 24)} Backups`,
    sub: "Backup from storage snapshot (FlashCopy + changed blocks) to Proxmox Backup Server, plus the regular Proxmox backups in progress.",
    actions: ME.admin ? btn("New schedule", "primary", 'id="bk-new"', "add") : "" });
  const progCell = r => {
    if (r.kind !== "snapshot") return `<span class="muted">see the task in Proxmox</span>`;
    const p = r.progress; if (!p) return `<span class="muted">no detail (old tool on the node)</span>`;
    const pc = p.total ? Math.min(100, Math.round(100 * p.done / p.total)) : null;
    return `<span>${esc(p.phase)}</span>${pc != null && p.phase === "reading and sending" ? ` · <b>${pc}%</b> <span class="helper">${bytes(p.done)} / ${bytes(p.total)}</span>` : ""} ${tag(p.mode === "incremental" ? "incremental" : "full", "neutral")}`;
  };
  main.innerHTML = `<div class="page">${head}
    ${badNodes.length ? notification("warn", "Snapshot backup unavailable on some nodes", badNodes.map(([n, x]) => `${esc(n)}: ${esc(x.error || "tool not installed (use Prepare node)")}`).join(" · ")) : ""}
    ${tabs([["andamento", "In progress", B.running.length], ["snapshots", "Mounted snapshots", B.snapshots.length], ["agendamentos", "Schedules", B.schedules.length], ["execucoes", "Latest runs"]], tab, "data-bt")}
    <div id="bk-tab"></div></div>`;
  $$("[data-bt]").forEach(b => b.onclick = () => location.hash = `#/backups/${b.dataset.bt}`);
  if (tab === "andamento") mount($("#bk-tab"), dataTable({ title: "In progress", search: false, empty: "No backup in progress.", rows: B.running, columns: [
    { t: "VM", sortVal: r => r.vmid, render: vmCell },
    { t: "Type", render: r => r.kind === "snapshot" ? tag("Storage snapshot", "info") : tag("Regular (Proxmox)", "neutral") },
    { t: "Source", render: r => esc(r.origin === "schedule" ? tr("schedule") : r.origin === "proxmox" ? (r.user || "Proxmox") : tr("manual")) },
    { t: "Node", key: "node" }, { t: "Start", sortVal: r => r.start, render: r => esc(dt(r.start)) },
    { t: "Duration", num: 1, sortVal: r => now - r.start, render: r => esc(dur(now - r.start)) },
    { t: "Progress", render: progCell } ] }));
  if (tab === "snapshots") mount($("#bk-tab"), dataTable({ title: "Mounted backup snapshots", search: false, empty: "No backup snapshot on the array.", rows: B.snapshots,
    desc: "FlashCopy snapshots created by the backup. They exist only while the backup reads the data; one left with no backup in progress is orphaned.", columns: [
    { t: "VM", sortVal: r => r.vmid, render: vmCell }, { t: "Storage", key: "storage" },
    { t: "Snapshot", key: "snap", render: r => `<span class="mono">${esc(r.snap)}</span>` },
    { t: "Created", sortVal: r => r.created, render: r => `${esc(dt(r.created))} <span class="helper">${esc(dur(now - r.created))}</span>` },
    { t: "Volumes", render: r => r.volumes.map(v => `<span class="mono">${esc(v.volname)}</span> ${v.mapped ? tag("mapped", "teal") : tag("not mapped", "neutral")} ${v.on_node ? tag("disk on the node", "teal") : ""}`).join("<br>") },
    { t: "Status", render: r => r.in_use ? status("info", "in use by the backup") : status("warn", "orphaned") },
    { act: 1, render: r => ME.admin && !r.in_use ? btn("Unmount and delete", "ghost", `data-bkc="${esc(r.storage)}|${r.vmid}|${esc(r.snap)}"`, "trash-can", "sm") : "" } ] }));
  const inPve = v => B.pve_jobs.some(j => j.all || j.vmids.includes(v));
  if (tab === "agendamentos") mount($("#bk-tab"), dataTable({ title: "Schedules", search: false, empty: "No schedule. Snapshot backup only runs when started or scheduled here.", rows: B.schedules,
    desc: "Each schedule becomes a systemd timer on the nodes: it runs even if the panel is down. Retention is the one configured in PBS (datastore prune).", columns: [
    { t: "Name", key: "name", render: c => `<b>${esc(c.name)}</b>` },
    { t: "VMs", render: c => c.vmids.map(v => `${v}${inPve(v) ? ` ${tag("also in Proxmox", "warn", "This VM is also in a Proxmox backup job: it will be backed up twice")}` : ""}`).join(", ") },
    { t: "PBS", key: "pbs" }, { t: "When", render: c => esc(schedWhen(c)) },
    { t: "Next", sortVal: c => c.next || 0, render: c => !c.enabled ? tag("paused", "neutral") : c.next ? esc(dt(c.next)) : `<span class="muted">–</span>` },
    { t: "Last", render: c => c.running ? status("info", "running") : c.last ? `${status(c.last.results.every(x => x.ok) ? "ok" : "err", dt(c.last.finished))}` : `<span class="muted">never</span>` },
    { act: 1, render: c => ME.admin ? ovfButton(`data-bks="${esc(c.id)}"`, `Actions for ${c.name}`) : "" } ] }));
  if (tab === "execucoes") mount($("#bk-tab"), dataTable({ title: "Latest runs", search: false, empty: "No runs recorded.", rows: B.recent, columns: [
    { t: "VM", sortVal: r => r.vmid, render: vmCell }, { t: "Start", sortVal: r => r.id, render: r => esc(dt(r.id)) },
    { t: "Mode", render: r => { const x = r.result; return !x ? "–" : x.fallback ? tag("regular (fallback)", "warn", x.reason || "") : tag(x.mode === "incremental" ? "incremental snapshot" : "full snapshot", "info") + (x.protected ? " " + tag("protected", "green") : x.protect_error ? " " + tag("not protected", "warn", x.protect_error) : ""); } },
    { t: "Result", render: r => { const x = r.result; return x && !x.error ? status("ok", "done") : `${status("err", "failed")}<div class="helper">${esc((x && x.error) || r.log || "").slice(0, 200)}</div>`; } },
    { t: "Duration", num: 1, render: r => r.result && r.result.t && r.result.t.total != null ? esc(dur(r.result.t.total)) : "–" },
    { t: "Read", num: 1, render: r => r.result && r.result.read_MiB != null ? esc(bytes(r.result.read_MiB * 1048576)) : "–" },
    { t: "Sent", num: 1, render: r => r.result && r.result.sent_MiB != null ? esc(bytes(r.result.sent_MiB * 1048576)) : "–" } ] }));
  $("#bk-new")?.addEventListener("click", () => schedFlow(B, null, () => { if (tab === "agendamentos") reload(); else location.hash = "#/backups/agendamentos"; }));
  main.querySelector(".page").addEventListener("click", async e => {
    const c = e.target.closest("[data-bkc]"), m = e.target.closest("[data-bks]");
    if (c) {
      const [storage, vmid, snap] = c.dataset.bkc.split("|");
      if (!await confirmDialog({ label: `VM ${vmid}`, title: "Unmount and delete the snapshot?", danger: true, ok: "Delete", body: `<p>The snapshot <b>${esc(snap)}</b> is removed from the node and the array. The VM disks are not affected.</p>` })) return;
      try { toast("info", "Removing snapshot…"); await api("POST", "/backup/snapshots/cleanup", { storage, vmid: +vmid, snap }); toast("ok", "Snapshot removed"); reload(); } catch (err) { toast("err", "Could not remove", err.message); }
    } else if (m) {
      const s = B.schedules.find(x => x.id === m.dataset.bks);
      openMenu(m, [
        { label: "Run now", icon: "launch", disabled: s.running, run: async () => { try { await api("POST", `/backup/schedules/${s.id}/run`); toast("ok", "Schedule started", s.name); setTimeout(reload, 2500); } catch (err) { toast("err", "Could not start", err.message); } } },
        { label: "Edit", icon: "edit", run: () => schedFlow(B, s, reload) },
        { label: s.enabled ? "Pause" : "Resume", icon: "time", run: async () => { try { await api("POST", "/backup/schedules", { ...s, enabled: !s.enabled }); reload(); } catch (err) { toast("err", "The operation failed", err.message); } } },
        "-", { label: "Delete", icon: "trash-can", danger: true, run: async () => { if (!await confirmDialog({ title: `Delete “${s.name}”?`, danger: true, ok: "Delete", body: "<p>The timer is removed from the nodes. Backups already taken stay in PBS.</p>" })) return; try { await api("DELETE", `/backup/schedules/${s.id}`); reload(); } catch (err) { toast("err", "The operation failed", err.message); } } },
      ]);
    }
  });
  if (B.running.length && (tab === "andamento" || tab === "snapshots")) BK_TIMER = setTimeout(() => { if (location.hash.startsWith("#/backups") && !$("#modal-root").innerHTML && !$("#menu-root").innerHTML) reload(); }, 8000);
}
async function schedFlow(B, s, done) {
  const pbs = await api("GET", "/pbs-storages");
  const sel = new Set(s ? s.vmids : []), days = new Set(s ? s.days : []);
  await modal({ title: s ? "Edit schedule" : "New schedule", size: "md",
    body: `<div class="form-stack">${textInput("sc-n", "Name", `value="${esc(s ? s.name : "")}" maxlength="60" placeholder="Daily 02:00"`)}
      ${selectInput("sc-p", "Target PBS storage", pbs.map(p => ({ v: p.storage, t: `${p.storage} (${p.datastore})`, sel: s && s.pbs === p.storage })))}
      <div class="g g-2">${textInput("sc-t", "Time", `type="time" value="${esc(s ? s.time : "02:00")}"`)}<div class="cds--form-item"><span class="cds--label">Days (none checked = every day)</span>
        <div class="inline-checks">${Object.keys(DAY_LABEL).map(d => checkbox("sc-d-" + d, DAY_LABEL[d], days.has(d), `data-day="${d}"`)).join("")}</div></div></div>
      <div class="cds--form-item"><span class="cds--label">VMs</span><div style="max-height:14rem;overflow:auto;border:1px solid var(--cds-border-subtle-01);padding:.5rem .75rem;width:100%">
        ${B.vms.map(v => checkbox("sc-v-" + v.vmid, `<b>${v.vmid}</b> <span data-noi18n>${esc(v.name || "")}</span> <span class="helper" data-noi18n>${esc(v.node || "")}</span>`, sel.has(v.vmid), `data-vm="${v.vmid}"`)).join("") || `<span class="muted">No VMs.</span>`}</div>
        <div class="cds--form__helper-text">VMs run one at a time. Only VMs with disks on plugin storage use the snapshot; the others fall back to the regular backup.</div></div>
      ${checkbox("sc-f", "If the snapshot backup fails, run the regular Proxmox backup", s ? s.fallback : true)}
      ${checkbox("sc-pr", "Mark the backup as <b>protected</b> on the PBS", s ? !!s.protected : false)}
      <p class="helper">Prune jobs and a plain remove skip protected backups; they stay until someone clears the mark in Proxmox, so they keep using space. The mark guards against mistakes, not against someone with rights on the datastore.</p></div>`,
    buttons: [{ id: "no", label: "Cancel" }, { id: "go", label: "Save", kind: "primary" }],
    onButton: async (id, root) => {
      if (id !== "go") return false;
      const body = { id: s ? s.id : undefined, name: $("#sc-n").value.trim(), pbs: $("#sc-p").value, time: $("#sc-t").value, enabled: s ? s.enabled : true,
        days: $$("[data-day]", root).filter(x => x.checked).map(x => x.dataset.day), vmids: $$("[data-vm]", root).filter(x => x.checked).map(x => +x.dataset.vm), fallback: $("#sc-f").checked, protected: $("#sc-pr").checked };
      try { const r = await busy($("[data-b=go]", root), () => api("POST", "/backup/schedules", body));
        const bad = r.nodes.filter(n => !n.ok); toast(bad.length ? "warn" : "ok", "Schedule saved", bad.length ? bad.map(n => `${n.node}: ${n.error}`).join(" · ") : ""); done(); return false; }
      catch (e) { toast("err", "Could not save", e.message); return true; }
    } });
}

/* A stylesheet that did not arrive (dropped connection, bad cached copy) leaves the menu and the
   pages without their layout. Before drawing anything, check that both sheets applied and fetch a
   missing one again, past the browser cache, in its original place so the rule order holds. */
async function ensureStyles() {
  const sheets = [["--cds-background", "vendor/carbon.min.css"], ["--sw-shell", "app.css"]];
  for (let i = 0; i < 3; i++) {
    const css = getComputedStyle(document.documentElement);
    const miss = sheets.filter(([v]) => !css.getPropertyValue(v).trim());
    if (!miss.length) return;
    await Promise.all(miss.map(([, f]) => new Promise(res => {
      const l = document.createElement("link");
      l.rel = "stylesheet"; l.href = `/static/${f}?r=${Date.now()}${i}`;
      l.onload = l.onerror = res; setTimeout(res, 10000);
      const old = document.querySelector(`link[href^="/static/${f}"]`);
      if (old) old.after(l); else document.head.appendChild(l);
    })));
  }
}

ensureStyles().finally(() => { buildNav(); setTitle(); route(); });
