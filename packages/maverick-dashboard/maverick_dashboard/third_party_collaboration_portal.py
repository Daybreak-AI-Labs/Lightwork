"""Public shell for invitation-authenticated third-party collaboration.

The page is deliberately tenant-neutral and contains no invitation material.
It loads a same-origin client which keeps the respondent credential in memory
and sends it only in the external API ``Authorization`` header.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

_PORTAL_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{15,127}\Z")
_TENANT_HEADERS = ("x-lightwork-tenant", "x-maverick-tenant", "x-tenant-id")
_STATIC = Path(__file__).with_name("static")

_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>Third-party assurance response | Lightwork</title>
  <link rel="stylesheet" href="portal/assets/styles.css">
  <script defer src="portal/assets/client.js"></script>
</head>
<body>
  <main class="portal-shell" id="portal-shell">
    <header class="portal-hero">
      <p class="eyebrow">Lightwork assurance exchange</p>
      <h1>Third-party assurance response</h1>
      <p class="lede">
        Review and answer only the questions authorized by your invitation.
        Conditional questions are resolved by the server after each draft save.
      </p>
    </header>

    <aside class="boundary" aria-labelledby="boundary-title">
      <h2 id="boundary-title">Security and evidence boundary</h2>
      <ul>
        <li>The invitation credential is held only in this page's memory and is cleared on lock.</li>
        <li>Evidence is represented by a SHA-256 digest manifest; files and file bytes are never accepted here.</li>
        <li>Respondent attestations remain unverified evidence until separately reviewed.</li>
        <li>Submission does not prove evidence completeness, truthfulness, or independent assurance.</li>
      </ul>
    </aside>

    <p class="status" id="portal-status" role="status" aria-live="polite">
      Enter the opaque invitation credential to open this workspace.
    </p>

    <section class="panel" id="unlock-panel" aria-labelledby="unlock-title">
      <h2 id="unlock-title">Open invitation</h2>
      <form id="unlock-form" autocomplete="off" novalidate>
        <div class="field">
          <label for="invitation-credential">Invitation credential</label>
          <input
            id="invitation-credential"
            type="password"
            minlength="48"
            maxlength="48"
            autocapitalize="none"
            autocomplete="off"
            spellcheck="false"
            aria-describedby="credential-help"
            required
          >
          <p class="help" id="credential-help">
            Paste the credential supplied out of band. It is not placed in a URL,
            cookie, or browser storage by this portal.
          </p>
        </div>
        <button type="submit" id="unlock-button">Open secure workspace</button>
      </form>
    </section>

    <section class="workspace" id="workspace" aria-labelledby="workspace-title" hidden>
      <div class="workspace-heading">
        <div>
          <p class="eyebrow">Invitation-scoped contract</p>
          <h2 id="workspace-title">Assessment workspace</h2>
        </div>
        <button class="secondary" type="button" id="lock-button">Lock and forget credential</button>
      </div>

      <dl class="contract-meta" aria-label="Invitation contract details">
        <div><dt>Respondent reference</dt><dd id="vendor-reference"></dd></div>
        <div><dt>Assessment reference</dt><dd id="assessment-reference"></dd></div>
        <div><dt>Notice reference</dt><dd id="notice-reference"></dd></div>
        <div><dt>Notice SHA-256</dt><dd class="digest" id="notice-digest"></dd></div>
        <div><dt>Invitation expires</dt><dd><time id="invitation-expiry"></time></dd></div>
        <div><dt>Draft revision</dt><dd id="draft-revision"></dd></div>
      </dl>

      <section class="limitations" aria-labelledby="limitations-title">
        <h3 id="limitations-title">Declared completeness limits</h3>
        <ul id="contract-limitations"></ul>
      </section>

      <form id="response-form" novalidate>
        <section aria-labelledby="questions-title">
          <div class="section-heading">
            <div>
              <h3 id="questions-title">Authorized questions</h3>
              <p class="help">
                Save a draft to have the server evaluate and return the next set of
                conditional questions. Prior values are never returned to the browser.
              </p>
            </div>
            <p class="progress" id="question-progress"></p>
          </div>
          <div id="questions"></div>
        </section>

        <section class="evidence" aria-labelledby="evidence-title">
          <div class="section-heading">
            <div>
              <h3 id="evidence-title">Evidence digest manifests</h3>
              <p class="help">
                Record metadata and an independently calculated SHA-256 digest.
                This form has no file chooser and does not transfer document bytes.
              </p>
            </div>
            <button class="secondary" type="button" id="add-artifact-button">
              Add evidence manifest
            </button>
          </div>
          <p class="policy" id="artifact-policy"></p>
          <div id="artifact-manifests"></div>
        </section>

        <section class="attestation" aria-labelledby="attestation-title">
          <h3 id="attestation-title">Final respondent attestation</h3>
          <label class="check-label" for="respondent-attestation">
            <input id="respondent-attestation" type="checkbox">
            <span>
              I attest that I am authorized to respond and that the submitted
              answers and digest manifests are accurate to the best of my knowledge.
              I understand this attestation is recorded as unverified evidence.
            </span>
          </label>
        </section>

        <div class="actions">
          <button type="button" id="save-draft-button">Save draft and refresh questions</button>
          <button class="danger" type="button" id="finalize-button">Finalize attested response</button>
        </div>
      </form>
    </section>

    <section class="panel receipt" id="receipt-panel" aria-labelledby="receipt-title" hidden>
      <p class="eyebrow">Submission recorded</p>
      <h2 id="receipt-title">Final response receipt</h2>
      <p>
        The invitation credential has been cleared from this page. Preserve the
        following digests with your internal response records.
      </p>
      <dl class="contract-meta">
        <div><dt>Assessment revision</dt><dd id="receipt-revision"></dd></div>
        <div><dt>Assessment integrity SHA-256</dt><dd class="digest" id="receipt-assessment-digest"></dd></div>
        <div><dt>Attestation SHA-256</dt><dd class="digest" id="receipt-attestation-digest"></dd></div>
        <div><dt>Evidence status</dt><dd id="receipt-evidence-status"></dd></div>
      </dl>
    </section>

    <noscript>
      <p class="boundary">JavaScript is required for the in-memory credential boundary.</p>
    </noscript>
  </main>
</body>
</html>
"""

