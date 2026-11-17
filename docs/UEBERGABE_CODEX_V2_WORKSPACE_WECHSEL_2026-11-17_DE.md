# XCMate EPG Agent – sichere Übergabe beim Codex-Arbeitsbereichwechsel

**Stand:** 17.11.2026, ca. 23:30 Uhr MEZ · **Kein Merge, kein Release, keine öffentliche EPG-Veröffentlichung.**

## 1. Warum der Wechsel?

Der bisherige Codex-Lauf meldet wiederholt **einzelne fehlgeschlagene Python-Kommandos**. Der Screenshot zeigt aber nur `Command failed`, keine vollständigen Stderr-/Traceback-Details; ein grundlegender Defekt ist **nicht erwiesen**. Gleichzeitig nutzt der alte Lauf nach eigener Rückmeldung **Codex-Umgebungsrevision 1**, ohne das inzwischen veröffentlichte persönliche TMDB-HTTP-Secret; `/3/configuration` scheiterte dort mit HTTP 401.

Ein **neuer Codex-Task** mit ausdrücklich ausgewählter veröffentlichter Umgebung `xcmate-epg` ist sinnvoll. Die alte Aufgabe erst verlassen, **nachdem private Snapshots, SQLite-Artefakte und laufende Zwischenstände sicher übertragbar sind**. Eine neue Codex-Cloud-Sitzung übernimmt ignorierte Arbeitsdateien NICHT zwangsläufig.

## 2. GitHub-Code ist bereits gesichert

