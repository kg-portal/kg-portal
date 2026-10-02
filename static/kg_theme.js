/* KG Dunkel / Hell – gemeinsamer Schalter für CRM, Business und Agent.
 *
 * - Standard: Dunkel. Die Wahl liegt im Browser des jeweiligen Geräts
 *   (localStorage "kgPortalTheme"), bleibt nach Neustart erhalten und
 *   beeinflusst andere Geräte nicht.
 * - Die Seiten selbst bleiben unverändert. Im Dunkelmodus werden helle
 *   Flächen abgedunkelt und dunkle Schrift aufgehellt; kräftige Farben
 *   (orange, grün, blau, rot …) bleiben wie sie sind.
 * - "Hell" zeigt die Seite exakt wie bisher. Drucken ist immer hell.
 *
 * Einbinden (so früh wie möglich im <head>):
 *   <script src="/static/kg_theme.js"></script>               Seite ist hell gestaltet
 *   <script src="..." data-kg-base="dark"></script>          Seite ist dunkel gestaltet
 *   data-kg-toggle="float"  → Schalter unten rechts einblenden
 *   <div data-kg-theme-slot></div> → Schalter an dieser Stelle
 */
(function () {
  "use strict";
  if (window.__kgTheme) return;
  var KEY = "kgPortalTheme";
  var script = document.currentScript || {};
  var data = script.dataset || {};
  var BASE = data.kgBase === "dark" ? "dark" : "light";
  var TOGGLE = data.kgToggle || "";
  var root = document.documentElement;

  function readTheme() {
    try {
      return localStorage.getItem(KEY) === "light" ? "light" : "dark";
    } catch (e) {
      return "dark";
    }
  }
  function saveTheme(t) {
    try {
      localStorage.setItem(KEY, t);
    } catch (e) {}
  }

  // ---------- Farben ----------
  function parse(c) {
    var m = /^rgba?\(([^)]+)\)$/.exec(c || "");
    if (!m) return null;
    var p = m[1].split(/[\s,\/]+/).filter(Boolean).map(parseFloat);
    if (p.length < 3) return null;
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  }
  function hsl(c) {
    var r = c.r / 255, g = c.g / 255, b = c.b / 255;
    var max = Math.max(r, g, b), min = Math.min(r, g, b);
    var l = (max + min) / 2, h = 0, s = 0, d = max - min;
    if (d) {
      s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
      if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
      else if (max === g) h = (b - r) / d + 2;
      else h = (r - g) / d + 4;
      h *= 60;
    }
    return { h: h, s: s, l: l };
  }
  function rgb(h, s, l, a) {
    function f(n) {
      var k = (n + h / 30) % 12;
      return l - s * Math.min(l, 1 - l) * Math.max(-1, Math.min(k - 3, 9 - k, 1));
    }
    var r = Math.round(f(0) * 255), g = Math.round(f(8) * 255), b = Math.round(f(4) * 255);
    return a < 1 ? "rgba(" + r + ", " + g + ", " + b + ", " + a + ")" : "rgb(" + r + ", " + g + ", " + b + ")";
  }
  function lum(c) {
    function ch(v) {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    }
    return 0.2126 * ch(c.r) + 0.7152 * ch(c.g) + 0.0722 * ch(c.b);
  }

  // Richtung: helle Seite → dunkel (BASE light) oder dunkle Seite → hell.
  var TO_DARK = BASE === "light";

  function mapBg(c) {
    if (!c || c.a === 0) return null;
    var x = hsl(c);
    if (TO_DARK) {
      if (x.l < 0.62) return null;
      var nl = 0.1 + (1 - x.l) * 0.45, h = x.h, s;
      if (x.s < 0.12) { h = 222; s = 0.32; } else s = Math.min(x.s, 0.6) * 0.7;
      return rgb(h, s, nl, c.a);
    }
    if (x.l > 0.36) return null;
    var nl2 = 0.97 - x.l * 0.35, h2 = x.h, s2;
    if (x.s < 0.15) { h2 = 215; s2 = 0.25; } else s2 = Math.min(x.s, 0.7) * 0.6;
    return rgb(h2, s2, nl2, c.a);
  }
  function mapFg(c) {
    if (!c || c.a === 0) return null;
    var x = hsl(c);
    if (TO_DARK) {
      if (x.l >= 0.6) return null;
      var nl = 0.93 - x.l * 0.42, h = x.h, s;
      if (x.s < 0.12) { h = 215; s = 0.22; } else s = Math.min(x.s, 0.85);
      return rgb(h, s, nl, c.a);
    }
    if (x.l <= 0.5) return null;
    var nl2 = 0.1 + (1 - x.l) * 0.4, h2 = x.h, s2;
    if (x.s < 0.15) { h2 = 222; s2 = 0.3; } else s2 = Math.min(x.s, 0.85);
    return rgb(h2, s2, Math.min(nl2, 0.32), c.a);
  }
  function mapBorder(c) {
    if (!c || c.a === 0) return null;
    var x = hsl(c);
    if (TO_DARK) {
      if (x.l < 0.6) return null;
      var h = x.h, s;
      if (x.s < 0.12) { h = 220; s = 0.22; } else s = Math.min(x.s, 0.6) * 0.6;
      return rgb(h, s, 0.22 + (1 - x.l) * 0.35, c.a);
    }
    if (x.l > 0.4) return null;
    return rgb(x.s < 0.15 ? 215 : x.h, x.s < 0.15 ? 0.2 : Math.min(x.s, 0.6) * 0.5, 0.86 - x.l * 0.2, c.a);
  }
  function mapGradient(img) {
    if (!img || img.indexOf("gradient") < 0) return null;
    var changed = false;
    var out = img.replace(/rgba?\([^)]+\)/g, function (m) {
      var n = mapBg(parse(m));
      if (n) { changed = true; return n; }
      return m;
    });
    return changed ? out : null;
  }

  // ---------- Regeln ----------
  var rules = Object.create(null), ruleCount = 0, sheet = null;
  function ensureSheet() {
    if (sheet) return sheet;
    var el = document.createElement("style");
    el.id = "kg-theme-rules";
    (document.head || root).appendChild(el);
    sheet = el.sheet;
    return sheet;
  }
  function ruleId(body) {
    if (rules[body]) return rules[body];
    var id = "k" + (++ruleCount).toString(36);
    rules[body] = id;
    try {
      ensureSheet().insertRule(
        // :not(#…) hebt die Spezifität über Seitenregeln mit IDs/!important
        '@media screen{html[data-kg-x="on"] [data-kgx="' + id + '"]:not(#kgx-a):not(#kgx-b){' + body + "}}",
        sheet.cssRules.length
      );
    } catch (e) {}
    return id;
  }

  var SKIP = { SCRIPT: 1, STYLE: 1, LINK: 1, META: 1, HEAD: 1, TITLE: 1, NOSCRIPT: 1, BR: 1, IMG: 1, VIDEO: 1, CANVAS: 1, IFRAME: 1, svg: 1, SVG: 1, PICTURE: 1, SOURCE: 1, OPTION: 0 };
  var info = new WeakMap(); // Element → {effDark: bool, fg: string, fgChanged: bool}

  function effInfo(el) {
    if (!el || el.nodeType !== 1) return { effDark: !TO_DARK, fg: null, fgChanged: false };
    var i = info.get(el);
    if (i) return i;
    // Unbearbeitetes Elternelement (z. B. außerhalb des Durchlaufs)
    var cs = getComputedStyle(el), bg = parse(cs.backgroundColor);
    if (bg && bg.a > 0.5) return { effDark: lum(bg) < 0.18, fg: cs.color, fgChanged: false };
    return effInfo(el.parentElement);
  }

  function processEl(el, parentInfo) {
    if (SKIP[el.tagName] || el.hasAttribute("data-kg-skip")) return;
    var cs = getComputedStyle(el);
    var body = "", effDark = parentInfo.effDark;
    var bg = parse(cs.backgroundColor);
    var nbg = mapBg(bg);
    if (nbg) {
      body += "background-color:" + nbg + "!important;";
      effDark = TO_DARK;
    } else if (bg && bg.a > 0.5) {
      effDark = lum(bg) < 0.18;
    }
    var gimg = cs.backgroundImage;
    var ng = mapGradient(gimg);
    if (ng) {
      body += "background-image:" + ng + "!important;";
      if (!bg || bg.a < 0.5) effDark = TO_DARK;
    }
    // Schrift: auf (jetzt) dunklem Grund aufhellen, auf kräftigem Grund lassen
    var fg = cs.color, nfg = null;
    var wantMapped = TO_DARK ? effDark : !effDark;
    if (wantMapped) nfg = mapFg(parse(fg));
    var finalFg = nfg || fg;
    if (nfg) body += "color:" + nfg + "!important;";
    else if (parentInfo.fgChanged && fg === parentInfo.origFg && !wantMapped) {
      // Elternteil wurde umgefärbt, dieses Element soll aber Originalfarbe behalten
      body += "color:" + fg + "!important;";
    }
    // Rahmen
    if (parseFloat(cs.borderTopWidth) || parseFloat(cs.borderRightWidth) || parseFloat(cs.borderBottomWidth) || parseFloat(cs.borderLeftWidth)) {
      var sides = ["Top", "Right", "Bottom", "Left"];
      for (var k = 0; k < 4; k++) {
        if (!parseFloat(cs["border" + sides[k] + "Width"])) continue;
        var nb = mapBorder(parse(cs["border" + sides[k] + "Color"]));
        if (nb) body += "border-" + sides[k].toLowerCase() + "-color:" + nb + "!important;";
      }
    }
    if (el.tagName === "INPUT" || el.tagName === "SELECT" || el.tagName === "TEXTAREA") {
      body += "color-scheme:" + (TO_DARK ? "dark" : "light") + ";";
    }
    if (body) el.setAttribute("data-kgx", ruleId(body));
    else if (el.hasAttribute("data-kgx")) el.removeAttribute("data-kgx");
    var mine = {
      effDark: effDark,
      fg: finalFg,
      origFg: fg,
      fgChanged: finalFg !== fg,
    };
    info.set(el, mine);
    return mine;
  }

  function walk(start) {
    var parentInfo = start.parentElement ? effInfo(start.parentElement) : { effDark: !TO_DARK, fg: null, fgChanged: false };
    var stack = [[start, parentInfo]];
    while (stack.length) {
      var item = stack.pop(), el = item[0];
      var mine = processEl(el, item[1]);
      if (!mine) continue;
      for (var c = el.lastElementChild; c; c = c.previousElementSibling) stack.push([c, mine]);
    }
  }

  // ---------- Umschalten ----------
  var active = false, processed = false, observer = null, pending = new Set(), timer = 0;
  function want() {
    return readTheme() !== BASE;
  }
  function apply() {
    var on = want();
    root.setAttribute("data-kg-theme", readTheme());
    if (on && document.body) {
      if (!processed) {
        root.classList.add("kgx-scan");
        root.setAttribute("data-kg-x", "off");
        walk(document.body);
        root.classList.remove("kgx-scan");
        processed = true;
      }
      root.setAttribute("data-kg-x", "on");
      startObserver();
    } else {
      root.setAttribute("data-kg-x", on ? "on" : "off");
    }
    active = on;
    root.classList.add("kgx-ready");
    syncToggles();
    tuneCharts();
  }

  function flush() {
    timer = 0;
    var list = Array.from(pending);
    pending.clear();
    if (!active) { processed = false; return; }
    root.classList.add("kgx-scan");
    list.forEach(function (el) {
      if (!el.isConnected) return;
      // Eigene Regel kurz entfernen, damit die echten Farben gelesen werden
      el.querySelectorAll && el.querySelectorAll("[data-kgx]").forEach(function (n) { n.removeAttribute("data-kgx"); info.delete(n); });
      el.removeAttribute("data-kgx");
      info.delete(el);
      walk(el);
    });
    root.classList.remove("kgx-scan");
  }
  function queue(el) {
    if (!el || el.nodeType !== 1 || SKIP[el.tagName]) return;
    pending.add(el);
    if (!timer) timer = setTimeout(flush, 40);
  }
  function startObserver() {
    if (observer || !window.MutationObserver) return;
    observer = new MutationObserver(function (list) {
      if (!active) { processed = false; return; }
      list.forEach(function (m) {
        if (m.type === "childList") m.addedNodes.forEach(queue);
        else if (m.type === "attributes") queue(m.target);
      });
    });
    observer.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["class", "style"] });
  }

  // Diagramme (Chart.js) lesbar machen
  function tuneCharts() {
    var C = window.Chart;
    if (!C || !C.instances) return;
    var light = TO_DARK ? active : !active;
    var tick = light ? "#cbd5e1" : "#475569", grid = light ? "rgba(148,163,184,.18)" : "rgba(15,23,42,.08)";
    try {
      C.defaults.color = tick;
      Object.values(C.instances).forEach(function (ch) {
        var sc = (ch.options && ch.options.scales) || {};
        Object.keys(sc).forEach(function (k) {
          sc[k].ticks = sc[k].ticks || {};
          sc[k].ticks.color = tick;
          sc[k].grid = sc[k].grid || {};
          sc[k].grid.color = grid;
        });
        if (ch.options.plugins && ch.options.plugins.legend) {
          ch.options.plugins.legend.labels = ch.options.plugins.legend.labels || {};
          ch.options.plugins.legend.labels.color = tick;
        }
        ch.update("none");
      });
    } catch (e) {}
  }

  // ---------- Schalter ----------
  function syncToggles() {
    var t = readTheme();
    document.querySelectorAll(".kg-theme-toggle button").forEach(function (b) {
      var on = b.getAttribute("data-kg-set") === t;
      b.classList.toggle("on", on);
      b.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }
  function makeToggle() {
    var box = document.createElement("div");
    box.className = "kg-theme-toggle";
    box.setAttribute("data-kg-skip", "");
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "Darstellung");
    box.innerHTML = '<button type="button" data-kg-set="dark">Dunkel</button><button type="button" data-kg-set="light">Hell</button>';
    box.addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (!b) return;
      saveTheme(b.getAttribute("data-kg-set"));
      apply();
    });
    return box;
  }
  function mountToggles() {
    var slots = document.querySelectorAll("[data-kg-theme-slot]");
    slots.forEach(function (s) {
      if (!s.querySelector(".kg-theme-toggle")) s.appendChild(makeToggle());
    });
    if (!slots.length && TOGGLE === "float" && !document.querySelector(".kg-theme-toggle")) {
      var t = makeToggle();
      t.classList.add("kg-theme-float");
      document.body.appendChild(t);
    }
    syncToggles();
  }

  // ---------- Start ----------
  var css = document.createElement("style");
  css.id = "kg-theme-base";
  css.textContent =
    '@media screen{html[data-kg-x="on"]{background:' + (TO_DARK ? "#0f1623" : "#f4f6fb") + "!important;color-scheme:" + (TO_DARK ? "dark" : "light") + "}" +
    'html[data-kg-x="pending"] body{opacity:0}' +
    "html.kgx-ready body{transition:opacity .12s}}" +
    // Während des Einlesens keine Farbübergänge, sonst wird ein Zwischenwert gelesen
    "html.kgx-scan,html.kgx-scan *,html.kgx-scan *::before,html.kgx-scan *::after{transition:none!important;animation-play-state:paused!important}" +
    ".kg-theme-toggle{display:inline-flex;gap:2px;padding:3px;border-radius:11px;border:1px solid rgba(148,163,184,.45);background:rgba(15,23,42,.06);font:600 12px/1 Arial,Helvetica,sans-serif;vertical-align:middle}" +
    ".kg-theme-toggle button{border:0;background:transparent;color:inherit;font:inherit;letter-spacing:.04em;padding:8px 12px;min-height:32px;border-radius:8px;cursor:pointer;opacity:.85}" +
    ".kg-theme-toggle button.on{background:#2563eb;color:#fff;opacity:1;box-shadow:0 4px 12px rgba(37,99,235,.35)}" +
    ".kg-theme-toggle button:focus-visible{outline:2px solid #60a5fa;outline-offset:1px}" +
    '@media screen{html[data-kg-x="on"] .kg-theme-toggle{color:' + (TO_DARK ? "#e2e8f0" : "#1e293b") + ";border-color:rgba(148,163,184,.35);background:" + (TO_DARK ? "rgba(255,255,255,.05)" : "rgba(15,23,42,.05)") + "}}" +
    ".kg-theme-float{position:fixed;right:14px;bottom:14px;z-index:2147483000;background:rgba(255,255,255,.92);color:#1e293b;box-shadow:0 6px 20px rgba(15,23,42,.18)}" +
    '@media screen{html[data-kg-x="on"] .kg-theme-float{background:' + (TO_DARK ? "rgba(17,24,39,.92)" : "rgba(255,255,255,.92)") + "}}" +
    // Dunkle Logos (z. B. KG-BUSINESS) auf dunklem Grund aufhellen
    (TO_DARK ? '@media screen{html[data-kg-x="on"] img[src*="business_logo"]{filter:brightness(2.1) saturate(.9)}}' : "") +
    "@media print{.kg-theme-toggle{display:none!important}}";
  (document.head || root).appendChild(css);

  root.setAttribute("data-kg-theme", readTheme());
  if (want()) root.setAttribute("data-kg-x", "pending");
  // Sicherheitsnetz: Seite nie unsichtbar lassen
  setTimeout(function () {
    if (root.getAttribute("data-kg-x") === "pending") root.setAttribute("data-kg-x", "off");
  }, 1500);

  function ready() {
    mountToggles();
    apply();
    // Spät geladene Stylesheets/Diagramme: nach "load" einmal neu einfärben
    if (document.readyState !== "complete") {
      window.addEventListener("load", function () {
        if (!want()) return;
        processed = false;
        apply();
      });
    }
    setTimeout(tuneCharts, 800);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", ready);
  else ready();

  // Andere Fenster/Rahmen derselben Seite (z. B. Süper Program) folgen sofort
  window.addEventListener("storage", function (e) {
    if (e.key === KEY) apply();
  });
  window.addEventListener("message", function (e) {
    if (e && e.data && e.data.kgTheme && (e.data.kgTheme === "dark" || e.data.kgTheme === "light")) {
      saveTheme(e.data.kgTheme);
      apply();
    }
  });

  window.__kgTheme = { apply: apply, read: readTheme };
})();
