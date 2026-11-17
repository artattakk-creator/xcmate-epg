# Issue #4 – erster echter privater XMLTV-Probelauf

## Ergebnis am 17.11.2026: PRIVATE_EPG_CREATED

Alle drei persönlich konfigurierten Quellen wurden einmal erfolgreich vollständig geladen.
Der erste Export ist ein ausdrücklich begrenzter DACH-Senderausschnitt aus allen drei Quellen,
ohne Artwork. Kein Demo-Fallback. Die Umgebung bestätigt alle sechs beantragten Domains mit
`observations_current=true` und aktiv erzwungener Allowlist.

| Quelle | HTTP | GZIP Byte | Entpackte Byte | Eingang Sender / Programme | Ausschnitt Sender / Programme |
|---|---:|---:|---:|---:|---:|
| s1 | 200 | 3.470.663 | 21.921.488 | 266 / 22.614 | 7 / 849 |
| s2 | 200 | 3.721.683 | 19.372.206 | 144 / 18.503 | 6 / 944 |
| s3 | 200 | 13.301.178 | 100.488.538 | 868 / 144.829 | 10 / 2.135 |

Die zwei EPGShare-Abrufe lieferten mit dem Standard-Python-Client zunächst HTTP 403 und
Cloudflare-Code 1010. Ein als `XCMateEPG/0.1 private-one-shot-preview` identifizierter Client
erhielt jeweils HTTP 200. Keine Browseridentität, Zugangsdaten oder Proxyumgehung.
Der private Importpfad sendet diese Kennung jetzt ebenfalls; Conditional-Header bleiben erhalten.
Nur drei erfolgreiche Payloads wurden gespeichert; Wiederholungsprüfungen verwenden lokale Snapshots.

## Artefakt und Integrität

- `xcmate-epg.xml.gz`: **875.298 Byte**, 23 quellenqualifizierte Sendereinträge, 3.928 Programme.
- Zeitumfang: **16.11.2026 05:00 UTC bis 24.11.2026 01:28 UTC**.
- SHA256: `3edde025d368f3069b541545b688a088ce7942132bf00209cdc460c161833f8e`.
- `manifest.json`: `PRIVATE_LIVE_PREVIEW_DO_NOT_PUBLISH`, identische SHA, Größe und Zählungen.
- `xcmate-epg.xml.gz.sha256`: unabhängig gegen die GZIP-Datei geprüft.
- Versionierte und aktuelle Datei sind bytegleich; GZIP, XML, Referenzen und Zeiten sind valide.
- Alle 3.928 Tupel aus Sender-ID, Beginn, Ende und Titel stimmen mit den echten importierten
  Ausschnitten überein. Keine unbekannten Programmreferenzen, Überlappungen oder Merge-Konflikte.
- Am 17.11.2026 um 17:02 UTC hatten alle 23 Einträge ein aktuelles Programm.
- Wiederholung: drei Snapshots `unchanged`, identische SHA und kein Manifest-Neuschreiben.
- Ausfall aller drei lokalen Inputs: drei echte LastGood-Snapshots, identische SHA; dabei korrekt
  null frisch erfolgreiche Quellen und drei Inputfehler. Danach Inputs wiederhergestellt.
- Keine erneuten Feed-Downloads für Wiederholungs- und LastGood-Prüfungen.

Aggregierte Belege: `docs/issue4-live-attempt.json`. Keine konkreten Feedadressen,
Senderkataloge, Programmtitel oder persönlichen Provider-IDs in dieser Dokumentation.

## Begrenzte Aufbereitung und Eingangsqualität

Die normalen Parser-/Exportgrenzen bleiben bei 32 MiB. Ein separater privater One-shot-Helfer
`work/private-live/subset.py` verarbeitet die Snapshots sequenziell: 64-KiB-Leseblöcke,
maximal 128 MiB entpackte Daten, 1 MiB pro Datensatz, Tiefe 32, eine Million Kanal-/Programmdatensätze
und 16 MiB pro Ausschnitt. Die dritte Quelle wurde tatsächlich bis zum GZIP-Ende gelesen;
der Trailer diente nur als Größenhinweis. Keine unbegrenzte Dekompression und kein vollständiger
XML-Baum. Die drei erzeugten Ausschnitte sind jeweils kleiner als 2 MiB.

Alle Quellen sind UTF-8. s2 enthält ausschließlich `DOCTYPE tv SYSTEM "xmltv.dtd"`.
Der private Helfer erkennt exakt diesen externen Deklarationsfall, lädt die DTD niemals und
serialisiert einen DTD-freien Ausschnitt. Interne DTDs, Entity-Deklarationen und externe
Entity-Auflösung bleiben gesperrt. Dies ist ein privates Aufbereitungsskript für diesen Lauf,
kein allgemeiner Streaming-Produktivpfad. Der normale Parser weist DTDs weiterhin zurück.

In der vollständigen Quelle s1 liegt ein Programm mit ungültiger Dauer außerhalb der ausgewählten
Sender. Im Export sind sämtliche Zeiten und Dauern gültig. Alle drei vollständigen Quellen haben
keine leeren Programmtitel und keine unbekannten Programm-Senderreferenzen.

## Sender, Bilder und Logos

IDs bleiben mit s1/s2/s3 qualifiziert. 23 Einträge sind keine Behauptung 23 unterschiedlicher
kanonischer Sender. Gleiche Namen zwischen Quellen begründen keine Provider-Aliase.
`channel_map_confirmed=0`: die persönliche Provider-Zuordnung bleibt offen.

