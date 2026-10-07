// Anleitung je Gerät: App installieren, Pushes, Lesezeichen zum Übertragen (wie in der Live-App)
export const GUIDE_DEVICES: Record<string, string> = {
  ios_safari: "iPhone/iPad · Safari", ios_chrome: "iPhone/iPad · Chrome", android: "Android · Chrome", pc: "PC/Mac · Chrome oder Edge",
};

export function guessDevice(): string {
  const ua = navigator.userAgent;
  if (/iPhone|iPad|iPod/.test(ua)) return /CriOS/.test(ua) ? "ios_chrome" : "ios_safari";
  if (/Android/.test(ua)) return "android";
  return "pc";
}

export const GUIDES: Record<string, { install: string[]; push: string; bookmark: string[] }> = {
  ios_safari: {
    install: ["Tailscale-App installieren, mit eurem Konto anmelden, VPN einschalten.",
      "Diese Seite in Safari öffnen → Teilen-Symbol (□↑) → „Zum Home-Bildschirm“.",
      "Ab jetzt die App über das Symbol auf dem Home-Bildschirm öffnen."],
    push: "Nur in der installierten App (ab iOS 16.4): unten „Pushes einschalten“ und erlauben.",
    bookmark: ["Discord verknüpfen (oben), dann unter „📥 Eigene GTCHA-Daten“ „Lesezeichen … kopieren“ antippen.",
      "In Safari irgendeine Seite als Lesezeichen sichern: Teilen → „Lesezeichen hinzufügen“, Name „An GTCHA Tracker“.",
      "Lesezeichen öffnen (Buch-Symbol) → „Bearbeiten“ → das neue Lesezeichen → Adresse löschen, kopierten Code einfügen.",
      "Übertragen: In Safari eingeloggt gtchaxonline.com öffnen, Adressleiste antippen, „An GTCHA Tracker“ eintippen und den Lesezeichen-Vorschlag wählen."],
  },
  ios_chrome: {
    install: ["Tailscale-App installieren, mit eurem Konto anmelden, VPN einschalten.",
      "Diese Seite in Chrome öffnen → Teilen-Symbol (□↑, rechts in der Adressleiste) → „Zum Home-Bildschirm“ (ab iOS 16.4).",
      "Ab jetzt die App über das Symbol auf dem Home-Bildschirm öffnen."],
    push: "Nur in der installierten App (ab iOS 16.4): unten „Pushes einschalten“ und erlauben.",
    bookmark: ["Discord verknüpfen (oben), dann unter „📥 Eigene GTCHA-Daten“ „Lesezeichen … kopieren“ antippen.",
      "In Chrome irgendeine Seite öffnen → „⋯“ → „Zu Lesezeichen hinzufügen“.",
      "„⋯“ → „Lesezeichen“ → das neue Lesezeichen lange drücken → „Lesezeichen bearbeiten“: Name „An GTCHA Tracker“, bei URL den kopierten Code einfügen.",
      "Übertragen: In Chrome eingeloggt gtchaxonline.com öffnen, Adressleiste antippen, „An GTCHA Tracker“ eintippen und den Vorschlag mit dem Stern wählen.",
      "Unter „🔗 GTCHA-Seite öffnen in“ unten „Chrome“ wählen, damit „Öffnen ↗“ in Chrome landet, wo du eingeloggt bist."],
  },
  android: {
    install: ["Tailscale-App aus dem Play Store installieren, mit eurem Konto anmelden, verbinden.",
      "Diese Seite in Chrome öffnen → „⋮“ → „App installieren“ (oder „Zum Startbildschirm hinzufügen“).",
      "Ab jetzt die App über das Symbol auf dem Startbildschirm öffnen."],
    push: "In Chrome und in der installierten App: unten „Pushes einschalten“ und erlauben.",
    bookmark: ["Discord verknüpfen (oben), dann unter „📥 Eigene GTCHA-Daten“ „Lesezeichen … kopieren“ antippen.",
      "In Chrome irgendeine Seite öffnen → „⋮“ → Stern (☆) antippen → „Bearbeiten“.",
      "Name „An GTCHA Tracker“, bei URL alles löschen und den kopierten Code einfügen, speichern.",
      "Übertragen: In Chrome eingeloggt gtchaxonline.com öffnen, Adressleiste antippen, „An GTCHA Tracker“ eintippen und den Vorschlag mit dem Stern wählen (nicht die Google-Suche)."],
  },
  pc: {
    install: ["Tailscale für Windows/Mac installieren (tailscale.com/download), mit eurem Konto anmelden.",
      "Diese Seite in Chrome oder Edge öffnen. Installieren (optional): Symbol „App installieren“ rechts in der Adressleiste.",
      "Geht auch ohne Installation einfach im Browser-Tab (auch in Firefox, dort ohne Installation)."],
    push: "In Chrome/Edge: unten „Pushes einschalten“ und erlauben. Pushes kommen, solange der Browser läuft.",
    bookmark: ["Discord verknüpfen (oben), dann unter „📥 Eigene GTCHA-Daten“ „Lesezeichen … kopieren“ anklicken.",
      "Lesezeichenleiste einblenden (Strg+Umschalt+B, Mac: ⌘+Umschalt+B) → Rechtsklick auf die Leiste → „Seite hinzufügen“.",
      "Name „An GTCHA Tracker“, bei URL den kopierten Code einfügen, speichern.",
      "Übertragen: Eingeloggt gtchaxonline.com öffnen und auf „An GTCHA Tracker“ in der Leiste klicken."],
  },
};
