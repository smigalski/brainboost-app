# Leads

Diese Anleitung erklärt einfach, wie Leads in BrainBoost entstehen, wo man sie findet und wie man sie weiterbearbeitet.

## Was ist ein Lead?

Ein Lead ist eine neue Anfrage oder Bewerbung über die Website. Es gibt drei Arten:

1. **Elternteil** fragt Nachhilfe für ein Kind an.
2. **SchülerIn** fragt selbst Nachhilfe an.
3. **TutorIn** bewirbt sich als NachhilfelehrerIn.

Jeder Lead enthält Kontaktdaten, gewünschte Kontaktart, Fach, Klassenstufe, Ziel, Dringlichkeit und je nach Rolle weitere Angaben.

## Schritt für Schritt: So entsteht ein Lead

1. Eine Person öffnet die Website oder einen Kampagnen-Link.
2. Wenn der Link UTM-Parameter enthält, werden diese automatisch in der Session gespeichert.
3. Die Person geht zum Kontaktformular.
4. Im Formular wird ausgewählt, ob die Anfrage von einem Elternteil, einer SchülerIn oder einer TutorIn kommt.
5. Die Person füllt die Pflichtfelder aus.
6. Es muss mindestens eine E-Mail-Adresse oder Telefonnummer angegeben werden.
7. Die Person stimmt der Verarbeitung der Angaben zu.
8. Nach dem Absenden wird der Lead in der Datenbank gespeichert.
9. Das System verschickt eine interne Benachrichtigung.
10. Wenn eine E-Mail-Adresse vorhanden ist, bekommt die Person eine Bestätigung.
11. Danach wird eine Danke-Seite angezeigt.

## Pflichtfelder

Für alle Leads:

1. Rolle
2. Name
3. Bevorzugte Kontaktart
4. E-Mail oder Telefonnummer
5. Datenschutz-Zustimmung

Für Nachhilfe-Anfragen von Eltern oder SchülerInnen zusätzlich:

1. Unterrichtsform
2. Fach oder Fächer
3. Klassenstufe
4. Ziel der Nachhilfe
5. Dringlichkeit

Für TutorInnen-Bewerbungen zusätzlich:

1. Fächer
2. Klassenstufen
3. Verfügbarkeit pro Woche
4. Erfahrung

## Was passiert bei TutorInnen?

Bei TutorInnen werden die Felder aus dem TutorInnen-Profil auch für die normale Lead-Übersicht übernommen:

1. `teaching_subjects` wird zu `subject`.
2. `teaching_grades` wird zu `grade`.
3. Die Anfrage wird auf die TutorInnen-Danke-Seite weitergeleitet.

Dadurch können TutorInnen in der Lead-Zentrale genauso nach Fach und Klassenstufe ausgewertet werden wie normale Nachhilfe-Anfragen.

## UTM-Tracking und Kampagnen

Kampagnen-Links können Parameter wie diese enthalten:

```text
utm_source
utm_medium
utm_campaign
utm_content
utm_term
campaign
source
role
```

Die App speichert diese Werte beim ersten Besuch in der Session. Wenn die Person später das Formular abschickt, werden die Werte am Lead gespeichert.

Wichtig:

1. `utm_campaign` wird auch als `campaign` verwendet, falls kein eigenes `campaign` gesetzt ist.
2. `role` kann das Formular vorauswählen, zum Beispiel `role=tutor`.
3. `referrer`, erste Landingpage und erste Query-Parameter werden ebenfalls gespeichert.

## Wo findet man Leads?

Es gibt zwei wichtige Bereiche:

1. **Lead-Zentrale**  
   Übersicht für Auswertung, Kampagnen und offene Follow-ups.

2. **Django-Admin**  
   Detailansicht zum Bearbeiten einzelner Leads.

In der internen Admin-Aufgaben-Seite gibt es dafür den Tab **Leads**. Dort sind Links zur Lead-Zentrale, zum Kampagnen-Link-Builder, zur Meta-Ads-Struktur und zum Django-Admin.

## Lead-Zentrale

Die Lead-Zentrale zeigt:

1. Leads gesamt
2. Neue Leads
3. Offene Follow-ups
4. Leads nach Rolle
5. Leads nach Status
6. Leads nach Fach
7. Leads nach Klassenstufe
8. UTM-Kampagnen mit Conversion-Werten
9. Leads nach Zeitraum
10. Offene Leads
11. Neueste Leads

Der Zeitraum kann über **Von** und **Bis** gefiltert werden.

## Lead-Status

Ein Lead kann diese Status haben:

1. **Neu** - gerade eingegangen und noch nicht bearbeitet.
2. **Kontaktiert** - es wurde Kontakt aufgenommen.
3. **Termin geplant** - ein Gespräch oder Termin ist geplant.
4. **Gewonnen** - der Lead wurde erfolgreich.
5. **Verloren** - der Lead wurde nicht erfolgreich.
6. **Unpassend** - der Lead passt nicht zum Angebot.

Wenn der Status auf **Kontaktiert** gesetzt wird, speichert das System automatisch den Kontaktzeitpunkt.

## Follow-ups

Für die Nachverfolgung gibt es:

1. `follow_up_date` für das nächste Follow-up-Datum.
2. `follow_up_done` als Haken, wenn das Follow-up erledigt ist.
3. `internal_notes` für interne Notizen.

Offene Follow-ups sind Leads mit Status **Neu** oder **Kontaktiert**, bei denen `follow_up_done` noch nicht gesetzt ist.

## CSV-Export

Leads können als CSV exportiert werden:

1. In der Lead-Zentrale über **CSV exportieren**.
2. Im Django-Admin über die Aktion **Ausgewählte Leads als CSV exportieren**.

Der Export enthält unter anderem Kontaktinformationen, Status, Fach, Klassenstufe, UTM-Daten, Quelle, Kampagne und interne Notizen.

## Empfohlener Arbeitsablauf

1. Täglich die Lead-Zentrale öffnen.
2. Neue Leads prüfen.
3. Jeden neuen Lead im Django-Admin öffnen.
4. Kontakt aufnehmen.
5. Status auf **Kontaktiert** setzen.
6. Interne Notiz eintragen.
7. Falls nötig, ein Follow-up-Datum setzen.
8. Nach dem Ergebnis den Status auf **Gewonnen**, **Verloren** oder **Unpassend** setzen.
9. Bei erledigtem Follow-up `follow_up_done` anhaken.
10. Für Kampagnen-Auswertung regelmäßig die UTM-Kampagnen in der Lead-Zentrale prüfen.

## Kurz gesagt

Leads kommen über das Kontaktformular rein, werden mit Kampagnen-Daten gespeichert, lösen Benachrichtigungen aus und werden danach intern über Lead-Zentrale und Django-Admin bearbeitet.
