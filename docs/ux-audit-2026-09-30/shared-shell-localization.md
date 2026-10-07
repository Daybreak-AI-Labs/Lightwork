# LW-043 shared shell localization checkpoint

This checkpoint adds 235 explicit product-copy entries, each with French,
German, Japanese and Chinese draft translations. It wires the existing dashboard
and chat shells, every registered navigation destination, finder controls and
empty states, recent-chat navigation, workspace-change recovery warning,
emergency-stop states and confirmations, clipboard feedback, and generic modal
validation. Shared timestamp formatting uses the selected interface locale.

The page finder retains the same server-authorized destinations and indexes both
translated labels and existing English labels/aliases. It does not create routes
or change admission. Workspace changes still block writes. Emergency-stop
confirmation distinguishes this server from connected workers, says work pauses
at its next step, and requires authorized resumption. Existing unknown/partial
status, timeout, permission and verification behavior remains intact.

Workspace names, original chat titles, records, setting metadata and dynamically
supplied form labels remain escaped and isolated with their own language and
direction. Recent-chat filtering reads original titles, excluding translated
pin/archive badges. Translation never changes source text or submitted values.
Arabic receives no new machine-completed catalog: missing regions declare
English, while the selected RTL direction remains intact. The existing partial
translation notice and `CONTENT_LANGS={en}` remain honest.

Local validation:

- 120 distinct Python checks passed: 100 across shell/journey catalogs, portal
  fonts/localization and customer navigation permissions, plus 20 finder, sidebar
  and active-navigation/form checks. Navigation fixtures assert semantic selected
  link text and the existing companion availability gate.
- 61 JavaScript checks passed across shell localization, journey formatting,
  finder keyboard/focus behavior, emergency-stop state/review, drawer behavior,
  forms and plain-language failures. Seven new jsdom scenarios cover localized
  scope confirmations, write blocking, original-content preservation, locale
  formatting and title-only filtering.
- Ruff and Git whitespace checks passed for the changed code.

This is evidence for the shared-shell portion of LW-043, not a claim that every
product body is fully localized or human-reviewed. Native-language and
legal/approval consequence-copy review, actual screen-reader pronunciation and
Arabic keyboard/RTL device qualification remain required. Companion workers own
the onboarding, workflow, chat/inbox/recovery, preferences and voice journeys.