_SECURITY_HEADERS = {
    "Cache-Control": "no-store, private, max-age=0",
    "Pragma": "no-cache",
    "Content-Security-Policy": (
        "default-src 'none'; base-uri 'none'; connect-src 'self'; "
        "font-src 'none'; form-action 'none'; frame-ancestors 'none'; "
        "img-src 'none'; manifest-src 'none'; media-src 'none'; "
        "object-src 'none'; script-src 'self'; style-src 'self'; "
        "worker-src 'none'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
        "serial=(), bluetooth=(), clipboard-read=(), clipboard-write=()"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
}

_ASSET_HEADERS = {
    "Cache-Control": "no-store, private, max-age=0",
    "Pragma": "no-cache",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
}


def _available(portal_ref: str, request: Request) -> None:
    if (
        not _PORTAL_REF_RE.fullmatch(portal_ref)
        or request.query_params
        or any(request.headers.get(name) for name in _TENANT_HEADERS)
    ):
        raise HTTPException(status_code=404, detail="portal unavailable")


def create_third_party_collaboration_portal_router() -> APIRouter:
    """Create the constant, non-enumerating external portal shell router."""

    router = APIRouter(
        prefix="/api/v1/external/third-party-collaboration",
        tags=["external-third-party-collaboration-portal"],
    )

    @router.get(
        "/{portal_ref}/portal",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    async def portal(portal_ref: str, request: Request) -> HTMLResponse:
        _available(portal_ref, request)
        return HTMLResponse(_PAGE, headers=_SECURITY_HEADERS)

    @router.get(
        "/{portal_ref}/portal/assets/client.js",
        include_in_schema=False,
        response_class=FileResponse,
    )
    async def client_asset(portal_ref: str, request: Request) -> FileResponse:
        _available(portal_ref, request)
        return FileResponse(
            _STATIC / "lightwork-third-party-portal.js",
            media_type="text/javascript",
            headers=_ASSET_HEADERS,
        )

    @router.get(
        "/{portal_ref}/portal/assets/styles.css",
        include_in_schema=False,
        response_class=FileResponse,
    )
    async def style_asset(portal_ref: str, request: Request) -> FileResponse:
        _available(portal_ref, request)
        return FileResponse(
            _STATIC / "lightwork-third-party-portal.css",
            media_type="text/css",
            headers=_ASSET_HEADERS,
        )

    return router


router = create_third_party_collaboration_portal_router()

__all__ = ["create_third_party_collaboration_portal_router", "router"]
