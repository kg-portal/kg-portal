const { Client, LocalAuth } = require('whatsapp-web.js');
const fs = require('fs');
const qrcodeTerminal = require('qrcode-terminal');
const QRCode = require('qrcode');
const express = require('express');
const axios = require('axios');
const cors = require('cors');

const CRM_BASE_URL = process.env.CRM_BASE_URL || 'http://127.0.0.1:5000';
const CRM_CONNECTOR_TOKEN = process.env.CRM_CONNECTOR_TOKEN || 'CHANGE_ME_KG_TOKEN';
const CONNECTOR_PORT = Number(process.env.CONNECTOR_PORT || 3010);

const BROWSER_PATHS = [
    process.env.CHROME_EXECUTABLE_PATH,
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
].filter(Boolean);

const EXECUTABLE_PATH = BROWSER_PATHS.find(p => fs.existsSync(p));

let isReady = false;
let lastError = '';
let sendingOutbox = false;
let latestQrDataUrl = '';

const app = express();
app.use(cors());
app.use(express.json({ limit: '20mb' }));

function cleanPhone(value) {
    return String(value || '').replace('@c.us', '').replace(/\D/g, '');
}

function toChatId(value) {
    const v = String(value || '').trim();

    if (v.endsWith('@c.us') || v.endsWith('@lid')) {
        return v;
    }

    return `${cleanPhone(v)}@c.us`;
}
function authHeaders() {
    return {
        'X-KG-WA-TOKEN': CRM_CONNECTOR_TOKEN,
        'Content-Type': 'application/json'
    };
}

const client = new Client({
    authStrategy: new LocalAuth({
        clientId: 'kg-crm-damla',
        dataPath: process.env.WA_SESSION_PATH || './session'
    }),
    puppeteer: {
        executablePath: EXECUTABLE_PATH,
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
    }
});

client.on('qr', async (qr) => {
    try {
        isReady = false;

        latestQrDataUrl = await QRCode.toDataURL(qr, {
            width: 420,
            margin: 3,
            errorCorrectionLevel: 'M'
        });

        console.log('');
        console.log('============================================================');
        console.log('KG WHATSAPP CONNECTOR - QR KOD HAZIR');
        console.log('Temiz QR sayfasi: /qr');
        console.log('============================================================');
        console.log('');

        qrcodeTerminal.generate(qr, { small: true });

    } catch (err) {
        lastError = `QR oluşturma hatası: ${err.message}`;
        console.log(lastError);
    }
});

client.on('ready', () => {
    isReady = true;
    lastError = '';
    console.log('');
    console.log('============================================================');
    console.log('KG WHATSAPP CONNECTOR HAZIR');
    console.log('Damla WhatsApp hesabi CRM Connector ile baglandi.');
    console.log('============================================================');
    console.log('');
});

client.on('auth_failure', (msg) => {
    isReady = false;
    lastError = `auth_failure: ${msg}`;
    console.log('AUTH FEHLER:', msg);
});

client.on('disconnected', (reason) => {
    isReady = false;
    lastError = `disconnected: ${reason}`;
    console.log('WHATSAPP KOPTU:', reason);
});

client.on('message_create', async (msg) => {
    try {
        console.log('');
        console.log('MESSAGE_CREATE TEST');
        console.log('fromMe:', msg.fromMe);
        console.log('from:', msg.from);
        console.log('to:', msg.to);
        console.log('body:', msg.body);
        console.log('');

        if (msg.fromMe && msg.to && msg.to.endsWith('@lid')) {
            await axios.post(
                `${CRM_BASE_URL}/api/whatsapp-connector/learn-id`,
                {
                    raw_from: msg.to
                },
                { headers: authHeaders(), timeout: 30000 }
            );

            console.log('LID CRM e kaydedildi:', msg.to);
        }

        // Damla kendi WhatsApp sayfasına yazarsa:
        // Bu sohbet KG AI ana pencere gibi çalışır.
        // Buraya yazılan metin aktif iş ilanı olarak Flask'a gider.
        if (msg.fromMe === true && msg.from && msg.to && msg.body) {
            const selfIds = [
                '491631947055',
                '274487199191086'
            ];

            const fromClean = cleanPhone(msg.from);
            const toClean = cleanPhone(msg.to);

            const isSelfWindow =
                selfIds.includes(fromClean) &&
                selfIds.includes(toClean);

            if (isSelfWindow) {
                const bodyText = String(msg.body || '').trim();

                // AI'nin kendi onay mesajını tekrar ilan olarak kaydetmesini engelle.
                if (
                    bodyText === 'Aktif iş ilanı bilgisi kaydedildi. Bundan sonra iş için yazanlara bu bilgiyi kullanacağım.' ||
                    bodyText.toLowerCase().includes('aktif iş ilanı bilgisi kaydedildi') ||
                    bodyText.toLowerCase().includes('aktif is ilani bilgisi kaydedildi')
                ) {
                    console.log('AI SELF WINDOW atlandi: kendi onay mesaji');
                    return;
                }

                // Aynı LID'den aynı LID'ye giden otomatik cevapları da atla.
                if (fromClean === toClean) {
                    console.log('AI SELF WINDOW atlandi: kendi kendine otomatik cevap');
                    return;
                }

                const payload = {
                    wa_message_id: msg.id && msg.id._serialized ? msg.id._serialized : '',
                    from: msg.from || '',
                    to: msg.to || '',
                    fromMe: true,
                    phone: cleanPhone(msg.to || ''),
                    name: '',
                    body: bodyText,
                    type: msg.type || '',
                    timestamp: msg.timestamp || null
                };

                await axios.post(
                    `${CRM_BASE_URL}/api/whatsapp-connector/incoming`,
                    payload,
                    { headers: authHeaders(), timeout: 30000 }
                );

                console.log('AI SELF WINDOW CRM e kaydedildi:', payload.body);
            }
        }

    } catch (err) {
        lastError = err.message;
        console.log('MESSAGE_CREATE LOG HATASI:', err.message);
    }
});

