# Late runtime regression qualification

Product repair: `5edd114`; tested immutable checkout: `0305674`.

Starting a watched code note omitted the two identity arguments required by
the governed code runner. The native background task raised `TypeError` before
admission. The endpoint now captures the same named execution identity as
Build a change, rejects an unmatched identity, and passes that identity through
the existing current-authority check. It does not grant authority to the
watcher's original creator. Native regressions retain the code protection test
and additionally check the requesting owner and revocation before dispatch.

The installed Preferences page passed a `source` interpolation value into a
translation helper whose first argument was also named `source`. This raised
`TypeError` while rendering the memory policy summary. Translation message and
language arguments are now positional-only, keeping format fields independent
of helper parameter names. Native page rendering and escaped hostile placeholder
content are covered. The catalog's content and deployment configuration are
unchanged.

Four complete files (code operations, code immunity/actions, privacy notice
publication targets, and journey translation) passed **54 tests ordinary and
54 tests with both secure-default switches enabled**. Logs and JUnit are
`audit/root-final-repairs-{ordinary,secure}-02.*`. The notice publication fixture
uses the native current revision before changing Settings. The first ordinary
attempt passed 53 and failed the new revocation fixture because it tried to
remove the final recovery administrator; provisioning a second recovery admin
made that revocation legal without weakening the recovery guard.

Final consolidated qualification is recorded separately.
