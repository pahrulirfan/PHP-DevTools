/* PIPanel docs: theme toggle, screenshot gallery, copy buttons, table-of-contents highlight. No dependencies. */
(function () {
  "use strict";
  var root = document.documentElement;
  var store = {
    get: function (k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode: ignore */ } }
  };

  // ---------- theme ----------
  function systemDark() { return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches; }
  function theme() { return root.getAttribute("data-theme") || (systemDark() ? "dark" : "light"); }
  function applyShots() {
    var t = theme();
    document.querySelectorAll("img[data-shot]").forEach(function (img) {
      img.src = "assets/img/" + t + "-" + img.getAttribute("data-shot") + ".png";
    });
    var b = document.getElementById("theme");
    if (b) b.setAttribute("aria-label", t === "dark" ? "Switch to the light theme" : "Switch to the dark theme");
  }
  var saved = store.get("pipanel-theme");
  if (saved === "light" || saved === "dark") root.setAttribute("data-theme", saved);
  var toggle = document.getElementById("theme");
  if (toggle) toggle.addEventListener("click", function () {
    var next = theme() === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    store.set("pipanel-theme", next);
    applyShots();
  });
  if (window.matchMedia) window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyShots);
  applyShots();

  // ---------- mobile menu ----------
  var menu = document.getElementById("menu"), nav = document.getElementById("nav");
  if (menu && nav) {
    menu.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      menu.setAttribute("aria-expanded", String(open));
    });
    nav.addEventListener("click", function (e) { if (e.target.tagName === "A") { nav.classList.remove("open"); menu.setAttribute("aria-expanded", "false"); } });
  }

  // ---------- screenshot gallery ----------
  var tabs = Array.prototype.slice.call(document.querySelectorAll(".tab"));
  var shot = document.getElementById("gallery-img"), cap = document.getElementById("gallery-caption");
  function select(tab) {
    tabs.forEach(function (t) { t.setAttribute("aria-selected", String(t === tab)); t.tabIndex = t === tab ? 0 : -1; });
    shot.setAttribute("data-shot", tab.getAttribute("data-shot"));
    shot.alt = tab.getAttribute("data-alt");
    cap.textContent = tab.getAttribute("data-caption");
    var natural = tab.hasAttribute("data-natural");   // small screenshots are shown at their own size, not stretched
    shot.style.width = natural ? "auto" : ""; shot.style.margin = natural ? "0 auto" : "";
    applyShots();
  }
  tabs.forEach(function (tab, i) {
    tab.addEventListener("click", function () { select(tab); });
    tab.addEventListener("keydown", function (e) {   // arrow keys move between tabs
      var n = e.key === "ArrowRight" ? i + 1 : e.key === "ArrowLeft" ? i - 1 : null;
      if (n === null) return;
      var next = tabs[(n + tabs.length) % tabs.length];
      next.focus(); select(next); e.preventDefault();
    });
  });

  // ---------- copy buttons ----------
  document.querySelectorAll("pre").forEach(function (pre) {
    var b = document.createElement("button");
    b.className = "copy"; b.type = "button"; b.textContent = "Copy";
    b.addEventListener("click", function () {
      var text = pre.querySelector("code").innerText.replace(/^\$ /gm, "");
      var done = function () { b.textContent = "Copied"; setTimeout(function () { b.textContent = "Copy"; }, 1600); };
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, function () { b.textContent = "Select and copy"; });
      else b.textContent = "Select and copy";
    });
    pre.appendChild(b);
  });

  // ---------- table of contents ----------
  var links = Array.prototype.slice.call(document.querySelectorAll(".toc a"));
  var targets = links.map(function (a) { return document.getElementById(a.getAttribute("href").slice(1)); });
  if ("IntersectionObserver" in window && links.length) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        links.forEach(function (a) { a.classList.toggle("on", a.getAttribute("href") === "#" + en.target.id); });
      });
    }, { rootMargin: "-90px 0px -70% 0px" });
    targets.forEach(function (t) { if (t) io.observe(t); });
  }
})();
