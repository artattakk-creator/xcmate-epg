# Issue #6 – privater Vollimport und Artwork V2

## Stand und Grenzen

Quellenvollständiger Import der drei eingefrorenen, bereits autorisiert geladenen Snapshots:
**1.278 quellenqualifizierte Sendereinträge, 185.946 Eingangsprogramme, 185.945 gültige Programme**.
Ein ungültiger Programmeintrag wird isoliert. 24 deklarierte Sender liefern keine Sendungen.
61 Startzeitkonflikte und 347 Überlappungen bleiben ausdrücklich nachvollziehbar erhalten;
identische Dubletten: 0. Keine stillen Kanal- oder Programm-Subsets, keine geratenen Alias-Merges.

Abnahme bleibt **PARTIAL_COVERAGE / ARTWORK_BLOCKED**, kein `PRIVATE_FULL_EPG_CREATED`:
Der zugängliche private Katalog umfasst nur 88 zwischengespeicherte Providerkanäle,
0 bestätigte Feed-Zuordnungen, 4 manuelle Kandidaten und 84 ungeklärte Einträge.
Die vollständige aktuelle Katalogdatenbank hat 78.516.224 Byte und überschreitet die
33.554.432-Byte-Transfergrenze des verbundenen Dateidownloads. Der Gesamt-Provider-Nenner
ist unbekannt; 88 ist ausdrücklich ein Ausschnitt. Kein Umgehen dieser Grenze.
Das separate Geräteprofil bleibt daher leer und darf nicht als nutzbarer Voll-EPG importiert werden.

TVMaze-Live-Ergebnisse und genaue aktuelle SHA/Dateigrößen stehen im aggregierten
`docs/issue6-acceptance.json`. Der abschließende Export enthält **463 Sendungen mit
500 geprüften Bildreferenzen: 463 Poster und 37 Episodenbilder, 55 unterschiedliche URLs**.
Das sind 0,249 % aller gültigen Programme; Backdrops: 0. Alle 500 Referenzen wurden
unabhängig gegen Originalprogramm, Identität und aktuelle Bildnachweise geprüft.
38 frische Bildabrufe bestanden die stärkere Strukturprüfung; anschließend erzeugte
eine reine Cache-Wiederholung ohne API-/Bildabruf identische GZIPs und ließ beide
Ansichtsmanifeste unverändert. Echte Bildpfade wurden angefordert, gültige Zuordnungen,
HTTP-Antworten und Bilddaten geprüft; Offline-Mocks gelten nicht als Live-Nachweis.

Alle 88 zugänglichen privaten Providerlogos wurden aus dem Backup materialisiert und
gegen SHA256, Abmessungen, PNG-Struktur und begrenzten Pixelstream geprüft. Sie sind
im privaten Sidecar an Provideridentitäten gebunden. Ohne bestätigte Provider-zu-Feed-Zuordnung
wird keines davon einem Feed-Sender untergeschoben. Die XMLTV-Logoquote bleibt 0.

## Sicherer TMDB-Anschluss

Der Benutzer hat ein persönliches HTTP-Secret `TMDB_READ_ACCESS_TOKEN` für
`api.themoviedb.org` eingerichtet. Die laufende Instanz liefert weiterhin Revision 1,
keine bereite Secret-Bindung und keinen Runtime-Platzhalter. Ein erneuter normaler
Aufruf von `/3/configuration` über den geerbten Proxy ohne eigenen Authorization-Header
lieferte HTTP 401. Daher ausschließlich
**TMDB_LIVE_PENDING** für den authentifizierten Live-Test; Vollimport, TVMaze,
Client-Implementierung und Regressionen werden fortgesetzt.

Keine Übernahme eines GitHub-Repository-Secrets in Codex, keine Token-Datei, keine
Zugangsdaten in Konfiguration, XMLTV, Sidecars, Logs oder Git. Verwalteten Proxy,
CA-Prüfung und geerbte Netzwerkeinstellungen unverändert verwenden. Ein neuer Auftrag
muss die beobachtete Binding-Readiness prüfen und mit einem frischen Prozess testen.
Keinen Platzhalterwert erfinden und den Token niemals im Chat anfordern.

