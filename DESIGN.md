# BrainBoost Design Style-Guide

Dieser Style-Guide dokumentiert die aktuell im Projekt verwendeten Farben, Fonts und grundlegenden UI-Konventionen. Die zentralen Werte liegen in `brainboost/core/static/core/styles.css`.

## Farben

### Primäre Markenfarben

| Name | CSS-Variable | Hex | Verwendung |
| --- | --- | --- | --- |
| Teal | `--teal` | `#2c8a8a` | Hauptmarkenfarbe, Hintergrundverläufe, Akzente |
| Dark Teal | `--teal-dark` | `#1f6666` | Primäre Buttons, aktive Elemente, starke Akzente |
| Logo Turquoise | `--logo-turquoise` | `#369693` | Logo- und Markentext-Akzente |
| Cream | `--cream` | `#f7f7f2` | Kartenflächen, helle Panels, Button-Text auf dunklem Grund |
| Yellow | `--yellow` | `#ffc928` | Highlights, Eyebrows, sekundäre Akzente |
| Red | `--red` | `#e63946` | Warnungen, starke visuelle Akzente |
| Slate | `--slate` | `#4a4a4a` | Fließtext, sekundäre Texte |
| Accent | `--accent` | `#1a2238` | Überschriften, primäre Textfarbe |

### Hintergrund und Flächen

| Wert | Verwendung |
| --- | --- |
| `linear-gradient(135deg, var(--teal) 0%, #43a6a6 50%, #c3e0db 100%)` | Globaler Seitenhintergrund |
| `rgba(247,247,242,0.96)` | Landingpage-Sektionen und CTA-Flächen |
| `#ffffff` | Karten, Eingabefelder, innere Content-Flächen |
| `rgba(255,255,255,0.8)` | Topbar-Hintergrund |
| `rgba(44,138,138,0.08)` bis `rgba(44,138,138,0.18)` | leichte Teal-Hintergründe für aktive/markierte UI |

### Status- und Hilfsfarben

| Farbe | Verwendung |
| --- | --- |
| `#b3261e` | Formularfehler |
| `#8a6500` | gelbe Info-Badges |
| `#a55600` | orange Alert-Badges |
| `#5fb5ff` | Feedback-Link auf der Startseite |
| `#1d4ed8` | Cookie-Hinweis-Link und Button |

## Typografie

### Hauptschrift

```css
--font-sans: "Nunito", "Avenir", "Helvetica Neue", Arial, sans-serif;
```

Verwendung:
- Standard-Schrift für Body, Formulare, Buttons und die meisten UI-Texte.
- Rund, freundlich und gut lesbar.
- Falls `Nunito` nicht verfügbar ist, greifen die System-Fallbacks.

### Marken-/Navigationsschrift

```css
@font-face {
    font-family: "Shababa W01 Regular";
    src: url("../fonts/shababa/shababa-w01-regular.woff2") format("woff2"),
         url("../fonts/shababa/shababa-w01-regular.woff") format("woff");
}

--font-brand-nav: "Shababa W01 Regular", var(--font-sans);
```

Verwendung:
- Brand-Titel in der Topbar.
- Einzelne Wordmark-Elemente auf der Startseite.
- Sparsam einsetzen, nicht für Fließtext.

## UI-Grundlagen

| Token | Wert | Verwendung |
| --- | --- | --- |
| `--card-radius` | `18px` | Standardradius für Cards und größere Panels |
| `--shadow` | `0 15px 45px rgba(0,0,0,0.12)` | Standard-Schatten für Cards und Buttons |

### Buttons

Primäre Buttons nutzen:

```css
background: var(--teal-dark);
color: var(--cream);
border-radius: 12px;
font-weight: 700;
```

Sekundäre Buttons sind heller und zurückhaltender:

```css
background: #fff;
color: var(--teal-dark);
border: 1px solid rgba(31,102,102,0.22);
```

### Cards und Sektionen

- Große Inhaltsflächen verwenden helle Cream- oder Weißtöne.
- Cards haben meist `12px` bis `18px` Border-Radius.
- Rahmen sind zurückhaltend, häufig mit Teal-Transparenzen wie `rgba(31,102,102,0.14)`.
- Schatten sollen Tiefe geben, aber nicht dekorativ dominieren.

## Landingpage-Konventionen

Für Lead- und Landingpages werden vor allem diese Klassen genutzt:

| Klasse | Zweck |
| --- | --- |
| `.lead-landing` | Seitencontainer mit Grid-Abständen |
| `.lead-hero` | Hero-Bereich mit zweispaltigem Desktop-Layout |
| `.lead-section-block` | Standard-Sektionsfläche |
| `.lead-audience-grid` | Zielgruppen-Grid, Desktop dreispaltig |
| `.lead-audience-card` | Zielgruppenkarte |
| `.lead-cta` | primärer Landingpage-CTA |
| `.lead-secondary-cta` | sekundärer CTA |

Zielgruppen-Karten nutzen farbige Oberkanten:

| Zielgruppe | Farbe |
| --- | --- |
| Eltern | `var(--teal-dark)` |
| SchülerInnen | `var(--yellow)` |
| TutorInnen | `var(--red)` |

## Responsive Verhalten

- Desktop: wichtige Landingpage-Grids sind mehrspaltig.
- Unter `860px`: Hero, Zielgruppen-Grid und Split-Layouts wechseln auf eine Spalte.
- Unter `560px`: CTAs werden vollbreit, damit sie mobil gut bedienbar sind.

## Hinweise für neue UI

- Bestehende CSS-Variablen verwenden, bevor neue Farben eingeführt werden.
- Für primäre Aktionen `var(--teal-dark)` nutzen.
- Gelb nur als Highlight einsetzen, nicht als dominante Seitenfarbe.
- Rot nur für Warnungen oder bewusst starke Akzente.
- Fließtext in `var(--slate)`, Überschriften in `var(--accent)`.
- Neue Schriftarten nur einführen, wenn es einen klaren Produktgrund gibt.
