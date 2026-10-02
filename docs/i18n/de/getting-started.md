<!-- Von der Community gepflegte Übersetzung von docs/getting-started.md (Quell-Commit: a7b9cee) — die englische Version ist maßgeblich. -->

# Erste Schritte

<!-- Installation section synchronized with docs/getting-started.md at e19f502. -->

## Installation

Verwenden Sie Python 3.10–3.12 und den vollständigen SHA eines geprüften Commits. Die acht eigenen Pakete sind noch nicht als vollständiger Satz auf PyPI veröffentlicht; installieren Sie sie gemeinsam aus dem Repository über den Manifest-Installer. Dazu gehören Dashboard, Assistent, Kanäle, MCP-Server sowie Wissens- und Lernpakete. `release-runtime` liefert den OpenAI-kompatiblen Client, `evidence-analysis` die PDF- und Tabellenparser.

macOS / Linux:

```bash
git clone https://github.com/Daybreak-AI-Labs/Lightwork
cd Lightwork
git checkout --detach <reviewed-full-40-character-commit-sha>
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade 'pip==26.2.1'
python scripts/install_release_cohort.py --core-extra release-runtime,evidence-analysis
lightwork init
lightwork dashboard
```

Unter Windows PowerShell ersetzen Sie zuerst den Commit-Platzhalter:

```powershell
git clone https://github.com/Daybreak-AI-Labs/Lightwork
Set-Location Lightwork
git checkout --detach <reviewed-full-40-character-commit-sha>
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip==26.2.1
.\.venv\Scripts\python.exe scripts/install_release_cohort.py --core-extra release-runtime,evidence-analysis
.\.venv\Scripts\lightwork.exe init
.\.venv\Scripts\lightwork.exe dashboard
```

Die vollständigen Programmpfade erfordern keine Änderung der PowerShell-Aktivierungsrichtlinie. Konfigurieren Sie Anbieter und Sandbox im Assistenten. Die Quellinstallation stellt keine externen Konten, API-Guthaben, Container-Engine oder Kubernetes-Cluster bereit. Prüfen Sie einen tatsächlich autorisierten Vorgang, bevor Sie eine Integration für Kunden einsetzen.

Prüfen Sie den Paketsatz mit Python aus derselben Umgebung:

```bash
python scripts/install_release_cohort.py --verify-only
python -m pip check
lightwork version
lightwork doctor
```

Verwenden Sie unter Windows `.\.venv\Scripts\python.exe` und `.\.venv\Scripts\lightwork.exe` für diese Programme.

Für den authentifizierten Desktop-Bootstrap beziehen Sie `deploy/desktop/install.sh` oder `deploy/desktop/install.ps1` aus dem geprüften Commit und setzen `MAVERICK_REF` auf dessen vollständigen SHA in Kleinbuchstaben. Dies bleibt eine Quellinstallation mit Repository-Zugriff und Plattformvoraussetzungen; sie ist kein eigenständiger signierter Produktinstaller. Die Skripte lehnen veränderliche Referenzen ab und verwenden keinen öffentlichen Index als Ersatz für eigene Pakete. Jede Variable `MAVERICK_*` kann auch `LIGHTWORK_*` heißen.

## Erster Start

```bash
maverick init
```

Der Assistent dauert etwa 2 Minuten. Er schreibt `~/.maverick/config.toml` und `~/.maverick/.env`.

Danach:

```bash
maverick start "Build a CLI that emails me a digest of today's top Hacker News stories — research the API, write it, and verify it runs"
```

## Dem Schwarm beim Zerlegen zusehen

Führen Sie `maverick monitor` in einem zweiten Terminal aus. Der Orchestrator plant das Ziel und startet dann spezialisierte Sub-Agenten, die parallel arbeiten — hier grenzt ein Researcher die API ein, ein Coder schreibt das Tool, und ein Verifier führt es aus:

