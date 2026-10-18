// Auftritt beim Öffnen einer Seite (nur Anzeige, keine Daten werden geändert).
// Kopf gleitet herein, Glanz läuft einmal darüber, danach erscheinen Menü und Inhalt weich nacheinander.
// Nur beim ersten Öffnen: spätere Neuzeichnungen (Monat wechseln, Suche, Filter) bleiben ohne Effekt.
// Einbinden: <script src="/static/kg_auftritt.js" data-seite="…"></script> vor dem Seitenkopf.
// Kein Effekt beim Drucken/PDF (print=1, drucken=1, automatischer Browser) und in Vorschau-Rahmen (data-nur-oben).
(function () {
    var root = document.documentElement;
    var skript = document.currentScript;
    var seite = skript && skript.getAttribute('data-seite');
    if (!seite || window.__kgAuftritt || !window.MutationObserver || !Element.prototype.animate || !document.timeline) return;
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    if (navigator.webdriver || /[?&](print|drucken)=1\b/.test(location.search)) return;
    if (skript.getAttribute('data-nur-oben') && window.self !== window.top) return;
    window.__kgAuftritt = true;

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
        pop: [{ opacity: 0, transform: 'scale(.7)' }, { opacity: 1, transform: 'scale(1.06)', offset: .7 }, { opacity: 1, transform: 'none' }],
        tag: [{ opacity: 0, transform: 'translateY(10px) scale(.9)' }, { opacity: 1, transform: 'none' }],
        weich: [{ opacity: 0 }, { opacity: 1 }],
        puls: [{ boxShadow: '0 0 0 0 rgba(37,99,235,.55)' }, { boxShadow: '0 0 0 12px rgba(37,99,235,0)' }],
        kugel: [{ opacity: 0, transform: 'scale(.3) rotate(-35deg)' }, { opacity: 1, transform: 'scale(1.1) rotate(4deg)', offset: .65 }, { opacity: 1, transform: 'none' }]
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
    // Allgemeiner Kopf: Kasten, Titel, Untertitel, rechte Seite
    function kopf(box, titel, unter, rechts) {
        var r = [{ sel: box, bild: 'kopf', d: 700, v: 0 }];
        if (titel) r.push({ sel: titel, bild: 'links', d: 650, v: 220 });
        if (unter) r.push({ sel: unter, bild: 'links', d: 650, v: 310, s: 60, max: 4 });
        if (rechts) r.push({ sel: rechts, bild: 'rechts', d: 650, v: 380, s: 60, max: 4 });
        return r;
    }
    var LEON_KOPF = '.kgb-hero, .na .na-hero, .ak-page .ak-hero, .kbl .kbl-hero, .kb .kb-hero, .leon-wrap .leon-head, .ldb .ldb-head';
    var LEON_KOPF_IN = function (x) { return LEON_KOPF.split(', ').map(function (k) { return k + ' ' + x; }).join(', '); };
    var EDITOR = kopf('.kgae-kopf', '.kgae-kopf > div:first-child > *', null, null).concat([
        { sel: '.kgae-schritte > *', bild: 'pop', d: 520, v: 420, s: 70, max: 4 },
        { sel: '.kgae > .kgae-liste', bild: 'hoch', d: 560, v: 360 },
        { sel: '.kgae-raster > main > *', bild: 'hoch', d: 600, v: 440, s: 90, max: 4 },
        { sel: '.kgae-vorschau', bild: 'rechts', d: 700, v: 520 }
    ]);
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
        ]),
        start: [
            { sel: '.index-hero', bild: 'kopf', d: 700, v: 0 },
            { sel: '.index-logo-box', bild: 'symbol', d: 850, v: 160 },
            { sel: '.index-logo-box', bild: 'ring', d: 1100, v: 800, extra: true },
            { sel: '.index-title', bild: 'links', d: 650, v: 240 },
            { sel: '.index-kal-box', bild: 'pop', d: 700, v: 320 },
            { sel: '.index-wetter-gross', bild: 'rechts', d: 700, v: 420 }
        ],
        whatsapp: kopf('.wa-header', '.wa-header > div:first-child', null, '.wa-header-actions > *').concat([
            { sel: '.wa-grid > .wa-box', bild: 'karte', d: 650, v: 420, s: 110, max: 2 }
        ]),
        leon: [
            { sel: 'nav.kg-leon-tabs', bild: 'runter', d: 600, v: 0 },
            { sel: 'nav.kg-leon-tabs .lo-name', bild: 'weich', d: 500, v: 160, s: 60, max: 4 },
            { sel: 'nav.kg-leon-tabs a', bild: 'pop', d: 480, v: 140, s: 24, max: 16 },
            { sel: LEON_KOPF, bild: 'kopf', d: 700, v: 120 },
            { sel: LEON_KOPF, bild: 'kugel', d: 950, v: 260, pseudo: '::before', extra: true },
            { sel: LEON_KOPF_IN('h1'), bild: 'links', d: 650, v: 340 },
            { sel: LEON_KOPF_IN('p'), bild: 'links', d: 650, v: 420, s: 60, max: 3 },
            { sel: LEON_KOPF_IN('.kg-pill'), bild: 'rechts', d: 650, v: 460 },
            { sel: LEON_KOPF_IN('.kg-stat'), bild: 'karte', d: 650, v: 480, s: 80, max: 6 },
            { sel: '.business-portal-card > :not(.kgb-hero), .leon-card, .na-card, .ak-card, .ldb-card, .kbl-box, .kbl-leer', bild: 'hoch', d: 600, v: 560, s: 80, max: 6 },
            { sel: 'table tbody > tr', bild: 'hoch', d: 520, v: 640, s: 35, max: 12, neu: true }
        ],
        rueckrufe: kopf('.rr-hero', '.rr-hero-row > div:first-child', null, '.rr-today').concat([
            { sel: '.rr-stat', bild: 'karte', d: 650, v: 400, s: 90, max: 4, zaehlen: '.rr-stat-value' },
            { sel: '.rr-body', bild: 'hoch', d: 600, v: 520 },
            { sel: '#rrList > *', bild: 'karte', d: 600, v: 600, s: 50, max: 12, neu: true }
        ]),
        interesse: kopf('.in-hero', '.in-hero-row > div:first-child', null, '.in-today').concat([
            { sel: '.in-stat', bild: 'karte', d: 650, v: 400, s: 90, max: 4, zaehlen: '.in-stat-value' },
            { sel: '.in-body', bild: 'hoch', d: 600, v: 520 },
            { sel: '#inList > *', bild: 'karte', d: 600, v: 600, s: 50, max: 12, neu: true }
        ]),
        angebot: EDITOR,
        vertrag: EDITOR,
        lv: kopf('.lv-hero', '.lv-hero .lv-title', null, null).concat([
            { sel: '.lv-info-grid > *', bild: 'karte', d: 600, v: 340, s: 80, max: 4 },
            { sel: '.lv-content > *', bild: 'hoch', d: 600, v: 460, s: 70, max: 8 },
            { sel: '.standard-grid > *', bild: 'karte', d: 650, v: 560, s: 90, max: 4 }
        ]),
        mitarbeiter: kopf('.kg-mit-hero', '.kg-mit-hero h2', '.kg-mit-hero p', '.kg-mit-hero-actions > *').concat([
            { sel: '.kg-mit-toolbar', bild: 'hoch', d: 600, v: 420 },
            { sel: '#mitarbeiterSortable > tr', bild: 'hoch', d: 520, v: 500, s: 40, max: 12 },
            { sel: '.kg-mit-blue-line, .kg-mit-archive-hero, .kg-mit-archive-card', bild: 'hoch', d: 600, v: 620, s: 60, max: 3 }
        ]),
        stundenzettel: kopf('.stz2-hero', '.stz2-titel > div:last-child', null, '.stz2-monat').concat([
            { sel: '.stz2-icon', bild: 'symbol', d: 850, v: 160 },
            { sel: '.stz2-icon', bild: 'ring', d: 1100, v: 800, extra: true },
            { sel: '.stz2-links > *', bild: 'pop', d: 500, v: 420, s: 70, max: 4 },
            { sel: '.stz2-kpi', bild: 'karte', d: 650, v: 420, s: 90, max: 4, zaehlen: 'b' },
            { sel: '.stz2-werkzeug', bild: 'hoch', d: 550, v: 520 },
            { sel: '.stz2-karte', bild: 'karte', d: 650, v: 580, s: 50, max: 12, neu: true }
        ]),
        buchhaltung: kopf('.buchhaltung-hero', '.bh-titel > div:last-child > :not(.bh-chips)', '.bh-chips > *', '.live-date-box').concat([
            { sel: '.bh-symbol', bild: 'symbol', d: 850, v: 160 },
            { sel: '.bh-symbol', bild: 'ring', d: 1100, v: 800, extra: true },
            { sel: '.bh-kpi', bild: 'karte', d: 650, v: 420, s: 90, max: 4, zaehlen: '.h4, .h2' },
            { sel: '.buchhaltung-body > .nav-tabs', bild: 'hoch', d: 600, v: 520 },
            { sel: '.buchhaltung-body > .nav-tabs > .nav-item', bild: 'pop', d: 480, v: 580, s: 45, max: 6 },
            { sel: '.tab-pane.active .list-group-item-action', bild: 'pop', d: 460, v: 660, s: 28, max: 12 },
            { sel: '.tab-pane.active tbody > tr, .tab-pane.active .list-group.shadow-sm > .list-group-item', bild: 'hoch', d: 520, v: 760, s: 40, max: 12 }
        ])
    }[seite];
    if (!REGELN) return;

    // Glanz über den Kopf (Pseudo-Element, damit der dunkle Modus nichts umfärbt)
    var GLANZ = ['.index-hero::before', '.wa-header::after', '.rr-hero::after', '.in-hero::after', '.kgae-kopf::after', '.lv-hero::after',
        '.kg-mit-hero::after', '.stz2-hero::before', '.bh-p .buchhaltung-hero::after'];
    var g2 = GLANZ.map(function (x) { return 'html.kgx-glanz ' + x; }).join(',');
    var css = document.createElement('style');
    css.textContent =
        'html.kgx-glanz .kgk-kopf::after,html.kgx-glanz .lh-kopf::after{content:"";position:absolute;top:0;bottom:0;left:0;width:42%;pointer-events:none;' +
        'background:linear-gradient(90deg,rgba(255,255,255,0) 0%,rgba(255,255,255,.10) 30%,rgba(255,255,255,.32) 50%,rgba(255,255,255,.10) 70%,rgba(255,255,255,0) 100%);' +
        'transform:translateX(-140%) skewX(-18deg);animation:kgxGlanz 1.5s .5s cubic-bezier(.45,0,.2,1) both}' +
        '@keyframes kgxGlanz{from{transform:translateX(-140%) skewX(-18deg)}to{transform:translateX(330%) skewX(-18deg)}}' +
        g2 + '{content:"";position:absolute;top:0;right:0;bottom:0;left:0;z-index:3;border-radius:inherit;pointer-events:none;' +
        'background:linear-gradient(105deg,rgba(255,255,255,0) 40%,rgba(255,255,255,.12) 45%,rgba(255,255,255,.3) 50%,rgba(255,255,255,.12) 55%,rgba(255,255,255,0) 60%) no-repeat;' +
        'background-size:250% 100%;animation:kgxGlanz2 1.5s .5s cubic-bezier(.45,0,.2,1) both}' +
        '@keyframes kgxGlanz2{from{background-position:100% 0}to{background-position:0% 0}}' +
        'html.kgx-glanz .rr-hero,html.kgx-glanz .in-hero,html.kgx-glanz .lv-hero{position:relative}' +
        '@media print{html.kgx-glanz .kgk-kopf::after,html.kgx-glanz .lh-kopf::after,' + g2 + '{display:none}}';
    (document.head || root).appendChild(css);

    var T0 = null;            // Startzeit (erst wenn die Seite sichtbar ist – im dunklen Modus nach dem Einfärben)
    var wartend = [];         // vor T0 angelegte, angehaltene Animationen
    var gruppen = {};         // je Regel: Startzeit und Zähler (Neuzeichnungen laufen auf derselben Zeitachse weiter)
    var gesehen = new WeakSet();

    var alle = [];
    function jetzt() { var t = document.timeline.currentTime; return t == null ? performance.now() : t; }

    function animieren(el, r, nr) {
        var g = gruppen[nr] || (gruppen[nr] = { start: null, n: 0 });
        if (T0 !== null && g.start === null) g.start = Math.max(T0, jetzt());
        var i = r.welle ? 0 : Math.min(g.n++, r.max || 0);
        var vorlauf = g.start === null ? 0 : g.start - T0;
        var v = Math.max(0, r.v - vorlauf) + (r.welle ? r.welle(el) : i * (r.s || 0));
        var a, opt = { duration: r.d, delay: v, easing: WEICH, fill: 'backwards', iterations: r.n || 1 };
        if (r.pseudo) {
            // Pseudo-Elemente (z. B. Leon-Kugel) nur, wenn der Browser das kann – sonst gar nicht
            if (!window.KeyframeEffect || !('pseudoElement' in KeyframeEffect.prototype)) return;
            opt.pseudoElement = r.pseudo;
        }
        try {
            a = el.animate(BILD[r.bild], opt);
        } catch (e) { return; }
        alle.push(a);
        if (g.start === null) { a.pause(); wartend.push({ a: a, nr: nr }); }
        else try { a.startTime = g.start; } catch (e) { a.cancel(); }
        // Zählen nur beim ersten Zeichnen (kurz nach dem Start der Gruppe), nicht bei späteren Neuzeichnungen
        if (r.zaehlen && (g.start === null || jetzt() - g.start < 300)) zaehlen(el.querySelector(r.zaehlen), v + 120, g.start);
    }

    // Zahlen laufen von 0 hoch (ganze Zahlen und Beträge wie „15.156,39 €“) – nur beim ersten Zeichnen.
    // Schreibt die Seite selbst einen neuen Wert hinein, hört das Zählen sofort auf.
    function zaehlen(el, v, start) {
        if (!el || el.children.length) return;
        var text = el.textContent.trim();
        var m = /^(-?)(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?(\s*(?:€|%)?)$/.exec(text);
        if (!m) return;
        var dez = m[3] ? m[3].length : 0;
        var ziel = Number(m[2].replace(/\./g, '') + (dez ? '.' + m[3] : '')) * (m[1] ? -1 : 1);
        if (!ziel) return;
        var gruppe = m[2].indexOf('.') >= 0;
        function form(x) {
            return x.toLocaleString('de-DE', { minimumFractionDigits: dez, maximumFractionDigits: dez, useGrouping: gruppe }) + m[4];
        }
        var original = el.textContent;
        var geschrieben = form(0);
        el.textContent = geschrieben;
        var dauer = 1000, beginn = null;
        function schritt() {
            if (el.textContent !== geschrieben) return;
            if (T0 === null) { requestAnimationFrame(schritt); return; }
            if (beginn === null) beginn = (start === null ? T0 : start) + v;
            var p = Math.min(1, Math.max(0, (jetzt() - beginn) / dauer));
            if (p < 1 && el.isConnected) {
                geschrieben = form(ziel * (1 - Math.pow(1 - p, 3)));
                el.textContent = geschrieben;
                requestAnimationFrame(schritt);
            } else {
                el.textContent = original;
            }
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
    // Drucken: sofort alles im Endzustand zeigen
    function sofortFertig() {
        alle.forEach(function (a) { try { a.finish(); } catch (e) { a.cancel(); } });
        root.classList.remove('kgx-glanz');
    }
    window.addEventListener('beforeprint', sofortFertig);
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
