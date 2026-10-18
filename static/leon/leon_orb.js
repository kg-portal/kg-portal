/* Leon „Orb-Hero“ – nur Optik: Score-Werte („7/10“) farbig markieren.
   .score bekommt data-lo; reine Tabellenzellen mit „N/10“ bekommen ein <span class="lo-score">.
   hoch = 8–10 (grün), mittel = 5–7 (orange), tief = 0–4 (rot). Text und Daten bleiben unverändert. */
(function () {
    "use strict";
    var MUSTER = /^\s*(\d{1,2})\s*\/\s*10\s*$/;
    function stufe(n) { return n >= 8 ? "hoch" : n >= 5 ? "mittel" : "tief"; }
    function faerben() {
        document.querySelectorAll(".score").forEach(function (el) {
            var m = MUSTER.exec(el.textContent || "");
            var lo = m ? stufe(Number(m[1])) : "";
            if (lo && el.getAttribute("data-lo") !== lo) el.setAttribute("data-lo", lo);
        });
        document.querySelectorAll("td").forEach(function (td) {
            if (td.children.length) return;
            var m = MUSTER.exec(td.textContent || "");
            if (!m) return;
            var span = document.createElement("span");
            span.className = "lo-score";
            span.setAttribute("data-lo", stufe(Number(m[1])));
            span.textContent = td.textContent.trim();
            td.textContent = "";
            td.appendChild(span);
        });
    }
    var geplant = false;
    function planen() {
        if (geplant) return;
        geplant = true;
        requestAnimationFrame(function () { geplant = false; faerben(); });
    }
    function start() {
        faerben();
        if (window.MutationObserver) new MutationObserver(planen).observe(document.body, { childList: true, subtree: true, characterData: true });
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
    else start();
})();
