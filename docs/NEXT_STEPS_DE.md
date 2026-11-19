# Einrichtungsschritte – XCMate EPG Agent (kostenfreie Stufe)

**Aktueller Stand:** Öffentliches Repo, Projektverknüpfung und manuell prüfbarer Offline-Smoke-Test. **Kein aktiver täglicher Job, kein echter XMLTV-Feed, keine GitHub-Pages-Veröffentlichung.** Demo-XMLTV darf nicht an Kodi verteilt werden.

## Jetzt zuerst prüfen
1. Kostenbudgets im GitHub-Konto bleiben bei 0 USD. Actions-Starter ist ein normaler kostenloser Ubuntu-Runner, max. fünf Minuten pro Job.
2. Im Repository unter Actions den Workflow **XCMate EPG – free offline smoke** prüfen. Der Test läuft nach Push auf dem Bootstrap-Branch, später nach Merge kann er auch manuell gestartet werden.
3. Nur bei sichtbarem grünen Testlauf gilt die GitHub-Actions-Basis als einsatzfähig. Ein roter oder blockierter Lauf darf **nicht** durch kostenpflichtige Runner oder ein höheres Budget umgangen werden.

## Darauf folgt der produktive Entwicklungsauftrag
1. Lizenz-/Weitergaberecht der XMLTV-Quellen vor öffentlicher Verteilung prüfen; wenn unklar, **keine** echten Daten auf GitHub Pages stellen.
2. Quellen-Registry und inkrementellen Downloader implementieren: ETag/Last-Modified/304, gesicherter Hash, Retry, LastGood, maximaler Download-/Entpack-/XML-Verarbeitungsumfang und DTD-Schutz.
3. Quellen-qualifizierten Sender-Abgleich (EPGShare DE/AT, Kaimandura) und provider-unabhängigen Kanalkatalog aufbauen. Private Provider-IDs und Backups **nicht** in das öffentliche Repo/Pages kopieren. 519 Provider-Kanal-IDs aus dem privaten 16.11.-Backup sind **keine** bereits nachgewiesenen XMLTV-Matches.
4. TMDB-Recherche und Artwork-Matching mit zwischengespeicherten Resultaten: Repository-Secret **TMDB_READ_ACCESS_TOKEN muss in diesem Repository neu eingetragen werden**; vorhandene Secrets aus kodi-noodleprivate werden nicht automatisch übernommen. Token nur im Bearer-Header; keine Tokenwerte im XMLTV/Manifest/Logs.
5. Zuverlässige, langlebige State-/Bild-/Quell-Cache-Speicherung festlegen und kostenloskeits- sowie größenmäßig prüfen. GitHub-Runner-Dateisystem ist ephemer, daher kein echtes Delta ohne extern persistierten Zustand.
6. XMLTV-GZIP+Manifest/SHA-Ausgabe nach vollständiger Validierung erst auf einem **nicht-öffentlichen Testweg** mit synthetischen Daten ausprobieren, danach Rechte und Quellqualität prüfen.
7. **GitHub Pages**: Im Repository Settings → Pages → Source „GitHub Actions“ wählen. Nur nach Freigabe einen separaten Pages-Workflow für echte XMLTV-Produktion aktivieren; erwartet wird später https://artattakk-creator.github.io/xcmate-epg/xcmate-epg.xml.gz .
8. EPG-Aggregator erst nach erfolgreichen Tests per 24h-Schedule aktivieren; Publish nur bei Änderungen, Server-Endpunkt prüfen und auf valide LastGood zurückfallen.
9. In **kodi-noodleprivate** die XCMate-Client-Integration (Einstellungen → EPG → XCMate EPG Agent → Aktivieren) implementieren; Provider-EPG bleibt immer verfügbar. Android-Geräteabnahme separat, Bildrenderer-Regression #468 bleibt offen.

## Projektlinks
- https://github.com/artattakk-creator/kodi-noodleprivate/issues/485
- https://github.com/artattakk-creator/kodi-noodleprivate/pull/486

**Freigabegrenze:** Keine echten externen EPG-Daten, keine persönlichen Stream-/Portal-URLs, keine neue Kodi-Build-/Pilot-Version und keine Veröffentlichung durch das bloße Anlegen dieser Bootstrap-Dateien.
