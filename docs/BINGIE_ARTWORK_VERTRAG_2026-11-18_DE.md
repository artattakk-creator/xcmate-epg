# XCMate EPG Agent – typisierter Artwork-Vertrag für BINGIE MOD und Sendungsdetails

**Status:** Verbindliche Benutzeranforderung vom 18.11.2026; Spezifikation, **noch keine implementierte Kodi-Abnahme**.
**Codebasis:** Draft-PR #7 / Issue #6, Branch `feat/full-epg-coverage-artwork-issue6`.
**Client-Design:** `artattakk-creator/kodi-noodleprivate` Issue #485 / Draft-PR #486.
**Keine Freigabe** für Merge, öffentl. XMLTV-Export, bezahlte APIs, automatischen Produktionsjob oder direkte Kodi-/Installer-Publikation.

## 1. Produktziel

Der EPG-Agent soll für eine eindeutig zugeordnete Sendung **möglichst alle rechtlich und technisch zulässigen, tatsächlich verfügbaren Bildrollen** bereitstellen: **Poster (Hochformat), Backdrop/Fanart (Querformat), Episodenstill/Thumb, Senderlogo**; optional, soweit verfügbar und sauber belegbar: Staffelposter, Serienposter, Clearlogo. Eine Rolle darf fehlen; niemals Bilder oder Identitäten erraten.

Der Client soll diese Rollen ohne erneute globale Suche unmittelbar für die **BINGIE-Startseite und XCMate-EPG-Oberflächen** verwenden können. „Direkt mitliefern“ meint **validierte, typisierte Bildreferenzen + optionalen begrenzten Bildcache**, nicht das ungeprüfte Einbetten sämtlicher binärer Bilddateien in einen riesigen XMLTV-Feed. URLs sind keine Garantie für sichtbare Kodi-Pixel.

Die zuerst funktionsfähige Bildanzeige auf Kodi 22/LG webOS im **Mini- und Groß-EPG nach lokalem TMDB-Tokenimport** gilt als **Client-Positivbefund**, nicht als Nachweis für EPG-Agent-Datenübernahme oder BINGIE-Widgets. Sie darf durch die Integration nicht verschlechtert werden.

## 2. Zielansichten / Bildrollen

| Ansicht / UI | Primär | Sekundär / Fallback |
| --- | --- | --- |
| BINGIE Startseite – Live-TV-Hero | bestätigtes `backdrop` / `fanart` (landschaftlich) | bestätigtes `episode_still`/Landscape-Thumb, andernfalls Poster nur als bewusst formatangepasster Hintergrund, danach Senderlogo/neutraler Fallback |
| BINGIE Film-/Serien-/Live-TV-Kacheln | `poster` für Hochkant-Poster-Kachel | Serien-/Staffelposter, `episode_still` bzw. geeigneter Zuschnitt eines Backdrops, Senderlogo/Platzhalter |
| XCMate Sendungsdetail – Hintergrund | `backdrop` / `fanart` | Episode-Still; **Poster als Notfallback nur per sicherem Layout (z. B. zentriert/abgedunkelt/blurred), nicht gestreckt** |
| XCMate Sendungsdetail – Zusatzposter | Film-/Serien-/Staffel-`poster` | passender Poster-Fallback, sonst ausblenden |
| Mini-EPG / großes EPG | geeignetes `episode_still`, `thumb` oder `poster` gemäß vorhandener UI | andere korrekt gematchte Bildrolle bzw. Senderlogo |
| Senderliste / Navigationsleiste | bestätigtes `channel_logo` | vorhandenes lokales Senderlogo, sonst Text |

Bildrollen sind **nicht austauschbare Metadaten**: Ein als Poster geliefertes Hochkantbild bleibt als `poster` klassifiziert, auch wenn der Kodi-Renderer es im Hintergrund darstellt. Dasselbe URL-Asset darf mehrere Rollen innehaben, muss dann für jede Rolle seine belegte Identität/Eignung behalten.

## 3. Datenvertrag (Versionierung und Provenienz)

Neben der XMLTV-kompatiblen `<programme><icon src="..."/></programme>`- bzw. `<channel><icon .../></channel>`-Ausgabe ein **versioniertes, privates, typisiertes Artwork-/Metadaten-Sidecar** je Programm/Kanal aufbauen bzw. die bereits implementierten JSONL-Records erweitern. **Kodi darf die Bildrolle nicht aus der Reihenfolge mehrerer XMLTV-`icon`-Tags raten.**

