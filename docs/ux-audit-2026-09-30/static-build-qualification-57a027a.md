# Final08 static and build qualification

Source `57a027adfdd8df955d33aaea88ec7089aafc6c60` passes **all 26 checks**:
Ruff, Vulture, 16 offline contracts and eight package wheel builds. All eight
wheel archives and SHA-256 hashes verify; **4,827 payload files match the exact
Git export byte for byte**. The frozen checkout stayed clean. Builds used a
separate Git archive and private environment; nothing was published or deployed.

Source archive SHA-256 is
`a83891941c08affe4641d3ec9f74aabdac9d2ff5aad4174047899d70d3bd131b`.
The archive's embedded commit matches the source above. The root verifier read
and checked every payload without changing file permissions.
[The evidence record](static-build-qualification-57a027a.json) retains execution
source, runner hash, gate results, wheel hashes, payload counts and artifact hashes. Full local
artifacts remain under `audit/qualification-final-08-contracts/`.

Final runtime preparation also copied 2,376 already-verified Node dependency
files into the frozen checkout. Both committed lockfiles match the donor.
Before/after donor and copied-file hashes agree. All five direct package pins
resolve to the private copy from the checkout root and both dashboard test
directories. [The dependency receipt](node-dependency-qualification-57a027a.json)
records exact sources, probes and the retained first failed preparation attempt.

This proof is limited to checkout-based resolution. A separate temporary-directory
probe resolves three Playwright packages from a host ancestor before `NODE_PATH`;
the receipt explicitly records `passed_checkout_only_temp_shadowing`. The
inspected Python temporary scripts use syntax checks or explicit SDK imports;
arbitrary external script locations are not qualified. No registry install or
dependency upgrade was performed by this copy. The first attempt failed before
copying because excluding system Git configuration changed CRLF interpretation;
the retry pins the existing Windows checkout convention on individual Git
commands without changing host or repository configuration.

This checkpoint includes the final widget overflow repair and supersedes the
older static/build result at `3640fcf`. Runtime, browser, supported-interpreter,
native service and release acceptance remain separate evidence.
