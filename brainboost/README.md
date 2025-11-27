# BrainBoost App

Einfache Django-Anwendung für BrainBoost: Login, Landing-Page und Tutor-Workflows.

## Lokale Entwicklung
- Python 3.11+ empfohlen.
- Abhängigkeiten installieren: `pip install -r requirements.txt`
- Migrationen ausführen: `python manage.py migrate`
- Dev-Server starten: `python manage.py runserver`

## Deployment-Hinweis
- Environment-Variablen für Secret Key und Datenbank setzen.
- Statische Dateien sammeln: `python manage.py collectstatic --noinput`
