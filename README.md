# XCMate EPG Agent

Zentraler, optionaler XMLTV-Dienst für **XCMate / Kodi**. Ziel: ausgewählte, rechtmäßig verwendbare EPG-Quellen inkrementell zusammenführen, TV-Programme mit freigegebenem Artwork und zulässigen Senderlogos ergänzen sowie eine validierte XMLTV-GZIP-Datei bereitstellen.

> **Status: Einrichtung / noch kein produktiver EPG-Agent.** Keine echten Datenquellen, automatischen 24-Stunden-Jobs, TMDB-Secrets oder öffentliche EPG-Datei sind hier bislang eingerichtet. Kostenpflichtige Dienste werden nicht verwendet.

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
- Der `TMDB_READ_ACCESS_TOKEN` muss später ausdrücklich und **separat als Repository-Secret** für dieses Repository angelegt werden; Repository-Secrets werden nicht automatisch zwischen Repositories geteilt.
- TMDB-API-Anfragen verwenden den geheimen Bearer-Token; XMLTV-Bild-URLs dürfen **keinen** Token enthalten.
- Quellen-/Artwork-Lizenzen und das Recht zur öffentlichen Weitergabe **vor** einer GitHub-Pages-Veröffentlichung prüfen. **Personal Use** allein erlaubt keine öffentliche Wiederveröffentlichung.
- Öffentliche XMLTV-/Pages-Dateien und Manifeste enthalten keine unnötigen Ursprungsquellen-Namen, Feed-URLs oder internen Fetch-/Debug-Informationen. Rechtlich verpflichtende Attribution darf nicht entfallen; bei Konflikt keine öffentliche Veröffentlichung.
- Keine täglichen Jobs oder Veröffentlichungen ohne separate Freigabe; Fehler dürfen niemals den letzten gültigen EPG überschreiben.

Die Architektur wird schrittweise implementiert, unabhängig vom derzeitigen Pilot-XCMate `0.1.29.97`.
