# BrainBoost App

Einfache Django-Anwendung für BrainBoost: Login, Landing-Page und TutorIn-Workflows.

## Lokale Entwicklung
- Python 3.11+ empfohlen.
- Abhängigkeiten installieren: `pip install -r requirements.txt`
- Migrationen ausführen: `python manage.py migrate`
- Dev-Server starten: `python manage.py runserver`
- Für absolute Links in E-Mails: `APP_BASE_URL` in `.env` setzen (z. B. `http://localhost:8000` lokal).

## PostgreSQL-Tests
- `python manage.py test` verwendet automatisch die konfigurierte Testdatenbank `POSTGRES_LOCAL_TEST_DB` (Default: `brainboost_local_test`).
- Wenn dein PostgreSQL-User keine Datenbanken anlegen darf, lege die Testdatenbank einmalig an:

```sql
CREATE DATABASE brainboost_local_test OWNER brainboost_user;
GRANT ALL PRIVILEGES ON DATABASE brainboost_local_test TO brainboost_user;
```

## Monatlicher Feedback-Reminder
- Command: `python manage.py send_monthly_feedback_reminders`
- Optional:
  - `--month YYYY-MM` (z. B. `2026-03`)
  - `--base-url https://www.nachhilfe-brainboost.de`
  - `--force` (erneuter Versand trotz vorhandenem Monatslog)
- Empfehlung: täglich per Cron ausführen, der Versand passiert dank Monatslog je Zielgruppe nur einmal pro Monat.

## Deployment-Hinweis
- Environment-Variablen für Secret Key und Datenbank setzen.
- Statische Dateien sammeln: `python manage.py collectstatic --noinput`


MINI-Änderung
