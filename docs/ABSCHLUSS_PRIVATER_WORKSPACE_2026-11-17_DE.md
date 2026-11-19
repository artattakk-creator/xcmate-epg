# Kontrollierter Abschluss und Arbeitsbereichwechsel

Auf ausdrücklichen Abschlussauftrag wurden weitere Import-, Artwork-, API- und
Wiederholungsversuche beendet. Kein Merge, Release, Tagesjob, Kodi-Eingriff oder
öffentlicher Datenexport. PR #7 bleibt Draft. Dieses Dokument enthält nur
aggregierte Ergebnisse; Inventare, Rohdaten und Caches bleiben außerhalb von Git.

Ausgangspunkt für den Wechsel ist die
[Übergabe auf dem Dokumentationsbranch](https://github.com/artattakk-creator/xcmate-epg/blob/docs/codex-v2-workspace-handoff-20261117/docs/UEBERGABE_CODEX_V2_WORKSPACE_WECHSEL_2026-11-17_DE.md).
Der Abschluss gehört zur Arbeitssitzung vom 17.11.2026. Der private Checkpoint
wurde am 17.11.2026 um 23:11:54 UTC angelegt (18.11., 00:11:54 MEZ).

## Git und letzter Teststand

Alle Code- und Teständerungen sind unter
`1e80645d299a0cbcbfa6c24bb5995671073653f7` auf
`feat/full-epg-coverage-artwork-issue6` gepusht. Letzte Änderung: konservative
Medien-/Episodenscope-Prüfung und Ablehnung fehlerhafter Provider-Cacheantworten.
Der beim Stopp bereits laufende Regressionstest endete mit **217 Tests, Exit 0**
(38,358 s); der fokussierte Test davor mit 19 Tests, Exit 0.
Nach dem Stopp wurden keine weiteren Anwendungstests oder Live-Läufe gestartet.
Die letzte konsolidierte Codekorrektur erhielt noch keinen weiteren unabhängigen
Review und keinen vollständigen Real-Daten-Replay.

## Privater Sicherungsstand

Lokales Verzeichnis: `work/private-closure-20261117/`.

- Vollinventar: `inventory.json`, jeder Pfad mit Größe, SHA256, Sicherungspfad und
  Sicherungs-SHA256; **964 Dateien / 1.944.803.781 Bytes**.
- Enthalten: alle vorhandenen Projektdateien unter `work/`, drei eingefrorene
  XMLTV-Rohquellen, sämtliche zehn SQLite-Datenbanken, Metadaten-/Artwork-Caches,
  Logo-Assets, alte/neue Run-Bundles, Berichte, private lokale Konfigurationen,
  Pilot und vorhandene LastGood-Stände. Git, Benutzer-/Browser-/OS-Zugangsdaten
  und Runtime-Secrets sind kein Bestandteil dieser Sicherung.
- Drei Rohquellen: zusammen **20.493.524 Bytes**; ihre Einzelhashes stehen nur im
  privaten Vollinventar. SQLite-Dateien: zusammen **1.040.814.080 Bytes**.
- Alle **10/10** Datenbanken wurden nach Ende der schreibenden Prozesse über die
  SQLite-Backup-API gesichert. Vollständiges `PRAGMA integrity_check` jeweils für
  Quelle und Sicherung: `ok`; Schemahashes und Tabellenzeilenzahlen gleich.
  Einzelbelege: `sqlite-integrity.json`.
- Archiv: `xcmate-private-workspace-checkpoint.tar.gz`, **640.688.709 Bytes**.
  SHA256: `9babecb7ce7fccb33d16636a64bf8a0d6a36fd155ce254d483fd32762449ecb2`.
- Inventar-SHA256:
  `6848a42cc2a21a055bf032df017bb0668d80ead24da651935e90efc36f275a87`.
- Archiv vollständig nach `restore-test/` entpackt: **964/964 Nutzdateien** mit
  identischen Größen/SHA256; zusätzlich vier Sicherungs-Metadateien. Keine
  ausgelassenen Symlinks/Spezialdateien, keine aufgezeichneten Prüfprobleme.
- Status **LOCAL_CHECKPOINT_VERIFIED**. Die Sicherung lief rein lokal und offline.
  Nachträglich erstellte Abschlussdokumentation liegt in Git; der private
  Sicherungsablauf und sein Protokoll liegen neben dem unveränderten Archiv.

**Die neue Cloud-Umgebung hat diese Dateien noch nicht erhalten.** Der lokale
Restore beweist die Lesbarkeit des Archivs, keinen Transfer in einen anderen
Arbeitsbereich. Git-Push übernimmt keine ignorierten Dateien. Ein externer Upload
wurde nicht durchgeführt. Die ältere Drive-Freigabe gilt nur für vier Pilotdateien,
nicht für dieses Archiv mit Rohdaten, Datenbanken und privaten Konfigurationen.
Das bisher vorbereitete Exportpaket mit 101 Dateien ersetzt diese Vollsicherung
nicht. Den alten Arbeitsbereich bis zu einem ausdrücklich freigegebenen privaten
Transfer samt vollständigem Readback erhalten. Es gibt derzeit keinen verifizierten
externen Wiederherstellungspunkt für den vollständigen lokalen Bestand.

## Tatsächlich vorhandener Gesamtexport und Grenzen

Der vor dem Abschluss erzeugte Export unter
`work/private-full/live-20261117/full/` enthält **1.278 quellenqualifizierte Sender
und 185.945 gültige Sendungen aus drei Quellen**; 24 deklarierte Sender sind leer.
Eine ungültige Sendezeit wurde verworfen. Sämtliche ursprünglichen nichtmedialen
Programm-/Senderinformationen wurden unabhängig gegen alle Eingänge geprüft:
keine fehlenden Originalprogramme und keine Inhaltsabweichungen.

GZIP: **17.411.748 Bytes**, SHA256
`7f56f2f75711eb7d521260a5de5f161dfa4a5beb367164d3421355c413f4daba`.
Erzeugender Codestand: `dee8b5bc5964879c0b66b00533ad5116a6f9fb13`, nicht die spätere
Sicherheitskorrektur `1e80645`. Kein neuer Replay nach dem Stopp.

Alle 26.812 zulässigen Identitäten wurden im Identitätslauf besucht:
5.248 identifiziert, 21.263 ohne belegte Identität, 283 mit ungeklärter
Quellen-Episodenscope und 18 Filmidentitäten ohne verfügbaren TMDB-Zugriff.
Es blieben keine durch API-Budget nicht besuchten Identitäten in dieser Queue.
9.586 Programmplätze haben verifizierte TVMaze-Metadaten; 9.566 Programmplätze
besitzen Bildreferenzen (5,145 %).
23.528 verifizierte typisierte Bildreferenzen: 9.554 Poster, 8.078 Backdrops/Fanart,
5.896 Episodenbilder, insgesamt 3.349 eindeutige URLs. XMLTV enthält 23.527 Icons,
weil eine URL zwei Bildrollen abdeckt. 90 abgelehnte Bildversuche bleiben sichtbar.
Diese Zahlen belegen weder TMDB-Treffer noch eine vollständige Bildabdeckung.

Alle XML-Iconmengen und aktuellen Bild-Hash-/Größen-/MIME-Belege wurden geprüft.
Die unabhängige Quellen-/Provider-Identitätsprüfung wurde jedoch beim Abschluss
**unvollständig und ungeklärt** beendet; ihre Abweichungen sind noch nicht als
Produktionsfehler oder erfolgreiche Abnahme eingeordnet. Kein abschließendes
Matching-PASS, kein vollständiger Pixel-/Kodi-Nachweis. Externe Artwork-Bildkörper
wurden überwiegend nicht gespeichert; Cache-Metadaten und Prüfbelege sind gesichert.

88 echte lokale Providerlogos wurden vollständig als PNG decodiert und ihrer
expliziten Providerbindung zugeordnet (1.942.167 Bytes). Im Teilkatalog: null
bestätigte Feed-Zuordnungen, vier Kandidaten aufgrund Provider-EPG-ID, 84 ungeklärt;
deshalb null exportierte Senderlogos und ein leeres bestätigtes Geräteprofil.
Der vollständige private Providerkatalog war nie lokal verfügbar: 78.516.224 Bytes
übersteigen die beobachtete Downloadgrenze von 33.554.432 Bytes. Er ist folglich
auch nicht Bestandteil des lokalen Checkpoints. Seine vorhandene externe Quelle
wurde nicht verändert. Keine fehlenden Katalogdaten erfunden.

**TMDB_LIVE_PENDING**: Diese Instanz beobachtete weiterhin Revision 1 ohne
Secretbindung und HTTP 401. Daraus folgt keine Aussage über die vom Benutzer
bereits veröffentlichte neue Umgebungsrevision. Kein Token wurde ausgelesen.
Gesamtstatus: **PARTIAL_COVERAGE / TMDB_LIVE_PENDING / AUDIT_INCOMPLETE**;
`PRIVATE_FULL_EPG_CREATED` wird nicht als abschließend abgenommen gemeldet.
Pilot, alter Vollstand, LastGood und vorherige Cacheversionen bleiben erhalten.

## Bereinigte Fehlerauswertung

Ein generisches `Command failed` belegt keinen grundlegenden Python-Defekt.
Fehlende Exit-Codes werden nicht nachträglich erfunden.

| Beobachtung | Exit-Code / Nachweis | Einordnung |
| --- | --- | --- |
| `sqlite3.OperationalError: database is locked` | Ursprünglicher Prozesscode nicht erhalten; damalige Stderr-Datei später ersetzt | Überlappender Audit/Render mit SQLite-Schreiber. Anschließender sequenzieller Inhaltsaudit vollständig, Exit 0. |
| Katalog zunächst unvollständig, ein beobachteter Providerdatensatz fehlte auf Indexseiten | Historischer kontrollierter Abbruch 4 laut Laufverlauf; ursprünglicher Tool-Return nicht erhalten | Index-/Updates-Versatz; gezielter Einzelabruf HTTP 200 stellte die beobachtete Abdeckung wieder her. Kein aktueller Katalogfehler. |
| TMDB-Konfigurationsprobe HTTP 401 | Probe-Python Exit 0; HTTP-Status separat ausgewertet | In dieser Runtime keine beobachtete Secretbindung; ausschließlich TMDB-Live-Test offen. |
| `exec-server transport disconnected` / `recovery timed out` | Kein Python-Exit-Code vorhanden | Tool-/Umgebungstransport abgerissen. Dateien und Prozesse überlebten teils; kein Beleg für einen Python-Absturz. |
| `Unknown process id` beim späteren Abrufen alter Sessions | Kein ursprünglicher Prozesscode ableitbar | Alte Tool-Session nach Bridge-Wechsel nicht abrufbar; auf Dateiergebnisse gestützt. |
| Fehlende Reportpfade bei `cat`, `rg`, `ls` bzw. falsches Arbeitsverzeichnis | Shell-Teilbefehle 1/2; ein zusammengesetzter Shell-Return kann vom letzten Befehl abhängen | Report vor Erstellung gelesen oder relativen Pfad falsch aufgelöst; kein Pythonfehler. Pfad korrigiert. |
| `ps -C python3` ohne Prozesse | Exit 1, nur Spaltenüberschrift | Erwarteter Nachweis beendeter Python-Schreiber. |
| `sqlite3`-CLI nicht installiert | Kein CLI vorhanden | Integritätsprüfung erfolgte erfolgreich mit Python-Standardbibliothek, nicht mit fehlendem CLI. |
| GitHub CLI `Forbidden`, Dokumentationsabruf CONNECT 403 | `gh` Exit 1, `curl` Exit 56 | Zugriffspfad blockiert; keine Umgehung, kein Pythondefekt. |
| Rote synthetische Regressionstests | Erwartete Testfehler; nicht jeder historische Return erhalten | Entwicklungsnachweise, später 217 Tests Exit 0; kein Beleg eines gescheiterten vollständigen Imports. |
| Unabhängige frische Artwork-Stichprobe abgebrochen | Exit 130 nach SIGINT auf ausdrücklichen Abschlussauftrag | Keine persistierten Live-Stichprobenergebnisse; Audit als unvollständig markiert. |
| Lokaler Checkpoint | Protokoll `COMPLETE`, leere Stderr, vollständiger Restore und SHA-Prüfung; ursprünglicher Tool-Session-Return nicht nachträglich abrufbar | Kein erneuter Sicherungslauf nötig. Separater `sha256sum -c` Exit 0. |

Nicht mehr vollständig belegbare historische Fehlermeldungen werden nicht als
gesicherte Ursachen ausgegeben. Rohprotokolle sind, soweit vorhanden, ausschließlich
im privaten Inventar enthalten. Der neue Arbeitsbereich muss zunächst den genehmigten
Datentransfer abschließen und anschließend den offenen Matching-Review sowie den
authentifizierten TMDB-Test mit der neuen Runtime fortsetzen.
