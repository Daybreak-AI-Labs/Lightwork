# Self-managed demo licenses

The standalone PIA and DSAR demos accept a license signed by a key chosen by
the deployment owner. A license is optional; without a valid one the demo uses
evaluation mode with a limit of five open cases. This is a deployment-owner
control, not a way to enforce a vendor license against the deployment owner.

On a trusted issuer machine with Lightwork and `cryptography` installed, create
an Ed25519 key pair and issue a token. These commands are for a POSIX shell:

```sh
umask 077
python -m maverick.licensing keygen --out-dir ./license-keys
python -m maverick.licensing make --key ./license-keys/license_signing.pem \
  --customer "My deployment" --sku pia_standalone --days 365 > ./license-keys/license.token
```

Use `dsar_standalone` for the DSAR demo. Keep `license_signing.pem` on the
issuer machine, outside the repository and application image. Back it up
securely; anyone with it can issue licenses trusted by that public key. The key
is written unencrypted, so use an encrypted, access-controlled location.

Copy the public key and token to the application host using your normal secure
deployment process. Set `LIGHTWORK_LICENSE_PUBKEY` to the public PEM file path
and `LIGHTWORK_LICENSE` to the token file path, as seen inside the running app.
Both variables also accept their contents directly. If using containers, mount
the files read-only and use their paths inside the container. Restart the demo
to load the new settings. Never mount or share the signing key with the demo.

To rotate the key, issue a replacement token using the new private key and
deploy its matching public key and token together. Removing either setting or
using an expired or invalid token returns the demo to evaluation mode.
