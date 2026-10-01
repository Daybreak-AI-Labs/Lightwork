# Corrected-source static and build qualification

Source `2decc28d5a2709ec1d362bdfb8499aaa13aacfce` passes all **26 checks**:
Ruff, Vulture, 16 offline contracts and eight package wheel builds. All eight
wheel archives and SHA-256 hashes verify, and **4,827 payload files match the
exact Git export byte for byte**. The frozen checkout stayed clean. Builds and
verification used the same native Windows account with isolated configuration;
no permissions, deployment configuration or credentials were changed.

The Git archive embeds the exact commit above and has SHA-256
`0a547bea71c7bdba2d9f045ced215e9a03f48d9740741ac1cb8338775c01d7da`.
The [machine-readable record](static-build-qualification-2decc28.json) retains
execution source, runner hash, all gate results, wheel hashes, payload counts
and artifact hashes. Local artifacts remain in
`audit/qualification-final-09-contracts/`. Nothing was published or deployed.

The [Node dependency receipt](node-dependency-qualification-2decc28.json) records
2,376 copied files, matching committed locks, before/after donor and destination
hashes, and exact resolution probes. All five direct pins resolve to the private
copy from the checkout root and both dashboard test directories. A separate
temporary-directory probe finds three host-ancestor Playwright packages before
`NODE_PATH`, so the recorded status remains
`passed_checkout_only_temp_shadowing`. Arbitrary external script locations are
not qualified; the inspected Python temporary scripts use syntax checks or
explicit SDK imports. No registry install or dependency upgrade occurred.

This source includes the deterministic payload fault-injection test repair;
production payload code is unchanged. The complete payload module separately
passes 18 cases in each posture. Full Python and compatibility runtime results
remain separate from these static/build results.
