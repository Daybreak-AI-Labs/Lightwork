# Apple companion connection settings

Local Swift package shared by the watch and visionOS prototypes. Add this
package to each Xcode app target and link `LightworkConnectionKit`.

The Connection screen saves the dashboard address and token together in one
device-only Keychain record. Existing scheme variables and legacy UserDefaults
are read only until the first successful in-app save; a saved Keychain record
then takes precedence. A successful save removes the legacy plaintext token.
Failed writes preserve the previous connection. Remote addresses require
HTTPS; embedded credentials, query strings, fragments, and redirects are
rejected. The companions continue to issue only read requests.

On macOS with Xcode, run `swift test --package-path apps/apple-companion-core`
from the repository root. The tests use an isolated in-memory storage double;
the real Security/SwiftUI code is compiled but these tests do not exercise a
device Keychain, watchOS input, or visionOS presentation. Full app SDK builds,
device connection changes, and accessibility testing remain separate checks.
