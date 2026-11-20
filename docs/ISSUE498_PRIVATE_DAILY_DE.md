# Privater XCMate-Tageslauf und XMLTV-Liefervertrag

Issue [Kodi #498](https://github.com/artattakk-creator/kodi-noodleprivate/issues/498) führt den Delta-Kern aus Backend #3 und den Streaming-/Artwork-Export aus #7/#9 zusammen. Der Arbeitsstand für den produktiven GitHub-Kanal lautet **PRIVATE_PENDING / PRODUCTION_DATA_NOT_APPROVED**. Backend [#12](https://github.com/artattakk-creator/xcmate-epg/issues/12) und Kodi [#499](https://github.com/artattakk-creator/kodi-noodleprivate/issues/499) führen den XMLTV-Standard für verifizierte Bilder im selben Archiv ein. Die Befehle unten sind lokale Entwicklungseinstiege. Die separate Freigabe für Rechte, Restore, Geräte, Verteilung und Betrieb bleibt erforderlich.

## Persistenter Tageslauf

`python -m xcmate_epg.daily --config "$XCMATE_PRIVATE_CONFIG" --source-rights "$XCMATE_SOURCE_RIGHTS" --private-root "$XCMATE_PRIVATE_STATE"`

Die Konfiguration entspricht `config/full-preview.example.json` und enthält drei unabhängige, vollständige Quellen. Die separate private Freigabedatei enthält je Quell-ID ein Objekt mit `input_sha256` und `evidence`. Der Hash gehört zur exakten konfigurierten URL oder zum absoluten lokalen Eingabepfad. Der Nachweis im Feld `evidence` verweist auf die geprüfte Erlaubnis zum vorgesehenen Datengebrauch. Für einen später genehmigten HTTP-Lauf ist zusätzlich `--authorize-network` erforderlich.

Der Workspace liegt auf persistentem, ausschließlich dem Eigentümer zugänglichem Speicher. Dazu gehören `daily-state.json`, die Snapshots samt ETag und Last-Modified, SQLite-Spool, Bildcache, validierte universelle Generationen und die getrennten Geräteprofile. Der Prozess serialisiert Läufe. Nach einem erfolgreich validierten Vollimport wird der nächste Versuch frühestens nach 24 Stunden fällig. Bei Fehlern steigt die Wartezeit ab fünf Minuten bis auf sechs Stunden. Ein kaputter oder ausgefallener Eingang erhält LastGood. Das Qualitätsprotokoll nennt den tatsächlichen Zeitraum, Quellabdeckung, Fingerprints, Dubletten, Konflikte, Mapping und Bildnachweise.

HTTP 304 und identische komprimierte SHA256-Werte umgehen das erneute Parsen. Geänderte Quellen werden vollständig und streamend validiert. Der Tageslauf reicht bereits vollständig angereicherte, unveränderte Events aus dem persistenten Cache durch. Offene Bildfälle bleiben für spätere verifizierte Treffer zugänglich. Die stabile Bildentscheidung berücksichtigt weiterhin Rechteänderungen, belegte Defekte und bestätigte manuelle Zuordnungen. Kanal- und Zeitachsen-Fingerprints sind zusätzlich zum Snapshot-SHA im privaten Bericht sichtbar.

Mit `--offline-replay` verarbeitet der Tageslauf lokale Eingaben oder die zuletzt validierten URL-Snapshots. Er prüft sie unter dem zuletzt validierten Zeitfenster erneut, soweit vorhanden. Onlinezugriff, neuer Lieferschreibvorgang und neuer Fälligkeitstermin entfallen. Nach einer partiellen Quellenaktualisierung kann das Replay bereits einen neueren gültigen Quellsnapshot neben älteren Snapshots sehen. Die vorherige atomar veröffentlichte lokale Generation bleibt dabei die Referenz.

## Zusätzliches Geräteprofil

`python -m xcmate_epg.profiles --private-root "$XCMATE_PRIVATE_STATE" --profile-id "$XCMATE_PROFILE" --mapping "$XCMATE_PROFILE_MAPPING"`

Der Befehl liest die unveränderliche, über `run-manifest.json` und SHA verifizierte Universaldatei. Die private Mapping-Eingabe verwendet die bestehende Struktur mit `catalog` und `confirmations`. Ausgabe, Quoten, Kandidaten und Mapping-Nachweise liegen unter `profiles/<profil>/`. Nur ausdrücklich bestätigte Zuordnungen gelangen in die Profil-XMLTV. Der universelle Download und dessen Bilddatei enthalten die originalen qualifizierten Quell-IDs.

## Lokaler Schema-3-Liefertest

`XCMATE_XMLTV_DTD="$XCMATE_LOCAL_DTD" python -m xcmate_epg.daily --private-root "$XCMATE_PRIVATE_STATE" --prepare-local-delivery "$XCMATE_DELIVERY_APPROVAL"`

Die private Freigabedatei bindet `generation` aus `run-manifest.json` an belegte `gates` für Restore, Quellen, Mapping, Artwork, Delivery, Client, Rechte und Freigabe. Ihre Schlüssel sind `restore`, `sources`, `mapping`, `artwork`, `delivery`, `client`, `rights` und `release`. Lokale Freigabebelege aus synthetischen Tests gelten nur für deren Testgeneration. Fehlende oder veraltete Freigabe ergibt `PRIVATE_PENDING / PRODUCTION_DATA_NOT_APPROVED`. Ein fehlgeschlagener Prüfstand erhält den vorherigen Lieferschreibstand.

Das private Bare-Git-Repository `delivery.git` erhält im sichtbaren Schema-3-Commit genau diese beiden Dateien.

| Datei | Inhalt |
| --- | --- |
| `manifest.json` | `schema_version=3`, `status`, `file`, `bytes`, `sha256`, `revision` mit demselben SHA256, `channel_count`, `programme_count`, `source_count`, `generated_at_utc`, `payload_ref`, `image_counts`, `window` |
| `xcmate-epg.xml.gz` | Universelle quellenqualifizierte XMLTV-Kanäle, Ereignisse und ihre verifizierten HTTPS-Bildverweise |

`window.start_utc` und `window.stop_utc` geben den frühesten Start und den spätesten Stop der enthaltenen Sendeplätze in ISO-UTC an. `image_counts` zählt die direkten XMLTV-Elemente nach `poster`, `backdrop`, `still`, `person`, `character`, `programme_icon` und `channel_logo`. Fehlende Rollen haben den Wert `0`.

Der Export platziert die typisierten `programme/image`-Elemente nach `review`. `episode_still` wird `still`, `fanart` wird `backdrop`. Jede Auswahl behält ihre Bildart und kann als Fallback in anderen Anzeigerollen dienen. `system` kennzeichnet `tmdb`, `tvmaze` oder `xmltv`. `size` und `orient` folgen den verifizierten Abmessungen und der DTD. Ein `programme/icon` dient älteren XMLTV-Clients als Sendungsbild, `channel/icon` enthält ein bestätigtes Senderlogo. Das Archiv ist auf 48 MiB komprimiert und 768 MiB expandiert begrenzt. Pro Ereignis sind bis zu 16 direkte typisierte Bildreferenzen erlaubt, jeweils bis zu 2048 Zeichen und mit sicherer HTTPS-URL.

Privater Nachweis und profilbezogene Bildauswahl liegen im Arbeitsstand. `artwork-metadata.jsonl` enthält die geprüften Referenzen der Universaldatei mit Hash, Abmessungen und Attribution. `artwork-selection.jsonl` enthält die stabilen Endgeräte-Entscheidungen und manuelle Zuordnungen. `artwork-extra-private.jsonl` bewahrt verfügbare geprüfte Spezialrollen wie Clearlogo, Staffelposter und Thumb auf. Die Profile können autorisierte manuelle Zuordnungen enthalten. Gleichartige öffentliche Bild-URLs bleiben im universellen XMLTV an ihre tatsächlich geprüfte Rolle und ihr Ereignis gebunden.

Vor `PRIVATE_READY` validiert libxml2 den gesamten GZIP mit der unveränderten offiziellen XMLTV-DTD aus [XMLTV/xmltv@f336016](https://github.com/XMLTV/xmltv/blob/f33601698cf1f1c7a67030b9ec2d3ad565421b5a/xmltv.dtd). SHA256 der lokalen Grammatik ist `a1c88c4b840a6a80f8c5b6e6b850a864a0340148a01e63d6f59326c846f1d5c2`. Dazu wird `lxml==6.1.1` benötigt. Der Validator lädt nur diese lokale, separat bereitgestellte Grammatik und löst beim Test keine externen Entitäten auf. Expat zählt Kanäle, Zeitfenster, Programme und Bildrollen separat und gleicht jede XMLTV-Bild-URL mit dem zum Run gehörenden privaten Prüfnachweis ab.

Zuerst entsteht der unveränderliche Payload-Commit mit der einen XMLTV-Datei. Seine 40-stellige Git-SHA ist `payload_ref`. Nach lokalem Git-Readback und Größen-/SHA-Abgleich entsteht der Manifestcommit. Ein atomarer Vergleich mit dem bisherigen Ref schaltet den Commit sichtbar. Der Kodi-Reader liest das Manifest per ETag und fordert XMLTV über den festen GitHub-Contents-Dateipfad mit `?ref=<payload_ref>` an. Gleiche XMLTV-Bytes behalten dieselbe Revision. Bestehende Schemas 1 und 2 bleiben historische Stände. Ein geprüftes Schema 3 wird als vollständige Generation umgeschaltet, während vorherige Commit-Objekte für Rollback verfügbar bleiben.

Der lokale Git-Test prüft den Transportvertrag. Für Produktivbetrieb sind die privaten GitHub-Lesezugänge je autorisiertem Gerät, Rechtelage, authentifizierter Remote-Readback, unautorisierter 404, Wiederherstellung auf geschütztem Speichervolume, Größe und Retention der langlebigen Zustände sowie Geräteabnahmen separat nachzuweisen. Die Vorlage unter `docs/workflows/production.yml.example` bleibt inaktiv.

## Offline-Prüfungen

- `XCMATE_XMLTV_DTD="$XCMATE_LOCAL_DTD" python -m unittest discover -s tests -v`
- `python tools/smoke_xmltv.py --output "$XCMATE_SYNTHETIC_SMOKE"`
- Synthetische Regressionen prüfen den gepinnten DTD-Lauf, 185.001 eindeutige Programme, standardisierte Mehrfachbilder samt Legacy-Icons und Senderlogos, die getrennten Rollen- und Zeitfensterzähler, private Overrides, Rechte vor I/O, drei Quellen, HTTP 304, Einzelfeed-Delta, HTTP 401/403/429, LastGood und Ableitung eines zusätzlichen Profils aus dem gespeicherten Universalstand.

## Wiederanlauf und privater Transport

Der Runner-Zustand benötigt ein separat bereitgestelltes, dauerhaftes, nur dem Betreiber zugängliches Dateisystem. Die Wiederherstellung erfolgt vor dem Tageslauf in ein neues Arbeitsverzeichnis. Der Speicher enthält SQLite, den Tageslauf-Termin, ausgewählte Quell-Snapshots mit Validatoren, das aktuelle verifizierte XMLTV-Bundle und vorhandene private Profile. Quellfreigaben, Secrets und die private Laufkonfiguration werden separat bereitgestellt.

```sh
python -m xcmate_epg.runner_state --store "$XCMATE_STATE_STORE" restore "$XCMATE_PRIVATE_STATE"
python -m xcmate_epg.runner_state --store "$XCMATE_STATE_STORE" save "$XCMATE_PRIVATE_STATE"
python -m xcmate_epg.runner_state --store "$XCMATE_STATE_STORE" restore "$XCMATE_RECOVERY_TEST" --previous
```

Vor einem Zeigerwechsel prüft das Modul Datei-SHA256, beide SQLite-Datenbanken und das unveränderliche Run-Bundle. Der Speicher hält den aktuellen und den vorherigen bestätigten Snapshot vor. Nach bestandenem Auslieferungs-Readback und Wiederanlauftest entfernt `runner_state --store "$XCMATE_STATE_STORE" prune` ausschließlich abgelaufene Snapshots außerhalb beider Zeiger. Für ältere verwaltete Snapshots gelten sieben Tage. Der erste bestätigte Snapshot kann durch diesen Aufruf nicht gelöscht werden.

Der manuelle GitHub-Smoke verwendet die getrennten Secrets `XCMATE_EPG_PRIVATE_WRITE_TOKEN` und `XCMATE_EPG_PRIVATE_READ_TOKEN`. Er prüft die Privatheit, HTTP 404 ohne Authentifizierung und den exakten Readback einer synthetischen Testdatei am geschriebenen Commit. Ein erfolgreicher Smoke ersetzt den privaten Produktions-Readback nicht.

`python -m xcmate_epg.github_delivery --private-root "$XCMATE_PRIVATE_STATE" --approval "$XCMATE_DELIVERY_APPROVAL"` verwendet die bestehende generationsgebundene lokale Freigabe und die gepinnte DTD. Das Tool legt die XMLTV-Datei als separaten, elternlosen Git-Commit ab und prüft ihre Bytes über die Contents-API mit dem READ-Secret. Erst daraufhin aktualisiert ein Fast-forward das Manifest auf dem privaten `main`. Die übrigen Repository-Dateien bleiben erhalten. Die Nutzdaten-Refs sind für kontrollierte Retention getrennt vom Manifestverlauf. Ihre automatische Rotation und der provisionierte persistente Runner-Speicher sind Voraussetzungen für einen unbeaufsichtigten Tagesbetrieb.

### Rollierender Drive-Live-Spiegel

Der Produktionsaufruf von `github_delivery` nimmt die aus GitHub bestätigte Produktionsgeneration auch in den privaten Drive-Spiegel auf. Ein gemeinsamer Lock auf dem dauerhaften Runner-Speicher serialisiert GitHub-Aktivierung und Drive-Rotation. Die Produktions-Workflow-Vorlage verwendet denselben Pfad. Die vorhandene Spiegeldatei bleibt bei Transportfehlern unter ihrem bisherigen Manifestzeiger abrufbar. Ein erfolgreicher Drive-Readback prüft die exakten Bytes, SHA256, GZIP und XMLTV. Nach bestätigtem neuem Zeiger verschiebt der Ablauf die alte verwaltete Kopie in den Drive-Papierkorb. Unveränderte SHA-Werte lösen keinen Upload aus. Verwaltete temporäre Uploads sind nach 48 Stunden für die Bereinigung berechtigt. Das private Audit `drive-mirror-audit.json` wird im Runner-Snapshot gesichert und enthält die Dateikennungen. Standardausgaben enthalten lediglich Status, SHA und gegebenenfalls einen bereinigten Fehlercode.

Der Runner erhält die Ordnerbindung über `XCMATE_DRIVE_MIRROR_FOLDER_ID` und einen dedizierten OAuth-Zugang über `XCMATE_DRIVE_CLIENT_ID`, `XCMATE_DRIVE_CLIENT_SECRET` und `XCMATE_DRIVE_REFRESH_TOKEN` aus der geschützten Actions-Umgebung. Die Autorisierung verwendet den Scope `drive.file` und den explizit für die App zugänglich gemachten Zielordner im My Drive des Eigentümers. Der Code verlangt für alle beteiligten Drive-Objekte dieselben Owner-only-Berechtigungen. Kodi verwendet weiterhin den GitHub-Abruf. Die Workflow-Vorlage bleibt bis zur Provisionierung des geschützten Runners und zum realen Test der Auslieferung inaktiv.