- Öffentlicher Code: [artattakk-creator/xcmate-epg](https://github.com/artattakk-creator/xcmate-epg)
- Entwicklungsbranch: `feat/full-epg-coverage-artwork-issue6`
- [Draft PR #7](https://github.com/artattakk-creator/xcmate-epg/pull/7), [Master-Issue #6](https://github.com/artattakk-creator/xcmate-epg/issues/6)
- Letzter zum Übergabezeitpunkt unabhängig verifizierter Branch-HEAD: **`dee8b5bc5964879c0b66b00533ad5116a6f9fb13`**. **Vor Fortsetzung neu prüfen**, ob weitere Commits gepusht wurden.
- Offline GitHub Actions [Run #44190835086](https://github.com/artattakk-creator/xcmate-epg/actions/runs/44190835086) am gesicherten Stand **SUCCESS**.
- Neuere Module: vollständiger Streaming-/SQLite-Import, getrennte Exportansichten, konservative Senderidentitäten, TMDB-Live-Integration (noch nicht live nachgewiesen), TVMaze-Episode/Cast/Galerie/Medium-Fallback, Bild-/Logo-Strukturprüfung, atomare LastGood-Publikation.
- Alle echten Rohfeeds, personalisierten Provideridentitäten, Token und private Caches bleiben **außerhalb des öffentlichen Git-Repos**.

## 3. Letzte unabhängig belegte Gesamtdurchlauf-Zahlen

`docs/issue6-acceptance.json` auf PR #7 ist bis zum letzten Check noch die **alte, überprüfte** private Abnahme:

| Merkmal | Stand |
|---|---:|
| Konfigurierte echte Quellen | 3 von 3 |
| Quellenqualifizierte Sendereinträge | 1.278 |
| Eingangssendungen | 185.946 |
| Gültige Sendungen | 185.945 |
| Ungültige, isolierte Sendungen | 1 |
| Sender mit Programmen | 1.254 |
| Programme mit nachgewiesenen Bildern | 463 |
| Verifizierte Bildreferenzen | 500 (463 Poster, 37 Episodenbilder, 0 Backdrops) |
| TMDB-Live-Nachweis | **TMDB_LIVE_PENDING** |
| Geprüfte lokale Providerlogos | 88 |
| Provider↔Feed-Zuordnungen bestätigt | 0 |

V2-GZIP vorheriger vollständiger Quell-Export: **16.927.160 Byte**, SHA256 `444092846c43ae9e0dfcb36cb107a89e207234e7ed75af768cea4a7327076845`. Die Quelldaten decken nur 15.–30.11.2026 ab, nicht nachweislich lückenlos 7 Tage rückwärts/20 Tage voraus. Das **Geräteprofil ist 0/0** mangels bestätigter Zuordnungen. Trotz neuem Code sind **keine verbesserten Live-Bildzahlen erwiesen**, bis ein neuer privater Gesamt-Export validiert wurde.

## 4. Vor dem Beenden: Übergabe aus dem *alten* Codex-Arbeitsbereich

### A. Sofort unterbrechen, kein destruktiver Abbruch

Der alte Agent soll **keine weiteren teuren Live-/Artwork-Schleifen** starten, sondern einen einmaligen sauberen Checkpoint erzeugen. Sichere offene SQLite-Schreibvorgänge synchronisieren und Writer schließen, dann konsistent über die SQLite-Backup-API sichern; **keine aktive Datenbank blind kopieren**.

### B. Inventar prüfen, dann privat sichern

Die folgenden relativen Pfade aus `docs/ISSUE6_HANDOFF_DE.md` *prüfen statt als garantiert vorhanden zu behandeln*:

- `work/private-live/raw/s1.bin`, `s2.bin`, `s3.bin` – 3 eingefrorene Originalquellen.
- `work/private-live/run/output/` – alter kleiner Pilot / LastGood.
- `work/private-full/issue6/config.json`, `stream.sqlite`, `artwork.sqlite`.
- `work/private-full/issue6/run-manifest.json`, `runs/`, `full/`, `device/`.
- `work/private-full/issue6/artwork-metadata.jsonl`, `provider-logo-metadata.jsonl`, `assets/logos/`.
- `work/issue6/` – Diagnostik, Bild-/Mapping-Reviews.
- Im aktuellen Terminal sichtbar erwähnte weitere private Pfade `work/live-20261117/` und `work/private-full/live-20261117/` mit ggf. `full-render-progress.json`, temporären Artwork-Daten oder Wiederanlaufdaten. **Existenz per Dateisystemabfrage belegen**.

Inventar **ohne private Pfad-URLs/IDs und ohne Secretwerte**: relativer Pfad, Bytegröße, SHA256, SQLite `PRAGMA integrity_check`, JSON-Validierung, GZIP-/XMLTV-Validität, Zeitpunkt, Vollständigkeit und Schreib-/Readback-Status. Der ehemals vorbereitete V2-Sicherungsordner mit 101 Nutzdateien **enthielt ausdrücklich keine Rohquellen oder SQLite-Caches** und reicht daher allein nicht zur identischen Wiederaufnahme.

Falls kein vollständiger privater Export möglich ist: nicht behaupten, die Dateien seien übertragbar. Festhalten, welche privaten Inputs neu geladen werden müssten und welche Caches entfallen würden.

### C. Externes Speicherziel kontrolliert wählen

- Öffentliches Code-Repo, öffentliche Actions-Artefakte und GitHub Pages **niemals** für echte Feedinhalte, Providerlisten oder Secrets nutzen.
- Zugelassenes privates Ausgabe-Repo `artattakk-creator/xcmate-epg-private` existiert und wurde als **private** verifiziert. Der synthetische Schreib-/Readback-Test [Run #44173864386](https://github.com/artattakk-creator/xcmate-epg/actions/runs/44173864386) war erfolgreich. **Dieser Test bedeutet nicht, dass ein echter EPG-Upload schon stattgefunden hat.**
- Persönlicher privater Google-Drive-Projektordner ist als alternatives Ziel bekannt. **Vor Neuübertragung der vollständigen, ggf. sensiblen V2-Rohdaten/Caches den genauen Zielort und die konkret freigegebenen Dateitypen mit dem Nutzer abstimmen.** Vorher ausschließlich lokale Sicherungsdateien vorbereiten, keine neue öffentliche oder unbestätigte Ablage.
- Backup-Schutz, Besitzerrechte, Integritätsmanifeste und **tatsächlichen Read-back** verifizieren; erst danach alten Arbeitsbereich beenden.

### D. Öffentliche Code-Änderungen sichern

`git status --short`, `git diff --stat`, `git log -1` erfassen. Nur bereinigte quelloffene Code-, Test- und Dokumentationsänderungen committen und auf bestehenden Featurebranch pushen, dann HEAD und grünen Test nachweisen. **Keine echten privaten Daten in PR/Issue/Log/Code.**

### E. Ursachenanalyse der lokalen Fehlversuche

Vom alten Task zu jeder wiederholten fehlgeschlagenen **Python**-Operation nur **bereinigte** Exit-Codes und relevante Stderr-/Traceback-Auszüge sichern; Eingabedaten oder Tokens nicht offenlegen. Prüfen: fehlende Datei, invalide Pfade, DB-Lock, falsche aktuelle Arbeitsumgebung, Timeout, API 401, unterbrochener Zwischenlauf oder Programmfehler. `Command failed` allein belegt nichts davon.

## 5. Neuer Codex-Task: erst Auth, dann vollständiges Artwork

1. **Neue Aufgabe**, ausdrücklich veröffentlichte Umgebung `xcmate-epg` und Repository `artattakk-creator/xcmate-epg` auswählen; bestehenden V2-Branch verwenden. Keine implizite Übernahme der alten Runtime annehmen.
2. Erst Runtime-Zugriff auf persönliches HTTP-Secret `TMDB_READ_ACCESS_TOKEN` für `api.themoviedb.org` prüfen, **ohne** Tokenanzeige oder Logwerte; echter erfolgreicher `/3/configuration`-Abruf, danach Movie/TV/Episode und öffentlicher Bild-CDN-Pfad. Bindung allein ist noch kein Live-Nachweis.
3. Alternativ zur Codex-HTTP-Bindung existiert ein **noch manuell zu startender** isolierter TMDB-Live-Test im öffentlichen Agent-Repo: `.github/workflows/xcmate-tmdb-live-auth-smoke.yml` auf `main`, der das dort hinterlegte **separate** GitHub-Secret nutzt. Keine Secrets aus GitHub auslesen und nicht in Codex kopieren.
4. Verifiziertes privates Backup wiederherstellen und frühere Quellen-/Artwork-Caches prüfen. Wenn nicht verfügbar: nur autorisierte Quellen neu beziehen und ehrlich kennzeichnen, welche Cachearbeit verloren ging.
5. Alle verfügbaren gültigen Sender/Programme mit **budgetiertem, in Etappen wiederaufnehmbarem** TMDB-/TVMaze-Abgleich verarbeiten. Echte Bild-URLs mit Gültigkeits-, Identitäts- und Herkunftsprüfung; Poster, Backdrops, Episodenbilder, Logos, Titel/Jahr/Genre/Beschreibung/Bewertungen/Cast soweit vorhanden. Keine falschen oder erdachten Zuordnungen. Quelle/Provider-Mapping nur ausdrücklich bestätigte IDs; 88 bekannte Providerlogos nicht blind auf Feed-Sender übertragen.
6. Einen **neuen privaten** XMLTV-GZIP-Gesamtexport, getrenntes verifiziertes Geräteprofil, Manifest, SHA256, Bild-/Logo-Sidecars und Qualitätsbericht erstellen. Auf Originalmetadaten-Erhalt, XML-Referenzen und LastGood testen. Private Zielveröffentlichung separat freigeben; keine öffentliche EPG-Ausgabe, kein Merge, kein Tagesjob, keine zusätzlichen kostenpflichtigen APIs.
7. Draft PR #7 + Issue #6 mit den **tatsächlich nachgewiesenen neuen Zahlen und Fehlerursachen** aktualisieren, und klar `TMDB_LIVE_PASS` vs. `TMDB_LIVE_PENDING`, `SOURCE_FULL` vs. `PROVIDER_MAPPING_PARTIAL` unterscheiden.

## 6. Kurzer Startauftrag für neue Codex-Sitzung

> Lies `docs/UEBERGABE_CODEX_V2_WORKSPACE_WECHSEL_2026-11-17_DE.md` aus Branch `docs/codex-v2-workspace-handoff-20261117`, Issue #6, Draft-PR #7 und `docs/ISSUE6_HANDOFF_DE.md`. Checkout Entwicklungsbranch `feat/full-epg-coverage-artwork-issue6`, prüfe zunächst Commit/Tests und den privaten Sicherungsbestand. Benutze ausdrücklich die neu veröffentlichte Umgebung `xcmate-epg` mit HTTP-Secret-Bindung. Teste authentifizierte TMDB-Live-Abfragen ohne Secret-Offenlegung, stelle autorisierte private Snapshots/Caches wieder her und führe dann einen vollständig gemessenen privaten Artwork-/XMLTV-Gesamtlauf aus. Kein Merge, keine öffentliche Publikation, kein 24h-Job, keine zusätzlichen kostenpflichtigen Dienste. Dokumeniere alle neuen Messwerte in PR #7.

**Hinweis:** Diese Übergabedatei ist absichtlich ausschließlich Dokumentation und ersetzt keine tatsächlich durchgeführte, verifizierte Sicherung privater Arbeitsdaten.
