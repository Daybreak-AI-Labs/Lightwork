"""Regression contracts for high-impact dashboard deployment mutations."""

from __future__ import annotations

from pathlib import Path

TEMPLATES = Path(__file__).parents[1] / "maverick_dashboard" / "templates"
STATIC = Path(__file__).parents[1] / "maverick_dashboard" / "static"


def _source(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


def _static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_learning_mutations_confirm_targets_and_recover_for_retry():
    source = _source("learning.html")

    assert "This changes the deployment-wide learning posture" in source
    assert "sw.label + '” for this deployment? ' + impact" in source
    assert "Scheduled and triggered workflows may begin running" in source
    assert "Disable automatic rollback of workflow regressions?" in source
    assert "Disable automatic application of workflow improvements?" in source
    assert "Disable research-only DGM for this deployment?" in source
    assert source.count("if (btn.dataset.inFlight === 'true') return;") >= 5
    assert source.count("btn.setAttribute('aria-busy', 'true')") >= 5
    assert source.count("delete btn.dataset.inFlight") >= 10
    assert "body.detail || ('Could not update “' + sw.label" in source
    assert "body.detail || ('Could not turn the flow engine on" in source
    assert "check your connection and try again" in source


def test_workforce_deploy_is_single_flight_and_surfaces_response_details():
    source = _source("workforce.html")

    deploy = source[source.index('if (act === "deploy")') : source.index("openModal(", source.index('if (act === "deploy")'))]
    assert 'if (btn.dataset.inFlight === "true") return;' in deploy
    assert '"Deploy the " + deptTitle + " team?' in deploy
    assert "btn.disabled = true" in deploy
    assert 'btn.setAttribute("aria-busy", "true")' in deploy
    assert "res.body.detail" in deploy
    assert '" team (HTTP " + res.status + "). Try again."' in deploy
    assert "check your connection and try again" in deploy
    assert "finally" in deploy
    assert "delete btn.dataset.inFlight" in deploy


def test_agent_factory_deploy_and_reset_mutations_are_guarded():
    source = _source("agents.html")

    assert "if (dlgDeploy.dataset.inFlight === 'true') return;" in source
    assert "if (btn.dataset.inFlight === 'true') return;" in source
    assert source.count("This creates an operational fleet and its specialists") >= 2
    assert source.count(".finally(function ()") >= 2
    assert "function beginMutation(button)" in source
    assert "if (!beginMutation(saveBtn)) return;" in source
    assert "if (!name || !beginMutation(resetBtn)) return;" in source
    assert "Its saved authority and workflow become available to operators" in source
    assert "Custom authority and workflow edits will be removed" in source
    assert "Could not reach the server — check your connection and try again." in source
    assert "finishMutation(saveBtn)" in source
    assert "finishMutation(resetBtn)" in source


def test_other_global_or_executable_deployments_have_the_same_safety_contract():
    assurance = _source("model_risk_assurance.html")
    governance = _source("_governance_operations.html")
    store = _source("store.html")
    roles = _source("roles.html")

    assert "let actionInFlight = false" in assurance
    assert "Activate policy pack “${target}” on the global channel?" in assurance
    assert "Every subscribed tenant may receive this governed version" in assurance
    assert "Policy-pack ${action} failed (HTTP ${response.status}). Try again." in assurance
    assert 'snapshotButton.dataset.inFlight === "true"' in assurance
    assert "Publish the governed portfolio-risk snapshot" in assurance
    assert "Snapshot publication failed (HTTP ${response.status}). Try again." in assurance
    assert "Could not reach the server — check your connection and try again." in assurance

    assert 'if (form.dataset.inFlight === "true") return null;' in governance
    assert 'delete form.dataset.inFlight' in governance
    assert 'policyActivate.dataset.confirmationPending === "true"' in governance
    assert "Activate policy “${policyId}” version ${version} for this tenant?" in governance
    assert "Could not reach the server — check your connection and try again." in governance

    assert "if (btn.dataset.inFlight === 'true') return;" in store
    assert "Add the skill “' + label + '”?" in store
    assert "window.mvErrorText(r, 'add the skill')" in store
    assert "HTTP" not in store
    assert "if (!installed)" in store

    assert "function beginMutation(button)" in roles
    assert "'Save changes to the ' + roleName + ' role?" in roles
    assert "'Put the ' + roleName + ' role back to its built-in settings?" in roles
    assert "Could not reach the server — check your connection and try again." in roles
    assert "finishMutation(saveBtn)" in roles
    assert "finishMutation(resetBtn)" in roles


def test_tenant_policy_and_notice_publication_are_confirmed_and_retryable():
    data_use = _source("data_use_governance.html")
    privacy = _source("privacy.html")

    assert "if(f.dataset.inFlight==='true')return" in data_use
    assert "Activate data-use policy “'+policyId+'” version '+version" in data_use
    assert "New authorization decisions will use this governed version" in data_use
    assert "delete f.dataset.inFlight" in data_use
    assert "Could not reach the server — check your connection and try again." in data_use
    assert "'HTTP '+response.status+'. Try again.'" in data_use

    assert "if (noticeBtn.dataset.inFlight === 'true') return;" in privacy
    assert "Publish privacy notice version “' + version + '”? It cannot be changed afterwards" in privacy
    assert "noticeBtn.setAttribute('aria-busy', 'true')" in privacy
    assert "errText(e, 'publish the notice')" in privacy
    assert "delete noticeBtn.dataset.inFlight" in privacy


def test_consent_lifecycle_mutations_are_targeted_single_flight_actions():
    experiments = _source("consent_experiments.html")
    opt_out = _static("lightwork-consent-global-opt-out.js")

    # Each lifecycle action is a row button: single-flight through CW.busy and
    # confirmed with its plain consequence first.
    assert "CW.busy(button, async function" in experiments
    assert "Visitors taking part will begin to see the test versions." in experiments
    assert "Everyone will see the original from now on." in experiments
    assert "expected_revision: x.revision" in experiments
    assert "Delete visitor records that are past their keep-until date?" in experiments

    # Opt-out set-ups: single-flight row buttons with plain confirmations.
    assert "return CW.busy(button, async function" in opt_out
    assert "Switch on opt-out for " in opt_out
    assert "Switch off opt-out for " in opt_out
    assert "expected_revision: item.revision" in opt_out
    assert "Delete opt-out records that are past their keep-until date?" in opt_out
