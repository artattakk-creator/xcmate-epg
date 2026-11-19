# XCMate EPG Agent – Entwicklungsstand

## Reproduzierbare Prüfung

Python 3.11 oder neuer, keine zusätzlichen Pakete:

```sh
python -m unittest discover -s tests -v
python tools/smoke_xmltv.py --output work/bootstrap-smoke
python -m xcmate_epg --state work/private/state.sqlite --output work/demo
```

Ohne Konfiguration verarbeitet die CLI ausschließlich eine synthetische Sendung.
Ausgaben bleiben als DEVELOPMENT_NOT_FOR_PUBLICATION gekennzeichnet.
`--config` akzeptiert lokale JSON-Konfiguration mit `sources` (`id`, `path`, `priority`)
und `aliases` (`source`, `xmltv_id`, `canonical_id`, `confirmed: true`).
Kleinere Prioritätswerte gewinnen. Reihenfolge löst Gleichstände.
Private Konfiguration und State niemals committen. Netzwerkquellen sind in der CLI gesperrt;
der separat getestete HTTPS-Adapter ist noch kein aktivierter Quellenbetrieb.

## Phase A – Quellen und Zustand

SQLite hält pro Quelle validierten Rohsnapshot, normalisiertes XML, Ereignis-Fingerprints, SHA256 und HTTP-Validatoren. 304 und identische Payload-Hashes überspringen die Quellvalidierung; Alias-Änderungen erzwingen eine neue Normalisierung. Ereigniszeilen bleiben bei identischem Fingerprint erhalten.
Erst nach erfolgreicher vollständiger Validierung werden Snapshot und Validatoren ersetzt.
304 ohne Snapshot ist ein Fehler. Bis zu drei Versuche bei Timeout/429/5xx,
mit begrenztem Backoff. HTTP-Redirects sind gesperrt, Requests haben Timeout und Größenlimit.
Ausfälle verwenden quellenspezifisches LastGood. Ein vollständig fehlender Datenbestand
kann keinen neuen EPG erzeugen. Kein Abruf echter Quellen wurde durchgeführt. `minimum_sources` (Standard 1) definiert die minimale Anzahl verwendbarer Quellen einschließlich gültiger LastGood-Snapshots; darunter bleibt das alte Manifest erhalten.

Actions-Runner verlieren lokalen State. Die vorbereitete CI demonstriert Wiederherstellung
über Actions Cache ausschließlich mit synthetischen Daten. Cache ist löschbar (TTL/Quota),
kein garantiert dauerhaftes Archiv. Cache-Miss führt zum Neuaufbau; niemals 304 ohne Body nutzen.
Für echten Betrieb: geschütztes, vom öffentlichen Backend getrenntes privates State-Repository
mit Git/LFS-freien komprimierten Snapshots und begrenzter Aufbewahrung prüfen. Das kann
innerhalb bestehender GitHub-Free-Kontingente betrieben werden, benötigt aber Größenprüfung
und eigene Rechte. Keine privaten Rohdaten in öffentliche Branches, PR-lesbare Caches oder
öffentliche Actions-Artefakte schreiben. Eine solche private Ablage ist hier weder eingerichtet
noch behauptet. Diese Persistenzfreigabe bleibt eine Produktionsvoraussetzung.

## Phase B – XMLTV und Sender

UTF-8 XMLTV mit expliziten Zeitzonen; DTD/Entities, kaputtes GZIP, fehlende Programme,
Duplikate und ungültige Senderreferenzen werden abgewiesen. Komprimierte und entpackte
Daten sind auf jeweils 32 MiB begrenzt. Quellenqualifizierung verhindert Kollisionen.
Sendernamen-Normalisierung dient nur als Hinweis; keine automatischen Provider-Matches.
Nur bestätigte Aliase führen IDs zusammen. HD/SD-Normalisierung erzeugt keine Zuordnung.
XMLTV-Programme, Beschreibungen, Kategorien und Episodenfelder bleiben erhalten.

## Phase C – Metadaten und Artwork

