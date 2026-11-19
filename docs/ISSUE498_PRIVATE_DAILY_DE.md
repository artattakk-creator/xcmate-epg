# Privater XCMate-Tageslauf und V2-Liefervertrag

Issue [Kodi #498](https://github.com/artattakk-creator/kodi-noodleprivate/issues/498) führt den Delta-Kern aus Backend #3 und den Streaming-/Artwork-Export aus #7/#9 zusammen. Der Arbeitsstand für den produktiven GitHub-Kanal lautet **PRIVATE_PENDING / PRODUCTION_DATA_NOT_APPROVED**. Die Befehle unten sind lokale Entwicklungseinstiege. Die separate Freigabe für Rechte, Restore, Geräte, Verteilung und Betrieb bleibt erforderlich.

## Persistenter Tageslauf

`python -m xcmate_epg.daily --config "$XCMATE_PRIVATE_CONFIG" --source-rights "$XCMATE_SOURCE_RIGHTS" --private-root "$XCMATE_PRIVATE_STATE"`

Die Konfiguration entspricht `config/full-preview.example.json` und enthält drei unabhängige, vollständige Quellen. Die separate private Freigabedatei enthält je Quell-ID ein Objekt mit `input_sha256` und `evidence`. Der Hash gehört zur exakten konfigurierten URL oder zum absoluten lokalen Eingabepfad. Der Nachweis im Feld `evidence` verweist auf die geprüfte Erlaubnis zum vorgesehenen Datengebrauch. Für einen später genehmigten HTTP-Lauf ist zusätzlich `--authorize-network` erforderlich.

Der Workspace liegt auf persistentem, ausschließlich dem Eigentümer zugänglichem Speicher. Dazu gehören `daily-state.json`, die Snapshots samt ETag und Last-Modified, SQLite-Spool, Bildcache, validierte universelle Generationen und die getrennten Geräteprofile. Der Prozess serialisiert Läufe. Nach einem erfolgreich validierten Vollimport wird der nächste Versuch frühestens nach 24 Stunden fällig. Bei Fehlern steigt die Wartezeit ab fünf Minuten bis auf sechs Stunden. Ein kaputter oder ausgefallener Eingang erhält LastGood. Das Qualitätsprotokoll nennt den tatsächlichen Zeitraum, Quellabdeckung, Fingerprints, Dubletten, Konflikte, Mapping und Bildnachweise.

HTTP 304 und identische komprimierte SHA256-Werte umgehen das erneute Parsen. Geänderte Quellen werden vollständig und streamend validiert. Der Tageslauf reicht bereits vollständig angereicherte, unveränderte Events aus dem persistenten Cache durch. Offene Bildfälle bleiben für spätere verifizierte Treffer zugänglich. Die stabile Bildentscheidung berücksichtigt weiterhin Rechteänderungen, belegte Defekte und bestätigte manuelle Zuordnungen. Kanal- und Zeitachsen-Fingerprints sind zusätzlich zum Snapshot-SHA im privaten Bericht sichtbar.

Mit `--offline-replay` verarbeitet der Tageslauf lokale Eingaben oder die zuletzt validierten URL-Snapshots. Er prüft sie unter dem zuletzt validierten Zeitfenster erneut, soweit vorhanden. Onlinezugriff, neuer Lieferschreibvorgang und neuer Fälligkeitstermin entfallen. Nach einer partiellen Quellenaktualisierung kann das Replay bereits einen neueren gültigen Quellsnapshot neben älteren Snapshots sehen. Die vorherige atomar veröffentlichte lokale Generation bleibt dabei die Referenz.

## Zusätzliches Geräteprofil

`python -m xcmate_epg.profiles --private-root "$XCMATE_PRIVATE_STATE" --profile-id "$XCMATE_PROFILE" --mapping "$XCMATE_PROFILE_MAPPING"`

Der Befehl liest die unveränderliche, über `run-manifest.json` und SHA verifizierte Universaldatei. Die private Mapping-Eingabe verwendet die bestehende Struktur mit `catalog` und `confirmations`. Ausgabe, Quoten, Kandidaten und Mapping-Nachweise liegen unter `profiles/<profil>/`. Nur ausdrücklich bestätigte Zuordnungen gelangen in die Profil-XMLTV. Der universelle Download und dessen Bilddatei enthalten die originalen qualifizierten Quell-IDs.

## Lokaler V2-Liefertest

`python -m xcmate_epg.daily --private-root "$XCMATE_PRIVATE_STATE" --prepare-local-delivery "$XCMATE_DELIVERY_APPROVAL"`

Die private Freigabedatei bindet `generation` aus `run-manifest.json` an belegte `gates` für Restore, Quellen, Mapping, Artwork, Delivery, Client, Rechte und Freigabe. Ihre Schlüssel sind `restore`, `sources`, `mapping`, `artwork`, `delivery`, `client`, `rights` und `release`. Lokale Freigabebelege aus synthetischen Tests gelten nur für deren Testgeneration. Fehlende oder veraltete Freigabe ergibt `PRIVATE_PENDING / PRODUCTION_DATA_NOT_APPROVED`. Bei defekten Dateien oder unvollständiger geprüfter Qualität bleibt der vorherige Lieferschreibstand erhalten.

Der Test erzeugt im privaten Workspace das lokale Bare-Git-Repository `delivery.git`. Dort liegen genau diese drei Dateien im sichtbaren Commit.

| Datei | Inhalt |
| --- | --- |
| `manifest.json` | `schema_version=2`, `status`, `file`, `bytes`, `sha256`, `channel_count`, `programme_count`, `source_count`, `generated_at_utc`, `payload_ref`, `artwork` |
| `xcmate-epg.xml.gz` | Universelle qualifizierte XMLTV-Ereignisse und ihre verifizierten Bildverweise |
| `xcmate-artwork.jsonl.gz` | Verifizierte typisierte HTTPS-Referenzen mit Quell-/Senderkennung, Rolle, Nachweis und Attribution. Programmrollen haben Start/Ende, Senderlogos den Scope `channel`. |

Artwork-Manifestfelder sind `file`, `bytes`, `sha256` und `records`. Beide Payloads sind auf je 48 MiB komprimiert begrenzt. Expandiert gelten 768 MiB XMLTV und 192 MiB JSONL. Der Lieferadapter prüft beide Richtungen der Verbindung zwischen XMLTV-Bild und Artwork-Referenz. Anbieterprofile, Mappingberichte, lokale Logos, Rohdaten und Credentials gehören ausschließlich in den separaten Workspace.

Zuerst entsteht der unveränderliche Payload-Commit. Seine 40-stellige Git-SHA ist `payload_ref`. Nach lokalem Readback und Größen-/SHA-Abgleich entsteht der Manifestcommit. Ein atomarer Vergleich mit dem bisherigen Ref schaltet den neuen Commit sichtbar. Kodi liest das Manifest per ETag. Beide Payloads werden über ihre festen GitHub-Contents-Dateipfade mit `?ref=<payload_ref>` aus derselben Generation gelesen. Bei unveränderten Payloads bleibt der Git-Ref gleich.

Das lokale Git-Readback prüft den Transportvertrag. Für Produktivbetrieb sind die privaten GitHub-Lesezugänge je autorisiertem Gerät, Rechtelage, authentifizierter Remote-Readback, unautorisierter 404, Wiederherstellung auf geschütztem Speichervolume, Größe/Retention der langlebigen Zustände und Geräteabnahmen separat nachzuweisen. Die Vorlage unter `docs/workflows/production.yml.example` bleibt inaktiv.

## Offline-Prüfungen

- `python -m unittest discover -s tests -v`
- `python tools/smoke_xmltv.py --output "$XCMATE_SYNTHETIC_SMOKE"`
- Synthetische Regressionen prüfen drei Quellen, 304 und Einzelfeed-Delta, Rechte vor I/O, 429/Backoff, LastGood, Upgrade vorläufiger Bilder, verifizierte Logodimensionen, SHA-/Bildbindungsfehler sowie Ableitung eines zusätzlichen Profils aus dem gespeicherten Universalstand.
