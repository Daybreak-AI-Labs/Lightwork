# Static and build qualification after the SQLite admission repair

The later [final06 checkpoint](static-build-qualification-3640fcf.md) includes
the Qdrant compatibility repair and supersedes this static/build result.

Source: `7d38f76ccc7c8b1752eaeb44d65dccfd0646d4d5`. All **26 checks pass**:
Ruff, Vulture, 16 offline contract/governance gates and eight package wheels.
The matching-source checkout remained clean before and after execution; builds
used an external archive of that exact Git object and isolated synthetic home.

All eight wheel archive and SHA-256 checks pass. All **4,826 payload files**
match the exact source export byte for byte, including the SQLite admission
repair. Archive SHA-256:
`ba73d35f0d1ea7fb11fd1e449001cd0b425cb2655e3f1603cc43995677356d72`.

The [receipt](static-build-qualification-7d38f76.json) contains check results,
wheel hashes, individual payload counts and helper hashes. Full logs and local
artifacts are retained in `audit/qualification-final-05-contracts/`. These
checks ran under the native account that built and owned the private artifacts;
no file permissions, shared dependencies or deployment configuration changed.

This source supersedes the earlier `500fc873` static/build checkpoint. Runtime,
browser, installed distribution and live-service evidence remains separately
tracked. The unsigned local wheels were not published or deployed.
