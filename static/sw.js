// static/sw.js - GÜNCELLENMİŞ TAM DOSYA

self.addEventListener("install", (event) => {
  // Yeni worker yüklendiği an bekleme yapmadan eskisinin yerine geçsin
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // Mevcut tüm sekmeleri (clients) hemen bu SW'ye bağla ki "active değil" hatası vermesin
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", (event) => {
  // Bildirimi göster
  event.waitUntil(
    self.registration.showNotification("KG GEBÄUDEREINIGUNG", {
      body: "Bitte Stundenzettel ausfüllen / Lütfen Stundenzettel doldurun",
      tag: "stundenzettel-reminder",
      requireInteraction: true
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  
  // Bildirim tıklandığında gidilecek URL (Hatırlatma parametresiyle birlikte)
  const url = new URL("/stundenzettel?reminder=1&lang=de", self.registration.scope).href;

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      // Eğer sayfa zaten açıksa oraya odaklan (focus), değilse yeni pencere aç
      for (const client of clientList) {
        if (client.url === url && "focus" in client) return client.focus();
      }
      if (clients.openWindow) return clients.openWindow(url);
    })
  );
});