```
Goal #1 active  2m elapsed
Build a CLI that emails me a digest of today's top Hacker News stories

Plan tree
  ├─        done  #2 Research the Hacker News Firebase API
  ├─      active  #3 Write the digest CLI (fetch + format + send)
  ├─      active  #4 Verify it runs and emails a sample digest
  ├─     pending  #5 Write a short usage README

Latest episode #7 (running)  $0.0431  in=18,204 out=2,910 tools=11

Recent activity
  4s ago [researcher] decision: top stories live at /v0/topstories.json, then /v0/item/<id>.json
  3s ago [coder] tool_call: write_file hn_digest.py (118 lines)
  1s ago [verifier] tool_call: run "python hn_digest.py --dry-run" -> printed 10 stories

Cumulative spend on this DB: $0.21
```

Wenn alles fertig ist:

```bash
maverick status      # what's currently active or blocked
maverick skills      # what the swarm distilled from this run
maverick facts       # what it learned about you
```

## Pausieren / Fortsetzen

Braucht der Schwarm etwas, das nur Sie beantworten können, pausiert er und stellt eine Frage in die Warteschlange:

```bash
maverick status
# shows: open questions: #3 (goal 1): Which dates are you traveling?

maverick answer 3 "May 15-29"
maverick resume
```

Ziele überleben Neustarts. Sie können Ihren Laptop zuklappen und morgen weitermachen.

## Dateiänderungen eines Laufs rückgängig machen

Mit aktiviertem Workspace-Journal (`[workspace] journal = true`, oder mit Ja
im Assistenten-Schritt *Workspace journal*) wird jede Datei, die die Agenten
über die Datei-Tools überschreiben oder anlegen, vor der Änderung
festgehalten:

```bash
maverick journal list --goal 12     # was angefasst wurde
maverick journal diff --goal 12     # Zustand vor dem Lauf vs. Festplatte jetzt
maverick journal revert --goal 12   # stellt jede Datei wieder her, neueste zuerst
```

Ein Revert schreibt eine signierte `workspace_revert`-Auditzeile und
archiviert das Journal. Änderungen über die Shell (`rm`, `mv`, Umleitungen)
werden nicht journaliert — der Audit-Trail zeichnet diese Befehle auf.

## Aus einer beobachteten Aufgabe einen eigenen Spezialisten bauen

Sie müssen eine Aufgabe nicht in Worten beschreiben — Sie können sie auch
vorführen. Erfassen Sie eine geordnete Aufzeichnung davon, wie jemand die Arbeit
erledigt (die ausgeführten Aktionen und jede Erläuterung des Warum), als JSONL
oder als einfachen, mit Präfixen versehenen Text, und übergeben Sie die Datei
dann an Lightwork:

```
ACTION[gmail]: send the morning digest -> ops@acme.com
NOTE: only the top 5 stories, with one-line summaries
SEE: digest looks right, ops confirmed receipt
```

```bash
maverick learn-demo demo.txt
```

Dies parst die Vorführung, leitet daraus einen Entwurf für einen Spezialisten
ab, zeigt Ihnen den abgeleiteten Workflow und wartet auf Ihre Freigabe, bevor
gespeichert wird. Secrets werden direkt an der Tür unkenntlich gemacht, und der
Entwurf erbt dieselbe Capability-Begrenzung und denselben Persona-Scan wie ein
beschriebenes Pack — nichts wird ohne Ihr Ja aktiviert. Nützliche Flags:

```bash
maverick learn-demo demo.txt --name "Morning Digest" --no-llm --yes
```

`--no-llm` bildet die beobachteten Schritte deterministisch nach (Tools = das,
was die Person verwendet hat); lassen Sie es weg, damit das Modell aus dem
Transkript Vorschläge macht.

Derselbe Agent-Factory-Ablauf läuft, wenn Sie ein Pack im Dialog aufbauen:

```bash
maverick onboard
```

Nach der Freigabe stellt `onboard` das Pack nun bereit — es installiert die
Katalog-Skills, die sein Workflow benötigt, und synthetisiert alle deklarierten
Tools, die nicht von Haus aus vorhanden sind, sodass ein frisch freigegebener
Spezialist von der ersten Ausführung an für seine Aufgabe ausgerüstet ist.
(Dieser Schritt berücksichtigt die Konfiguration `[self_learning]` /
`provision_packs` und weitet die abgeklemmte Hülle des Packs niemals aus.)

## Spracheingabe (eingebaut, offline)

