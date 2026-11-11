/* Interface languages: English (source), Portuguese and Spanish.
   The code is written in English. For the other languages the text on screen is
   replaced using the dictionary (i18n-dict.js), by exact match or by regex. */
"use strict";
const LANGS = {
  en: { name: "English", locale: "en-US" },
  pt: { name: "Português", locale: "pt-BR" },
  es: { name: "Español", locale: "es-ES" },
};
const FLAGS = {
  pt: '<svg class="flag" viewBox="0 0 28 20" width="22" height="16" aria-hidden="true"><rect width="28" height="20" fill="#009c3b"/><path d="M14 2.5 25 10 14 17.5 3 10z" fill="#ffdf00"/><circle cx="14" cy="10" r="4.4" fill="#002776"/><path d="M9.8 9.2c2.9-.5 5.9.2 8.4 1.9" fill="none" stroke="#fff" stroke-width=".9"/></svg>',
  en: '<svg class="flag" viewBox="0 0 28 20" width="22" height="16" aria-hidden="true"><rect width="28" height="20" fill="#fff"/><g fill="#b22234"><rect width="28" height="1.54"/><rect y="3.08" width="28" height="1.54"/><rect y="6.15" width="28" height="1.54"/><rect y="9.23" width="28" height="1.54"/><rect y="12.31" width="28" height="1.54"/><rect y="15.38" width="28" height="1.54"/><rect y="18.46" width="28" height="1.54"/></g><rect width="12" height="10.77" fill="#3c3b6e"/><g fill="#fff"><circle cx="2.4" cy="2.2" r=".7"/><circle cx="6" cy="2.2" r=".7"/><circle cx="9.6" cy="2.2" r=".7"/><circle cx="4.2" cy="5.4" r=".7"/><circle cx="7.8" cy="5.4" r=".7"/><circle cx="2.4" cy="8.6" r=".7"/><circle cx="6" cy="8.6" r=".7"/><circle cx="9.6" cy="8.6" r=".7"/></g></svg>',
  es: '<svg class="flag" viewBox="0 0 28 20" width="22" height="16" aria-hidden="true"><rect width="28" height="20" fill="#aa151b"/><rect y="5" width="28" height="10" fill="#f1bf00"/></svg>',
};
const LANG = (() => {
  let l = null;
  try { l = new URLSearchParams(location.search).get("lang") || localStorage.getItem("swlang"); } catch (e) { }
  if (!LANGS[l]) { const b = (navigator.language || "en").slice(0, 2).toLowerCase(); l = b === "pt" || b === "es" ? b : "en"; }
  return l;
})();
const LOCALE = LANGS[LANG].locale;
document.documentElement.lang = LOCALE;
function setLang(l) { try { localStorage.setItem("swlang", l); } catch (e) { } const u = new URL(location.href); if (u.searchParams.has("lang")) { u.searchParams.delete("lang"); location.replace(u.toString()); } else location.reload(); }

const I18N_DEBUG = /[?&]i18n-debug\b/.test(location.search) ? (window.__i18nMissing = new Set()) : null;
const I18N = window.I18N_DICT || { pt: {}, es: {} }, I18N_RE = window.I18N_PATTERNS || { pt: [], es: [] };
function tr(s) {
  if (LANG === "en" || s == null) return s;
  const m = String(s).match(/^(\s*)([\s\S]*?)(\s*)$/), core = m[2];
  if (!core || !/[A-Za-zÀ-ÿ]{2}/.test(core)) return s;
  const d = I18N[LANG] || {};
  if (Object.prototype.hasOwnProperty.call(d, core)) return m[1] + d[core] + m[3];
  for (const [re, rep] of I18N_RE[LANG] || []) { if (re.test(core)) return m[1] + core.replace(re, rep) + m[3]; }
  if (I18N_DEBUG) I18N_DEBUG.add(core);
  return s;
}
(function () {
  if (LANG === "en") return;
  const SKIP = "script,style,pre,code,.mono,.codebox,[data-noi18n],textarea";
  const ATTRS = ["title", "placeholder", "aria-label"];
  const node = n => {
    if (n.nodeType === 3) {
      const p = n.parentElement; if (!p || p.closest(SKIP)) return;
      if (n.__i18n === n.nodeValue) return;
      const t = tr(n.nodeValue); if (t !== n.nodeValue) n.nodeValue = t; n.__i18n = n.nodeValue;
    } else if (n.nodeType === 1) {
      if (n.matches(SKIP)) return;
      for (const a of ATTRS) { const v = n.getAttribute(a); if (v && n["__i18n_" + a] !== v) { const t = tr(v); if (t !== v) n.setAttribute(a, t); n["__i18n_" + a] = n.getAttribute(a); } }
      if (n.tagName === "INPUT" && n.type === "text" && n.dataset.i18nValue) { const t = tr(n.value); if (t !== n.value) n.value = t; }
      for (const c of n.childNodes) node(c);
    }
  };
  new MutationObserver(ms => { for (const m of ms) { if (m.type === "characterData") node(m.target); else if (m.type === "attributes") node(m.target); else for (const a of m.addedNodes) node(a); } })
    .observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  document.addEventListener("DOMContentLoaded", () => node(document.body));
})();
