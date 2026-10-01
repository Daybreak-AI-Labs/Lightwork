# Audit implementation checklist

Generated from the complete 195-entry register. Built means code is integrated;
it does not mean all acceptance checks or release qualification have passed.

[Full evidence and remaining criteria](implementation-tracker.json) · [Original 24-field backlog](source-backlog.json) · [Local qualification](local-qualification.md)

Current dispositions: 189 implemented-test-needed, 1 planned, 5 test-needed.

| ID | Disposition | Audit item |
| --- | --- | --- |
| LW-001 | Built; acceptance pending | Keep conversation responsive while governed tasks run |
| LW-002 | Built; acceptance pending | Graduate page-help Copilot into a shared product operation interface |
| LW-003 | Built; acceptance pending | Stop silently dropping attachments on clarification answers |
| LW-004 | Built; acceptance pending | Give each employee a private background-work control center |
| LW-005 | Built; acceptance pending | Join Action Center and task questions into one actionable inbox |
| LW-006 | Built; acceptance pending | Preserve failed Copilot questions and context safely |
| LW-007 | Built; acceptance pending | Stop disappears while a task waits for the user |
| LW-008 | Built; acceptance pending | Failed and cancelled tasks cannot reach their existing restart control |
| LW-009 | Built; acceptance pending | Reconnecting live streams can fail silently forever |
| LW-010 | Built; acceptance pending | Clarification fetch failures silently remove the useful next step |
| LW-011 | Built; acceptance pending | Accepted cancellation has no acknowledgement or cleanup state |
| LW-012 | Built; acceptance pending | Generic error wording hides the actual remedy |
| LW-013 | Built; acceptance pending | Long conversation history disappears without an explanation |
| LW-014 | Built; acceptance pending | File selections have no remove/review controls |
| LW-015 | Built; acceptance pending | Adding a file to an initially fileless task is hidden |
| LW-016 | Built; acceptance pending | Partial multi-file upload failures have no selective retry |
| LW-017 | Built; acceptance pending | Add conversation organization without deleting audit evidence |
| LW-018 | Built; acceptance pending | Give live plan polling a bounded lifetime and truthful access/retry state |
| LW-019 | Built; acceptance pending | Add conflict-safe deliverable revisions |
| LW-020 | Built; acceptance pending | Prevent stale and concurrent settings overwrites |
| LW-021 | Built; acceptance pending | Protect unsaved All Settings edits |
| LW-022 | Built; acceptance pending | Do not erase edits typed while saving |
| LW-023 | Built; acceptance pending | Save main Settings sections without discarding other sections |
| LW-024 | Built; acceptance pending | Make preference changes previewable without navigation |
| LW-025 | Built; acceptance pending | Chat drafts lack the recovery offered to other editors |
| LW-026 | Built; acceptance pending | Settings-file check and install are not bound to one reviewed change |
| LW-027 | Built; acceptance pending | Settings-file activity claims installation before the write succeeds |
| LW-028 | Built; acceptance pending | Fence duplicate child-task creation in the task map |
| LW-029 | Built; acceptance pending | Protect task-map edits from overwriting another user's current state |
| LW-030 | Built; acceptance pending | Define personal workspace and organization preference scopes |
| LW-031 | Built; acceptance pending | Expose all supported persona styles consistently |
| LW-032 | Built; acceptance pending | Add personal and per-conversation custom output styles |
| LW-033 | Built; acceptance pending | Make the preferred user name truly per-user |
| LW-034 | Built; acceptance pending | Sync safe display preferences across devices |
| LW-035 | Built; acceptance pending | Unify settings discovery across dedicated pages |
| LW-036 | Built; acceptance pending | Show effective changes and runtime impact before saving |
| LW-037 | Built; acceptance pending | Distinguish restore product default from inherit configuration |
| LW-038 | Built; acceptance pending | Portable configuration profiles without secrets |
| LW-039 | Built; acceptance pending | Expose settings history and safe selective rollback |
| LW-040 | Built; acceptance pending | Save and compare outcome-oriented configuration presets |
| LW-041 | Built; acceptance pending | Personal favorites and saved navigation views |
| LW-042 | Built; acceptance pending | Create a configurable daily work home from existing projections |
| LW-043 | Built; acceptance pending | Localize complete employee journeys |
| LW-044 | Built; acceptance pending | Add follow-system appearance and readable text scaling |
| LW-045 | Built; acceptance pending | Make custom themes accessible before applying them |
| LW-046 | Built; acceptance pending | Make read-aloud complete and reliably stoppable |
| LW-047 | Built; acceptance pending | Bind mobile connection test feedback to the tested inputs |
| LW-048 | Built; acceptance pending | Multiple named mobile connections and safe preference parity |
| LW-049 | Built; acceptance pending | Evolve mobile from read-only oversight to a least-privilege decision companion |
| LW-050 | Built; acceptance pending | Every question poll steals keyboard focus |
| LW-051 | Built; acceptance pending | Corrupt saved connection has no UI reset |
| LW-052 | Active implementation | Older tasks unreachable |
| LW-053 | Built; acceptance pending | Event explanation clipped without expand |
| LW-054 | Built; acceptance pending | Online overview omits offline attention counts |
| LW-055 | Built; acceptance pending | Offline readiness depends on Overview; detail ignores cache |
| LW-056 | Built; acceptance pending | No sign-out/cache purge/retention controls |
| LW-057 | Qualification required | Native streaming transport not qualified |
| LW-058 | Built; acceptance pending | Native preferences/large-text/targets need qualification |
| LW-059 | Built; acceptance pending | Android Back/background lifecycle not modeled |
| LW-060 | Built; acceptance pending | Mobile docs use wrong default port |
| LW-061 | Built; acceptance pending | Restore a visible Close menu control on phone chat |
| LW-062 | Built; acceptance pending | Keep focus out of obscured chat content while its mobile drawer is open |
| LW-063 | Built; acceptance pending | Make the task-plan text alternative work with scripts enabled and graph dependencies unavailable |
| LW-064 | Built; acceptance pending | Keep the embeddable widget within narrow viewports |
| LW-065 | Built; acceptance pending | Bring widget interaction and announcements to the dashboard's accessibility baseline |
| LW-066 | Built; acceptance pending | Honor reduced-motion preferences for programmatic scrolling |
| LW-067 | Built; acceptance pending | Compose template search and suggested filters |
| LW-068 | Built; acceptance pending | Typed workflow-template inputs |
| LW-069 | Built; acceptance pending | Promote workflows through draft, test, version and publish |
| LW-070 | Built; acceptance pending | Accept ordinary office documents when drafting workflows |
| LW-071 | Built; acceptance pending | Make schedule-list failures visible and preserve last good state |
| LW-072 | Built; acceptance pending | Promote schedules from pending queue entries to durable managed objects |
| LW-073 | Built; acceptance pending | Complete in-app recurring-job recovery without unsafe replay |
| LW-074 | Built; acceptance pending | Make workflow templates earn trust through measured outcomes |
| LW-075 | Built; acceptance pending | Do not render Connections outages as an empty vault |
| LW-076 | Built; acceptance pending | Show connection test freshness and coverage |
| LW-077 | Built; acceptance pending | Allow employees to connect approved own accounts |
| LW-078 | Built; acceptance pending | Preview connection dependencies and guide safe rebinding |
| LW-079 | Built; acceptance pending | Publish a qualified OAuth/platform support matrix |
| LW-080 | Built; acceptance pending | Guide connection setup by the job and required capabilities |
| LW-081 | Built; acceptance pending | Add a governed extension qualification workbench |
| LW-082 | Built; acceptance pending | Conflict recovery can overwrite another administrator's connection edits |
| LW-083 | Built; acceptance pending | Editing a saved non-preset connector can erase its tool binding |
| LW-084 | Built; acceptance pending | “Not working” conflates bad credentials with unproven or unreachable readiness |
| LW-085 | Built; acceptance pending | Key connection requests allow duplicate submissions and have no bounded waiting state |
| LW-086 | Built; acceptance pending | Account management is fragmented across Connections and Automations |
| LW-087 | Built; acceptance pending | Connector help still directs normal setup to terminal/environment editing |
| LW-088 | Built; acceptance pending | AWS connection region picker excludes backend-admitted regions without an escape path |
| LW-089 | Built; acceptance pending | Replace extension setup's configuration-file instructions with the existing in-product setting |
| LW-090 | Built; acceptance pending | Show and explain exactly what “Send this page” will share |
| LW-091 | Built; acceptance pending | Make new role grants least-privileged |
| LW-092 | Built; acceptance pending | Separate resetting role from removing access |
| LW-093 | Built; acceptance pending | Scope Users page to tenant admin authority |
| LW-094 | Built; acceptance pending | Create complete employee departure workflow |
| LW-095 | Built; acceptance pending | Define and enforce admin audit-metadata-only default |
| LW-096 | Built; acceptance pending | Explain effective permissions and their owner |
| LW-097 | Built; acceptance pending | Make enterprise first-login defaults explicit |
| LW-098 | Built; acceptance pending | Show effective posture, not just profile name |
| LW-099 | Built; acceptance pending | Make multi-tab workspace switches explicit |
| LW-100 | Built; acceptance pending | View-only users receive active mutation controls |
| LW-101 | Built; acceptance pending | Failed SSO ends at a plain-text “login failed” dead end |
| LW-102 | Built; acceptance pending | “Keep until I choose” cannot disable an existing retention period |
| LW-103 | Built; acceptance pending | Retention deletion needs an impact preview tied to saved effective policy |
| LW-104 | Built; acceptance pending | Retention result hides skipped records and partial execution |
| LW-105 | Built; acceptance pending | Stop coworkers' activity evicting personal memory |
| LW-106 | Built; acceptance pending | Resolve contradictory and retracted preferences |
| LW-107 | Built; acceptance pending | Let employees inspect and correct remembered preferences |
| LW-108 | Built; acceptance pending | Offer explicit cross-channel personal preference scope |
| LW-109 | Built; acceptance pending | Separate remembering, organizational reuse and training controls |
| LW-110 | Built; acceptance pending | Let employees report knowledge errors without deletion power |
| LW-111 | Built; acceptance pending | Bring source confidence and freshness into reused answers |
| LW-112 | Built; acceptance pending | Unify my-data export, retention and deletion status |
| LW-113 | Built; acceptance pending | Unify discovery across business metadata and governed knowledge |
| LW-114 | Built; acceptance pending | Make search scale limits useful to employees |
| LW-115 | Built; acceptance pending | Connect governed knowledge selection directly to task context |
| LW-116 | Built; acceptance pending | “Search chats” searches only 25 recent titles |
| LW-117 | Built; acceptance pending | Shared facts can overwrite a newer edit or delete a changed fact |
| LW-118 | Built; acceptance pending | Facts hide provenance, age, history and retention consequences |
| LW-119 | Built; acceptance pending | Facts page does not scale to a large business memory |
| LW-120 | Built; acceptance pending | Knowledge purpose picker only inspects 250 registered datasets |
| LW-121 | Built; acceptance pending | Knowledge picker backend failure masquerades as no approved uses |
| LW-122 | Built; acceptance pending | Knowledge reindex lacks bounded job supervision |
| LW-123 | Built; acceptance pending | Tell the truth about feedback and learning |
| LW-124 | Built; acceptance pending | Make projects useful workspaces, not only task groupings |
| LW-125 | Built; acceptance pending | Turn team review into a complete revise-and-resubmit loop |
| LW-126 | Built; acceptance pending | Make approval quorum progress actionable |
| LW-127 | Built; acceptance pending | Provide requester-scoped decision history |
| LW-128 | Built; acceptance pending | Clarify different kinds of approved/completed |
| LW-129 | Built; acceptance pending | Transfer tasks with selected evidence and explicit acceptance |
| LW-130 | Built; acceptance pending | Package reviewed artifacts for the recipient's actual job |
| LW-131 | Built; acceptance pending | Live completion omits result actions, sign-off and newly produced artifacts |
| LW-132 | Built; acceptance pending | Share-link audience, prerequisites and expiry are not visible before creation |
| LW-133 | Built; acceptance pending | Gist failures incorrectly promise nothing was shared |
| LW-134 | Built; acceptance pending | Gist evidence errors are misreported as missing credentials |
| LW-135 | Built; acceptance pending | Separate stopping a task from rating its quality |
| LW-136 | Built; acceptance pending | Preview the exact history payload before uploading it |
| LW-137 | Built; acceptance pending | Qualify the promised receiving journey for portable task history |
| LW-138 | Built; acceptance pending | “Download full history” is silently partial |
| LW-139 | Built; acceptance pending | Data export needs a readable business handoff alongside technical bundles |
| LW-140 | Built; acceptance pending | Help users choose models by capability and tradeoff |
| LW-141 | Built; acceptance pending | Add personal notification preferences and quiet hours |
| LW-142 | Built; acceptance pending | Show notification delivery health and test results |
| LW-143 | Built; acceptance pending | Show usable budget headroom before work starts |
| LW-144 | Built; acceptance pending | Warn before budgets pause important schedules |
| LW-145 | Built; acceptance pending | Route provider accounting blocks to responsible admin |
| LW-146 | Built; acceptance pending | Add job-specific employee activation paths |
| LW-147 | Built; acceptance pending | Give employees safe task-level diagnostics |
| LW-148 | Built; acceptance pending | Replace recovery offsets with source lookup |
| LW-149 | Qualification required | Qualify actual enterprise employee journeys |
| LW-150 | Built; acceptance pending | Failure pages need a support reference tied to the actual failure |
| LW-151 | Built; acceptance pending | Review support-report content locally before it leaves Lightwork |
| LW-152 | Built; acceptance pending | Make older failed tasks selectable in Help |
| LW-153 | Built; acceptance pending | A different service on the desktop port is treated as Lightwork |
| LW-154 | Qualification required | Establish customer-release evidence for the Windows installer candidate |
| LW-155 | Qualification required | Define and complete the supported non-Windows customer installation journey |
| LW-156 | Built; acceptance pending | Replace manual maintenance authorization transfer with a protected handoff |
| LW-157 | Built; acceptance pending | Add a supported coordinated recovery path when protected rollback is refused |
| LW-158 | Built; acceptance pending | Support a verified repair option during network/update-feed outages |
| LW-159 | Built; acceptance pending | Source-bootstrap cancellation still needs a nontechnical partial-install recovery journey |
| LW-160 | Built; acceptance pending | Monthly billing silently omits records beyond 10,000 |
| LW-161 | Built; acceptance pending | Savings report both truncates volume and treats unreadable history as zero |
| LW-162 | Built; acceptance pending | Savings mixes selected currency with dollar costs |
| LW-163 | Built; acceptance pending | Spend's “never estimated” claim contradicts pricing modes |
| LW-164 | Built; acceptance pending | Stripe execution is not bound to the preview |
| LW-165 | Built; acceptance pending | Stripe failure claims nothing changed after possible external mutations |
| LW-166 | Built; acceptance pending | Invoice display rounds each line independently of total |
| LW-167 | Built; acceptance pending | Single-day audit search filters only the latest page |
| LW-168 | Built; acceptance pending | Invalid audit range dates silently alter the requested search |
| LW-169 | Built; acceptance pending | Shared dashboard allows stale requests to overwrite current range |
| LW-170 | Built; acceptance pending | Spend outcome chart quietly samples only 200 episodes |
| LW-171 | Built; acceptance pending | Spend time range affects only some of the visible report |
| LW-172 | Built; acceptance pending | Embedded analytics presents a latest-100 snapshot as task status totals |
| LW-173 | Built; acceptance pending | Oversight failures render as healthy/empty state |
| LW-174 | Built; acceptance pending | Make cross-ledger cost differences understandable |
| LW-175 | Built; acceptance pending | External-agent sample acts before approval |
| LW-176 | Built; acceptance pending | External-agent sample reports success after halt |
| LW-177 | Built; acceptance pending | REST polling example loses history and can poll forever |
| LW-178 | Built; acceptance pending | Apple clients retain private view after denial |
| LW-179 | Built; acceptance pending | VS Code allows remote HTTP bearer after generic consent |
| LW-180 | Built; acceptance pending | Watch Today can be yesterday's snapshot |
| LW-181 | Built; acceptance pending | visionOS selection masks error and stays stale |
| LW-182 | Built; acceptance pending | visionOS has no accessible alternate tree/size controls |
| LW-183 | Built; acceptance pending | Apple fetch unbounded and refresh not owned |
| LW-184 | Qualification required | Distribution/native qualification unfinished |
| LW-185 | Built; acceptance pending | Pairing and contextual handoff opportunity |
| LW-186 | Built; acceptance pending | VS Code CLI lifecycle weaker than SSE |
| LW-187 | Built; acceptance pending | VS Code first-workspace and numeric-ID friction |
| LW-188 | Built; acceptance pending | VS Code bearer in normal settings |
| LW-189 | Built; acceptance pending | JetBrains env-only setup and silent invalid input |
| LW-190 | Built; acceptance pending | Editor budgets and stop scope need direct controls |
| LW-191 | Built; acceptance pending | Synchronous editor controls may block UI |
| LW-192 | Built; acceptance pending | Main API start guide omits existing receipt recovery |
| LW-193 | Built; acceptance pending | External TS helper lacks timeout/typed recovery |
| LW-194 | Built; acceptance pending | SDK schema declaration isn't argument validation |
| LW-195 | Built; acceptance pending | Capability/build metadata drift |
