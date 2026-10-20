// Digitale Unterschrift im Mail-Fenster (Angebot: gmail_mini.html, Vertrag: vertrag_mail.html).
// Kästchen „Button Digital unterschreiben“ (Testphase: aus) → Absatz + blauer Button in der Mail.
// Der persönliche Link entsteht erst beim Senden (POST /api/esign/anlegen, esign.py) und wird in den Button geschrieben.
(function () {
    function art() {
        if (typeof miniData === 'undefined') return '';
        if (miniData.modus === 'vertrag' && Number(miniData.vertragId)) return 'vertrag';
        if (miniData.modus === 'angebot' && Number(miniData.angebotId)) return 'angebot';
        return '';
    }
    function buttonHtml(a) {
        const satz = a === 'angebot'
            ? 'Sie können das Angebot auch ganz bequem digital annehmen – per Smartphone, Tablet oder Computer, ohne Ausdrucken und Einscannen:'
            : 'Noch einfacher: Sie können den Vertrag auch digital unterschreiben – per Smartphone, Tablet oder Computer, ohne Ausdrucken und Einscannen:';
        const knopf = a === 'angebot' ? 'Angebot digital unterschreiben' : 'Vertrag digital unterschreiben';
        return '<p>' + satz + '</p>' +
            '<p style="margin:16px 0 22px 0;"><a href="#esign" data-esign-link="1" style="display:inline-block;background:#1d4ed8;color:#ffffff;text-decoration:none;font-weight:700;font-size:15px;line-height:1.2;padding:13px 22px;border-radius:8px;">✍&nbsp; ' + knopf + '</a></p>';
    }
    // Absatz nach „… unterzeichnete Bestätigung auf Seite 3 …“ (Angebot) bzw. „… unterschreiben Sie ihn auf Seite 4 …“ (Vertrag)
    function blockSetzen() {
        const box = document.getElementById('mailMessage'), an = document.getElementById('esignAn');
        if (!box) return;
        box.querySelectorAll('[data-esign]').forEach(x => x.remove());
        const a = art();
        if (!a || !an || !an.checked) return;
        const anker = a === 'angebot' ? 'Bestätigung auf Seite 3' : 'auf Seite 4';
        const ziel = Array.from(box.querySelectorAll('p')).find(p => p.textContent.indexOf(anker) >= 0);
        const div = document.createElement('div');
        div.setAttribute('data-esign', '1');
        div.innerHTML = buttonHtml(a);
        if (ziel) ziel.after(div); else box.appendChild(div);
    }
    window.esignVorbereiten = async function (fenster) {
        const box = document.getElementById('mailMessage');
        const link = box ? box.querySelector('[data-esign-link]') : null;
        if (!link) return true;
        const a = art();
        const to = (document.getElementById('mailTo').value || '').trim();
        if (!a || !to) return true;  // ohne Empfänger meldet das Senden selbst „Empfänger fehlt“
        setStatus('Link für die digitale Unterschrift wird erstellt …', '');
        try {
            const r = await fetch('/api/esign/anlegen', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ art: a, id: Number(a === 'vertrag' ? miniData.vertragId : miniData.angebotId) || 0, email: to }) });
            const d = await r.json().catch(() => ({}));
            if (!r.ok || !d.ok) throw new Error(d.error || ('Fehler ' + r.status));
            link.setAttribute('href', d.url);
            return true;
        } catch (e) {
            setStatus('Digitale Unterschrift: ' + (e.message || 'Fehler') + ' – die Mail wurde nicht gesendet.', 'err');
            try { if (fenster) fenster.close(); } catch (x) { /* Fenster schon zu */ }
            return false;
        }
    };
    window.esignInit = function () {
        if (!art()) return;
        const feld = document.getElementById('mailMessage');
        const rahmen = feld ? feld.closest('.field') : null;
        if (!rahmen || document.getElementById('esignAn')) return;
        const zeile = document.createElement('label');
        zeile.id = 'esignOpt';
        zeile.style.cssText = 'display:flex;align-items:center;gap:9px;margin:0 0 10px;padding:10px 12px;border:1px dashed #93c5fd;border-radius:12px;background:#eff6ff;color:#1e3a8a;font-size:13px;font-weight:800;cursor:pointer;';
        zeile.innerHTML = '<input type="checkbox" id="esignAn" style="width:18px;height:18px;margin:0;accent-color:#1d4ed8;flex:none">' +
            '<span>Button „' + (art() === 'angebot' ? 'Angebot' : 'Vertrag') + ' digital unterschreiben“ in die Mail einfügen <small style="font-weight:700;color:#64748b">(Test)</small></span>';
        rahmen.parentNode.insertBefore(zeile, rahmen);
        document.getElementById('esignAn').addEventListener('change', blockSetzen);
        // nach jedem Neuaufbau der Mail (Vorlage gewählt) den Button wieder einsetzen
        if (typeof buildDefaultMail === 'function') {
            const original = buildDefaultMail;
            window.buildDefaultMail = function () { const r = original.apply(this, arguments); blockSetzen(); return r; };
        }
    };
})();
