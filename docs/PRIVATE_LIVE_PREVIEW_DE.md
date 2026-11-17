# Issue #4 – einmaliger privater XMLTV-Probelauf

## Tatsächliches Ergebnis: LIVE_INPUT_BLOCKED

Am 17.11.2026 wurden die drei aktivierten externen Feeds aus der vorhandenen privaten
XCMate-Konfiguration ermittelt. Die Konfiguration wurde über den verbundenen Google-Drive-
Zugang gezielt gelesen, kein vollständiges Kodi-Backup kopiert, kein Token gelesen.
Die konkreten Adressen und die privaten Konfigurationsdateien liegen ausschließlich unter
`work/private-live/` und sind von Git ausgeschlossen. Keine Feed-Adresse steht in diesem Bericht.

Alle drei HTTPS-Abrufe scheitern bereits beim Proxy-Tunnelaufbau mit **403 Forbidden**.
Dies ist keine HTTP-Antwort des jeweiligen Feed-Servers. Die verwaltete Umgebung meldet
`network_policy.mode=restricted`, `state=enforced`, `observations_current=true`.
Die beiden konfigurierten Feed-Hosts fehlen in der effektiven Domain-Allowlist.
Ein Werkzeug zum Ändern/Beantragen der laufenden Netzwerkfreigaben ist nicht verfügbar.
Die Proxyvorgaben und TLS-Prüfung wurden nicht umgangen oder abgeschaltet.

| Nachweis | Tatsächlicher Stand |
|---|---|
| Konfigurierte externe Quellen | 3 |
| Erfolgreich geladene echte Quellen | 0 |
| Übernommene echte Sender / Programme / Bilder / Logos | 0 / 0 / 0 / 0 |
| Gemessene Feed-Größen, Encoding, Zeitumfang | Nicht messbar: kein Quellenpayload empfangen |
| Echte xcmate-epg.xml.gz / SHA256 | Nicht vorhanden / nicht vorhanden |
| Bestätigte neue Provider-Zuordnungen | 0 |
| TMDB | PENDING; kein Secret gelesen oder kopiert, kein API-Aufruf |
| Privater Download | Kein Download einer nicht erzeugten Datei angeboten |

Das Issue ist hinsichtlich echter EPG-Abnahme **nicht abgeschlossen**. Offline-Testdaten
werden ausdrücklich nicht als echter Dateinachweis ausgegeben.

## Implementierter privater Ausführungspfad

`python -m xcmate_epg.private_preview` ist von der bisherigen Demo-CLI getrennt:

- Explizite Quellenkonfiguration zwingend; kein synthetischer Ersatz bei fehlenden Daten.
- `sources[].path` für private lokale XML/XML.GZ-Dateien; `sources[].url` nur mit
  `--authorize-network`, ausschließlich HTTPS ohne Zugangsdaten/Query/Fragment.
- Ursprüngliche Grenzen: 32 MiB komprimiert **und** dekomprimiert sowie für den Gesamtoutput.
  Übergröße führt zu `*_LIMIT_REQUIRES_SUBSET`, niemals zu unlimitierter Dekompression.
  Vor Aufhebung einer Grenze ist ein separat geprüfter Streaming-/Subset-Import nötig.
- Optional `channel_ids` pro Quelle: explizite Feed-IDs für einen Ausschnitt innerhalb der
  Größenlimits. Keine automatische Gleichsetzung mit Provider-IDs; Identitäten bleiben
  mit anonymen Quell-IDs qualifiziert. Bestätigte Provider-Abbildung bleibt separat privat.
- Explizite, lokal geprüfte `approved_image_hosts` für vorhandene öffentliche HTTPS-Icons.
  Keine signierten Querystrings, Zugangsdaten oder unbekannten Hosts. Icons mit zusätzlichen
  Inhalten/Attributen werden abgewiesen; Attribution wird nicht heimlich entfernt.
- ETag/Last-Modified, 304, Payload-SHA, persistente validierte Snapshots und quellenspezifisches
  LastGood. Andere Inputs oder geänderte Policy dürfen keinen fremden Snapshot erben.
- `minimum_sources` zählt verfügbare validierte Quellen einschließlich LastGood. Darunter
  kein neuer Export; ein existierendes Manifest bleibt erhalten.
- Private Verzeichnisse 0700, Dateien 0600, Git-Ignore-Prüfung bei Ausgabe im Repository.
  Symlinks, Hardlinks und spezielle Dateien in der privaten Ablage werden abgewiesen.
- Vollständiger validierter XMLTV-Export, deterministisches GZIP, SHA256, Manifest mit
  `PRIVATE_LIVE_PREVIEW_DO_NOT_PUBLISH`, danach unabhängiger GZIP-/Parser-/Hash-Check.
  Größen-/Inhaltsprüfungen liegen vor dem Manifest-Commit.
- Qualitätsbericht enthält Zahlen und Zeitfenster, keine Inputadressen oder Programmtitel.
  LastGood-Nutzung wird getrennt von frisch erfolgreichen Quellen gezählt.

## Sichere lokale Erzeugung nach Freigabe des Inputs

Voraussetzung: autorisierter persönlicher Rechner oder diese Umgebung mit gezielter Freigabe
der zwei tatsächlichen Feed-Hosts in den Umgebungseinstellungen. Konkrete Namen ausschließlich
aus der privaten Konfiguration übernehmen, keine allgemeinen Proxyumgehungen.
Die Freigabe eines Feed-Hosts stellt keine Erlaubnis zur öffentlichen Weiterverbreitung dar.

Python 3.11+, keine Zusatzpakete, keine kostenpflichtigen Dienste. Im Entwicklungscheckout:

```sh
mkdir -p work/private-live
chmod 700 work/private-live
```

