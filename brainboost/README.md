# BrainBoost App

Einfache Django-Anwendung für BrainBoost: Login, Landing-Page und TutorIn-Workflows.

## Lokale Entwicklung
- Python 3.13, Django 6.1.1 und PostgreSQL 16.
- Im Repository-Root virtuelle Umgebung erstellen: `python3.13 -m venv .venv`
- Dort aktivieren: `source .venv/bin/activate`
- Abhängigkeiten installieren: `python -m pip install -r requirements.txt`
- Danach in das Verzeichnis mit `manage.py` wechseln: `cd brainboost`
- Migrationen ausführen: `python manage.py migrate`
- Dev-Server starten: `python manage.py runserver`
- Für absolute Links in E-Mails: `APP_BASE_URL` in `.env` setzen (z. B. `http://localhost:8000` lokal).

## PostgreSQL-Tests
- `python manage.py test core --noinput` verwendet automatisch die konfigurierte Testdatenbank `POSTGRES_LOCAL_TEST_DB` (Default: `brainboost_local_test`). Diese muss von der Entwicklungsdatenbank verschieden sein.
- Wenn dein PostgreSQL-User keine Datenbanken anlegen darf, lege die Testdatenbank einmalig an:

```sql
CREATE DATABASE brainboost_local_test OWNER brainboost_user;
GRANT ALL PRIVILEGES ON DATABASE brainboost_local_test TO brainboost_user;
```

Mit einer vorab angelegten Testdatenbank `python manage.py test core --keepdb`
verwenden. Ohne `--keepdb` versucht Django, die Datenbank neu anzulegen und
nach dem Testlauf zu löschen.

## Monatlicher Feedback-Reminder
- Command: `python manage.py send_monthly_feedback_reminders`
- Optional:
  - `--month YYYY-MM` (z. B. `2026-03`)
  - `--base-url https://www.nachhilfe-brainboost.de`
  - `--force` (erneuter Versand trotz vorhandenem Monatslog)
- Empfehlung: täglich per Cron ausführen, der Versand passiert dank Monatslog je Zielgruppe nur einmal pro Monat.

## Deployment-Hinweis
- Upgrade-Anleitung: [PythonAnywhere-Stack aktualisieren](../deploy/README.md).
- In der Produktionskonsole immer `export DJANGO_SETTINGS_MODULE=brainboost.settings.production` setzen; `manage.py` verwendet sonst lokale Settings.
- Environment-Variablen für Secret Key und Datenbank setzen.
- Optional für IndexNow: `INDEXNOW_KEY` setzen.
- Optional für Google-Bewertungen auf der Landingpage: `GOOGLE_PLACES_API_KEY` und `GOOGLE_PLACE_ID` setzen. Die Reviews werden pro aktiver Sprache von Google Places geladen und gecached.
- Statische Dateien sammeln: `python manage.py collectstatic --noinput`
- Nach dem Deployment öffentliche Seiten an IndexNow senden: `python manage.py submit_indexnow`


MINI-Änderung