`artwork.tmdb_secret_env` akzeptiert ausschließlich `TMDB_READ_ACCESS_TOKEN`.
Falls die Plattform stattdessen ein bereites HTTP-Secret injiziert, kann
`artwork.tmdb_http_binding` die **beobachtete**, nicht erfundene Kontrollmetadatenform
`{"name":"TMDB_READ_ACCESS_TOKEN","host":"api.themoviedb.org","state":"ready"}`
enthalten. Der Client sendet dann keinen eigenen Authorization-Header und behält
Proxy und TLS-Prüfung bei. `TMDB_RUNTIME_BOUND` bestätigt nur die Client-Bindung,
keinen erfolgreichen Live-Test. Ohne Bindung bleibt `TMDB_LIVE_PENDING`; die anderen
Quellen und TVMaze werden weiterverarbeitet.

TMDB unterstützt Filme, Serien ohne Episodennummer und explizite Staffel/Folge.
Titel/Jahr und Detailidentitäten werden konservativ geprüft, Episodenjahr gegen
das Ausstrahlungsdatum der tatsächlichen Episode. Poster, Backdrops/Fanart,
Episodenbilder und optionale Staffelposter erhalten getrennte Typen/Evidenz;
Originaltitel, Genres, Beschreibungen und Bewertungen ergänzen fehlende Felder.
Eine Serienbewertung wird nicht als fehlende Episodenbewertung ausgegeben.
Ein fehlender optionaler Detail-/Staffelabruf verwirft kein bereits bestätigtes
Episodenbild. Mocktests belegen diese Pfade, ersetzen aber nicht den ausstehenden
authentifizierten TMDB-Live-Nachweis.

## Lokaler privater Ablauf

Python 3.11+ auf Linux, Standardbibliothek, kein kostenpflichtiger Dienst.
Die ursprüngliche kleine CLI und sämtliche alten Tests/Caches bleiben erhalten.
Der Vollimport ist ein eigener Befehl:

```sh
python3 -m xcmate_epg.full_preview \
  --config work/private-input/full-config.json \
  --private-root work/private-full/run --authorize-network
```

Vorlage: `config/full-preview.example.json`. Die echte Konfiguration und sämtliche
Inputs liegen ausschließlich in ignorierten privaten Pfaden. `--authorize-network`
und die jeweilige Metadatenfreigabe sind zusätzlich erforderlich. Für reine lokale
Inputs ohne Metadaten-Netzwerk den Schalter weglassen. Alle konfigurierten Quellen
müssen verfügbar sein; Source-Fehler melden `SOURCE_BLOCKED` und erhalten LastGood.

`mapping` verweist auf eine private JSON-Datei, siehe `config/mapping.example.json`.
Nur explizite Bestätigungen mit Evidenz dürfen Gerätekanäle auswählen; Namen,
HD/SD-Ähnlichkeit und Provider-EPG-Hinweise erzeugen ausschließlich Vorschläge.
Ein fehlendes gesperrtes Ziel bleibt gesperrt und prüfbar. Review und vorherige
Version werden privat gespeichert. Vollständigkeit des Katalogs niemals aus einem
Widget-/Cache-Ausschnitt ableiten.

Streaming nutzt begrenzte Expat-Datensätze und SQLite-Spooling. Keine komplette
100-MB-Dekompression oder Feed-Baumhaltung. Größen-, Verhältnis-, Datensatz-, Tiefen-,
Zeit-, Prozess-RSS- und Speicherdatenbank-Limits sind konfigurierbar. Die reine externe
XMLTV-DTD-Deklaration wird erkannt, niemals geladen; Entities/XXE bleiben gesperrt.
Rechtmäßige XMLTV_NS-Schreibweisen einschließlich Leerraum, Totals und Teilangaben
werden sicher erkannt; Originalwerte bleiben im Export erhalten. Serienpremiere und
Episodenjahr werden getrennt geprüft; fehlende Evidenz erzeugt keinen Treffer.

Private Roh-Snapshots enthalten die unveränderten Originalbilder und Metadaten.
Ungeprüfte Medienverweise werden im privaten Provenienzbestand behalten, nicht blind
in XMLTV aktiviert. TMDB/TVMaze-Bilder benötigen echte Identitäts- und Medienprüfung;
unsichere URLs, Zugangsdaten und fremde Redirects bleiben gesperrt. Positive und
negative Caches bleiben dauerhaft; transiente Fehler werden nicht als negative
Medienzuordnung gespeichert. Request-Limits und nicht versuchte Identitäten erscheinen
im Bericht. Fehlende Poster, Backdrops oder Episodenbilder werden nicht erfunden.

## Ausgaben, Kohärenz und Rückfall

Aktueller privater Arbeitsbereich: `work/private-full/issue6/`.