Der erste Export ist bewusst ohne Artwork. Die ausgewählten Originaldatensätze enthalten
11 Kanal-Icon-Verweise, 3.952 Programm-Icon-Verweise und 2.178 zusätzliche nichtstandardisierte
`image`-Elemente. Sie verwenden zusätzliche, nicht freigegebene Hosts und teils HTTP.
Keine weiteren Hosts wurden freigegeben, keine Bilder heruntergeladen oder auf TMDB umgebogen.
13 Kanal-URLs und 91 Programm-URLs fehlen ebenfalls im Export, damit keine Quellen-/Metadatenadressen
in die XMLTV gelangen. Originalattribute und alle ausgelassenen Elemente bleiben in privaten
Snapshots und quellen-/sender-/zeitbezogenen Auslassungsprotokollen für Herkunft und Attribution
erhalten. Dies ist keine Distributionsfreigabe.

`logos_present=0`, `programme_images_present=0`, `artwork_matched=0`, `artwork_status=PENDING`.
Die Bindung der Eingangsverweise an Quelle und Datensatz wurde geprüft; Bildinhalt und
Erreichbarkeit dieser zusätzlichen Hosts wurden nicht bestätigt.

Weitere Netzwerkprüfungen: TVmaze API HTTP 200, TMDB API ohne Authentifizierung HTTP 401,
TMDB-CDN-Wurzel HTTP 404, TVmaze-CDN-Wurzel HTTP 301. Der letzte Redirect auf einen nicht
freigegebenen Webhost wurde nicht verfolgt. Wurzelantworten sind keine Bilddateiprüfung.
Kein TMDB-Secret gelesen, kopiert oder verwendet; kein authentifizierter Artwork-Aufruf.

## Private Ablage und Transfer

Die Dateien liegen nur unter `/workspace/xcmate-epg/work/private-live/run/output/`:

- `xcmate-epg.xml.gz`
- `manifest.json`
- `xcmate-epg.xml.gz.sha256`
- unveränderliche Kopie unter `versions/<sha256>/`

Verzeichnisse 0700, Dateien 0600; `work/` ist von Git ausgeschlossen. Rohquellen, Konfiguration,
Senderinventare, Auslassungsprotokolle und Prüfhistorie liegen nur im privaten Arbeitsbaum.
Keine dieser Dateien ist im Commit.

Ein privater Drive-Zielordner wurde mit ausschließlich Eigentümerberechtigung und `shared=false`
geprüft. Der Upload wurde jedoch von der automatischen Freigabeprüfung wegen fehlender
expliziter Zustimmung zu diesem Ziel abgelehnt. Kein EPG-Payload wurde übertragen.
Ein funktionierender privater Downloadendpunkt wird deshalb nicht behauptet; kein öffentlicher
Ersatzendpunkt wurde erstellt. Ein neu angelegter privater Zielordner enthält keine EPG-Datei.

Nach autorisiertem privatem Transfer die drei Artefakte und den Qualitätsbericht übernehmen,
keine Konfiguration/Rohquellen. Auf dem Benutzerrechner im selben Verzeichnis prüfen:

```sh
sha256sum -c xcmate-epg.xml.gz.sha256
gzip -t xcmate-epg.xml.gz
```

Für bereits eingerichtetes authentifiziertes SSH/SFTP ist das lokale Quellverzeichnis oben
angegeben; ein nicht eingerichteter Cloud-SSH-Download wird damit nicht behauptet.
Ein privater Drive-Upload erfordert ausdrückliche Zielzustimmung und erneute Prüfung von
Ordner- und Dateiberechtigungen auf ausschließlich Eigentümerzugriff. Keine Linkfreigabe.

Lokale Wiederholung ohne Netzwerk:

```sh
python3 -m xcmate_epg.private_preview \
  --config work/private-live/subset-config.json \
  --private-root work/private-live/run
```

## Kodi und Regressionen

Der zuvor geprüfte Kodi-Stand `6e34671456495b2ce514721402b05236789382c2` unterstützt im EPG-Manager
**Quellen hinzufügen → XMLTV-Datei**, anschließend **Jetzt aktualisieren** und manuelle Zuordnung.
Die ausgelieferte Version 0.1.29.97 wurde nicht auf Android verifiziert; sichtbare BINGIE-Bilder
werden nicht behauptet. Keine Kodi-Datei oder Provider-Zuordnung geändert.

47 lokale Tests bestanden; die ursprünglichen 34 Tests sind unverändert. Der synthetische
Bootstrap-Smoke bestand separat und zählt nicht als Live-Nachweis. Unabhängige Codeprüfung:
keine handlungsrelevanten Befunde. Die Git-Ignore-Schutzprüfung bindet jetzt an das tatsächlich
erkannte Repository, damit ein ungültiger verschachtelter `.git`-Marker keine Ignore-Regel des
übergeordneten Repositories erbt. In dieser Umgebung enthalten `/tmp` und `/workspace` geschützte
`.git`-Marker; die Tests verwenden deshalb ein ignoriertes privates Temp-Verzeichnis:

```sh
mkdir -p work/private-live/tmp
TMPDIR="$PWD/work/private-live/tmp" python3 -m unittest discover -s tests -q
```

Kein Merge, kein Pages-Upload, keine öffentlichen Datenartefakte, kein täglicher Job,
kein Live-Datenlauf in Actions, kein Secret-Export und keine kostenpflichtigen Dienste.