Die eigene `xtreammate_epg_sources.json` lokal als `work/private-live/xcmate-sources.json`
ablegen; keine Portale oder TMDB-Dateien kopieren. Einmalig die aktiven Quellen umwandeln:

```sh
python3 - <<'PY'
import json
from pathlib import Path
base = Path('work/private-live')
xcmate = json.loads((base / 'xcmate-sources.json').read_text())
active = [s for entry in xcmate.values() for s in entry.get('sources', [])
          if s.get('enabled') and not s.get('local')]
config = {'minimum_sources': 1, 'approved_image_hosts': [],
          'sources': [{'id': f's{i}', 'url': s['uri'], 'priority': i}
                      for i, s in enumerate(active, 1)]}
path = base / 'config.json'
path.write_text(json.dumps(config))
path.chmod(0o600)
PY
python3 -m xcmate_epg.private_preview \
  --config work/private-live/config.json \
  --private-root work/private-live/run --authorize-network
```

Bei gesperrtem Egress können auf dem autorisierten Rechner heruntergeladene echte
XMLTV-Dateien privat übertragen werden. Die jeweilige Quelle in der privaten JSON-Datei
als `{"id":"s1","path":"/absoluter/privater/pfad/feed.xml.gz","priority":1}` eintragen,
`url` entfernen und `--authorize-network` weglassen. Keine vollständigen Kodi-Backups,
keine Zugangsdaten und keine Rohprogramme in Issues/PRs/öffentliche Artefakte laden.
Ein größerer Originalfeed benötigt zunächst einen sicher erzeugten echten Ausschnitt;
`channel_ids` hebt das Dekompressionslimit nicht auf.

Erfolgreiche Ausgabe ausschließlich unter `work/private-live/run/`:

- `output/xcmate-epg.xml.gz`
- `output/xcmate-epg.xml.gz.sha256`
- `output/manifest.json`, unveränderliche Versionen unter `output/versions/`
- `quality-report.json`, `state.sqlite` (privat, nicht übertragen nötig)

Nach Erfolg erneut ausführen und Hash vergleichen; danach eine Quelle gezielt unerreichbar
machen und im Qualitätsbericht `cache_status=lastgood` bei gleichem Ausgabestand prüfen.
Diese Echtprüfungen wurden hier mangels Input noch nicht durchgeführt. LastGood und
Unverändert-Verhalten sind ausschließlich offline mit Testfixtures abgesichert.

## Geschützter Transfer und persönliche Prüfung

Diese Umgebung bietet aktuell keinen verifizierten privaten EPG-Downloadendpunkt für den
Benutzer. Kein Webserver, keine Pages-Datei und kein öffentliches Actions-Artefakt wird erzeugt.
Falls die Datei auf einem eigenen per SSH erreichbaren Rechner erzeugt wird, ausschließlich
bestehendes authentifiziertes SSH/SFTP/SCP für die drei Ausgabedateien nutzen, z.B.:

```sh
scp user@eigener-rechner:/privater/pfad/xcmate-epg/work/private-live/run/output/xcmate-epg.xml.gz .
scp user@eigener-rechner:/privater/pfad/xcmate-epg/work/private-live/run/output/xcmate-epg.xml.gz.sha256 .
scp user@eigener-rechner:/privater/pfad/xcmate-epg/work/private-live/run/output/manifest.json .
sha256sum -c xcmate-epg.xml.gz.sha256
gzip -t xcmate-epg.xml.gz
```

Dies ist eine Anleitung für einen eigenen bereits autorisierten Rechner, kein behaupteter
funktionierender Download aus dieser Codex-Umgebung. Eine spätere private Drive-Ablage müsste
vor dem Upload auf ausschließlich persönliche Zugriffsrechte geprüft werden; hier kein Upload.

## TMDB und Kodi

Kein Auslesen des bestehenden GitHub-Secrets nach Codex. Kein neuer Secret-führender
PR-Workflow; ohne vertrauenswürdigen separat eingerichteten Ausführungspfad bleibt TMDB
`PENDING`. Vorhandene erlaubte Feed-Bildlinks werden separat gezählt, nicht als TMDB-Treffer.

Der gelesene Kodi-Quellstand `6e34671456495b2ce514721402b05236789382c2` unterstützt im EPG-Manager
**Quellen hinzufügen → XMLTV-Datei** (`.xml`, `.gz`, `.xml.gz`), anschließend **Jetzt aktualisieren**
und die manuelle Senderzuordnung. Referenzen: `epg_manager53.py`, `epg_sources.py`,
`xmltv_stream.py` unter `src/plugin.video.xtreammate/resources/lib/` im Kodi-Repository.
Diese Codebasis weist im addon.xml Version 0.1.29.92 aus; die konkrete ausgelieferte
**0.1.29.97 wurde hier nicht binär oder auf Android verifiziert**. Der Agent-Schalter aus
Kodi-Draft #486 ist weiterhin nur ein Entwurf. Quellequalifizierte IDs benötigen bestätigte
manuelle Zuordnung im Client; bestehende Provider-Zuordnungen nicht überschreiben.
Keine Behauptung sichtbarer BINGIE-Bilder oder funktionierender Pilot-Integration.

## Prüfungen und Release-Sperren

46 lokale Unit-/Mocktests bestanden, davon die ursprünglichen 34 unverändert und zwölf neue
Regressionen für privaten Import, Zählung, Rechte, Fehler, LastGood und Export-Rollback.
Der vorhandene Bootstrap-Smoke bleibt ein synthetischer Offline-Nachweis.
Kein Merge, kein Pages-Upload, keine echte öffentliche Ausgabe, kein Secret-Export,
kein aktiver Tagesplan, keine Kodi-Änderungen und keine kostenpflichtigen Dienste.
