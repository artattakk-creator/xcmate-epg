# XCMate EPG Agent

Zentraler, optionaler XMLTV-Dienst für **XCMate / Kodi**. Ziel: EPGShare01 und Kaimandura inkrementell zusammenführen, TV-Programme mit TMDB-/TVMaze-Artwork und rechtlich zulässigen Senderlogos ergänzen, validierte XMLTV-GZIP-Datei über GitHub Pages bereitstellen.

> **Status: privater V2-Probelauf, noch kein Produktivbetrieb.** Drei vollständige Quellen liefern 1.278 quellenqualifizierte Sendereinträge und 185.945 gültige Programme. Gerätezuordnung und Artwork bleiben `PARTIAL_COVERAGE / ARTWORK_BLOCKED`; der authentifizierte TMDB-Test ist in der aktuellen Instanz `TMDB_LIVE_PENDING`. Keine öffentliche EPG-Datei, Tagesjobs oder kostenpflichtigen Dienste.

Vollimport, echte Bildnachweise und Grenzen: [Issue-6-Runbook](docs/ISSUE6_PRIVATE_FULL_DE.md),
[aggregierte Abnahme](docs/issue6-acceptance.json), [Fortsetzung auf dem bestehenden Branch](docs/ISSUE6_HANDOFF_DE.md).
Alle tatsächlichen EPG-Daten, persönlichen Kataloge und Caches bleiben privat außerhalb von Git.

## Projektverknüpfungen

- **Kodi-Add-on und spätere UI-Integration:** [XCMate / GS Media Kodi](https://github.com/artattakk-creator/kodi-noodleprivate)
- **Fachlicher Masterauftrag:** [XCMate EPG Agent – Issue #485](https://github.com/artattakk-creator/kodi-noodleprivate/issues/485)
- **Design der Ein-Schalter-Integration in Kodi:** [Draft-PR #486](https://github.com/artattakk-creator/kodi-noodleprivate/pull/486)

XCMate soll später unter **Einstellungen → EPG → XCMate EPG Agent** einen einzigen Opt-in-Schalter erhalten. Die **Anbieter-/Xtream-EPG-Daten bleiben unabhängig vom Agenten immer verfügbar**, auch bei Fehlern oder deaktiviertem Agenten.

## Getrennte Zuständigkeiten

| Repository | Zweck |
|---|---|
| `xcmate-epg` (hier) | Server-Agent, Quellenprüfung, Delta-XMLTV, Bild-/Logo-Zuordnung, Tests und Pages-Publikation |
| `kodi-noodleprivate` | Kodi-Client, Aktivierungsschalter, EPG-Import, Source-ID-Mapping, UI und Android-Abnahme |

Der geplante Downloadpfad lautet `https://artattakk-creator.github.io/xcmate-epg/xcmate-epg.xml.gz`; **er ist noch nicht veröffentlicht**.

## Kostenloser Ersttest

Die erste Entwicklungsphase sieht einen isolierten GitHub-Actions-Smoke-Test auf einem Featurebranch vor. Er nutzt ausschließlich `ubuntu-latest` und die Python-Standardbibliothek. Er erzeugt nur **synthetische XMLTV-Testdaten im temporären Runner-Verzeichnis**, veröffentlicht keine Dateien und greift nicht auf Netzwerk-EPG-Quellen oder TMDB zu.

Die GitHub-Budgets bleiben auf 0 USD. Kein Budget erhöhen und keine kostenpflichtigen Runner, Codespaces oder AI-Credits nutzen.

## Sicherheit und Rechte

- **Keine persönlichen Senderkataloge, Portaladressen, EPG-Zugangsdaten oder Backups** in dieses öffentliche Repository laden.
- Der private Codex-Lauf verwendet ausschließlich die ausdrücklich autorisierte Runtime-Bindung von `TMDB_READ_ACCESS_TOKEN`. Persönliche HTTP-Secrets und GitHub-Repository-Secrets sind getrennt; Zugangsdaten werden nicht zwischen ihnen kopiert. Details und Live-Status stehen im Issue-6-Runbook.
- TMDB-API-Anfragen verwenden den geheimen Bearer-Token; XMLTV-Bild-URLs dürfen **keinen** Token enthalten.
- Quellen-/Artwork-Lizenzen und das Recht zur öffentlichen Weitergabe **vor** einer GitHub-Pages-Veröffentlichung prüfen.
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
