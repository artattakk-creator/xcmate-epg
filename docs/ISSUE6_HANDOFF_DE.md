# Issue #6 – Fortsetzung auf dem bestehenden Branch

Branch: `feat/full-epg-coverage-artwork-issue6`.
Draft-PR: https://github.com/artattakk-creator/xcmate-epg/pull/7
Basis bleibt PR #5 / `feat/private-live-preview-issue-4`; kein Merge.

## Aktueller Abschlussauftrag: keine weiteren Läufe

Die Arbeiten sind auf Benutzeranweisung kontrolliert gestoppt. Maßgeblich für
den aktuellen Stand ist der [Abschlussbericht](ABSCHLUSS_PRIVATER_WORKSPACE_2026-11-17_DE.md):
Code/Test-Commit `1e80645`, zuletzt 217 Tests Exit 0, neues privates Vollarchiv
mit 964 Dateien und zehn integritätsgeprüften SQLite-Datenbanken. Das Archiv wurde
lokal erfolgreich wiederhergestellt, **noch nicht in eine neue Umgebung übertragen**.
Vor einem Wechsel den alten Arbeitsbereich erhalten. Keine erneuten Live- oder
Artwork-Läufe ohne neuen Auftrag. Die folgenden Abschnitte beschreiben den älteren
Abnahmestand; sie ersetzen den neueren Abschlussbericht nicht. Vier Kandidaten
beruhen auf Provider-EPG-IDs, nicht allein auf Sendernamen.

## Gesicherter Entwicklungsstand

Vollständige Streaming-Verarbeitung, getrennte Quellenidentitäten, SQLite-Spool,
dauerhafte positive/negative Caches, LastGood, konservative Senderzuordnung,
privater Gesamt-/Geräteexport, atomarer Run-Commit und echte TVMaze-Bildprüfung
sind implementiert. Die zusätzliche TMDB-Integration ist in `9f43b5c` enthalten:
Filme, Serien, Episoden, Staffelposter, Metadaten, sichere Runtime-Bindung.
`b59445f` ergänzt die geprüfte Trennung von Serien- und Episodenbewertungen.
160 Regressionstests bestanden; die neue TMDB-Integration wurde zusätzlich
unabhängig mit 65 fokussierten Tests ohne Befund geprüft.
Genaue Abnahmezahlen und Grenzen: `docs/issue6-acceptance.json` und
`docs/ISSUE6_PRIVATE_FULL_DE.md`.

## Private Daten im bestehenden Arbeitsbereich

Diese Pfade gehören nicht in Git oder öffentliche Artefakte:

- `work/private-live/raw/s1.bin`, `s2.bin`, `s3.bin`: die drei vollständigen eingefrorenen Snapshots.
- `work/private-live/run/output/`: erster funktionierender Pilot, unverändert erhalten.
- `work/private-full/issue6/config.json`: private lokale Vollimport-Konfiguration.
- `work/private-full/issue6/stream.sqlite`, `artwork.sqlite`: validierte Quellen und Artwork-Caches.
- `work/private-full/issue6/run-manifest.json`: autoritativer Zeiger auf ein vollständiges unveränderliches Bundle unter `runs/`.
- `work/private-full/issue6/full/`, `device/`: Komfortpfade für beide Exportvarianten.
- `work/private-full/issue6/artwork-metadata.jsonl`, `provider-logo-metadata.jsonl`, `assets/logos/`: private Evidenz.
- `work/issue6/`: Implementierungs-, Review-, Mapping- und Live-Prüfberichte; nicht löschen.

Git sichert ausschließlich Code, synthetische Fixtures und aggregierte Berichte.
Ein neuer Auftrag muss die privaten Arbeitsdateien bzw. ein ausdrücklich
autorisiertes privates Backup übernehmen. Ein Git-Clone enthält diese Daten nicht.
Den Pilot und vorhandene Caches niemals durch einen leeren Erstlauf überschreiben.

## TMDB im nächsten Auftrag

Die aktuelle Instanz meldete Revision 1 ohne Secret-/Runtime-Bindung; ein normaler
GET auf `/3/configuration` über den geerbten Proxy, ohne eigenen Authorization-Header,
lieferte 401. Deshalb `TMDB_LIVE_PENDING`, keine Behauptung über ein global fehlendes Secret.

