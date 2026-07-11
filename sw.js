/* Service Worker fuer das Niederschlagsradar.
   Strategie: NETWORK-FIRST fuer alles. Solange Internet da ist, wird IMMER die aktuelle
   Version vom Server geladen (garantiert frischer Stand, auch in der installierten PWA auf iOS).
   Nur wenn offline, wird auf den letzten gecachten Stand zurueckgegriffen (Offline-Bonus).
   Die Radar-Bilder/frames.json sind ohnehin zeitkritisch und tragen eigene Cache-Buster. */

const CACHE = 'radar-v2';          // bei groesseren Aenderungen hochzaehlen -> alter Cache wird geloescht

// Bei Installation sofort aktiv werden (nicht auf Schliessen aller Tabs warten)
self.addEventListener('install', function(e){ self.skipWaiting(); });

// Bei Aktivierung alte Caches wegraeumen und Kontrolle uebernehmen
self.addEventListener('activate', function(e){
  e.waitUntil(
    caches.keys().then(function(keys){
      return Promise.all(keys.filter(function(k){ return k!==CACHE; }).map(function(k){ return caches.delete(k); }));
    }).then(function(){ return self.clients.claim(); })
  );
});

self.addEventListener('fetch', function(e){
  const req = e.request;
  if(req.method !== 'GET'){ return; }                        // nur GET cachen
  const url = new URL(req.url);
  if(url.origin !== self.location.origin){ return; }         // fremde Hosts (Kartenkacheln, CDN) nicht anfassen

  // Network-first: erst Netz versuchen, bei Erfolg Cache aktualisieren; bei Fehler aus Cache liefern.
  e.respondWith(
    fetch(req).then(function(res){
      if(res && res.status===200 && res.type==='basic'){
        const copy = res.clone();
        caches.open(CACHE).then(function(c){ c.put(req, copy); });
      }
      return res;
    }).catch(function(){
      return caches.match(req).then(function(hit){ return hit || caches.match('./'); });
    })
  );
});