TMDB-Bearer-Client ist separat injizierbar; Tests verwenden ausschließlich lokale Antworten.
Titel und Jahr müssen exakt plausibel sein; mehrere Kandidaten bleiben ungeklärt.
Filme benötigen zusätzlich Kategorie Film/Movie/Spielfilm. Episoden benötigen vollständige
nullbasierte `xmltv_ns`-Angaben und explizites Serienjahr; es gibt keine Namensraten-Heuristik.
Positive Treffer 30 Tage, negative Treffer 1 Tag; API-Ausfälle werden nicht negativ gecacht.
Poster/Backdrop/Still fehlen dürfen. Öffentliche HTTPS-Bilder nur auf geprüften Hosts,
keine Querystrings, Zugangsdaten oder fremden Hosts. TMDB-IDs sind optionale `episode-num`
Erweiterungen mit `system="tmdb"`; ältere Importer können sie ignorieren.
TVMaze hat einen optionalen Suchadapter, keine automatische Gleichsetzung mit TMDB-IDs.
Senderlogos werden aus validierten XMLTV-Icons oder expliziten `logos`-Zuordnungen übernommen; kein Logo-Scraping. Die injizierbare `pipeline.aggregate` verbindet Merge, Filme, Episoden und Logos. Film-Details ergänzen Originaltitel, Beschreibung, Genre und Bewertung; vorhandene Beschreibungen bleiben erhalten.
Keine Live-API-Abnahme, keine aktuelle Dokumentations-/Lizenzprüfung durchgeführt.
Kein Token wurde ausgelesen oder verwendet. Secret-Existenz nur aus dem Auftrag bekannt.

## Phase D – Export

Deterministisches GZIP (mtime=0), SHA256 und Manifest v1. Unveränderte geprüfte Ausgaben werden nicht erneut geschrieben.
`manifest.file` zeigt auf `versions/<sha256>/xcmate-epg.xml.gz`.
Diese Version wird vor atomarem Manifest-Austausch vollständig geschrieben.
Clients müssen Manifest-Version und Hash prüfen; stabile Root-Dateien sind Kompatibilitätskopien,
kein Mehrdateien-Transaktionsprotokoll. Bei unveränderter Version ist kein Vollimport nötig.
Ein fehlgeschlagener Export lässt das vorherige Manifest und seine Version bestehen.
Keine öffentliche Bereitstellung. Textprüfung blockiert URLs und typische Secret-Muster;
sie ersetzt keine Rechte-/Datenschutzprüfung. Erforderliche Attribution niemals still entfernen.

## Phase E – CI und spätere Veröffentlichung

Offline-CI: ubuntu-latest, Standard-Python, fünf Minuten, keine Secrets, keine Uploads.
Produktionsentwurf liegt außerhalb `.github/workflows`, mit deaktiviertem Job und
kommentiertem Tagesplan. Pages ist nicht aktiviert. Budget unverändert bei 0 USD.
Keine kostenpflichtigen Dienste, KI-APIs oder neuen Abhängigkeiten.

## Offene Punkte und Grenzen

- Issue #2 und Master-Issue #485 nach CLI-Forbidden über den GitHub-Connector abgeglichen.
- Dokumentierte Masterinventur berücksichtigt: drei Quellenfamilien und getrennte Provider-/Feed-Identitäten. Der private ID-Snapshot und die Backups wurden nicht gelesen oder kopiert; bestätigte Provider-IDs bedeuten weiterhin keine verifizierten Feed-Zuordnungen.
- Feed-Adressen, Nutzungsrechte, Attribution und geschützte Persistenz müssen vor Echtbetrieb
  geprüft werden. Keine Quellenlizenz wird aus Personal Use abgeleitet.
- Kein echtes TMDB/TVMaze-Ergebnis, GitHub-Actions-Lauf oder Android/Kodi-Test nachgewiesen.
- TVMaze-Episodenbilder und private Logo-/Provider-Kataloge
  benötigen weitere Integration. Die CLI aktiviert noch kein Live-Artwork.
- Kein vollwertiger XMLTV-DTD-Validator: bewusst eingeschränkte Struktur-/Sicherheitsprüfung.
- TMDB-CDN-Größe ist aktuell fest auf w500 begrenzt; Konfigurations-/Größenauswahl via `/configuration` vor Live-Abnahme ergänzen.
- Größenlimit ist absichtlich konservativ und muss mit freigegebenen Feeds vermessen werden.
- LastGood kann veraltet sein; zukünftige Clients müssen die Sendungszeiträume beachten.

## Freigabepunkte

Draft prüfen; anschließend getrennt freigeben: echte Quellennutzung und Persistenz,
Live-API-Abnahme samt Attribution, später öffentliche Distribution und Tagesplan.
Kein Merge auf main, kein produktiver Pages-Upload, kein Kodi-Pilot-Eingriff.

## Review und Nachweise

Separate Codeprüfung: defektes DEFLATE und nicht-stringförmige Bildpfade durch Regressionstests abgesichert; Senderreferenzen verwenden eine Menge für lineare Validierung. 34 lokale Unit-/Mocktests bestehen. Kein Live- oder Android-Nachweis daraus abgeleitet.