Bestehende `live_artwork.py`-Records enthalten bereits `type`, `url`, `provider`, `provider_id`, `verified`, `attribution`, `evidence`, `channel`, `start`, `stop`; diese Informationen erhalten. Ergänzende, eindeutig dokumentierte Felder/Semantik:

- `schema_version`, `programme_key` bzw. stabile quellenqualifizierte (Sender-ID, Start, Stop, Sendungs-Identität) und optional `media_identity` einschließlich `movie|series|season|episode`;
- `poster_url`, `backdrop_url`, `episode_still_url`, `thumb_url`, `channel_logo_url` **als abgeleitete Client-Sicht**, jeweils null/fehlend wenn nicht verifiziert;
- pro tatsächlich vorhandenem Bild `role`, `scope`, `provider`, `provider_id`, `source_kind`, `confidence/identity_evidence`, `attribution`, `verified_at`, ggf. bekannte `width`/`height`/`mime` und verfügbare Auflösungen;
- `artwork_status` inklusive `AVAILABLE`, `PARTIAL`, `NO_SAFE_MATCH`, `SOURCE_DISABLED`, `AUTH_PENDING`, `TEMPORARILY_UNAVAILABLE`; Zahlen nach Rolle/Quelle und Gründe im Qualitätsbericht;
- Senderlogos **separat** und nur bei bestätigter Provider↔Feed-Zuordnung; Provider-/Portal-ID bleibt privat, niemals im öffentlichen Gesamt-XMLTV.

**Kompatibilität:** Bestehendes XMLTV und die derzeitigen Sidecar-Verbraucher nicht brechen; neue Client-Sicht nur nach dokumentierter Schema-/Rollback-Regel. Für 304/ETag/SHA/LastGood müssen XMLTV und Sidecar als zueinander passendes, atomar versioniertes Bundle behandelt werden. Keine fälschlich neuen Daten, wenn Eingaben/Matches unverändert sind.

## 4. Identitäts-, Quellen- und Fallbackregeln

1. **Pro Bildrolle getrennt** nach sicher bestätigten TMDB-/TVMaze-/zulässigen XMLTV-Bildern suchen. Bevorzugte Quelle: **TMDB → TVMaze → geprüfte XMLTV-Quelle → bestätigtes Senderlogo**. Ein TMDB-Treffer ohne Backdrop darf einen **belegt passenden** TVMaze-Backdrop erhalten; Anbieteridentitäten nie ohne Evidenz vermischen.
2. Filme: Titel + Jahr + Medientyp konservativ bestätigen. Serien: Serienidentität; Episodenstills nur bei belegter Staffel-/Folgenidentität. Das Serienposter kann in Episode-Widgets dienen, darf nicht als konkretes Episodenstill deklariert werden.
3. TVMaze `background` aus geprüfter Galerie sowie vorhandene `poster`/`episode_still` weiterverwenden. TMDB-Film-/Serien-/Episoden-/Staffelbilder und unabhängige Größenwahl ergänzen, sobald der **authentifizierte Livezugriff** nachweislich funktioniert. Keine 100-%-Bildquote behaupten.
4. Vorhandene lokale Nutzerzuordnungen, manuell bestätigte Bilder und LastGood haben Vorrang vor unsicheren automatischen Vorschlägen; keine Bildwechsel bei flackernden/temporär fehlerhaften API-Antworten.
5. Sicherheits- und Lizenzprüfung: nur freigegebene HTTPS-Bildhosts und zulässige konkrete Pfade, keine Redirects zu fremden Hosts oder Query-Tokens, kein Bearer an Bild-CDN; TMDB-/TVMaze-Attribution und Nutzungs-/Weitergaberechte einhalten. Geprüfte URL, MIME/Größe/Hash/strukturelle Medienprüfung dokumentieren; später tatsächliche Pixel-/Kodi-Abnahme.

## 5. Ladepriorität und speichersparende Darstellung

