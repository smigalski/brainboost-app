# Upgrade auf Python 3.13, Django 6.1 und PostgreSQL 16

Stand: 28.09.2026. Ziel: Python **3.13**, Django **6.1.1**, PostgreSQL **16**.
Lokal ist Python 3.13.14 in `.python-version` festgelegt. PythonAnywhere verwaltet
den dort verfügbaren Python-Patchstand; entscheidend ist eine aktuelle 3.13-Version.
Django 6.1 unterstützt Python 3.13 und PostgreSQL ab Version 15.

Die folgenden Schritte werden auf PythonAnywhere ausgeführt. Die Umstellung der
Live-Web-App ist erst Schritt 5. PostgreSQL wurde vom Hoster bereits aktualisiert;
kein erneutes Datenbank-Major-Upgrade und kein Import einer lokalen Datenbank nötig.

## 1. Ausgangszustand und Sicherungen

- Lokal geprüfte Änderungen zuerst auf den gewünschten Deployment-Branch bringen.
  Nicht ungeprüft weitere lokale Änderungen mit veröffentlichen.
- Auf PythonAnywhere unter **Web** die bisherige Python-Version, den venv-Pfad,
  den Projektpfad, Static-/Media-Mappings und die WSGI-Konfiguration notieren.
- Unter **Tasks** die Befehle geplanter und Always-on-Tasks sichern.
- Bisherigen Git-Commit mit `git rev-parse HEAD` notieren; mit `git status --short`
  prüfen, ob auf dem Server eigene Änderungen existieren. Diese vorher sichern.
- Mit dem bisherigen venv-Interpreter `python -m pip freeze` in einer privaten
  Datei außerhalb des Repositorys sichern. Die alte venv erhalten.
- `.env`, WSGI-Datei und Upload-Verzeichnis privat sichern. Der Upload-Pfad ist
  standardmäßig `<repo>/brainboost/brainboost/media`; maßgeblich sind die Settings
  und das Media-Mapping unter Web. Ein Datenbankdump enthält keine Uploads.
- Ein aktuelles PostgreSQL-Backup erstellen und die Wiederherstellungsmöglichkeit
  prüfen. Vor Schemaänderungen Schreibzugriffe und schreibende Tasks pausieren.

Beispiel für den Datenbankdump in einer Bash-Konsole (Platzhalter durch Werte aus
dem PythonAnywhere-Tab **Databases** ersetzen, Passwort am Prompt eingeben):

```bash
umask 077
mkdir -p ~/backups
pg_dump --version
pg_dump -h <DB_HOST> -p <DB_PORT> -U <DB_USER> -d <DB_NAME> \
  -W -Fc -f ~/backups/brainboost-before-stack-upgrade.dump
pg_restore --list ~/backups/brainboost-before-stack-upgrade.dump >/dev/null
```

Der `pg_dump`-Client muss mindestens Version 16 haben. Falls der Standardclient
älter ist, den verfügbaren 16er-Client verwenden oder PythonAnywhere nach dessen
Pfad fragen. Die Inhaltsprüfung ersetzt keinen Restore-Test in eine separate DB.
Eine vorhandene Sicherung nicht überschreiben; dafür einen neuen Dateinamen wählen.

## 2. Python 3.13 bereitstellen und neue venv erstellen

In einer neuen Bash-Konsole:

```bash
python3.13 --version
```

Falls Python 3.13 fehlt: **Account → System image**, ein Image mit Python 3.13
wählen (z. B. `innit`). Vorher prüfen, ob die bisherige Python-Version für einen
Rückweg weiter verfügbar ist. Nach dem Image-Wechsel eine neue Konsole öffnen;
virtuelle Umgebungen müssen für das neue Image neu erstellt werden. Der Wechsel
betrifft auch andere Websites und Tasks desselben Accounts.

```bash
# Falls das Repository woanders liegt, diesen Pfad entsprechend anpassen.
cd ~/brainboost-app
git status --short
git branch --show-current

# Nur auf dem vorgesehenen Deployment-Branch ausführen.
git pull --ff-only

# Neuen Namen verwenden; die bisherige Umgebung erhalten.
python3.13 -m venv ~/.virtualenvs/brainboost-py313-django61
source ~/.virtualenvs/brainboost-py313-django61/bin/activate
python -m pip install --upgrade pip
python -m pip install --no-cache-dir -r requirements.txt
python -m pip check
python -c "import sys, django; print(sys.version); print(django.get_version())"
```

