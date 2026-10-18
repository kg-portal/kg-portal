// Auftritt beim Öffnen von Kalender, KG-Mail und Aufgaben (nur Anzeige, keine Daten werden geändert).
// Kopf gleitet herein, Glanz läuft einmal darüber, danach erscheinen Menü und Inhalt weich nacheinander.
// Nur beim ersten Öffnen: spätere Neuzeichnungen (Monat wechseln, Suche, Filter) bleiben ohne Effekt.
// Einbinden: <script src="/static/kg_auftritt.js" data-seite="kalender|mail|aufgaben"></script> vor dem Seitenkopf.
(function () {
    var root = document.documentElement;
    var skript = document.currentScript;
    var seite = skript && skript.getAttribute('data-seite');
    if (!seite || !window.MutationObserver || !Element.prototype.animate || !document.timeline) return;
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    var WEICH = 'cubic-bezier(.22,1,.36,1)';
    var BILD = {
        kopf: [{ opacity: 0, transform: 'translateY(-16px) scale(.985)' }, { opacity: 1, transform: 'none' }],
        symbol: [{ opacity: 0, transform: 'scale(.35) rotate(-20deg)' }, { opacity: 1, transform: 'scale(1.14) rotate(5deg)', offset: .6 }, { opacity: 1, transform: 'none' }],
        ring: [{ boxShadow: '0 0 0 0 rgba(255,255,255,.55)' }, { boxShadow: '0 0 0 18px rgba(255,255,255,0)' }],
        links: [{ opacity: 0, transform: 'translateX(-16px)' }, { opacity: 1, transform: 'none' }],
        rechts: [{ opacity: 0, transform: 'translateX(16px)' }, { opacity: 1, transform: 'none' }],
        runter: [{ opacity: 0, transform: 'translateY(-10px)' }, { opacity: 1, transform: 'none' }],
        hoch: [{ opacity: 0, transform: 'translateY(14px)' }, { opacity: 1, transform: 'none' }],
        karte: [{ opacity: 0, transform: 'translateY(20px) scale(.96)' }, { opacity: 1, transform: 'none' }],
        tag: [{ opacity: 0, transform: 'translateY(10px) scale(.9)' }, { opacity: 1, transform: 'none' }],
        weich: [{ opacity: 0 }, { opacity: 1 }],
        puls: [{ boxShadow: '0 0 0 0 rgba(37,99,235,.55)' }, { boxShadow: '0 0 0 12px rgba(37,99,235,0)' }]
    };
    // Monatsansicht: Tage laufen als Welle von oben links nach unten rechts
    function welle(el) {
        var i = Array.prototype.indexOf.call(el.parentNode.children, el);
        return ((i % 7) + Math.floor(i / 7)) * 38;
    }
    var KOPF = [
        { sel: '.kgk-kopf, .lh-kopf', bild: 'kopf', d: 700, v: 0 },
        { sel: '.kgk-icon, .lh-icon', bild: 'symbol', d: 850, v: 160 },
        { sel: '.kgk-icon, .lh-icon', bild: 'ring', d: 1100, v: 800, extra: true },
        { sel: '.kgk-kopf h1, .lh-kopf h1', bild: 'links', d: 650, v: 230 },
        { sel: '.kgk-kopf p, .lh-kopf p', bild: 'links', d: 650, v: 320 },
        { sel: '.kgk-heute, .lh-pager', bild: 'rechts', d: 650, v: 380 }
    ];
    var REGELN = {
        kalender: KOPF.concat([
            { sel: '.gc-topbar', bild: 'runter', d: 600, v: 260 },
            { sel: '.gc-sidebar > *', bild: 'links', d: 550, v: 330, s: 45, max: 12 },
            { sel: '.gc-weekdays', bild: 'weich', d: 500, v: 380 },
            { sel: '.gc-day', bild: 'tag', d: 520, v: 420, welle: welle, neu: true },
            { sel: '.gc-date.gc-today', bild: 'puls', d: 950, v: 1250, n: 2, extra: true, neu: true },
            { sel: '.gc-agenda-day, .gc-schedule-head, .gc-all-day-row-view', bild: 'hoch', d: 550, v: 420, s: 45, max: 12, neu: true }
        ]),
        mail: KOPF.concat([
            { sel: '.mail-app > .topbar', bild: 'runter', d: 600, v: 260 },
            { sel: '.mail-app .sidebar .compose, .mail-app .sidebar .nav-title, .mail-app .sidebar .nav-btn, .mail-app .sidebar .routing-card', bild: 'links', d: 520, v: 320, s: 32, max: 18 },
            { sel: '.mail-app .list-head, .mail-app .filters', bild: 'hoch', d: 550, v: 380, s: 70 },
            { sel: '.mail-app .msg:not([style])', bild: 'karte', d: 600, v: 470, s: 55, max: 10, neu: true },
            { sel: '.mail-app .reader', bild: 'rechts', d: 650, v: 520 }
        ]),
        aufgaben: KOPF.concat([
            { sel: '.lh-tab', bild: 'hoch', d: 500, v: 420, s: 35, max: 10, neu: true },
            { sel: '.lh-kpi', bild: 'karte', d: 650, v: 380, s: 90, max: 4, neu: true, zaehlen: 'b' },
            { sel: '.lh-suche', bild: 'hoch', d: 550, v: 480 },
            { sel: '.lh-laden', bild: 'weich', d: 400, v: 520, s: 30, max: 10, neu: true },
            { sel: '.lh-karte', bild: 'karte', d: 650, v: 560, s: 45, max: 15, neu: true }
        ])
    }[seite];
    if (!REGELN) return;

    // Glanz über den Kopf (Pseudo-Element, damit der dunkle Modus nichts umfärbt)
    var css = document.createElement('style');
    css.textContent =
        'html.kgx-glanz .kgk-kopf::after,html.kgx-glanz .lh-kopf::after{content:"";position:absolute;top:0;bottom:0;left:0;width:42%;pointer-events:none;' +
        'background:linear-gradient(90deg,rgba(255,255,255,0) 0%,rgba(255,255,255,.10) 30%,rgba(255,255,255,.32) 50%,rgba(255,255,255,.10) 70%,rgba(255,255,255,0) 100%);' +
        'transform:translateX(-140%) skewX(-18deg);animation:kgxGlanz 1.5s .5s cubic-bezier(.45,0,.2,1) both}' +
        '@keyframes kgxGlanz{from{transform:translateX(-140%) skewX(-18deg)}to{transform:translateX(330%) skewX(-18deg)}}' +
        '@media print{html.kgx-glanz .kgk-kopf::after,html.kgx-glanz .lh-kopf::after{display:none}}';
    (document.head || root).appendChild(css);

    var T0 = null;            // Startzeit (erst wenn die Seite sichtbar ist – im dunklen Modus nach dem Einfärben)
    var wartend = [];         // vor T0 angelegte, angehaltene Animationen
    var gruppen = {};         // je Regel: Startzeit und Zähler (Neuzeichnungen laufen auf derselben Zeitachse weiter)
    var gesehen = new WeakSet();
    var zaehlerFertig = false;

    var alle = [];
    function jetzt() { var t = document.timeline.currentTime; return t == null ? performance.now() : t; }

    function animieren(el, r, nr) {
        var g = gruppen[nr] || (gruppen[nr] = { start: null, n: 0 });
        if (T0 !== null && g.start === null) g.start = Math.max(T0, jetzt());
        var i = r.welle ? 0 : Math.min(g.n++, r.max || 0);
        var vorlauf = g.start === null ? 0 : g.start - T0;
        var v = Math.max(0, r.v - vorlauf) + (r.welle ? r.welle(el) : i * (r.s || 0));
        var a;
        try {
            a = el.animate(BILD[r.bild], { duration: r.d, delay: v, easing: WEICH, fill: 'backwards', iterations: r.n || 1 });
        } catch (e) { return; }
        alle.push(a);
        if (g.start === null) { a.pause(); wartend.push({ a: a, nr: nr }); }
        else try { a.startTime = g.start; } catch (e) { a.cancel(); }
        if (r.zaehlen && !zaehlerFertig) zaehlen(el.querySelector(r.zaehlen), v + 120, g.start);
    }

    // Zahlen (z. B. Aufgaben-Kacheln) laufen von 0 hoch – nur beim ersten Zeichnen
    var zaehlListe = [];
    function zaehlen(el, v, start) {
        if (!el) return;
        var ziel = parseInt(el.textContent, 10);
        if (!(ziel > 0) || String(ziel) !== el.textContent.trim()) return;
        zaehlListe.push(el);
        el.textContent = '0';
        var dauer = 900, beginn = null;
        function schritt() {
            var t = jetzt();
            if (T0 === null) { requestAnimationFrame(schritt); return; }
            if (beginn === null) beginn = (start === null ? T0 : start) + v;
            var p = Math.min(1, Math.max(0, (t - beginn) / dauer));
            el.textContent = String(Math.round(ziel * (1 - Math.pow(1 - p, 3))));
            if (p < 1 && el.isConnected) requestAnimationFrame(schritt);
            else el.textContent = String(ziel);
        }
        requestAnimationFrame(schritt);
    }

    function pruefen(knoten) {
        REGELN.forEach(function (r, nr) {
            var treffer = [];
            if (knoten.matches && knoten.matches(r.sel)) treffer.push(knoten);
            if (knoten.querySelectorAll) Array.prototype.push.apply(treffer, knoten.querySelectorAll(r.sel));
            treffer.forEach(function (el) {
                if (!r.extra && gesehen.has(el)) return;
                if (r.extra) { if (el['kgx' + nr]) return; el['kgx' + nr] = 1; } else gesehen.add(el);
                if (!r.neu && gruppen[nr] && gruppen[nr].start !== null && jetzt() - gruppen[nr].start > 1500) return;
                animieren(el, r, nr);
            });
        });
    }

    var beob = new MutationObserver(function (liste) {
        liste.forEach(function (m) {
            m.addedNodes.forEach(function (k) { if (k.nodeType === 1) pruefen(k); });
        });
        if (zaehlListe.length) zaehlerFertig = true;
    });
    beob.observe(root, { childList: true, subtree: true });

    function starten() {
        if (T0 !== null) return;
        T0 = jetzt();
        root.classList.add('kgx-glanz');
        wartend.forEach(function (w) {
            var g = gruppen[w.nr];
            if (g.start === null) g.start = T0;
            try { w.a.startTime = g.start; } catch (e) { w.a.cancel(); }
        });
        wartend = [];
        Object.keys(gruppen).forEach(function (k) { if (gruppen[k].start === null) gruppen[k].start = T0; });
        // Nach dem Auftritt alles wieder normal: Beobachter aus, Glanz-Klasse weg
        setTimeout(function () { root.classList.remove('kgx-glanz'); }, 2400);
        setTimeout(function () { beob.disconnect(); }, 6000);
    }
    // Sicherheitsnetz: nie etwas unsichtbar lassen (z. B. wenn ein Start nicht klappt)
    setTimeout(function () {
        alle.forEach(function (a) { if (a.playState === 'paused') a.cancel(); });
    }, 4000);
    // Erst mit dem ersten sichtbaren Bild starten, damit nichts vom Auftritt verloren geht
    function bildStart() { requestAnimationFrame(function () { starten(); }); }

    // Dunkler Modus: kg_theme.js blendet die Seite erst nach dem Einfärben ein – dann starten
    if (root.getAttribute('data-kg-x') === 'pending') {
        var warte = new MutationObserver(function () {
            if (root.getAttribute('data-kg-x') !== 'pending') { warte.disconnect(); bildStart(); }
        });
        warte.observe(root, { attributes: true, attributeFilter: ['data-kg-x'] });
        setTimeout(function () { warte.disconnect(); bildStart(); }, 1600);
    } else {
        bildStart();
    }
})();
