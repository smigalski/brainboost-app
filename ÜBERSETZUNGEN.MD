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
.venv/bin/python brainboost/manage.py makemessages -l tr
.venv/bin/python brainboost/manage.py makemessages -l ru
.venv/bin/python brainboost/manage.py makemessages -l ar
```

Die Dateien liegen danach hier:

```text
brainboost/brainboost/locale/en/LC_MESSAGES/django.po
brainboost/brainboost/locale/pl/LC_MESSAGES/django.po
brainboost/brainboost/locale/tr/LC_MESSAGES/django.po
brainboost/brainboost/locale/ru/LC_MESSAGES/django.po
brainboost/brainboost/locale/ar/LC_MESSAGES/django.po
```

## 3. Übersetzungen automatisch mit DeepL füllen

Für DeepL API Free den API-Key als Umgebungsvariable setzen:

```bash
export DEEPL_AUTH_KEY="dein-api-key"
```

Wenn dein DeepL-Plan nicht den alten API-Free-Endpoint nutzt, zusätzlich den API-Endpoint setzen:

```bash
export DEEPL_API_URL="https://api.deepl.com/v2/translate"
```

Bei altem API Free bleibt der Standard:

```text
https://api-free.deepl.com/v2/translate
```

Hinweis: DeepL-API-Free-Keys enden typischerweise auf `:fx`. Solche Keys müssen den `api-free.deepl.com`-Endpoint verwenden.

Lokale Werte aus `.env` für `DEEPL_AUTH_KEY` und `DEEPL_API_URL` überschreiben exportierte Terminal-Werte, damit ein alter Shell-Export nicht versehentlich den falschen Endpoint verwendet.

Das Script sendet den Key als `Authorization: DeepL-Auth-Key ...` Header. Das ist wichtig, weil DeepL die alte `auth_key`-Übergabe im Request-Body nicht mehr zuverlässig akzeptiert.

FAQ-Datenbankinhalte werden separat übersetzt. Vor dem ersten FAQ-Übersetzungslauf die Migration ausführen:

```bash
.venv/bin/python brainboost/manage.py migrate
```

FAQ-Übersetzungen prüfen:

```bash
.venv/bin/python scripts/translate_faqs.py --dry-run --locales en pl tr ru ar
```

FAQ-Übersetzungen schreiben:

```bash
.venv/bin/python scripts/translate_faqs.py --provider deepl --locales en pl tr ru ar
```

Vorab prüfen, wie viele leere Einträge übersetzt würden:

```bash
python3 scripts/translate_po.py --dry-run --locales en pl tr ru ar
```

Leere `msgstr`-Einträge automatisch übersetzen:

```bash
python3 scripts/translate_po.py --provider deepl --locales en pl tr ru ar
```

Das Script überschreibt bestehende Übersetzungen standardmäßig nicht. Für Tests ohne DeepL-Aufruf gibt es den Mock-Provider:

```bash
python3 scripts/translate_po.py --provider mock --locales en pl tr ru ar
```

Die komplette Pipeline für Template-Markierung, `makemessages`, DeepL-Übersetzung und `compilemessages`:

```bash
scripts/update_translations.sh
```

Ohne Argumente verarbeitet der Wrapper die Startseite und die globale Navigation:

```text
brainboost/core/templates/landing.html
brainboost/core/templates/landing_alle.html
brainboost/core/templates/landing_eltern.html
brainboost/core/templates/landing_schuelerinnen.html
brainboost/core/templates/landing_tutor.html
brainboost/core/templates/contact.html
brainboost/core/templates/base.html
brainboost/core/templates/partials/price_banner.html
brainboost/core/templates/partials/cookie_notice.html
```

Optional andere Templates gezielt markieren:

```bash
scripts/update_translations.sh brainboost/core/templates/landing.html
```

## 4. Übersetzungen manuell prüfen

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

## 5. Übersetzungen kompilieren

Nach dem Bearbeiten der `.po`-Dateien:

```bash
.venv/bin/python brainboost/manage.py compilemessages
```

Dadurch werden die `.mo`-Dateien erstellt, die Django zur Laufzeit verwendet.

Wenn du vom Projekt-Root kompilierst, ignoriere die virtuelle Umgebung, damit Django nicht alle `.po`-Dateien aus `.venv` scannt:

```bash
.venv/bin/python brainboost/manage.py compilemessages --ignore ".venv/*" --ignore "venv/*" --ignore "env/*"
```

## 6. Lokal testen

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
4. `makemessages` für `en`, `pl`, `tr`, `ru` und `ar` ausführen.
5. `translate_po.py` ausführen.
6. `.po`-Dateien manuell prüfen oder leere Einträge nachpflegen.
7. `compilemessages` ausführen.
8. Seite lokal mit Sprach-Cookie testen.

## Hinweise

- Datenbankinhalte wie FAQ-Fragen und FAQ-Antworten werden nicht automatisch durch Template-Übersetzungen übersetzt.
- Maschinelle Übersetzungen sollten vor Veröffentlichung geprüft werden.
- Nach jeder Änderung an `.po`-Dateien muss `compilemessages` erneut ausgeführt werden.