Erwartet: Python 3.13.x und Django 6.1.1. Die bestehende Web-App vorerst noch
nicht neu laden. Diese Schritte in einem kurzen Wartungsfenster ausführen:
Ein Update im bestehenden Checkout ändert bereits die dortigen Quellcodedateien.

## 3. Produktionskonfiguration und Stack prüfen

`manage.py` verwendet ohne Angabe **lokale** Settings. Deshalb in dieser Konsole:

```bash
export DJANGO_SETTINGS_MODULE=brainboost.settings.production
python brainboost/manage.py check
python brainboost/manage.py check --deploy
python brainboost/manage.py makemigrations --check --dry-run
python brainboost/manage.py migrate --plan
python brainboost/manage.py shell -c \
  "from django.db import connection; c = connection.cursor(); c.execute('SHOW server_version'); print(c.fetchone()[0]); c.close()"
python - <<'PY'
from weasyprint import HTML
pdf = HTML(string='<h1>BrainBoost PDF check</h1>').write_pdf()
assert pdf.startswith(b'%PDF-')
print('PDF-Erzeugung OK:', len(pdf), 'Bytes')
PY
```

Die Datenbank muss Version 16.x melden. Verbindungseinstellungen einschließlich
Host und Port mit dem Tab **Databases** abgleichen, falls der Hoster sie beim
Upgrade geändert hat. Zugangsdaten nicht in Git eintragen.

Die Produktionsvariablen müssen auch in der Konsole verfügbar sein. Das Projekt
lädt `.env` u. a. aus dem Repository-Root; ausschließlich in der WSGI-Datei gesetzte
Variablen stehen Konsolenbefehlen nicht automatisch zur Verfügung. Erforderlich
sind unter anderem `DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD` und die drei
`STRIPE_*`-Variablen aus `production.py`. `APP_BASE_URL` auf die öffentliche
HTTPS-Adresse setzen. Beispielwerte oder lokale DB-Zugangsdaten nicht übernehmen.

Bei Fehlern hier stoppen und erst deren Ursache beheben. Hinweise von
`check --deploy` einzeln bewerten; z. B. HSTS nicht ungeprüft einschalten.
Wenn WeasyPrint Systembibliotheken vermisst, PythonAnywhere um die passenden
Pango-/Harfbuzz-Bibliotheken bitten. `pip install` allein installiert diese nicht.
Keine Anwendungstests gegen die Produktionsdatenbank starten.

## 4. Migrationen und statische Dateien

Nach geprüftem Backup und geprüftem Migrationsplan:

```bash
python brainboost/manage.py migrate --noinput
python brainboost/manage.py migrate --check
python brainboost/manage.py collectstatic --noinput
```

Der Framework-Wechsel selbst benötigt in diesem Projekt keine neue eigene
Migration. Alle zusätzlich mit veröffentlichten App-Migrationen müssen aber
geprüft werden, etwa `0068_student_school_details`, sofern sie im Release liegt.

## 5. Web-App und Tasks umstellen

Unter **Web** für jede betroffene Web-App:

1. **Python version** auf **3.13** stellen.
2. **Virtualenv** auf den absoluten Pfad der neuen Umgebung stellen, z. B.
   `/home/brainboost/.virtualenvs/brainboost-py313-django61`.
3. In der bestehenden WSGI-Datei prüfen, dass der Projektpfad auf das Verzeichnis
   mit `manage.py` zeigt und vor `get_wsgi_application()` Folgendes gesetzt wird:

   ```python
   os.environ["DJANGO_SETTINGS_MODULE"] = "brainboost.settings.production"
   ```

   Bestehende Konfiguration/Zugangsdaten erhalten. Die WSGI-Datei im Repository
   verwendet standardmäßig lokale Settings; sie nicht unverändert als
   Produktionskonfiguration einsetzen.
4. Static-/Media-Mappings prüfen und **Reload** ausführen.
5. Fehlerlog prüfen. Landingpage, Login/Logout, Dashboard und eine PDF-Rechnung
   prüfen. Einen kontrollierten E-Mail-Test an eine eigene Adresse durchführen.
   Zahlungsabläufe im Stripe-Testmodus prüfen, keine echte Testzahlung auslösen.

Tasks müssen ebenfalls den neuen Interpreter und Produktions-Settings verwenden.
Beispiel für einen bestehenden Feedback-Reminder (Pfade bei Bedarf anpassen):

