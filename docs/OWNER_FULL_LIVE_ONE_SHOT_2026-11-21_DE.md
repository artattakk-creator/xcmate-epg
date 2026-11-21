# XCMate EPG: einmaliger vollständiger ECHTDATEN-Produktivlauf (Owner-only)

**Keine Demo:** Der Besitzer verlangt den vollständigen aktuellen EPG aus
EPGShare01 DE, EPGShare01 AT und Kaimandura DE, verifizierte
TMDB-/TVMaze-/XMLTV-Bilder, Senderlogos, echten aktuellen Senderkatalog
und die spätere geschützte EPG-Auslieferung an Android-/LG-Kodi.

## Aktuell bestätigte Blocker (Stand 21.11.2026)

- Letzter echter Codex-Cloud-Lauf: `RESTORE_PASS`, `TMDB_AUTH_200`,
  aber `SOURCE_RIGHTS_NOT_APPROVED` **vor dem Netzwerkabruf**.
  Keine nachgewiesene Redistributability für die Original-EPG-Datensätze
  in einem privaten GitHub-XMLTV-Feed. Kein pauschaler Gate-Bypass!
- Vollständiger aktueller Kodi-Provider-Katalog und bestätigte
  qualifizierte ID-Bindungen fehlen im restaurierten Teilprofil.
- Für die ursprünglich verwendete Codex-Cloud wurden 403 beim GitHub-Proxy
  und fehlender privater Publisher-/Readerzugang berichtet. Die dortige
  Authentifizierung ist nicht gleich der GitHub-Actions-Secret-Bindung.
- Das private GitHub-Hauptmanifest bleibt `PRIVATE_PENDING`, die reale
  `xcmate-epg.xml.gz` fehlt. Die von 21.11. kurz angelegte
  synthetische Demo wurde wieder aus dem aktiven Verzeichnis entfernt.

## Realen Einmallauf in der autorisierten privaten Runtime starten

Voraussetzungen: wiederhergestellter owner-only Workspace; aktuelle 3 echten
vollständigen Quellen; tatsächliche inhaltliche Rechtebelege pro Source-ID
und SHA256; gültiges TMDB-Runtime-Secret; bereinigter vollständiger
privater Geräte-Katalog und bestätigte Mappings; ausreichend Speicher.

Alle Eingabepfade und Secrets nur als geschützte Runner-/Codex-Environment-Bindings,
**nicht** in Repo, Issue, Prompt, Download-URL oder CI-Protokoll:

```sh
python -m tools.owner_full_live_once \
  --config "$XCMATE_PRIVATE_CONFIG" \
  --source-rights "$XCMATE_SOURCE_RIGHTS" \
  --private-root "$XCMATE_PRIVATE_STATE"
```

Der echte 3-Quellen-Import und das Artwork werden über
`xcmate_epg.daily.run(... authorize_network=True)` ausgeführt.
Die Ausgabe berichtet nur nichtgeheime Zähler, Zeitfenster und Gate-Status.
Die lokale echte Universaldatei muss aus dem vollständigen Lauf stammen,
nicht aus einem historischen 88-/192-Sender-Teilprofil, nicht aus einem
synthetischen Fixture und nicht aus einem abgeschwächten Qualitätsgate.

Für die separat nachgewiesene Freigabegeneration kann danach der
**gleiche** Aufruf mit `--delivery-approval "$XCMATE_DELIVERY_APPROVAL"`
und `--dtd "$XCMATE_LOCAL_DTD"` einen lokal geprüften Schema-3-Git-Stand
erzeugen. Der lokale PASS schaltet **keinen** privaten Remote-EPG live:
Publisher-Authentifizierung, GitHub-Payload-Commit, Remote-Readback,
Manifest als letztes, Kodi-Import und Drive-Mirror sind zusätzliche Gates.

**Stopbedingungen:** `SOURCE_RIGHTS_NOT_APPROVED`, 3/3 Quellen nicht
erfolgreich, kein aktueller vollständiger Gerätkatalog, keine bestätigten
Zuordnungen, keine verifizierten Bilder/Logos, DTD-/GZIP-/SHA-/Remote-Fehler.
Provider-EPG und LastGood bleiben. `PRIVATE_READY` nicht fingieren.

## Messwerte nach dem ersten echten Lauf

- Aktuelle IDs und tatsächlicher Eingang: je Quelle Sender/Sendungen,
  GZIP/SHA/ETag, Zeitfenster und 3/3 Erfolgsstatus
- Gesamter Universalbestand, Gerätprofil, Zuordnung bestätigt/unbestätigt,
  Überlappungen, Dubletten und Quellhorizont
- Tatsächliche `poster`-/`backdrop`-/`still`-Rollen, echte gültige
  Bild-URLs, bestätigte Senderlogos; Artwork-Stichproben in Kodi
- Schema3-DTD, bytes+sha256, `payload_ref`, privater Remote-Readback,
  anonymer Zugriff verweigert, Kodi Android/LG getestet
- Drive-Spiegel identische SHA + schrittweiser Rollback. Danach erst
  24h-Zeitplan und weitere echte Läufe.

Hauptkoordination: https://github.com/artattakk-creator/xcmate-epg/issues/14
Kodi-Quelle: https://github.com/artattakk-creator/kodi-noodleprivate/issues/504
