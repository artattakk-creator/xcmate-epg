# XCMate EPG Agent – Entwicklungsentwurf

Autorisiert durch den Masterauftrag: selbstständig bis zum überprüfbaren Draft-PR.
Python-Standardbibliothek; keine produktiven Quellenabrufe, API-Aufrufe oder Veröffentlichungen.

## Architektur und Alternativen
SQLite speichert validierte Quellen mit HTTP-Validatoren und Artwork-Ergebnisse.
Ein reiner Actions-Cache ist kostenlos, aber löschbar und kein dauerhaftes Archiv.
Ein öffentlicher State-Branch würde möglicherweise lizenzierte Rohdaten offenlegen.
Daher: lokaler SQLite-State, vorbereiteter Actions-Cache nur für synthetische Tests;
für echte Daten ist eine geschützte Persistenz mit geprüften Rechten vor Freigabe nötig.
Snapshots werden erst nach erfolgreichem Parsing aktualisiert. Ausfälle nutzen LastGood.
Quellen-IDs qualifizieren Sender. Nur explizite bestätigte Aliase führen Sender zusammen.
Programme werden deterministisch nach Priorität zusammengeführt; Konflikte werden gezählt.
Zeitangaben benötigen explizite Zeitzonen. DTDs, Entities und übergroße Daten werden abgelehnt.
Artwork wird konservativ anhand Titel, Jahr und Medientyp zugeordnet; Mehrdeutigkeit bleibt offen.
Export: unveränderliche Hash-Versionen, deterministisches GZIP und Manifest als Commit-Punkt.
Keine produktiven Pages- oder Schedule-Aktionen. Kodi bleibt unverändert.

## Nicht geklärt
Issue #2 ist über GitHub API mit Forbidden blockiert. Keine konkreten Feed-Adressen,
keine geprüften Weitergaberechte und keine privaten bestätigten Senderzuordnungen verfügbar.
Live-API-, GitHub-Actions- und Android-Abnahmen sind separate Schritte.
