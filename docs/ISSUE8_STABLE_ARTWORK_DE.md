# V2-Artwork-Auswahl für Issue #8

## Ausführung und Bestandsschutz

Der Einstieg ist `python3 -m xcmate_epg.full_preview`. `run_full` lädt die
konfigurierten Snapshots in `StreamStore`, ruft in `_render` pro Sendung
`ArtworkEnricher` auf und veröffentlicht beide XMLTV-Ansichten und die
Sidecars als privates, geprüftes Run-Bundle. Der alte `private_preview`-Pfad
spielt dabei keine Rolle.

Für die neue Auswahllogik im vorhandenen privaten Konfigurationsblock
`artwork` ausdrücklich `"stable_selection": true` setzen. Die Einstellung
ermöglicht einen kontrollierten Übergang für bestehende V2-Verbraucher.
Für einen späteren Vergleichslauf einen separaten privaten Ausgabeordner
verwenden. Die bestehenden Provider-, Netz- und Bildfreigaben bleiben
erforderlich. Insbesondere ist XMLTV-Artwork über `allow_feed_images`
freizugeben, und TVMaze-Backdrops benötigen die vorhandene Galerieoption.
Die aktuelle Bildprüfung akzeptiert die bereits freigegebenen CDN-Hosts.
Die Auswahl erzeugt keine neuen Rechte zur Weitergabe.

## Entscheidung und Wiederholung

- Der Eventschlüssel ist SHA-256 über quellenqualifizierten Kanal, UTC-Epochenzeit
  von Start und Ende, normalisierten Originaltitel und Untertitel, Quelljahr
  und sortierte Episodenkennungen des Feeds. Beschreibungen, hinzugefügte
  Provider-IDs und Bild-URLs beeinflussen ihn nicht.
- Pro Anzeigerolle entscheidet zuerst die Quelle TMDB, TVMaze, XMLTV,
  bestätigtes lokales Senderlogo, dann das mitgelieferte Standardmotiv.
  Innerhalb einer Quelle bevorzugt der Hero Backdrop, Episodenstill, Poster.
  Die Posteransicht bevorzugt Poster, Episodenstill, Backdrop. Die
  episodenzentrierte Ansicht bevorzugt Episodenstill, Backdrop, Poster.
  `selected_role` bewahrt den tatsächlichen Bildtyp. Ein Poster im Hero
  braucht eine passende Einbettung ohne Stretching.
- TMDB-Identität und Bildverifikation sind getrennt. Fehlen verifizierte
  TMDB-Bilder für native Rollen, kann TVMaze passende Serienbilder liefern.
  Für Filme läuft die Kette bei fehlendem TMDB-Bild über XMLTV und die lokalen
  Stufen weiter. XMLTV-Originalbilder bleiben im Quellsnapshot. Rollen für
  untypisierte Original-Icons werden erst nach Bildprüfung anhand der
  Dimensionen abgeleitet. Ein breites Original-Icon ist kein Episodennachweis.
- Der private SQLite-Spool speichert die Entscheidung komprimiert und getrennt
  vom TTL-Cache. Ein gewöhnlicher Replay prüft für festgelegte Events keine
  Bild-URLs erneut. Die bestehenden Metadaten-Caches, Request-/Bildbudgets und
  Backoffs gelten weiter. Die Auswahl speichert keine CDN-Bildkörper dauerhaft.
  Entscheidungen von Sendungen, die länger als sieben Tage beendet sind,
  werden aus dem Entscheidungsindex entfernt. Alte Run-Bundles werden von
  dieser Änderung nicht bereinigt.
- Ein Rollback beim privaten Export rollt die neuen Datenbankentscheidungen
  ebenfalls zurück. Bei unverändertem Event behält jede Entscheidung ihre
  Generation. Eine neue Sendung erhält eine neue, unabhängige Entscheidung.
- `ARTWORK_SELECTED` protokolliert bei aktivierter INFO-Stufe Eventhash,
  Anzeigerolle, Quelle, Grund und Generation. Der Qualitätsbericht zählt
  Entscheidungen getrennt nach Provider. Logo und Standardmotiv zählen als
  Grafik-Fallback, nicht zur Sendungsbildquote.

## Manuelle Zuordnungen

Alle Einträge liegen in der privaten Konfiguration unter `artwork` und
verwenden den `event_key` aus dem Sidecar. Beispiele der Struktur mit
synthetischen Schlüsseln und Werten

