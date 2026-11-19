# EPG Agent – erster vollständiger Privatbetrieb (GitHub-only)

> Historischer Schema-2-Entwurf. Backend #12 und Kodi #499 definieren die aktuelle Ein-Datei-Auslieferung mit Schema 3. [Aktueller Liefervertrag](ISSUE498_PRIVATE_DAILY_DE.md)

**Nutzerentscheidung 19.11.2026: keine Teil-/Demo-V1.** Auslieferung erst nach vollständiger Verarbeitung der drei XMLTV-Quellen, verifizierter privater Senderkatalog-Zuordnung, aller verfügbaren Bildrollen und Senderlogos. Kodi-Provider-EPG und existierende Bildanzeige dürfen nicht schlechter werden. Kein eigener GS-Media-Server.

## Zustandsabgleich

- Backend Draft PR #7, 185.945 gültige Programme, 1.278 qualifizierte Feed-Kanäle, letztes GZIP 17.411.748 B.
- Der letzte vollständige TVMaze-Lauf hat nur 9.566 Sendungsplätze mit Bildern / 23.528 typisierte Bildreferenzen, **kein** TMDB-Live-Match im Export, Artwork-Identitätsaudit nicht abgeschlossen.
- Drive-V2-Checkpoint vollständig owner-only geprüft, 26 Teile, 640.688.709 Bytes, aber in der neuen vertrauenswürdigen Runtime **noch nicht wiederhergestellt**.
- Vollständiger Providerkatalog (2520 Sender laut 16.11.-Snapshot, 519 Provider-EPG-IDs) fehlt im gesicherten Laufkontext; 0 bestätigte Feed↔Gerät-Mappings. Diese 0 blockieren die Nutzerabnahme; nicht per Namensraten auffüllen.
- Privates Ausgaberepo \`artattakk-creator/xcmate-epg-private\`: \`manifest.json\` ist live als **PRIVATE_PENDING**, keine echte XMLTV oder Artwork-Payload vorhanden. Kein öffentlicher Export / Pages.

## V2-Ausgabevertrag für das erste produktive Paket

**Stabile GitHub Contents API** (Token im Authorization-Header, nur read-only auf das separate Ausgaberepo; Tokenwert niemals in Git-Dateien):

- \`https://api.github.com/repos/artattakk-creator/xcmate-epg-private/contents/manifest.json\`
- \`https://api.github.com/repos/artattakk-creator/xcmate-epg-private/contents/xcmate-epg.xml.gz\`
- \`https://api.github.com/repos/artattakk-creator/xcmate-epg-private/contents/xcmate-artwork.jsonl.gz\`

Der **Backend-Publisher muss ein Delivery-Adapter für diesen festen 3-Datei-Vertrag** erhalten. Der aktuelle \`xcmate_epg/full_export.py\`-Manifestvertrag mit \`status=PARTIAL_COVERAGE\`, \`file=versions/<sha>/xcmate-epg.xml.gz\`, \`channels\`/\`programmes\` ist **nicht direkt kompatibel** mit dem clientseitigen GitHub-Delivery-Manifest. Nicht blind umbenennen; bewusst adaptieren und alle Hash/Generationswerte prüfen.

Erst nach vollständigem privaten Lauf und QA \`PRIVATE_READY\`, \`schema_version=2\`, \`file=xcmate-epg.xml.gz\`, \`bytes\`, \`sha256\`, \`channel_count\`, \`programme_count\`, \`source_count >= 3\`, \`generated_at_utc\`, und Pflichtobjekt:
\`\`\`json
{"artwork":{"file":"xcmate-artwork.jsonl.gz","bytes":1,"sha256":"<64 lowercase hex>","records":1}}
\`\`\`
Die Zahlen 1 sind *nur* Schema-Typbeispiele, keine reale Datenbehauptung. Die Zieldatei \`xcmate-artwork.jsonl.gz\` muss alle verifizierten typisierten Bildreferenzen im bestehenden \`live_artwork.py\`-Format enthalten (poster/backdrop/episode_still/thumb/channel_logo etc., Quelle, Scope, Nachweis/Attribution). Einzelne fehlende Rollen werden gezählt; keine erfundenen Bilder. Der **XCMate-Client** hat bereits einen nur im Draft-PR #494 vorhandenen Staging-/Integritätsprüfbaustein; echte SQL-/Kodi-Integration folgt.

Die **GitHub-Lese-Zugangsdaten** kommen nach Nutzerentscheid per privatem autorisiertem ZIP-Build automatisch ins Add-on. Der Leser braucht keine Token-Eingabe, kennt aber nur GitHub, nicht gs-media.eu. Ein verteiltes ZIP offenbart den eingebetteten Token; ausschließlich für hierfür autorisierte Feed-Daten nutzen; separate Rotation/Sperrung/Verteilrechte.

## Produktionsgates, strikt vollständig

1. **RESTORE_PASS:** Drive-Checkpoint im privaten neuen Workspace vollständig verifiziert (alle 964 Inventarhashes / zehn SQLite-DBs / Original-/LastGood unverändert).
2. **SOURCES_PASS:** Drei aktuell verfügbare XMLTV-Feeds vollständig erfasst, qualifizierte IDs/ETags, Inhaltsaudit, Originalprogramme ohne unbegründete Verluste. Kein willkürlicher kleinerer Sender- oder Zeitfenster-Scope.
3. **MAPPING_PASS:** Vollständiger Geräte-/IPTV-Senderkatalog aus privatem, autorisiertem Export; IDs mit exakter Quelle und Zeitfenster belegt; jede nicht zuordenbare Station/ambige Alias unter klaren Statusflag. 0 bestätigte Verknüpfungen bleibt No-Go.
4. **ARTWORK_PASS:** Alle **verfügbaren/verlässlich passenden** TMDB-/TVMaze-/XMLTV-Poster, Backdrops, Episodenbilder, Senderlogos, BINGIE-Metadaten anreichern. TMDB Live-Backend-Run (API-Smoke alleine reicht nicht), unabhängiger Matching-Audit, lizenzierte Hosts und Attribution; nicht behaupten, es gebe Bilder zu jeder Sendung.
5. **DELIVERY_PASS:** Exakte Bytes/SHA256/GZIP/XMLTV/JSONL, identische Generation, Manifest **als letztes** hochladen. Reproduzierbarer privater authenticated Readback, anonymer 404, Persistenz von LastGood, Budget-/Rechteprüfung. Keine historischen Gigabyte-Artefakte täglich im Repo.
6. **CLIENT_PASS:** Kodi PR #494 vollständig in Streaming-Importer / lokal gespeicherte exakte Mappings / Bilder-Sidecar / EPG-Opt-in / Profil- und Token-Persistenz / Rollback integrieren. Echte LG Kodi22 + Android21 Tests; vorherige Mini-/Groß-EPG-Bilder dürfen nicht verschwinden.

**Nur SOURCES_PASS + MAPPING_PASS + ARTWORK_PASS + DELIVERY_PASS + CLIENT_PASS => PRIVATER GO-LIVE**, sonst \`PRIVATE_PENDING\`. Kein öffentlicher XMLTV-Mirror, kein Cron, kein Prod-Kundentoken oder frühzeitiges Installations-ZIP. Testcode/Draft-PRs dürfen vorher weiterentwickelt werden.

**Verweise:** Backend Issue #6 / Draft-PR #7, Kodi Issue #485 / Draft-PR #494, privates Ausgabe-Repository.