Der Mikrofon-Button im Dashboard und das Tool `transcribe_audio` funktionieren
ohne Provider-Schlüssel: Die lokale Whisper-Engine wird mit dem Dashboard-Paket
ausgeliefert, beim Öffnen der App werden Modelldateien aber standardmäßig nicht
heruntergeladen. Unter **Settings → Models** kann ein globaler Administrator
im Bereich für Sprachdiktat Quelle und Größe des konfigurierten Modells (beim
Standardmodell etwa 148 MB) prüfen und es kontrollieren oder herunterladen.
Der Fortschritt bleibt nach einem Neuladen der Seite erhalten; ein Abbruch
entfernt den unvollständigen Download, und Fehler lassen sich dort erneut
versuchen. Vorhandene Dateien gelten erst nach Prüfung von genauer Größe und
Prüfsumme als verifiziert. Das belegt weder, dass die Sprach-Engine geladen
ist, noch, dass das Mikrofon funktioniert.

Der Installer fragt ausdrücklich, ob Downloads beim Start erlaubt sind. Eine
gespeicherte Einstellung `[voice] auto_fetch_model = true` oder
`MAVERICK_VOICE_AUTO_FETCH=1` aktiviert sie weiterhin; die Netzwerkrichtlinie
gilt auch für Weiterleitungen. Verwaltete Pfade zu eigenen Modellen und ein
gewähltes Cloud-Sprachbackend bleiben erhalten. Die optionale CLI ist ebenfalls verfügbar:

```bash
maverick voice setup    # lädt das checksummen-geprüfte Modell (~148 MB)
maverick voice status   # zeigt, welche Speech-to-Text-Backends nutzbar sind
maverick voice transcribe clip.wav   # Schnelltest der Pipeline
```

Lieber eine System-Engine? Ein `whisper.cpp`-Binary im PATH
(`brew install whisper-cpp`) oder faster-whisper fügen sich in dieselbe
Kette ein. Mit `[voice] stt_backend = "local"` bleibt Audio auch dann auf der
Maschine, wenn OpenAI/Groq-Schlüssel konfiguriert sind.

## Modelle oder Provider wechseln

Öffnen Sie nach der Installation **Settings → Provider keys** oder
**Settings → Models**, um diese Werte zu ändern. Die Seite kennzeichnet Werte,
die vom Deployment vorgegeben werden; ein Installationsadministrator muss
diese Quelle ändern, bevor ein neuer Wert wirksam wird. Die zugrunde liegende
Konfiguration ist in [`configuration.md`](../../configuration.md) beschrieben.

## Wo die Daten liegen

| Datei | Inhalt |
|---|---|
| `~/.maverick/config.toml` | Ihre Konfiguration (Deployment, Modelle, Sicherheit, Budget) |
| `~/.maverick/.env` | API-Schlüssel (chmod 600) |
| `~/.maverick/world.db` | Persistentes Weltmodell: Ziele, Fakten, Episoden |
| `~/.maverick/skills/` | Automatisch destillierte SKILL.md-Dateien aus erfolgreichen Läufen |
| `~/maverick-workspace/` | Standard-Arbeitsverzeichnis der Sandbox |
| `~/.maverick/learned-skills/` | Von den Lernschleifen destillierte Skills |
| `~/.maverick/dreams/` | Konsolidierte Erkenntnisse, Probelauf-Warteschlange, Lern-Snapshots |

Lightwork speichert die oben genannten Dateien in Ihrem Deployment. Wenn Sie
Cloud-KI-Provider oder verbundene Dienste aktivieren, kann die an sie
gesendete Arbeit das Deployment verlassen. Prüfen Sie jeden Provider,
Connector und jede Egress-Richtlinie vor der Nutzung.

Sobald Sie ein paar Läufe hinter sich haben, besteht die Lernoberfläche aus vier
Befehlen: `maverick dream` (Erfahrung konsolidieren), `maverick hindsight` (hat
das Lernen geholfen oder zu einer Verschlechterung geführt?), `maverick proof`
(Ergebnisse, vermiedene Kosten, ROI) und `maverick domains-lint` (den Katalog
der 2,020 Spezialisten-Agenten prüfen), dazu `maverick domains-audit`
(Governance-Lage: was jeder Agent erreichen, verweigern und ablehnen kann) und
`maverick domains-eval --check` (verhaltensbezogene Golden Cases).