```json
{
  "manual_titles": {"EVENT_HASH": {"title": "Bestätigter Suchname", "evidence": "manuell geprüft"}},
  "manual_images": {"EVENT_HASH": {"backdrop": {
    "role": "backdrop", "url": "https://image.tmdb.org/fixture.png",
    "rights_reviewed": true, "evidence": "manuell geprüft"
  }}},
  "confirmed_invalid_images": {"EVENT_HASH": {"poster": {
    "selected_url_sha256": "SHA256_DER_BISHERIGEN_URL", "evidence": "Datei nachweislich defekt"
  }}}
}
```

Der manuelle Suchname ändert den angezeigten EPG-Titel und den Eventschlüssel
nicht. Der Provider muss den Suchnamen über den normalen konservativen
Matchingpfad bestätigen. Manuelle Bildreferenzen benötigen weiterhin eine
sichere URL und ein geprüftes Bild. Eine bestätigte Defektmeldung gilt nur
für die konkrete bisherige URL und erlaubt einmaligen automatischen Ersatz.
Ein gewöhnlicher Timeout oder ein leerer Cache löst keinen Defektwechsel aus.

## Gemeinsamer Kodi-Vertrag

Das zum `run-manifest.json` gehörende `artwork-selection.jsonl` hat
`schema_version: 1`. Jede Zeile enthält einen Eventschlüssel, quellenqualifizierten
Kanal, UTC-Zeiten, bestätigte Gerätekanal-Aliase und die `decisions` für
`backdrop`, `poster` und `episode_still`. Entscheidungen tragen Quelle,
tatsächliche Bildrolle, Referenz, Confidence, Grund und stabile Generation.
Remoteentscheidungen liefern auch Dimensionen und verfügbare Attribution.
`offline` und `standard_image` enthalten bundle-relative Pfade für den lokalen
Fallback. Bei fehlendem Logo steht das neutrale PNG zur Verfügung.

BINGIE-Home und Detail-Hintergrund verwenden `backdrop`, Detailposter
`poster`, Mini-EPG und große Programmzeitschrift die zur jeweiligen Ansicht
passende Rolle. Ansichten lesen dieselbe Entscheidung über den Eventschlüssel.
Beim Laden und bei temporär nicht auflösbarer Remote-Referenz zeigt der Client
zuerst seine letzte gültige Textur oder den lokalen Fallback. Die gespeicherte
Providerentscheidung bleibt gleich. Das PNG ist knapp 1 KiB groß und soll
für den Offline-Betrieb mit der Kodi-Integration lokal verfügbar sein.
Im Auswahlmodus enthält `artwork-metadata.jsonl` die eingefrorenen Remote-
Nachweise je Anzeigerolle. Das Entscheidungs-Sidecar enthält zusätzlich die
lokalen Stufen. XMLTV enthält die deduplizierten gewählten HTTPS-Referenzen für kompatible
Verbraucher. Die Rollenverträge stehen im Sidecar. Die optionale Integration
muss bei deaktivierter Erweiterung den vorhandenen Provider-EPG weiter anzeigen.

## Abnahmegrenzen

- Synthetisch: Quellen- und Rollenreihenfolge, TMDB-Identität ohne Bild mit
  TVMaze-Fallback, Cache-Verlust, neue Sendung, manuelles Bild und manuelle
  Titelzuordnung, bestätigter Defekt, bestätigtes Senderlogo, lokale Grafik
  und Rollback. Der Testtransport liefert ausschließlich Fixture-Antworten.
- **Real-Daten-Abnahme offen.** Der verifizierte Checkpoint stammt aus der
  alten Runtime. Erst nach freigegebenem privatem Transfer mit SHA- und
  Readback-Nachweis einen schreibgeschützten Eingangsbestand und ein separates
  Ausgabeziel für den Vergleich verwenden. Die Referenzproben aus Issue #8
  ersetzen diesen Lauf nicht.
- **Kodi-/Android-Abnahme offen.** Dieser Backend-Branch liefert den
  Clientvertrag. Ein Kodi-Verbraucher und dessen lokaler Textur-Cache sind
  separat zu integrieren und auf Android/Kodi 21.3 mit wenig Speicher,
  Neustart, Offline-Wechsel, Sendergruppenwechsel und BINGIE-Home,
  Mini-/Groß-EPG und Details in 720p und 1080p am sichtbaren Bild zu prüfen.
  Für JPEG/WebP liefert die bestehende Backendprüfung Strukturbelege.
  Die vollständige Dekodierung und sichtbare Darstellung bleiben Gerätetests.
