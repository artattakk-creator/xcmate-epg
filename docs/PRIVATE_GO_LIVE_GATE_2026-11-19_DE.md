# XCMate EPG Agent – privates Go-live-Gate / Ist-Abgleich 19.11.2026

**Ziel:** Heutige Fortsetzung mit dem Ziel einer echten produktiven, **privaten** Nutzung nach durchlaufenen Gates; kein öffentliches XMLTV und keine Erfolgsmeldung ohne Client-/Gerätebeleg.

**Standabgleich:** GitHub Repositories `artattakk-creator/xcmate-epg`, `artattakk-creator/xcmate-epg-private`, `artattakk-creator/kodi-noodleprivate`; privater Google-Drive-Ordner „EPG-Agent / V2 Workspace Checkpoint 2026-11-18“ und die darin enthaltenen `REMOTE_VERIFICATION.json`, `transfer-manifest.json`, `checkpoint-summary.json`, `RESTORE_CHECKPOINT_DE.md`. Keine Tokenwerte gelesen. Dieser Bericht ersetzt veraltete **Transfer-Pending**-Aussagen vom 17.11., aber **nicht** die offene Wiederherstellung/Abnahme.

## Tatsächlich verifizierte Grundlagen

1. **Backend-Quelle:** Draft-PR [#7](https://github.com/artattakk-creator/xcmate-epg/pull/7), Branch `feat/full-epg-coverage-artwork-issue6`, Dokumentations-HEAD `52f5e4b`; letzter Code-Teststand `1e80645`. Alle drei XMLTV-Feeds in einem privaten Komplettimport: 1.278 quellenqualifizierte Sender, 185.945 gültige von 185.946 gelieferten Programmen. Der 17.11.-Export mit 9.566 Programmplätzen und 23.528 typisierten TVMaze-Artwork-Referenzen hat weiter `PARTIAL_COVERAGE / AUDIT_INCOMPLETE`. **0 bestätigte Feed↔Provider-Kanalzuordnungen**, Geräteprofil aktuell leer; verifizierte 88 Logos sind deshalb noch nicht exportiert.
2. **Automatisierte Tests:** GitHub Actions [offline run 44410782253](https://github.com/artattakk-creator/xcmate-epg/actions/runs/44410782253) auf Stand `52f5e4b`: **217 Tests PASS** (plus Smoke), aber kein neuer Real-Daten-Replay/Pixel-/Kodi-Gerätetest.
3. **TMDB-Secret funktioniert im getrennten GitHub-Actions-Test:** [Run 44210183373](https://github.com/artattakk-creator/xcmate-epg/actions/runs/44210183373) am 18.11. erfolgreich: `/3/configuration` HTTP 200, echte Movie-/TV-Titel, öffentlicher Posterabruf ohne Bearer. **Das ist kein Nachweis der vererbten Secret-Bindung in der alten Codex-Cloud-Runtime und kein Live-Match im 185.945-Programm-Export.** Im letzten Export bleibt `TMDB_LIVE_PENDING` zutreffend.
4. **Privates Ausgaberepository existiert:** `artattakk-creator/xcmate-epg-private`, GitHub-API bestätigt `visibility=private`. [Synthetischer Zustelltest 44173864386](https://github.com/artattakk-creator/xcmate-epg/actions/runs/44173864386): private Zielprüfung PASS, anonyme GET = 404, authentifizierter Upload/Readback PASS. Das Repository enthält derzeit **nur synthetische Smoke-Dateien und Dokumentation**, noch keine reale `manifest.json`/`xcmate-epg.xml.gz`.
5. **Drive-Checkpoint wurde inzwischen vollständig offsite gesichert:** `REMOTE_VERIFICATION.json` meldet `PRIVATE_DRIVE_CHECKPOINT_VERIFIED`. 26 exakt nummerierte Teile, **640.688.709 Bytes** rekonstruiertes TAR.GZ, SHA256 `9babecb7ce7fccb33d16636a64bf8a0d6a36fd155ce254d483fd32762449ecb2`. Laut protokolliertem Readback: sämtliche 37 Nutz-/Begleitdateien vollständig auf Größe/SHA geprüft, owner-only-Freigaben erneut geprüft; 964 Nutzdateien / 1.944.803.781 Bytes und zehn SQLite-Datenbanken im Ursprung erfolgreich integritätsgeprüft. **`new_workspace_actual_restore_performed=false`**: kein externer neuer Codex-Arbeitsbereich hat den Checkpoint bislang nachweislich restauriert. Der vollständige Providerkatalog fehlt im Checkpoint.
6. **Drive-TMDB-Datei** existiert als Eigentümerdatei mit `shared=false`; GitHub-Repositories/Branches erhalten dadurch **nicht automatisch** Zugriff. Eine private GitHub-Repository-Datei oder ein privater Branch ist nicht dasselbe wie das GitHub-Actions-Secret; Secretwerte niemals in Git speichern.
7. **Installer 0.5.27** ist ein separater **RAW-Gerätetest** im GS Media Pilot/NeoDark3, kein livegeschalteter EPG-Agent. [Installer-PR #491](https://github.com/artattakk-creator/kodi-noodleprivate/pull/491) bleibt Draft. XCMate-Client-Integration [PR #486](https://github.com/artattakk-creator/kodi-noodleprivate/pull/486) besteht nur als Design, kein opt-in-fähiger Backend-Reader/Importer.

## P0-Gates für einen privaten produktiven Betrieb

**GATE 1 – Private Wiederherstellung:** Alle 26 Teile ausschließlich über authentifizierten Owner-Zugriff in einen neuen privaten Arbeitsbereich übertragen; Nummernfolge, Teil-SHA, Gesamt-TAR-SHA, 964 Datei-SHA, SQLite `integrity_check`, Original-Pilot und LastGood nachprüfen. Keine privaten Quell-/Portal-Daten in öffentliche Git-Repos, Actions-Artifacts oder Logs. Altumgebung behalten, bis Readback erfolgreich. **Ergebnis: RESTORE_PASS + nachvollziehbarer neuer Laufkontext**.

**GATE 2 – Tatsächlicher Provider-Abgleich:** Der Vollbestand von 1.278 quellenspezifischen Sendern ist kein gemapptes Geräteprofil. Den vollständigen Kodi-Providerkatalog mit einer **autorisierten datensparsamen lokalen Extraktion** bereitstellen (kein Umgehen bestehender Dateidienst-Grenzen). Die 519 im 16.11.-Backup vorhandenen Provider-EPG-IDs sind zunächst Kandidaten, keine bestätigten Feed-IDs. Exakte ID-Kette bestätigen, unsichere Titel-/Namen-Matches als Kandidat ausweisen. **Ergebnis: quantifizierte, bestätigte abspielbare Sender / Programme und reproduzierbare Geräteprofil-Zuordnung**.

**GATE 3 – Reale Bildanreicherung:** In der neuen vertrauenswürdigen Ausführungsumgebung den `TMDB_READ_ACCESS_TOKEN` ausschließlich aus dafür genehmigter Runtime-Bindung nutzen; API-/Download-Budget, Rate-Limits, Identitätsprüfung, Attribution, cachefähige und typisierte Rollen `poster`, `backdrop`, `episode_still`, `channel_logo`. Sicherer Fallback TVMaze, XMLTV und Anbieter-EPG. Den unabhängigen Identitätsaudit nachholen. **Ergebnis: realer TMDB-Match-/Bildnachweis im EPG-Export**. Ein grüner API-Smoke allein genügt nicht.

**GATE 4 – Geschützte, atomare Auslieferung:** Nur nach geprüften Feeds/Logo-/Artwork-/Lizenz-/Privatheitsgates `xcmate-epg.xml.gz`, Checksumme und ein versioniertes Metadaten-/Artwork-Sidecar in `xcmate-epg-private` stellen. Manifest nach allen Dateien **zuletzt** schreiben; `PRIVATE_READY` nur bei bestandenem Qualitätsgate, sonst `PRIVATE_PENDING` / vorhandenes LastGood beibehalten. Authentifizierte READ-only Geräteabfrage, 404 für anonymen Zugriff, vollständige Byte-/SHA-Prüfung, kein OAuth-/PAT-Token in URL/Logs. Quellen-/Artwork-Nutzungsrechte prüfen. Keine öffentliche Pages-Kopie.

**GATE 5 – Funktionierender Kodi-Client:** Den ausstehenden XCMate-Opt-in-Reader aus #485/#486 tatsächlich implementieren: `Einstellungen → EPG → XCMate EPG Agent`, private Manifest-/XMLTV-/Sidecar-Abrufe mit sicherer Berechtigung, ETag/SHA, lokaler Sender-ID-Zuordnung, Provider-EPG dauerhaft als unabhängige Baseline, atomarer LastGood-Switch. Android/Kodi 21 und LG webOS/Kodi 22 mit sichtbaren BINGIE-Hero/Poster/EPG-Bildern real testen. **Keine Umstellung nur wegen des Installer 0.5.27**.

**GATE 6 – Betriebsfreigabe:** Nur bei belegten Gates 1–5, geprüfter Rechtebasis und Geräteabnahme den ersten privaten Produktionsstand ausdrücklich markieren. Danach 24h-Inkrementaljob in getrenntem kontrolliertem Schritt einschalten; Ausfall-/Rollback-Test, Monitoring und 0-USD-Budgetgrenzen prüfen.

## Heute sinnvoller minimaler Ablauf

1. Zuerst privaten Drive-Checkpoint wiederherstellen, ohne Feed-Neuimport und ohne Produktivschreiben.
2. TMDB-Smoke-Nachweis als erledigt abhaken; gezielten limitierten **Backend**-Live-Test auf tatsächlichen verifizierbaren Sendungsidentitäten durchführen, falls die Runtime autorisiert und bereit ist.
3. Vollständigkeit der Geräte-ID-Map feststellen; bis dahin **keine** Freigabe `PRIVATE_READY` für das leere Geräteprofil.
4. Wenn Mapping/Client noch blockieren: **privaten Staging-Stand** und testbaren Abnahmebericht bereitstellen, ausdrücklich **nicht** als produktiv melden. Der bestehende funktionierende IPTV-Anbieter-EPG sowie Mini-/Groß-EPG-Bilder auf LG bleiben unverändert.

## Rückfall, Datenschutz, Qualitätsstatus

- **No-go:** 0 bestätigte Mappings / leeres Geräteprofil, kein echter Client-Import, defektes Manifest/SHA oder Nutzungsrechte ungeklärt, obwohl Produktoberfläche `READY` behauptet.
- **No-go:** Externen Token aus Owner-only-Drive-Datei als Git-Commit, URL-Parameter oder öffentliche Workflow-Ausgabe „bequem“ verfügbar machen.
- **No-go:** Öffentliches XMLTV/Artwork auf GitHub Pages oder automatischen Tagesjob freischalten, bloß um ein Datum einzuhalten.
- **Erhalten:** Pilot/LastGood, XCMate-Anbieter-EPG, lokale Zuordnungen, bereits funktionierendes LG-EPG-Bildverhalten. Rollback auf Version 0.5.26 des Installers bleibt separat.

Diese Dokumentation ist ein **verifizierter Ist-Abgleich und bedingter Go-live-Plan**, keine Behauptung einer abgeschlossenen Live-Inbetriebnahme.
