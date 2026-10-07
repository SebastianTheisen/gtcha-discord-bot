# gtcha-discord-bot

## Web-App „GTCHA Tracker“ (iPhone)

Private, inoffizielle App mit den Daten des Bots: alle Banner mit Ø Rückgabe, Top 10, Hit-Liste,
Pack-Verlauf, Versandschübe und Push-Benachrichtigungen. „Auf GTCHA ziehen“ öffnet die offizielle Seite.

Die App läuft als Container `gtcha-app` neben dem Bot, liest nur dessen Datenbank und lauscht nur auf
`127.0.0.1:8080`. Von außen erreichbar ist sie ausschließlich über **Tailscale** (kostenlos) – nur für
deine Geräte und Leute, mit denen du den Server teilst.

### Einrichtung (einmalig, auf dem VPS)

```bash
# 1. App starten
cd ~/gtcha-discord-bot && git pull origin main && docker compose up -d --build gtcha-app

# 2. Tailscale installieren und anmelden (Link im Terminal öffnen, mit deinem Konto bestätigen)
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up

# 3. App über Tailscale mit HTTPS bereitstellen (bleibt nach Neustarts aktiv)
tailscale serve --bg 8080
tailscale serve status        # zeigt die Adresse, z.B. https://vmd170353.tailXXXX.ts.net
```

Falls `tailscale serve` nach HTTPS-Zertifikaten fragt: in der Tailscale-Admin-Konsole unter
**DNS** „MagicDNS“ und „HTTPS Certificates“ einschalten.

### Beta (neue Oberfläche, parallel zur Live-App)

Läuft als eigener Container `gtcha-app-beta` mit denselben Daten und derselben Verknüpfung, aber ohne Pushes und
andere Hintergrundaufgaben – die Live-App bleibt unberührt.

```bash
cd ~/gtcha-discord-bot && ./update.sh beta                      # bauen und starten
tailscale serve --bg --set-path /beta http://127.0.0.1:8081     # einmalig: unter /beta erreichbar machen
```

Danach im Browser: App-Adresse + `/beta/` (z. B. `https://vmd170353.tailXXXX.ts.net/beta/`). Ein normales
`./update.sh` aktualisiert die Beta automatisch mit, solange sie läuft. Beenden: `docker stop gtcha-app-beta`.

### Auf dem iPhone

1. App **Tailscale** aus dem App Store laden, mit demselben Konto anmelden, VPN einschalten.
2. In **Safari** die Adresse aus `tailscale serve status` öffnen.
3. **Teilen → Zum Home-Bildschirm**, dann die App vom Home-Bildschirm öffnen.
4. Tab **Push** → „Pushes einschalten“ (geht nur in der installierten App, ab iOS 16.4).

### Auf Android oder am PC

- **Android:** Tailscale aus dem Play Store, anmelden, verbinden. Adresse in **Chrome** öffnen →
  „⋮“ → „App installieren“. Pushes gehen in Chrome und in der installierten App.
- **PC/Mac:** Tailscale von tailscale.com/download, anmelden. Adresse in **Chrome oder Edge** öffnen
  (installieren über das Symbol in der Adressleiste ist optional). Pushes kommen, solange der Browser läuft.

Die genaue Anleitung je Gerät (auch Lesezeichen in Chrome) steht in der App unter **Ich → 📖 Anleitung**.

### Pflicht: alle 7 Tage übertragen

Banner, Top 10, Archiv, Kartensuche und Banner-Pushes gibt es nur für Geräte, die mit Discord verknüpft sind
und deren Person in den letzten **7 Tagen** ihre GTCHA-Daten per Lesezeichen übertragen hat. Sonst zeigt die
App einen Sperrbildschirm mit Weg zum Übertragen; danach ist sofort alles wieder offen. Nach 3 und 6 Tagen
kommt eine Erinnerung per Push. Admins (`APP_ADMIN_IDS`) sind ausgenommen. Die Frist steht in
`webapp/server.py` (`SYNC_REQUIRED_DAYS`).

**Mehrere GTCHA-Konten einer Person** (z. B. eins in Safari, eins in Chrome): Das Lesezeichen schickt einen
die GTCHA-Mitglieds-ID mit. Im Reiter „👥 Nutzer“ bei der Person „Anzahl Konten“
einstellen – dann müssen alle Konten alle 7 Tage übertragen; ein noch nie übertragenes Konto sperrt sofort.

### Weitere Personen freischalten

In der Tailscale-Admin-Konsole beim VPS **Share…** wählen und die Person einladen. Sie braucht
ein eigenes (kostenloses) Tailscale-Konto und die Tailscale-App, sieht aber nur diesen Server.
Zugriff entziehen: dort die Freigabe wieder entfernen.

### Hits über die App melden (Medaillen)

1. In Discord `/tracker-verknüpfen` eingeben – der Bot zeigt dir (nur für dich) einen Code, 10 Minuten gültig.
2. In der App unter **Push → Discord verknüpfen** den Code eingeben.
3. Auf jeder Banner-Seite unter **„🏅 Gezogen melden“** auf „Ich hab's gezogen“ tippen. Der Bot prüft die
   Meldung wie ein „T1“ im Thread, postet sie im Discord-Thread (als dein Discord-Name) und aktualisiert
   Hit-Liste und Rechnung. Eigene Meldungen lassen sich dort auch zurücknehmen.

Melden kann man alle Karten ab Packpreis (T1 = teuerste, höchstens T50).

### Nützliches

```bash
docker logs -f gtcha-app                 # Log der App
./update.sh                              # nach Updates: holt main, baut nur Geändertes, prüft danach
./update.sh --alles                      # alles neu bauen
```

Optional in `.env`: `WEBAPP_PORT` (Standard 8080), `WEBAPP_CONTACT` (Kontakt für Push-Dienste,
`mailto:…` oder `https://domain`).
