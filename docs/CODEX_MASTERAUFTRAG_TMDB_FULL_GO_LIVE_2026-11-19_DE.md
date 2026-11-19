# CODEX-MASTERAUFTRAG – XCMate EPG Agent V2: echter TMDB-Livebetrieb, vollständige Bilder und privater Go-live

**Datum:** 19.11.2026
**Hauptrepository:** \`artattakk-creator/xcmate-epg\`
**Ausgangsbranch:** \`feat/full-epg-coverage-artwork-issue6\` (bestehender Draft-PR #7, Issue #6). Den Iststand vor Änderungen neu abrufen.
**GitHub-only-Auslieferung:** \`artattakk-creator/xcmate-epg-private\`, bestehendes \`manifest.json\` mit \`PRIVATE_PENDING\`.
**Kodi-Client:** \`artattakk-creator/kodi-noodleprivate\`, Draft-PR #494; gesondert integrieren, nicht mit Installer #491 verwechseln.
**Daten-Checkpoint (privat):** Google Drive, \`https://drive.google.com/drive/folders/1ldKhXOd06Ty7Pg4X5d7uyK25Nl706W7W\`.

## Primärer P0-Auftrag

**Nicht wieder einen synthetischen oder auf wenige Kanäle reduzierten Test als produktionsfähig melden.** Ziel ist die **erste vollständige, private, real nutzbare Agent-V2-Version**, mit allen vorhandenen gültigen Sendungen der drei konfigurierten Feeds, dem vollständigen tatsächlichen IPTV-Senderkatalog und belegter Senderzuordnung sowie allen **tatsächlich verfügbaren und verifizierbar passenden** Bildern für XCMate und BINGIE MOD. Weitergabe nur bei geklärter Nutzungsberechtigung. Fehlende echte Bilder/IDs sind transparent zu markieren, niemals zu erfinden oder unbewiesen zuzuordnen.

## Arbeitsumgebung und Zugang

1. Nutze eine private Codex-Cloud-Umgebung mit diesem Hauptrepo und **Python 3**, ausreichendem Datenspeicher; prüfe vor Restore tatsächlichen freien Disk-Space. Das alte Vollarchiv ist 640.688.709 Bytes, entpackter Stand 1.944.803.781 Bytes; temporärer Platz ist zusätzlich erforderlich. SQLite und notwendige Test-/Bildbibliotheken bereitstellen. Das bisherige Arbeitsverzeichnis/LastGood nie vor einem verifizierten Restore löschen.
2. Setze \`TMDB_READ_ACCESS_TOKEN\` als **geschütztes Codex-Environment-Secret/Variable**, **nicht** als Klartext im Prompt, Git-Commit, Dokument, Workflow-Log oder Testreport. GitHub-Actions-Secrets sind nicht automatisch Codex-Secrets. Der bereits erfolgreiche GitHub-Actions-Live-Test [#44210183373](https://github.com/artattakk-creator/xcmate-epg/actions/runs/44210183373) beweist das Secret **nur in dessen Laufzeit**, nicht in deiner neuen Codex-Runtime.
3. Zulässige HTTPS-Ziele (sofern für die aktuelle Aufgabe erforderlich): \`api.themoviedb.org\`, \`image.tmdb.org\`, \`api.tvmaze.com\`, \`static.tvmaze.com\`, \`epgshare01.online\`, \`kaimandura.github.io\`, \`github.com\`, \`api.github.com\`, die bei autorisierter Drive-Wiederherstellung tatsächlich benötigten Drive-/Google-Downloadhosts und GitHub-CDN-Redirecthosts. Netzwerk auf diese Ziele begrenzen, soweit die Umgebung es unterstützt.
4. Wenn Drive-Berechtigung oder TMDB-Secret in der Runtime fehlen, den **konkreten Blocker** ohne Geheimniswert dokumentieren. Keinen vermeintlich erfolgreichen Lauf simulieren. Verwende bei technisch begrenztem Cloud-Netz alternativ den bereits berechtigten GitHub-Actions-Smokepfad für den reinen TMDB-Livetest; sichere Checkpoints/Feeds nicht ungeprüft in öffentliche Artifacts.
5. Für private GitHub-Auslieferung nur getrennte, zweckgebundene und autorisierte Schreibberechtigung verwenden; **keine** Portallogins/Quell-Rohfeeds ins Ausgaberepository. Die spätere XCMate-Reader-Authentifizierung bleibt ein dedizierter \`Contents: read\`-Token, automatisch im **autorisierten privaten ZIP-Build** bereitgestellt; **nicht** in Quellcode oder öffentliche Pilot-ZIPs committen.

## A – Checkpoint wiederherstellen

- Hole die **26 Teile** des Drive-V2-Checkpoints autorisiert in die private Runtime, prüfe Nummernfolge, Teil- und Gesamt-SHA und den kompletten Datei-/DB-Inventarbestand; Gesamt-TAR.GZ SHA256: \`9babecb7ce7fccb33d16636a64bf8a0d6a36fd155ce254d483fd32762449ecb2\`.
- Erwarteter Altbestand: **964 Dateien**, **10 SQLite-Datenbanken**. SQLite \`PRAGMA integrity_check\`, LastGood/Pilot-Assets und Quellcache erhalten. **Kein Blind-Neuimport**, solange Restore offen oder unklar ist.
- Bericht: \`RESTORE_PASS\` oder eindeutig \`RESTORE_BLOCKED:<Grund>\`; Zeit/Hashes/Dateizahlen, keine geheimen Pfade/URLs/Portal-Daten.

## B – TMDB-Live-Tokenprüfung (absolutes P0)

- Verwende die vorhandenen Module \`xcmate_epg/tmdb_runtime.py\`, \`xcmate_epg/live_artwork.py\`, \`xcmate_epg/image_validation.py\` und tatsächliche \`TMDB_READ_ACCESS_TOKEN\`-Bindung. Keine zusätzliche geheime Token-Datei ins Repo.
- **Echte Authentifizierung** gegen \`https://api.themoviedb.org/3/configuration\` und tatsächliche Movie-/TV-/Episode-Detail-/Image-API-Aufrufe; Erfolg durch HTTP-200 und plausible reale Metadaten belegen. 401/403/Rate-Limit getrennt auswerten, Tokens niemals ausgeben.
- Für bestätigte Identitäten **alle vorhandenen Bildrollen** prüfen: \`poster\`, \`backdrop/fanart\`, \`episode_still/thumb\`, gegebenenfalls \`season_poster\`, \`series_poster\`, \`clearlogo\`, \`channel_logo\`. Movie- oder Serienposter nie als belegtes Episodenstill ausgeben. TMDB-IDs, Typ, Land/Jahr, Staffeln/Folgen nur bei ausreichender Evidenz matchen; Mehrdeutigkeiten markieren.
- **Verifizierte Bildlinks** erfordern mehr als ein gültiges URL-Format: öffentliche HTTPS-Bild-URL auf freigegebenem Host, **ohne API-/Bearer-Token**, bestätigter Status (HTTP 200 bzw. kontrollierter GET), passende Bild-MIME, tatsächlich dekodierbarer Bildinhalt, bekannte Maße/Orientierung, URL- und Bildherkunft/Identitätsnachweis, letzter Prüfzeitpunkt und zulässige Attribution. Keine falsche Behauptung für 404/401/403/429, abgelaufene Links, HTML-Fehlerseiten oder falsche TMDB-Identität. Bounded Streaming/Budgets/Caching nutzen.
- TMDB → TVMaze → zulässiges XMLTV, **separat pro Rolle**; vorhandene TVMaze-Treffer/Manuell-Overrides/LastGood erhalten. Bildquote und echte TMDB-API- sowie URL-Verifikationsquoten nach Bildart/Quelle dokumentieren. **TMDB_LIVE_PENDING erst bei nachgewiesenem Real-EPG-Match ersetzen**, nicht nach einem erfolgreichen API-Smoke.

## C – Vollständiger EPG-/Sender-/Logo-Abgleich

- Drei vollständige Quellen; Ausgangsreferenz des vorherigen privaten Imports: **1.278 quellenqualifizierte Sender, 185.945 gültige Sendungen**, GZIP **17.411.748 Bytes**. Das sind historische **Vergleichswerte**, nicht starr erzwungene heutige Zahlen. Reale Deltas transparent ausweisen. Quell-ID, Gültigkeitszeitraum, Dedup, Felder und erhaltene Originalinformationen auditieren.
- Vollständigen persönlichen Kodi-/IPTV-Senderkatalog **autorisiert und privat** beschaffen, nicht nur die damaligen 519 vorhandenen Provider-EPG-IDs aus einem Teilbestand. Der letzte Kodi-Snapshot nannte ca. 2.520 Live-Sender; **0** Feed↔Provider-Mappings waren damals bestätigt. Keine ähnlich benannten Länder-/HD-Varianten raten. Sichere ID-Bindungen oder explizit bestätigte Zuordnungen anwenden; sämtliche unmapped/ambiguous-Zeilen ausweisen.
- 88 lokal geprüfte Logos waren bisher noch ohne bestätigte Feed-Bindung. Quellengebundene Senderlogos nur bei belastbarer ID-Zuordnung exportieren. Keine Portaldaten/privaten Benutzerkennungen im öffentlich lesbaren Quell-Repo.
- Der Nutzer verlangt **alle zulässig verfügbaren Bilder und Sendungen**, keinen Demo-Ausschnitt. Das ist nicht die Zusicherung von Bildern für alle Sendungen; fehlende Inhalte ehrlich berichten.

## D – Vollständiges privates GitHub-Auslieferungsbundle

- Achte auf die **bestehende Client-V2-Vertragsversion** aus \`kodi-noodleprivate\` Draft-PR #494 und \`docs/FULL_PRIVATE_DELIVERY_V2_2026-11-19_DE.md\`. Das \`full_export.py\`-Manifest mit \`PARTIAL_COVERAGE\`/\`versions/<sha>/...\` ist **nicht unmittelbar kompatibel** – implementiere einen bewusst getrennten GitHub-Delivery-Adapter.
- Stabile private GitHub-Contents-API-Pfade: \`manifest.json\`, \`xcmate-epg.xml.gz\`, \`xcmate-artwork.jsonl.gz\` im \`xcmate-epg-private\`-Repository. **Keine** GitHub-Pages-XMLTV.
- Vollständig geprüftes \`xcmate-epg.xml.gz\` enthält alle gültigen Programme, Bildlinks; typisierte Bildreferenzen und ihre Provenienz stehen zusätzlich in \`xcmate-artwork.jsonl.gz\`. Manifest \`schema_version=2\`, \`status=PRIVATE_READY\`, \`source_count>=3\`, \`channel_count\`, \`programme_count\`, \`bytes\`, \`sha256\`, \`generated_at_utc\` sowie \`artwork.file\`, \`artwork.records\`, \`artwork.bytes\`, \`artwork.sha256\`. Alle Daten-Dateien zuerst, **Manifest zuletzt** veröffentlichen, Client-Readback/SHA/GZIP/XMLTV/JSONL/identische Generation verifizieren.
- **PRIVATE_PENDING bleibt**, wenn Restore, realer TMDB-Lauf, vollständiger Source-Import, Provider-Mapping, Artwork-Audit oder Lizenz-/Client-Vertragsprüfung nicht PASS ist. Fehler dürfen das bisherige LastGood und die Anbieter-EPG-Daten niemals überschreiben.

## E – Kodi-Integration und Abnahme

- Hauptcode bleibt \`xcmate-epg\`. Parallel die XCMate-Integrationsanforderung an Draft-PR **#494** übergeben, nicht versehentlich Installer **#491** verändern: bestehende \`epg_agent_github.py\`-Staging-Route an echten Kodi-Streaming-EPG-Importer und bestätigte Mappings anschließen. Der Legacy-Parser mit **150.000-Programm-Limit** darf die vollständigen 185.945 Sendungen nicht abschneiden.
- Einstellungsmenü \`XCMate EPG Agent aktivieren\`, automatisch aus privater Installation geladener read-only-GitHub-Token (keine Kundeneingabe); Provider-EPG bleibt verfügbar, auch bei Agent-Fehler/Deaktivierung. Gültige Manifest-SHA → kein unnötiger Re-Download; SQL-Transaktionen/atomarer LastGood-Wechsel; BINGIE-Hero=Backdrop, Widgets=Poster, EPG=Stills/Thumbs, Senderlogos persistent.
- Device-PASS getrennt für Kodi22 LG webOS und Kodi21 Android; sichtbare Bilder, Kategorienwechsel, EPG-Rückfall, Neustart, Offline/401/403/429, Rotation und Bild-Fallbacks prüfen. Keine Hosttests als Geräte-PASS ausweisen.

## Tests, Bericht, Freigabe

1. Offline-Regressionen einschließlich bestehender **217 Tests** und neuem V2-Delivery-Vertrag; echte API-/Image-Live-Probe **mit nicht geloggtem Secret**, realer vollumfänglicher Export, unabhängiger Identitäts-/Bild-Audit, private Auth-/Manifest-Readback-Probe.
2. Prüfergebnis pro Gate: \`RESTORE\`, \`TMDB_AUTH\`, \`TMDB_REAL_MATCH\`, \`IMAGE_URL_VALIDATION\`, \`SOURCES_FULL\`, \`MAPPING_FULL\`, \`ARTWORK_AUDIT\`, \`DELIVERY_PRIVATE\`, \`KODI_DEVICE\`. Fehlende Gates explizit als BLOCKED.
3. Schreibe Änderungen auf Branch für bestehenden Draft-PR **#7** bzw. dokumentiere technisch notwendige Folgebranches; übernehme die bestehenden Caches/LastGood. Update Issue #6, PR #7 und Kodi #494 mit konkreten, nicht geheimen Evidenzen.
4. **Ziel ist eine vollständig funktionsfähige Privatversion, nicht ein weiterer Teiltest.** Arbeite eigenständig, aber **kein öffentlicher Merge/Release, keine tokenhaltige öffentliche ZIP und keine Aktivierung auf Benutzergeräten ohne gesonderte Abnahme**. Private \`PRIVATE_READY\`-Auslieferung nur, falls sämtliche dazugehörigen Gates erfolgreich und Rechte geklärt sind. Andernfalls klare Blockerliste und ehrlicher Fertigstellungsstand.
