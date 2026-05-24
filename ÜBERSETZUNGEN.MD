# Übersetzungen

Kurzer Ablauf, wenn Texte in der WebApp geändert oder neue Seiten übersetzt werden sollen.

## 1. Template-Texte markieren

Neue statische Texte in Django-Templates müssen mit `{% trans %}` oder `{% blocktrans %}` markiert sein.

Für einfache Texte:

```django
{% trans "Jetzt anfragen" %}
```

Für längere Texte oder HTML im Text:

```django
{% blocktrans trimmed %}
Die <strong>Registrierung</strong> erfolgt gemeinsam mit deiner TutorIn.
{% endblocktrans %}
```

Für die Landingpage kann das Hilfsscript genutzt werden:

```bash
python3 scripts/mark_template_i18n.py brainboost/core/templates/landing.html
```

Wenn Änderungen vorgeschlagen werden:

```bash
python3 scripts/mark_template_i18n.py --write brainboost/core/templates/landing.html
```

Danach kurz prüfen, ob das Template noch korrekt lädt:

```bash
.venv/bin/python brainboost/manage.py check
```

## 2. PO-Dateien aktualisieren

Nach Template-Änderungen die Übersetzungsdateien neu erzeugen/aktualisieren:

```bash
.venv/bin/python brainboost/manage.py makemessages -l en
.venv/bin/python brainboost/manage.py makemessages -l pl
```

Die Dateien liegen danach hier:

```text
brainboost/brainboost/locale/en/LC_MESSAGES/django.po
brainboost/brainboost/locale/pl/LC_MESSAGES/django.po
```

## 3. Übersetzungen eintragen

In den `.po`-Dateien alle leeren `msgstr ""` füllen.

Beispiel:

```po
msgid "Jetzt kostenlos Probestunde buchen!"
msgstr "Book a free trial lesson now!"
```

Polnisch:

```po
msgid "Jetzt kostenlos Probestunde buchen!"
msgstr "Zarezerwuj teraz bezpłatną lekcję próbną!"
```

Wichtig: HTML-Tags aus `msgid` müssen im `msgstr` erhalten bleiben, z. B. `<strong>...</strong>`.

## 4. Übersetzungen kompilieren

Nach dem Bearbeiten der `.po`-Dateien:

```bash
.venv/bin/python brainboost/manage.py compilemessages
```

Dadurch werden die `.mo`-Dateien erstellt, die Django zur Laufzeit verwendet.

## 5. Lokal testen

Server starten:

```bash
.venv/bin/python brainboost/manage.py runserver
```

Startseite öffnen:

```text
http://127.0.0.1:8000/
```

Sprache im Browser per DevTools-Konsole setzen:

```js
document.cookie = "django_language=pl; path=/";
location.reload();
```

Englisch:

```js
document.cookie = "django_language=en; path=/";
location.reload();
```

Deutsch:

```js
document.cookie = "django_language=de; path=/";
location.reload();
```

## Empfohlene Reihenfolge

1. Template ändern.
2. `mark_template_i18n.py` im Check-Modus laufen lassen.
3. Falls nötig mit `--write` Marker ergänzen.
4. `makemessages` für `en` und `pl` ausführen.
5. `.po`-Dateien übersetzen oder leere Einträge nachpflegen.
6. `compilemessages` ausführen.
7. Seite lokal mit Sprach-Cookie testen.

## Hinweise

- Datenbankinhalte wie FAQ-Fragen und FAQ-Antworten werden nicht automatisch durch Template-Übersetzungen übersetzt.
- Maschinelle Übersetzungen sollten vor Veröffentlichung geprüft werden.
- Nach jeder Änderung an `.po`-Dateien muss `compilemessages` erneut ausgeführt werden.