client.on('message', async (msg) => {
    try {
        if (msg.fromMe) return;
        if (!msg.from) return;

        const allowedChat =
            msg.from.endsWith('@c.us') ||
            msg.from.endsWith('@lid');

        if (!allowedChat) return;

        const contact = await msg.getContact();

        const realPhone =
            contact.number ||
            contact.id?.user ||
            msg.from;

        const payload = {
            wa_message_id: msg.id && msg.id._serialized ? msg.id._serialized : '',
            from: msg.from,
            phone: cleanPhone(realPhone),
            name: contact.pushname || contact.name || contact.shortName || '',
            body: msg.body || '',
            type: msg.type || '',
            timestamp: msg.timestamp || null
        };

        console.log('');
        console.log('GELEN MESAJ:', payload.phone, payload.body);
        console.log('');

        await axios.post(
            `${CRM_BASE_URL}/api/whatsapp-connector/incoming`,
            payload,
            { headers: authHeaders(), timeout: 30000 }
        );

        console.log('CRM e kaydedildi.');

    } catch (err) {
        lastError = err.message;
        console.log('Gelen mesaji CRM e yollama hatasi:', err.message);
    }
});

async function pollOutbox() {
    if (!isReady || sendingOutbox) return;

    sendingOutbox = true;

    try {
        const response = await axios.get(
            `${CRM_BASE_URL}/api/whatsapp-connector/outbox?limit=10`,
            { headers: authHeaders(), timeout: 30000 }
        );

        const items = response.data && response.data.items ? response.data.items : [];

        for (const item of items) {
            try {
                await client.sendMessage(toChatId(item.phone), String(item.text || '').trim());

                await axios.post(
                    `${CRM_BASE_URL}/api/whatsapp-connector/outbox/${item.id}/sent`,
                    { ok: true },
                    { headers: authHeaders(), timeout: 30000 }
                );

                console.log('GIDEN MESAJ:', item.phone, item.text);

            } catch (sendErr) {
                await axios.post(
                    `${CRM_BASE_URL}/api/whatsapp-connector/outbox/${item.id}/error`,
                    { ok: false, error: sendErr.message },
                    { headers: authHeaders(), timeout: 30000 }
                );

                console.log('Gonderme hatasi:', sendErr.message);
            }
        }

    } catch (err) {
        lastError = err.message;
    } finally {
        sendingOutbox = false;
    }
}

setInterval(pollOutbox, 3000);

app.get('/qr', (req, res) => {
    if (isReady) {
        return res.send(`
            <!doctype html>
            <html lang="tr">
            <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <title>KG WhatsApp Bağlandı</title>
            </head>
            <body style="font-family:Arial;text-align:center;padding:40px">
                <h1>WhatsApp bağlantısı hazır</h1>
                <p>Yeni QR okutmaya gerek yok.</p>
            </body>
            </html>
        `);
    }

    if (!latestQrDataUrl) {
        return res.status(503).send(`
            <!doctype html>
            <html lang="tr">
            <head>
                <meta charset="utf-8">
                <meta http-equiv="refresh" content="3">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <title>QR hazırlanıyor</title>
            </head>
            <body style="font-family:Arial;text-align:center;padding:40px">
                <h1>QR hazırlanıyor...</h1>
                <p>Sayfa otomatik yenilenecek.</p>
            </body>
            </html>
        `);
    }

    return res.send(`
        <!doctype html>
        <html lang="tr">
        <head>
            <meta charset="utf-8">
            <meta http-equiv="refresh" content="20">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>KG WhatsApp QR</title>
        </head>
        <body style="font-family:Arial;text-align:center;padding:30px;background:#f5f7fb">
            <div style="display:inline-block;background:white;padding:28px;border-radius:18px">
                <h1>Damla WhatsApp bağlantısı</h1>
                <img src="${latestQrDataUrl}" alt="WhatsApp QR" style="width:420px;max-width:90vw">
                <p>WhatsApp → Bağlı cihazlar → Cihaz bağla</p>
            </div>
        </body>
        </html>
    `);
});

app.get('/status', (req, res) => {
    res.json({
        ok: true,
        whatsapp_ready: isReady,
        crm_base_url: CRM_BASE_URL,
        browser_path: EXECUTABLE_PATH || '',
        last_error: lastError
    });
});

app.post('/send-message', async (req, res) => {
    try {
        if (!isReady) {
            return res.status(503).json({ ok: false, message: 'WhatsApp hazir degil.' });
        }

        const phone = cleanPhone(req.body.phone || req.body.to || '');
        const text = String(req.body.text || '').trim();

        if (!phone || !text) {
            return res.status(400).json({ ok: false, message: 'phone/to ve text gerekli.' });
        }

        await client.sendMessage(toChatId(phone), text);

        res.json({ ok: true, phone, text });

    } catch (err) {
        lastError = err.message;
        res.status(500).json({ ok: false, message: err.message });
    }
});

app.listen(CONNECTOR_PORT, () => {
    console.log('');
    console.log('============================================================');
    console.log('KG WHATSAPP CONNECTOR BASLADI');
    console.log(`Local status: http://localhost:${CONNECTOR_PORT}/status`);
    console.log(`CRM_BASE_URL: ${CRM_BASE_URL}`);
    console.log(`Browser: ${EXECUTABLE_PATH || 'BULUNAMADI'}`);
    console.log('QR kod bekleniyor...');
    console.log('============================================================');
    console.log('');
});

client.initialize();
