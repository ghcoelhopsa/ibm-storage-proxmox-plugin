/* applies the theme before the page paints: light by default */
(function () {
  var t = "light";
  try { t = new URLSearchParams(location.search).get("theme") || localStorage.getItem("swtheme") || "light"; } catch (e) {}
  t = t === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = t;
  document.documentElement.className = t === "dark" ? "cds--g100" : "cds--g10";
})();