- **Kodi-Client:** vorhandenes Provider-EPG sofort → lokal gespeicherte Senderlogos → erstes sichtbares Live-Hero (maximal **fünf** priorisierte Hero-Bilder im dauerhaften Cache) → sichtbare Mini-/Groß-EPG-/Detailbilder → übrige Film-/Seriencover auf Anforderung.
- Kein globales gleichzeitiges Herunterladen aller Poster/Backdrops. Begrenzte Worker/Netzwerk-/Cachebudgets, Lazy Load und passende Bildauflösungen; flackerfreie Texturen beim Sender-/Gruppenwechsel und nach Neustart.
- Artwork-URL-Modus auch bei wenig freiem Gerätespeicher funktionsfähig; keine pauschale 1–6-GiB-Reserve. Falls Bild lokal gespeichert werden soll: ausdrücklich optionale, überprüfte Speicherwahl (intern/USB/NAS), Cache-Limits, TTL, LRU/LastGood/atomare Schreibvorgänge.
- **Backend:** Metadaten- und negative Cacheeinträge inkrementell wiederverwenden, nur geänderte Sendungen/Identitäten neu bearbeiten. Qualität nicht durch unbegrenzte API-Requeries oder unrealistische Erwartungen an 20-Tage-Quellhorizont erkaufen.

## 6. Bestehender Stand – nicht als neue Implementierung ausgeben

PR #7 hat bereits eine sichere Artwork-Pipeline unter `xcmate_epg/live_artwork.py` mit `ArtworkEnricher`, TVMaze-Galerie-Backdrop, Poster und Episodenstill, typisierten Records, Bildprüfung und XMLTV-`icon`-Ausgabe; `xcmate_epg/artwork.py` und `xcmate_epg/pipeline.py` bilden ältere kompatible Pfade ab. **Weiterentwickeln statt neu schreiben.**

Letzter dokumentierter privater Gesamtexport: 1.278 quellenqualifizierte Sender, 185.945 gültige Sendungen, 9.566 Sendungsplätze mit Bildern (**5,145 %**), typisiert 9.554 Poster-, 8.078 Backdrop- und 5.896 Episodenbildreferenzen. Die Bilder beruhen auf TVMaze; **TMDB_LIVE_PENDING**. Providerlogos: 88 lokal verifiziert, **0 bestätigte Feed-Zuordnungen und 0 exportierte Senderlogos**. Unabhängiger Identitätsaudit **AUDIT_INCOMPLETE**. Ein Gerät- oder vollständiger Artwork-PASS ist nicht erreicht.

Der lokal verifizierte private Checkpoint aus 964 Dateien / 640.688.709 Bytes ist **noch nicht verifiziert in eine neue Umgebung übertragen**. Die alten Caches/Rohdaten/LastGood bleiben geschützt. Dokumentationsänderung bedeutet **keine** Freigabe für einen neuen Netz-/Artwork-Vollrun oder ungeschützten Datenexport.

## 7. Akzeptanztests / Codex-Lieferung

1. Synthetische Film-, Serie- und Episodenfälle: alle Bildrollen vorhanden; fehlendes Backdrop bei vorhandenem Poster; falsche Episode/gleichnamige Serie abweisen; unabhängiger TMDB/TVMaze-Fallback je Rolle, Datensatz bleibt deterministisch.
2. Typed JSONL/Sidecar ↔ XMLTV stimmt für Programm/Sender/Zeit/Quell-ID; mehrfache gleichlautende URLs bleiben rollenqualifiziert, XMLTV dedupliziert nur URLs; altes XMLTV weiter parsebar, Sidecar-Version rückwärtskompatibel oder fail-closed.
3. Image-Policy / Bilddekodierungs- und Quellenidentitätsprüfung, kontrollierte 401/403/429/404/Timeout, negative Caches, unveränderte ETag/SHA-Ketten und LastGood/rollback.
4. Senderlogo-Fälle: confirmed/candidate/unmapped; nie blind nach Namen wählen, keine privaten Portalidentitäten in offenen Artefakten.
5. Renderer-Vertrag in **Kodi-Repository** dokumentieren/testen: BINGIE-Hero bekommt querformatiges Backdrop; Widgets Poster; Sendungsdetails Backdrop mit Poster als Fallback; Mini-/Groß-EPG bleiben funktionsfähig. **Echte** Bildsichtbarkeit auf LG webOS/Kodi 22 und Android/Kodi 21 getrennt abnehmen. Cloud-/Host-/Linux-Erfolg ≠ Gerät-PASS.
6. Bericht: Bildquote je Typ/Provider, Senderlogoquote, belegte Fallbackpfade, neue Tests und offene P0-Risiken. Codeänderungen nur in nachfolgendem eigenen Draft-PR/Arbeitsauftrag; dieses Dokument selbst ist **Spezifikation**.

**Release-Schranken:** kein Merge, kein öffentliches XMLTV oder Quellmaterial, kein Produktions-Scheduler, keine Offenlegung von TMDB-Token, Kodi-Portalen/IDs oder Rohfeeds, keine kostenpflichtigen APIs und kein erzwungener Massenbilddownload.
