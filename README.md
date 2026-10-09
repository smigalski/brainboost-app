# BrainBoost App

<p align="center">
  <img src="docs/assets/brand/brainboost-logo.svg" alt="BrainBoost Logo" width="140" />
</p>

<p align="center">
  Webplattform fuer Nachhilfe-Organisation mit Rollen, Terminplanung, Lernmaterial, Rechnungsprozess und Stripe-Checkout.
</p>

<p align="center">
  <a href="https://www.nachhilfe-brainboost.de"><img alt="Live" src="https://img.shields.io/badge/Live-Website-2ea44f?style=for-the-badge"></a>
  <a href="https://brainboost.pythonanywhere.com"><img alt="Deployment" src="https://img.shields.io/badge/Deploy-PythonAnywhere-1f6feb?style=for-the-badge"></a>
  <a href="https://github.com/smigalski/brainboost-app/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/smigalski/brainboost-app/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Django 6.1" src="https://img.shields.io/badge/Django-6.1-0c4b33?style=for-the-badge">
  <img alt="PostgreSQL 16" src="https://img.shields.io/badge/PostgreSQL-16-336791?style=for-the-badge">
</p>

## Produktueberblick

BrainBoost ist eine Django-Anwendung fuer den operativen Alltag eines Nachhilfe-Teams:

- Rollen- und Accountverwaltung fuer Eltern, Schueler:innen, Tutor:innen und Admins
- Termin- und Unterrichtsverwaltung inkl. Aenderung, Absage und Kalender-Export
- Upload von Aufgaben/Loesungen und tutor-spezifischen Vorlagen
- Rechnungsprozess inkl. Freigabe- und Benachrichtigungs-Workflow
- Stripe-Integration (Checkout + Webhook) fuer digitale Zahlungsablaeufe
- E-Mail-Flows fuer Registrierung, Passwort-Reset, Unterrichts-Events und Erinnerungen

## Screenshots

> Die Screenshots sind prod-reif und kommen aus einer realen online-Umgebung.

### Landing Page
![Landing Page](docs/assets/screens/landing-page.png)

### Dashboard
![Dashboard](docs/assets/screens/dashboard-overview.png)

### Rechnungen & Zahlung
![Rechnungen](docs/assets/screens/invoice-workflow.png)

### Tutor:innen Workflow
![Tutor Workflow](docs/assets/screens/tutor-workflow.png)

## Brand Assets

- Logo (SVG): `docs/assets/brand/brainboost-logo.svg`
- Logo (PNG): `docs/assets/brand/brainboost-logo.png`
- OpenGraph Image (PNG): `docs/assets/brand/og-image.png`
- Icon/App Symbol (SVG): `docs/assets/brand/icon.svg`

## Tech Stack

- Python 3.13 (lokaler Patchstand in `.python-version`)
- Django 6.1.1 (aktuelle stabile Version am 28.09.2026)
- PostgreSQL 16 (lokal + Produktion)
- Stripe API
- WeasyPrint (PDF)
- Pillow, openpyxl, qrcode
- Deployment auf PythonAnywhere

## Projektstruktur

```text
brainboost-app/
├─ brainboost/                  # Django-Projektroot (manage.py liegt hier)
│  ├─ brainboost/               # settings, urls, wsgi/asgi
│  └─ core/                     # App mit Models, Views, Templates, Static
├─ .env.example                 # Beispiel-Konfiguration
├─ requirements.txt
└─ README.md
```

## Quickstart (Lokal)

```bash
# 1) Repository klonen und ins Projekt
git clone <REPO_URL>
cd brainboost-app

# 2) Virtuelle Umgebung
# Bei einem Python-Wechsel eine neue venv erstellen, die alte vorher sichern.
python3.13 -m venv .venv
source .venv/bin/activate

# 3) Dependencies
python -m pip install -r requirements.txt
python -m pip check

# 4) Umgebungsvariablen
cp .env.example .env
# .env mit lokalen Werten anpassen

# 5) Django starten
cd brainboost
python manage.py migrate
python manage.py runserver
```

App lokal: `http://127.0.0.1:8000`