Im neuen Auftrag zuerst die beobachtete Runtime-Bereitschaft prüfen. Der Benutzer
hat das persönliche HTTP-Secret `TMDB_READ_ACCESS_TOKEN` für `api.themoviedb.org`
ausdrücklich autorisiert. Keine Tokenwerte anzeigen, keine Secrets in Dateien kopieren,
keine GitHub-Secrets auslesen und keinen Platzhalter erfinden.
`runtime_tmdb_client()` verarbeitet ausschließlich die exakte geerbte Variable
oder beobachtete bereite Metadaten unter `artwork.tmdb_http_binding`:

```json
{"name":"TMDB_READ_ACCESS_TOKEN","host":"api.themoviedb.org","state":"ready"}
```

`TMDB_RUNTIME_BOUND` bedeutet nur verfügbare Bindung. Erst ein tatsächlicher
erfolgreicher API-Abruf und echte Film-/Serien-/Episoden-/Bildtreffer sind Live-Belege.
Normale Proxy-/TLS-Einstellungen beibehalten; keine Zugangsdaten an CDN-Hosts senden.
Für weitere Live-Anreicherung ein begrenztes API-/Bildbudget nutzen und die
bestehenden Datenbanken wiederverwenden.

## Verbleibende fachliche Blocker

Der zugängliche Providerkatalog ist ein Ausschnitt mit 88 Einträgen; der vollständige
78.516.224-Byte-Katalog überschreitet die 33.554.432-Byte-Downloadgrenze des verbundenen
Dateidienstes. Gesamtzahl unbekannt. Ein autorisierter kleinerer privater Export
oder Filterauszug muss die fehlenden Katalog-/Bestätigungsdaten bereitstellen.
Die Grenze nicht umgehen. Vier Namenskandidaten benötigen belastbare Bestätigung;
keine davon automatisch zuordnen. Ohne bestätigte Feed-Zuordnung bleiben Geräteprofil
und XMLTV-Senderlogos leer. 88 echte lokale Providerlogos sind unabhängig geprüft.

Der Gesamt-EPG enthält sämtliche 185.945 gültigen Eingangsprogramme, aber Quellen
liefern nur 15.–30.11.2026; 24 deklarierte Sender haben keine Programme. Fehlende
Tage/Sendungen nicht erfinden. Artwork-Abdeckung ist begrenzt; nicht versuchte
Identitäten sind im Qualitätsbericht ausgewiesen. JPEG/VP8-Prüfung ist eine
begrenzte Strukturprüfung, keine Pixel- oder Kodi-Renderinggarantie.

## Reproduzierbare Prüfung und Sicherung

```sh
TMPDIR="$PWD/work/private-live/tmp" python3 -m unittest discover -s tests -q
python3 tools/smoke_xmltv.py --output work/offline-smoke
python3 -m xcmate_epg.full_preview --config work/private-full/issue6/config.json \
  --private-root work/private-full/issue6 --authorize-network
```

Der letzte Befehl verwendet das konfigurierte Netzwerkbudget. Für eine reine
Cache-Wiederholung API- und Bildbudget in einer privaten Konfigurationskopie auf 0 setzen.
Der feste Bezugszeitpunkt verhindert zeitabhängige Unterschiede.

Das V2-Backup wird nur privat vorbereitet: Export-GZIPs, Manifeste, Prüfsummen,
Qualitäts-/Zuordnungsberichte, typisierte Artwork-Evidenz und verifizierte Logo-Assets.
Keine Rohquellen, Konfigurationen, SQLite-Caches oder Secretwerte übertragen.
Vor einer neuen Übertragung die konkrete Datei-/Zielfreigabe und Owner-only-Rechte
prüfen, danach alle Dateien vollständig zurücklesen und Größe/SHA256 vergleichen.
Die frühere Upload-Freigabe betraf ausschließlich vier Dateien des ersten Piloten.

Kein Merge, keine Pages, keine öffentlichen XMLTV-/CI-Artefakte, kein Tagesjob,
keine Kodi-Änderung, 0 USD zusätzliche GitHub-Kosten und keine kostenpflichtigen KI-APIs.
