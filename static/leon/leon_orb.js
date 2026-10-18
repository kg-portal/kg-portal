/* Leon „Orb-Hero“ – nur Optik: Score-Werte („7/10“) farbig markieren.
   .score bekommt data-lo; reine Tabellenzellen mit „N/10“ bekommen ein <span class="lo-score">.
   hoch = 8–10 (grün), mittel = 5–7 (orange), tief = 0–4 (rot). Text und Daten bleiben unverändert.
   Premium-Tabellen: Zellen bekommen nur Attribute (data-lo-col, data-lo-av, data-lo-farbe) nach der
   Spaltenüberschrift – Datum, Kontakt, Telefon, Dauer. Der Zelltext selbst wird nicht verändert. */
(function () {
    "use strict";
    var MUSTER = /^\s*(\d{1,2})\s*\/\s*10\s*$/;
    var SPALTEN = [
        [/^(datum|zeit|erstellt|angelegt|aktualisiert)\b/i, "datum"],
        [/^(kontakt|name|firma|kunde|unternehmen)\b/i, "kontakt"],
        [/^(telefon|tel\.?|rufnummer|nummer)\b/i, "telefon"],
        [/^(dauer)\b/i, "dauer"]
    ];
    function stufe(n) { return n >= 8 ? "hoch" : n >= 5 ? "mittel" : "tief"; }
    function art(text) {
        for (var i = 0; i < SPALTEN.length; i++) if (SPALTEN[i][0].test(text)) return SPALTEN[i][1];
        return "";
    }
    // Kürzel aus den ersten zwei Wörtern (z. B. „Praxis Weber“ → „PW“), Farbe fest pro Name
    function kuerzel(name) {
        var w = (name.match(/[A-Za-zÄÖÜäöüß0-9]+/g) || []).filter(function (x) { return !/^(gmbh|ug|ag|kg|ohg|ek|e\.k)$/i.test(x); });
        return ((w[0] || "").charAt(0) + (w[1] || "").charAt(0)).toUpperCase();
    }
    function farbe(name) {
        var h = 0;
        for (var i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) % 997;
        return String(h % 6);
    }
    function spalten() {
        document.querySelectorAll("table").forEach(function (tab) {
            var kopf = tab.tHead && tab.tHead.rows[0];
            if (!kopf) return;
            var arten = Array.prototype.map.call(kopf.cells, function (th) { return art((th.textContent || "").trim()); });
            if (!arten.some(Boolean)) return;
            // nur die erste Kontakt-Spalte bekommt ein Kürzel (z. B. Firma, nicht noch einmal Ansprechpartner)
            var erste = arten.indexOf("kontakt");
            arten = arten.map(function (x, i) { return x === "kontakt" && i !== erste ? "" : x; });
            Array.prototype.forEach.call(tab.tBodies, function (tb) {
                Array.prototype.forEach.call(tb.rows, function (tr) {
                    if (tr.cells.length !== arten.length) return;
                    Array.prototype.forEach.call(tr.cells, function (td, i) {
                        var a = arten[i];
                        if (!a) return;
                        if (td.getAttribute("data-lo-col") !== a) td.setAttribute("data-lo-col", a);
                        if (a !== "kontakt") return;
                        var name = ((td.querySelector("strong, b, a") || td).textContent || "").trim();
                        var k = kuerzel(name);
                        if (!k) { td.removeAttribute("data-lo-av"); return; }
                        if (td.getAttribute("data-lo-av") !== k) td.setAttribute("data-lo-av", k);
                        var f = farbe(name);
                        if (td.getAttribute("data-lo-farbe") !== f) td.setAttribute("data-lo-farbe", f);
                    });
                });
            });
        });
    }
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
        spalten();
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