- `full/`: kompletter gültiger Quellenbestand mit GZIP, Manifest, SHA256 und Versionen.
- `device/`: ausschließlich bestätigtes Geräteprofil; derzeit explizit 0/0 und gesperrte Abnahme.
- `artwork-metadata.jsonl`: typisierte, quellen-/sender-/zeitgebundene Bildzuordnungen.
- `provider-logo-metadata.jsonl` und `assets/logos/`: bestätigte lokale Providerlogo-Evidenz.
- `mapping-review.json`, `quality-report.json`, `source-media.jsonl`: private Prüfung/Provenienz.
- `run-manifest.json`: autoritativer Commit-Punkt zum unveränderlichen vollständigen Run-Bundle.

Crash-konsistente Leser folgen `run-manifest.json` zum unveränderlichen Bundle unter
`runs/`; Komfortpfade können während eines harten Prozessabbruchs zwischen Kopien
stehen. Behandelte Fehler rollen die gesamten Komfortpfade zurück. Ein beschädigtes
wiederverwendetes Bundle wird vor jeder Veröffentlichung anhand Typen, Dateiinventar
und SHA geprüft und abgewiesen. Es gibt keinen automatischen Lösch-/Pruning-Job.
Der One-shot-Runner setzt einen einzelnen Schreiber je privatem Wurzelverzeichnis voraus.
Das RSS-Budget wird an begrenzten Datensatz-/Allokationsgrenzen geprüft, nicht als
Betriebssystem-Hardlimit gesetzt.

Der erste private Pilot unter `work/private-live/run/output/` bleibt unverändert:
875.298 Byte, SHA256 `3edde025d368f3069b541545b688a088ce7942132bf00209cdc460c161833f8e`.
Vorbereitetes V2-Backup darf nur die geprüften Exportdateien/Sidecars/Logo-Assets enthalten,
keine Rohquellen, Konfigurationen, Credentials oder privaten Cache-Datenbanken.
Neue Übertragung nur nach erforderlicher Ziel-/Dateifreigabe und Prüfung von
Eigentümerrechten; anschließend vollständiger Readback/Hashvergleich. Keine öffentliche Ausweichablage.

Das V2-Paket ist lokal unter `work/private-full/issue6-backup-prepared/` vorbereitet:
101 geprüfte Nutzdateien (19.837.885 Byte) plus `backup-index.json`. Das Inventar
enthält Größe und SHA256 jeder Datei; Dateien/Verzeichnisse sind nur für den lokalen
Eigentümer lesbar. Der private Drive-Projektordner wurde erneut als ungeteilt mit
einer Owner-Berechtigung geprüft. Für dieses neue Paket erfolgte **kein Upload**.
Die frühere Übertragungsfreigabe galt den vier Pilotdateien; für die neuen V2-Sidecars
und Logo-Assets wird vor einer Übertragung die konkrete Ziel-/Dateifreigabe benötigt.

## QA und verbleibende Abnahmen

```sh
mkdir -p work/private-test-tmp
TMPDIR="$PWD/work/private-test-tmp" python3 -m unittest discover -s tests -q
python3 tools/smoke_xmltv.py --output work/offline-smoke
```

Das private TMPDIR ist in dieser verwalteten Umgebung wegen geschützter `.git`-Marker
unter `/tmp` erforderlich; die Schutzprüfung wird nicht abgeschaltet. Tests umfassen
mehr als 100 MB Streaming, mehr als 32 MB Streaming-Export, Bomben/XXE, DST, einzelne
fehlerhafte Events, 5 parallele Source-Downloads, Mapping-Sperren, Artwork-Ambiguität,
Cache-Migration, Budgets, echte Bildstruktur und atomaren Gesamt-Rollback.
Abschließend bestanden 160 Regressionstests einschließlich 19 neuer TMDB-Integrationstests;
die unabhängige TMDB-Nachprüfung bestand 65 fokussierte Tests. Ein gefundener
Serien-/Episodenbewertungsfehler wurde vor der Abnahme korrigiert und erneut geprüft.

Quellabdeckung ist nicht Geräteabdeckung; Zeitgrenzen sind nur verfügbare Zeit-Envelopes,
keine erfundene Garantie lückenloser Programme über den ganzen angefragten Zeitraum.
Keine Android-/BINGIE-Pixelabnahme. Kein Kodi-Pilot geändert. Kein Merge, keine Pages,
keine öffentlichen EPG-Artefakte oder echten Programme in CI, kein produktiver Tagesjob,
kein GitHub-Budgeteingriff und keine kostenpflichtige KI-API.
