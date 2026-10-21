# Biegeliste bereinigen

Räumt die Biegeformen in SOFiSTiK-Stahllisten (STLI „Stahl- und Mattenliste“) auf:
Überlappende Maße und Winkel werden entfernt, die Form wird vergrößert neu gezeichnet
und die Beschriftung kollisionsfrei gesetzt. Abgestimmt auf SOFiSTiK 2024.

## Im Browser (ohne Python)

Website öffnen, PDFs hineinziehen, bereinigte Dateien herunterladen.
Die Verarbeitung läuft komplett im Browser (Pyodide + PyMuPDF) – die PDFs werden
nirgendwohin hochgeladen. Der erste Aufruf lädt ca. 20 MB, danach aus dem Cache.

## Lokal mit Python

Voraussetzung: Python mit `pymupdf`.

```
python Biegeliste.py                         # alle PDFs aus ./input -> ./output
python Biegeliste.py liste.pdf               # einzelne Datei -> liste_bereinigt.pdf
python Biegeliste.py --nur-ueberlappungen    # nur Formen mit Überlappungen anfassen
```

## Website lokal testen

```
python -m http.server 8000 --bind 127.0.0.1
```
dann http://localhost:8000 öffnen (direkt als Datei geöffnet funktioniert es nicht).

## Dateien

| Datei | Zweck |
|---|---|
| `Biegeliste.py` | die eigentliche Logik (wird lokal und von der Website verwendet) |
| `index.html` | Oberfläche der Website |
| `worker.js` | lädt Pyodide/PyMuPDF im Hintergrund und ruft `Biegeliste.process` auf |

## Hinweise

- Ergebnis immer stichprobenartig mit dem Original vergleichen. Stellen ohne sauberen
  Platz werden unter „Bitte prüfen“ mit Seite und Position gemeldet.
- Im Browser wird statt Consolas die freie Schrift DejaVu Sans Mono verwendet.
- PyMuPDF steht unter der AGPL-3.0.