PostgreSQL 16 muss laufen und Datenbank/User müssen zu `POSTGRES_LOCAL_*`
passen. Auf macOS können die Systemabhängigkeiten mit
`brew install python@3.13 postgresql@16 pango` und
`brew services start postgresql@16` eingerichtet werden. Vorhandene Datenbanken
bei einem Major-Upgrade zuerst sichern; ein neues PostgreSQL-Paket übernimmt
keinen alten Datenbestand automatisch.

Nach dem Wechsel von Python 3.9 ein neues Terminal öffnen oder die Umgebung
erneut aktivieren. In VS Code den Interpreter `.venv/bin/python` auswählen.
`python --version` muss 3.13 anzeigen.

Prüfungen aus dem Repository-Root:

```bash
python brainboost/manage.py check
python brainboost/manage.py makemigrations --check --dry-run
python brainboost/manage.py test core --noinput
```

## Wichtige Env-Variablen

Beispielwerte siehe [`.env.example`](.env.example).

- `DJANGO_DEV_SECRET_KEY`
- `DJANGO_SECRET_KEY`
- `POSTGRES_LOCAL_*` / `POSTGRES_*`
- `PUBLIC_CONTACT_EMAIL`: öffentliche Kontaktadresse, aktuell `brainboost.nachhilfe@gmail.com`
- `INTERNAL_CONTACT_EMAIL`: interne Fallback-Adresse, aktuell `brainboost.nachhilfe@gmail.com`
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`: SMTP-Zugangsdaten, in Produktion ohne Passwort im Repo
- `EMAIL_BACKEND`: optionaler Backend-Pfad (Standard: SMTP). Diese Env-Variablen
  werden in Django 6.1 auf `MAILERS["default"]` abgebildet; bestehende `.env`- und
  PythonAnywhere-Werte können unverändert bleiben. Für lokale Mail-Vorschauen
  `django.core.mail.backends.console.EmailBackend` verwenden.
- `DEFAULT_FROM_EMAIL`, `SERVER_EMAIL`, `DEFAULT_REPLY_TO_EMAIL`: Absender-/Antwortadressen für Systemmails
- `EMAIL_RECIPIENT` / `LEAD_NOTIFICATION_EMAIL`: Empfänger für Kontaktformular-/Lead-Benachrichtigungen; kann intern weiter auf Gmail zeigen
- `STRIPE_PUBLIC_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`
- `APP_BASE_URL`

## Deployment (PythonAnywhere)

Die erstmalige Umstellung von Python 3.9/Django 4.2 auf den neuen Stack ist in
[deploy/README.md](deploy/README.md) beschrieben, inklusive Backup, neuer venv,
Versionsprüfung und Rückweg.

Für spätere Updates nach der Umstellung (im Repository-Root und mit aktivierter
Python-3.13-venv):

```bash
git pull --ff-only origin staging #oder einfach git pull, wenn remote gesetzt ist
python -m pip install -r requirements.txt
python -m pip check
export DJANGO_SETTINGS_MODULE=brainboost.settings.production
python brainboost/manage.py check
python brainboost/manage.py migrate
python brainboost/manage.py collectstatic --noinput
```

Danach Web-App in PythonAnywhere neu laden.

PythonAnywhere muss für diese Version mit Python 3.13 und PostgreSQL 16 laufen.
Web-App, virtuelle Umgebung und Tasks müssen dieselbe Python-Version verwenden.

## Bewerbungen, Absagen und TutorInnen-Warteliste

In der **Lead-Zentrale** (oder im Django-Admin beim zugehörigen **Lead**) steuert
der Bewerbungsstatus die folgenden E-Mails:

- **Unpassend:** wertschätzende Absage an die TutorIn. Das verknüpfte Tutorprofil
  erhält den Status „abgelehnt“. Eltern-/SchülerInnen-Leads erhalten keine
  TutorInnen-Absage.
- **Warteliste:** E-Mail, dass aktuell keine SchülerInnen verfügbar sind. Das
  Tutorprofil bleibt auf „Warteliste“; es wird nicht automatisch aktiviert.
- **Verfügbarkeit anfragen:** In der Warteliste gezielt passende TutorInnen
  auswählen. Die E-Mail führt zum persönlichen Dashboard. Dort kann die TutorIn
  ihr Interesse bestätigen oder auf der Warteliste bleiben.
- **Interesse bestätigt:** Das Team wird an `LEAD_NOTIFICATION_EMAIL` informiert.
  Mit **Bewerbung weiterführen** wird der frühere Bewerbungs-/Onboardingstand
  wiederhergestellt. Danach Kennenlernen bzw. Onboarding abschließen und unter
  **SchülerInnen und Zuweisungen verwalten** passende Lernprofile zuweisen.

Die Warteliste bleibt unabhängig vom Zeitraumfilter sichtbar. Fächer, Klassen
und Verfügbarkeit helfen bei der manuellen Auswahl; freie Kapazitäten werden
nicht automatisch aus bestehenden Zuweisungen abgeleitet. Vor der Wiederaufnahme
verhindern die Zuweisungsformulare neue SchülerInnen-Zuweisungen.

Die E-Mails werden erst nach erfolgreichem Speichern versendet. Fehler erscheinen
unter **Ausstehende Bewerbungs-E-Mails** und können dort erneut versucht werden.
Das erneute Speichern desselben Status versendet keine weitere Nachricht. Für
ältere Bewerbungen ohne Konto zunächst **Zur TutorIn machen** verwenden; die
Warteliste bleibt dabei erhalten. **Mail erneut senden** verschickt weiterhin
die Passwort-Mail, nicht die Bewerbungsentscheidung.

`EMAIL_NOTIFICATIONS["tutor_application_status"]` steuert diese Benachrichtigungen.
SMTP, Absender und Reply-To verwenden die bestehende `MAILERS`-Konfiguration.
Interne Notizen und SchülerInnendaten werden nicht an BewerberInnen versendet.
Migration `0069_tutor_application_waitlist` legt Status und Versandprotokoll an;
sie versendet keine E-Mails für bestehende Bewerbungen.

## Continuous Integration

GitHub Actions fuehrt bei jedem Push, Pull Request und manuellen Start den Workflow
`.github/workflows/ci.yml` aus. Der Workflow verwendet Python 3.13 und PostgreSQL 16,
prüft die Python-Abhängigkeiten, Django, fehlende Migrationen und PDF-Erzeugung und führt
`python brainboost/manage.py test core` aus.

## Security-Hinweise

- Keine Secrets in Git committen (`.env`, Credentials, API Keys).
- Keine lokalen Dumps/Uploads versionieren (`*.sql`, `media/`, lokale Exporte).
- Bei versehentlich geleakten Secrets: sofort rotieren.

## Roadmap

- [ ] Testabdeckung ausbauen (Unit + Integration)
- [x] CI-Pipeline fuer Django-Checks und Tests
- [ ] Monitoring/Alerting fuer Zahlungs- und E-Mail-Flows
- [ ] Verbesserte Admin-Reports

## Lizenz

Copyright © 2025-2026 BrainBoost Nachhilfe. Alle Rechte vorbehalten.

### Hierarchische TutorInnenbezeichnungen

Die Migration `0070_hierarchical_tutor_numbers` ersetzt bestehende TutorInnennummern.
Kiara Puppe wird bei dieser Erstumstellung anhand ihres Vor- und Nachnamens eindeutig
ermittelt und erhält `TUT1`. Direkt zugeordnete TutorInnen erhalten `TUT1-1`,
`TUT1-2` usw.; deren TutorInnen beispielsweise `TUT1-1-1`. Die Zählung beginnt
auf jeder Unterebene bei 1. Andere TutorInnen ohne übergeordnete Person erhalten
`TUT2`, `TUT3` usw.; `TUT1` bleibt für Kiara reserviert. Bei der Erstumstellung
bestimmt die aufsteigende Datenbank-ID die Reihenfolge unter Geschwistern.

Zuordnungen werden im TutorInnenprofil unter **Assigned tutors** gepflegt: Dort
stehen die untergeordneten TutorInnen. Eine Person darf genau eine übergeordnete
TutorIn haben; Selbstzuordnungen und Kreise werden abgewiesen. Bei einer Umordnung
werden die Bezeichnungen der betroffenen Person und ihrer untergeordneten
TutorInnen angepasst. Bereits vergebene Nummern werden niemals erneut vergeben.
Das gilt auch für die vor einer Umordnung verwendeten Nummern.

Im Django-Admin zeigt **TutorInnenbezeichnungen (Archiv)** die aktuellen und
archivierten Nummern einschließlich der ursprünglichen Profil-ID. Löschen eines
Profils oder Benutzerkontos erhält diese Reservierungen. Untergeordnete Personen
behalten nach dem Löschen ihrer übergeordneten Person zunächst ihre Nummer;
erst eine ausdrückliche neue Zuordnung ändert diese. Bezeichnungen sind nicht
manuell editierbar. Änderungen der Hierarchie müssen über den Admin oder den
Django-Many-to-many-Manager erfolgen, nicht per SQL oder direktem Schreiben in
die Zwischentabelle; `bulk_create` für TutorInnen umgeht ebenfalls die Vergabe.

Deployment: Datenbank sichern, Schreibzugriffe während der Umstellung pausieren,
Code aktualisieren, `python manage.py migrate` ausführen und Web-App neu laden.
Die Migration bricht bei mehrdeutiger Kiara-Zuordnung, mehreren übergeordneten
Personen oder Kreisen ab, ohne diese Beziehungen selbst zu verändern. Diese
Datenfehler müssen zuerst bereinigt werden. Die Datenmigration ist absichtlich
nicht rückwärts ausführbar; für einen Rollback das vorherige Backup verwenden.

## Adressvorschläge im Kontaktformular

PLZ und Straße verwenden die Places Autocomplete Data API (New) über den
**Browser-Key `GOOGLE_MAPS_API_KEY`**. In dessen Google-Cloud-Projekt müssen
**Maps JavaScript API**, **Places API (New)** und Abrechnung aktiviert sein.
Den Browser-Key auf die tatsächlichen Website-Referrer (einschließlich Staging
und gegebenenfalls localhost) und diese APIs beschränken. Der separate
serverseitige `GOOGLE_PLACES_API_KEY` für Bewertungen wird nicht veröffentlicht.

PLZ-Vorschläge sind auf deutsche Postleitzahlen beschränkt und übernehmen nur
die fünfstellige PLZ. Straßen werden anhand strukturierter `addressComponents`
vor der Anzeige auf Land `DE` und die **exakte eingegebene PLZ** geprüft; übernommen
wird nur `route`, niemals eine Hausnummer. Textsuche oder geografische Nähe allein
zählen nicht als PLZ-Nachweis. Straßen ohne eindeutige PLZ-Zuordnung bei Google
werden deshalb nicht angeboten, auch wenn sie tatsächlich im Gebiet liegen.
Das ist eine Eingabehilfe, keine verbindliche Adressprüfung: Freitext bleibt möglich.

350 ms Verzögerung, mindestens zwei Zeichen, maximal fünf Kandidaten und die
Prüfung auf überholte Antworten begrenzen Anfragen. Zur strikten Filterung wird
pro Kandidat ein Place-Details-Aufruf (`addressComponents`) vor der Anzeige
benötigt. Diese Aufrufe können zusätzlich kostenpflichtig sein; es wird keine
Autocomplete-Session-Abrechnung unterstellt, da nicht erst die Nutzerauswahl
Details lädt. Keine dauerhafte Speicherung von Google-Vorschlägen oder Place-IDs.
Bei Timeout, fehlendem Key, fehlenden Treffern oder API-Fehlern bleibt das Formular
manuell nutzbar. Ein PLZ-Wechsel entfernt die bisherige Straße.

Prüfungen (keine echten Google-Aufrufe):

```bash
node --test scripts/tests/lead_address_autocomplete.test.cjs
python brainboost/manage.py test core.tests.LeadFormFlowTests --noinput
```

Vor Bereitstellung mit dem echten, eingeschränkten Browser-Key auf Staging prüfen:
PLZ-Vorschläge, Straßen in derselben und einer anderen PLZ, PLZ-Wechsel während
der Suche, Tastaturbedienung sowie blockierte Google-Anfragen. Lokale Tests können
die Freischaltung, Abrechnung und tatsächliche Google-Datenabdeckung nicht prüfen.
