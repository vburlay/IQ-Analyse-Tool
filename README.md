# IQ-Analyse-Tool

Dash-Prototyp für die operative Analyse und Prognose von Lückentagen. Das
Projekt liest aus der lokalen DuckDB. Über die Oberfläche kann `raw_bestand`
aus einer SAP-HANA-Tabelle aktualisiert werden.

## Funktionsumfang

### Ladedatum-Analyse

- Jahresauswahl für die letzten drei Kalenderjahre
- Umschaltung zwischen `Erstes Ladedatum` und `IPL`
- operative KPI für Potential, Ertrag, Ertragsanteil und Ertragswert
- Kreuztabelle nach Sachverhalt und Ergebnisschlüssel
- zeitliche Entwicklung der Lückentage

### Ankerprognose

- Auswahl von Ankerjahr, Sachverhalt, Stichtag und Intervallniveau
- Abgrenzung der Kohorte über die Überschneidung des Ankerzeitraums mit dem
  gewählten Ankerjahr
- eindeutiger Fallschlüssel aus `BWPKKD + Ankerbeginn + YBWRIQBPS + YBWRBPF`
- direkte Verwendung von `/BIC/YBWRELAD` als `erstes_ladedatum`
- einmalige Berücksichtigung jedes Falls in der Prognose, auch wenn er in
  mehreren IPL-Snapshots enthalten ist
- kumulierte Prognose für Fallzahl und `/BIC/YBWRFTAGE`
- Prognoseintervalle von 80, 90 oder 95 Prozent
- Ergebnistabelle mit `Aktuell`, `Noch erwartet` und `Zum Stichtag`
- monetäre Bewertung mit 11 Euro je Lückentag

## Datenfluss und Aktualisierung

```text
SAP HANA -- Anmelden und aktualisieren --> DuckDB/raw_bestand --> Dash
```

- Beim Start wird die vorhandene lokale DuckDB gelesen.
- Ohne HANA-Verbindung bleiben die vorhandenen lokalen Daten sichtbar.
- Nur eine vollständig geladene und geprüfte HANA-Lieferung ersetzt DuckDB.
- Bei einem Fehler bleiben die lokalen Daten unverändert.
- Es gibt keine automatische Erzeugung synthetischer Daten.

Host, SQL-Port, Schema und Tabelle können in der Datei `hana.ini` im
Projektordner unter `[hana]` gespeichert werden. Die Datei ist von Git
ausgeschlossen. Die Umgebungsvariablen `HANA_HOST`, `HANA_PORT`,
`HANA_SCHEMA` und `HANA_TABLE` haben Vorrang vor den entsprechenden
Dateiwerten. Benutzername und Passwort werden
nur in der Oberfläche eingegeben, nicht gespeichert und ausschließlich für
den aktuellen Import verwendet.

Die separate Schaltfläche **Lokale Daten löschen** verlangt eine Bestätigung
und ersetzt die lokale Datei durch eine neue DuckDB mit leerer
`raw_bestand`-Struktur. Danach bleiben Anwendung und Datenmodell startfähig.

## Methodischer Status

Die aktuell implementierte Prognose ist eine illustrative, gedämpfte
Fortschreibung auf den lokal gespeicherten Wochenwerten. Prognoseintervalle werden aus
simulierten zukünftigen Pfaden abgeleitet.

Die folgenden Bestandteile der methodischen Prüfung des ersten Prototyps sind
**noch umzusetzen**:

- Naive-Baseline sowie Mittelwert- und Median-Baseline
- Vergleich mit ETS und ARIMA
- zeitgerechtes Rolling Backtesting
- dokumentierte Modellwahl anhand stabiler Prognosegüte

Die aktuelle Darstellung darf deshalb nicht als nachgewiesene produktive
Prognosequalität interpretiert werden.

## Projektstruktur

```text
IQ-Analyse-Tool/
├── app.py
├── requirements.txt
├── data/
│   └── iq_analyse.db
├── tests/
└── pack/
    ├── config.py
    ├── data_access.py
    ├── services/
    └── ui/
```

## Einrichtung in PyCharm

1. Den Ordner `IQ-Analyse-Tool` mit **File → Open** öffnen.
2. Unter **Settings → Project → Python Interpreter** einen Interpreter in
   `IQ-Analyse-Tool/.venv` anlegen oder auswählen.
3. Im PyCharm-Terminal die Abhängigkeiten installieren:

```bash
python -m pip install -r requirements.txt
```

4. Anwendung starten:

```bash
python app.py
```

Danach ist die Oberfläche unter `http://127.0.0.1:8050/` erreichbar.

Vor dem ersten HANA-Import die vier Verbindungsparameter in `hana.ini`
oder in der Run Configuration setzen. Login und Passwort gehören nicht in Quellcode, YAML,
`.env`, Notebook oder Git.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Technologie

- Python
- Dash und Plotly
- Dash AG Grid
- DuckDB
- SAP HANA Client (`hdbcli`)
- Pandas und NumPy
