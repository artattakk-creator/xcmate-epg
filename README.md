# XCMate EPG Agent

Zentraler, optionaler XMLTV-Dienst für **XCMate / Kodi**. Er führt freigegebene XMLTV-Quellen inkrementell zusammen und ergänzt verifizierte Sendungsbilder und Senderlogos. Der geplante Kodi-Bezug verwendet ein separates privates GitHub-Repository.

> **Status: PRIVATE_PENDING / PRODUCTION_DATA_NOT_APPROVED.** Der Tageslauf und der Schema-3-Liefervertrag sind für synthetische Entwicklungstests verfügbar. Für den Produktivbetrieb stehen die separate Freigabe, der geschützte Restore und die Geräteabnahme aus.

Den lokalen Tageslauf, die eigenständige Profilableitung und den Git-Liefertest beschreibt [Issue #498](docs/ISSUE498_PRIVATE_DAILY_DE.md).
Historische Vollimporte und Bildnachweise stehen im [Issue-6-Runbook](docs/ISSUE6_PRIVATE_FULL_DE.md).
Alle tatsächlichen EPG-Daten, persönlichen Kataloge und Caches bleiben privat außerhalb von Git.

## Projektverknüpfungen

- **Kodi-Add-on und spätere UI-Integration:** [XCMate / GS Media Kodi](https://github.com/artattakk-creator/kodi-noodleprivate)
- **Fachlicher Masterauftrag:** [XCMate EPG Agent – Issue #485](https://github.com/artattakk-creator/kodi-noodleprivate/issues/485)
- **Design der Ein-Schalter-Integration in Kodi:** [Draft-PR #486](https://github.com/artattakk-creator/kodi-noodleprivate/pull/486)

XCMate soll später unter **Einstellungen → EPG → XCMate EPG Agent** einen einzigen Opt-in-Schalter erhalten. Die **Anbieter-/Xtream-EPG-Daten bleiben unabhängig vom Agenten immer verfügbar**, auch bei Fehlern oder deaktiviertem Agenten.

## Getrennte Zuständigkeiten

| Repository | Zweck |
|---|---|
| `xcmate-epg` (hier) | Server-Agent, Quellenprüfung, Delta-XMLTV, Bild-/Logo-Zuordnung und lokaler Test des privaten XMLTV-Liefervertrags |
| `kodi-noodleprivate` | Kodi-Client, Aktivierungsschalter, EPG-Import, Source-ID-Mapping, UI und Android-Abnahme |

## Kostenloser Ersttest

Die erste Entwicklungsphase sieht einen isolierten GitHub-Actions-Smoke-Test auf einem Featurebranch vor. Er nutzt ausschließlich `ubuntu-latest` und die Python-Standardbibliothek. Er erzeugt nur **synthetische XMLTV-Testdaten im temporären Runner-Verzeichnis**, veröffentlicht keine Dateien und greift nicht auf Netzwerk-EPG-Quellen oder TMDB zu.

Die GitHub-Budgets bleiben auf 0 USD. Kein Budget erhöhen und keine kostenpflichtigen Runner, Codespaces oder AI-Credits nutzen.

## Sicherheit und Rechte

- **Keine persönlichen Senderkataloge, Portaladressen, EPG-Zugangsdaten oder Backups** in dieses öffentliche Repository laden.
- Der private Codex-Lauf verwendet ausschließlich die ausdrücklich autorisierte Runtime-Bindung von `TMDB_READ_ACCESS_TOKEN`. Persönliche HTTP-Secrets und GitHub-Repository-Secrets sind getrennt; Zugangsdaten werden nicht zwischen ihnen kopiert. Details und Live-Status stehen im Issue-6-Runbook.
- TMDB-API-Anfragen verwenden den geheimen Bearer-Token; XMLTV-Bild-URLs dürfen **keinen** Token enthalten.
- Quellen-/Artwork-Lizenzen sowie die Rechte für den vorgesehenen privaten Empfängerkreis sind vor der Datenfreigabe zu prüfen.
- Keine täglichen Jobs oder Veröffentlichungen ohne separate Freigabe; Fehler dürfen niemals den letzten gültigen EPG überschreiben.

Die Architektur wird schrittweise implementiert, unabhängig vom derzeitigen Pilot-XCMate `0.1.29.97`.

## Entwicklungsbranch: inkrementeller Agent

Der eigenständige Python-Backend-Kern, synthetische CLI und Offline-Tests sind implementiert.
Aufruf und Grenzen: [Entwicklungsstand](docs/DEVELOPMENT_DE.md).
Der Standardlauf nutzt keine echten Daten, kein Secret und kein Netzwerk:

```sh
python3 -m unittest discover -s tests -v
python3 -m xcmate_epg --state work/private/state.sqlite --output work/demo
```

**Weiterhin gesperrt:** Merge, öffentliche Veröffentlichung, produktiver Tagesplan und Kodi-Änderungen.
Die synthetische Standard-CLI bleibt erhalten; der vollständige private Import ist ein eigener,
explizit autorisierter One-shot-Befehl. Authentifizierte TMDB-Live-Ergebnisse werden erst
nach tatsächlichen erfolgreichen Abrufen ausgewiesen.