```bash
cd /home/brainboost/brainboost-app/brainboost && DJANGO_SETTINGS_MODULE=brainboost.settings.production /home/brainboost/.virtualenvs/brainboost-py313-django61/bin/python manage.py send_monthly_feedback_reminders
```

Vorhandene Argumente und Zeitpläne erhalten. Diesen Reminder nicht zusätzlich
manuell als Test ausführen, da er echte Nachrichten verschickt. Always-on-Tasks
nach einem System-Image-Wechsel deaktivieren und erneut aktivieren.

## Rückweg bei Problemen

- Vor Migrationen/Reload kann die bestehende Web-Konfiguration weiter verwendet
  werden; bei geändertem Checkout gegebenenfalls den gesicherten Release-Stand
  wiederherstellen. Serveränderungen vorher sichern, kein pauschales `reset --hard`.
- Nach Reload: vorherigen Code, Python-Version, venv-Pfad und WSGI-Konfiguration
  gemeinsam wiederherstellen, statische Dateien mit dem alten Stack neu sammeln
  und die Web-App neu laden. Tasks ebenfalls zurückstellen.
- Nach Schemaänderungen zuerst die Kompatibilität des alten Codes prüfen. Ein
  Restore überschreibt zwischenzeitliche Änderungen und darf nur koordiniert bei
  pausierten Schreibzugriffen erfolgen. Kein automatisches Restore ausführen.
- Eine alte venv ist nach einem System-Image-Wechsel möglicherweise nicht mehr
  lauffähig. Den Rückweg für das Image und den bisherigen Interpreter **vorher**
  klären. Django 4.2/Python 3.9 dienen nur als kurzfristiger Rückweg.

## Bekannte Hinweise

Die E-Mail-Konfiguration verwendet Django 6.1 `MAILERS`. Die bestehenden
**Umgebungsvariablen** `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`,
`EMAIL_HOST_USER` und `EMAIL_HOST_PASSWORD` bleiben unverändert; das Projekt
übersetzt sie in `MAILERS["default"]["OPTIONS"]`. Auch Absender, Reply-To und
Empfänger behalten ihre bisherigen Variablen. Kein Passwort muss wegen dieser
Umstellung geändert werden.

`EMAIL_BACKEND` ist eine optionale Umgebungsvariable. Auf PythonAnywhere muss sie
fehlen oder `django.core.mail.backends.smtp.EmailBackend` enthalten. Ein lokales
Console-/Memory-Backend nicht in Produktion übernehmen. Die bisherige zusätzliche
SMTP-Konfiguration in `production.py` entfällt; die Produktionsumgebung verwendet
dieselbe zentrale Konfiguration aus `base.py`.

Nach Schritt 3 kann die Konfiguration ohne Versand und ohne Passwortausgabe
geprüft werden:

```bash
python brainboost/manage.py shell -c \
  "from django.core.mail import mailers; m = mailers['default']; print(type(m).__module__); print('SMTP-Zugangsdaten vorhanden:', bool(getattr(m, 'username', None) and getattr(m, 'password', None)))"
```

Erwartet: `django.core.mail.backends.smtp` und `SMTP-Zugangsdaten vorhanden: True`.
Das bestätigt die Konfiguration, aber noch nicht die Anmeldung am SMTP-Server.
Für einen echten Zustelltest eine eigene Empfängeradresse einsetzen:

```bash
python brainboost/manage.py sendtestemail deine-eigene-adresse@example.com
```

Dieser Befehl verschickt eine echte Testmail. Danach Posteingang/Spam und die
Absenderadresse prüfen. Bei Gmail weiterhin das vorhandene App-Passwort verwenden.

Django 6.1 ist kein LTS-Release; weitere Sicherheits- und Versionsupdates bleiben
erforderlich.

## Offizielle Quellen

- [Aktuelle Django-Version und Supportzeiträume](https://www.djangoproject.com/download/)
- [Django 6.1: Kompatibilität und Änderungen](https://docs.djangoproject.com/en/6.1/releases/6.1/)
- [Django 6.0: Änderungen beim Upgrade](https://docs.djangoproject.com/en/6.0/releases/6.0/)
- [Django 6.1: E-Mail-Konfiguration mit MAILERS](https://docs.djangoproject.com/en/6.1/topics/email/#configuring-email)
- [PythonAnywhere: System-Image wechseln](https://help.pythonanywhere.com/pages/ChangingSystemImage/)
- [PythonAnywhere: virtuelle Umgebungen](https://help.pythonanywhere.com/pages/Virtualenvs/)
