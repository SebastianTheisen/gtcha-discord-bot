# Anleitung: Hit-Erkennung beobachten und nach 30 Tagen nachschärfen

Seit dem 03.10.2026 läuft die Hit-Erkennung mit diesen Regeln:

1. **Medaille = Versand angefordert**: Ein Hit mit Medaille (Discord, App, Lesezeichen) steckt im ersten
   Versand-Schub nach der Medaille – auch wenn die Seite dafür einen anderen Wert zählt (24188: Mewtwo als
   19.580 gezählt). Admin-Haken („durch Admin abgehakt“) zählen dafür nicht. Falsch gesetzte Medaillen bitte
   entfernen, sonst wird der falsche Schub zugeordnet.
2. **Große Schübe zuerst nur aus Versand-Hits**: Geht ein Schub allein aus Versand-Hits auf, zählen nur diese
   Erklärungen.
3. **Billige Karten zuletzt**: Karten unter 3× Packpreis werden fast immer umgewandelt. Sie zählen nur, wenn es
   ohne sie nicht aufgeht.
4. **Unklares bleibt ❓**: Mit Wahrscheinlichkeit, aber ohne Haken.

## Laufend (ohne festen Termin)

- **Kontrolle**: App → Reiter „👥 Nutzer“ → „🤖 Automatisch abgehakt“. Dort steht alles, was der Bot in den
  letzten 14 Tagen selbst abgehakt hat.
  - Stimmt etwas nicht, tippst du auf **„❌ war falsch“**. Der Haken verschwindet sofort, und der Bot hakt diesen
    Hit bei diesem Banner nicht mehr automatisch ab. Mit „↩️ zulassen“ machst du das rückgängig.
- **Ihr wisst, dass ein Hit raus ist**, die Erkennung aber nicht: Karte antippen → **„🛠️ abhaken (ohne Person)“**.
- **Einzelfall prüfen**:
  ```
  docker exec -i gtcha-discord-bot python - <Banner-ID> <Tn> < tools/why_not_pulled.py
  ```
  Die Liste „Jeder Versand-Schub einzeln“ rechnet genau wie der Bot (✅ sicher, ❓ unklar, „(Medaillen-Frist)“).

## Übersetzung

Japanische Namen übersetzt der Bot selbst (Wörterbuch + MyMemory, kostenlos und ohne Anmeldung). Nichts zu
tun. Nur wenn euch die Übersetzungen zu schlecht sind: DeepL-Key in die `.env` eintragen (siehe `.env.example`).

## In 30 Tagen (ca. 02.11.2026)

1. Auf dem VPS:
   ```
   cd ~/gtcha-discord-bot && ./update.sh
   docker exec -i gtcha-discord-bot python - < tools/monthly_report.py
   ```
2. **Screenshot der Ausgabe** an Claude schicken. Sie passt auf etwa eine Bildschirmseite.
3. Für Details liegt die ausführliche Fassung in `~/gtcha-discord-bot/data/report-<Datum>.txt`. Bei Bedarf so
   anzeigen und abfotografieren:
   ```
   cat ~/gtcha-discord-bot/data/report-*.txt | tail -80
   ```
4. Dazu kurz sagen:
   - welche „❌ war falsch“-Fälle es gab und was wirklich war,
   - ob euch Hits aufgefallen sind, die raus waren, aber nicht erkannt wurden.

**Was Claude damit macht:**

| Abschnitt im Bericht | Was daraus nachgeschärft wird |
|---|---|
| 1. Automatisch abgehakt | Wie oft lag der Bot falsch? Welche Regel war schuld? |
| 2. Versand-Schübe, Faktor 2×/3×/4× | Den Packpreis-Faktor (aktuell 3) anpassen, wenn ein anderer mehr erkennt, ohne Fehler zu erzeugen |
| 3. Medaillen-Fristen | Viele „passen nicht“ heißen: Medaillen werden anders gesetzt als angenommen, dann Regel anpassen |
| 4. Lernen | Ab genug Beobachtungen werden die ❓-Prozente genauer, ab ~100 Fällen kommt ein echtes Modell infrage |
| 5. Treffsicherheit | Die Ø-Rückgabe-Vorhersage korrigieren, wenn sie dauerhaft in eine Richtung abweicht |
| 6. Rohdaten | Ob genug Daten gesammelt werden |

Das Rohdaten-Protokoll hält 30 Tage, der Pack-Verlauf 90 Tage. Den Bericht also nicht viel später als nach
30 Tagen machen, sonst fehlen die ersten Wochen.
