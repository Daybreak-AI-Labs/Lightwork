"""Compliance & governance CLI commands: soc2, enterprise, ropa, dpia, ai-act,
controls, hunt, remediate, assess, dsar.

Split out of cli/__init__.py; registered via import at the end of the package
__init__ so the @main.group/@main.command decorators fire on package import.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import click

from . import _soc2_posture_ready, main


@main.command("soc2")
@click.option("--json", "compact", is_flag=True,
              help="Emit compact single-line JSON (default: pretty, indent=2).")
def soc2(compact: bool) -> None:
    """Print a SOC 2 technical-posture snapshot as JSON.

    Serializes ``collect_soc2_evidence()`` -- which controls are ON in this
    deployment and whether the audit log verifies -- for auditors / CI /
    automation. The collector is fail-soft (it never raises), so this command
    always emits a JSON object. The command exits non-zero when required
    controls or audit-log checks are not in a SOC 2-ready state.
    """
    import json as _json

    from ..soc2 import collect_soc2_evidence
    evidence = collect_soc2_evidence()
    if compact:
        click.echo(_json.dumps(evidence, default=str))
    else:
        click.echo(_json.dumps(evidence, default=str, indent=2))
    if not _soc2_posture_ready(evidence):
        sys.exit(1)


# ----- Enterprise (regulated-deployment) posture -----------------------

@main.group("enterprise")
def enterprise_group() -> None:
    """Enterprise (regulated-deployment) posture."""


@enterprise_group.command("verify")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text",
              help="Output format.")
@click.option("--require", "require", is_flag=True,
              help="Treat this as a deploy gate: exit non-zero unless every "
                   "boundary guarantee holds (same as setting "
                   "MAVERICK_REQUIRE_ENTERPRISE=1). Without it, the command still "
                   "exits non-zero on any failure but is advisory.")
def enterprise_verify(fmt: str, require: bool) -> None:
    """Actively verify the regulated-deployment guarantees (exits non-zero if any fail).

    Unlike 'maverick compliance' (which maps configured controls to articles),
    this *exercises* the load-bearing guarantees: it proves the egress lock
    refuses a cloud provider and that at-rest sealing round-trips on this box,
    upgrading "the flag is on" to "the boundary holds." Wire it into CI / a
    deploy gate the same way as 'maverick compliance --strict'.

    Pass --require to make it an explicit *preflight gate* (the same one the
    container/dashboard startup runs when MAVERICK_REQUIRE_ENTERPRISE=1): it
    prints a summary of which guarantees failed and exits non-zero so an unsafe
    deployment is blocked rather than merely reported.
    """
    from ..deployment import (
        _preflight_summary,
        all_passed,
        render_json,
        render_text,
        verify_deployment,
    )
    checks = verify_deployment()
    click.echo(render_json(checks) if fmt == "json" else render_text(checks))
    passed = all_passed(checks)
    if require and not passed:
        # Explicit deploy gate: surface which guarantees failed on stderr, the
        # same summary require_enterprise_or_die() raises at startup, and block.
        click.echo("", err=True)
        click.echo(_preflight_summary(checks), err=True)
    if not passed:
        sys.exit(1)


@main.command("ropa")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text",
              help="Output format.")
@click.option("--output", "-o", type=click.Path(), default=None,
              help="Write to file (default stdout).")
def ropa_cmd(fmt: str, output) -> None:
    """Generate a GDPR Art. 30 record-of-processing scaffold for this deployment.

    Pre-fills the technical half from the live config and schema -- personal-data
    categories, recipients / international transfers (from the egress lock),
    retention, and the active Art. 32 security measures -- and marks the
    organizational fields (controller, DPO, lawful basis, purposes) for the
    controller to complete. A scaffold for a DPO to finish, not a legal
    attestation.
    """
    from ..ropa import generate_ropa, render_ropa_json, render_ropa_text
    record = generate_ropa()
    payload = render_ropa_json(record) if fmt == "json" else render_ropa_text(record)
    if output:
        try:
            fd = os.open(str(output), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload + "\n")
        except OSError as e:
            raise click.ClickException(f"could not write {output}: {e}") from e
        click.echo(f"wrote {output}")
    else:
        click.echo(payload)


@main.group("clause-playbook")
def clause_playbook_group() -> None:
    """Your standard contract positions -- what a vendor's paper is redlined to."""


@clause_playbook_group.command("init")
@click.option("--output", "-o", type=click.Path(), default=None,
              help="Write here instead of the configured playbook path.")
@click.option("--force", is_flag=True,
              help="Overwrite an existing playbook.")
def clause_playbook_init(output, force: bool) -> None:
    """Write a starter clause playbook you can edit.

    The shipped clauses are OUR standard positions; a deployment's are its
    own. Editing a value changes the exact language every vendor-paper redline
    demands, with no code change. Clauses you delete fall back to the shipped
    position, so a partial playbook is a valid one.
    """
    from ..paper_review import playbook_path, write_playbook
    target = Path(output) if output else playbook_path()
    if target.exists() and not force:
        raise click.ClickException(
            f"{target} already exists; pass --force to overwrite")
    written = write_playbook(path=target)
    click.echo(f"wrote {written}")
    click.echo("Edit the 'positions' object, then run "
               "`maverick clause-playbook show` to confirm what is active.")


@clause_playbook_group.command("show")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]),
              default="text", help="Output format.")
def clause_playbook_show(fmt: str) -> None:
    """Show the clause positions in force, and which ones you have customised."""
    import json as _json

    from ..paper_review import default_playbook, load_playbook, playbook_path
    shipped, active = default_playbook(), load_playbook()
    custom = sorted(k for k, v in active.items() if shipped.get(k) != v)
    if fmt == "json":
        click.echo(_json.dumps(
            {"path": str(playbook_path()), "customised": custom,
             "positions": active}, indent=2, sort_keys=True))
        return
    path = playbook_path()
    click.echo(f"playbook: {path}"
               f"{'' if path.exists() else '  (not written yet — shipped positions in force)'}")
    click.echo(f"clauses:  {len(active)}   customised: {len(custom)}")
    for key in sorted(active):
        mark = "*" if key in custom else " "
        click.echo(f" {mark} {key:24} {active[key][:96]}")
    if custom:
        click.echo("\n* = your language, not the shipped position.")


@clause_playbook_group.command("draft")
@click.argument("vendor")
@click.option("--instrument", type=click.Choice(["dpa", "addendum"]),
              default="dpa", help="Which of our instruments to draft.")
@click.option("--org", default="", help="Your legal entity name for the "
              "party line (defaults to [paper_review] org_name; blank keeps "
              "a red placeholder).")
@click.option("--output", "-o", type=click.Path(), default=None,
              help="Write the .docx here (default: <vendor>-<instrument>-"
              "our-paper.docx).")
def clause_playbook_draft(vendor: str, instrument: str, org: str,
                          output) -> None:
    """Draft OUR instrument for a vendor, values filled in red.

    The clause language is your playbook (shipped positions underneath);
    the only inserted values are the party names and date, each rendered in
    red in the document and listed here so counsel verifies exactly what
    was auto-filled. Deterministic -- no model is involved.
    """
    from ..paper_review import draft_our_paper
    draft = draft_our_paper(vendor, instrument=instrument, org=org)
    stem = "".join(ch if (ch.isalnum() or ch in "-_") else "-"
                   for ch in vendor).strip("-")[:60] or "vendor"
    target = Path(output) if output else Path(
        f"{stem}-{instrument}-our-paper.docx")
    target.write_bytes(draft.content)
    click.echo(f"wrote {target} ({draft.clause_count} clauses)")
    click.echo("auto-filled (in red — verify before signature):")
    for value in draft.filled:
        click.echo(f"  - {value}")


@main.group("graph")
def graph_group() -> None:
    """The entity graph -- lineage and dossier queries across your records."""


@graph_group.command("rebuild")
def graph_rebuild_cmd() -> None:
    """Regenerate the index from the governed stores (always safe: derived)."""
    from ..entity_graph import rebuild
    result = rebuild()
    if not result.get("enabled"):
        raise click.ClickException(
            "the entity graph is disabled ([entity_graph] enable)")
    click.echo(f"rebuilt: {result['entities']} entities, "
               f"{result['edges']} edges")


@graph_group.command("dossier")
@click.argument("vendor")
@click.option("--as-of", "as_of", default="",
              help="Epoch or YYYY-MM-DD: the graph as it stood on that date.")
def graph_dossier_cmd(vendor: str, as_of: str) -> None:
    """Everything the graph knows about VENDOR, with record ids."""
    import json as _json

    from ..entity_graph import dossier
    ts = None
    if as_of:
        try:
            ts = float(as_of)
        except ValueError:
            import datetime as _dt
            day = _dt.datetime.strptime(as_of, "%Y-%m-%d").replace(
                tzinfo=_dt.timezone.utc)
            ts = (day + _dt.timedelta(days=1)).timestamp()
    result = dossier(vendor, as_of=ts)
    click.echo(_json.dumps(result.get("summary") or result, indent=2,
                           default=str))


@graph_group.command("why")
@click.argument("vendor")
def graph_why_cmd(vendor: str) -> None:
    """Walk VENDOR's latest decision back to its evidence, hop by hop."""
    from ..entity_graph import why
    result = why(vendor)
    if not result.get("found"):
        raise click.ClickException("the graph has no such vendor")
    if not result.get("decision"):
        click.echo(result.get("note", "no decision recorded"))
        return
    for step in result["chain"]:
        click.echo(f"[{step['step']:18}] {step['what']}  "
                   f"({step['record_type']}:{step['record_id']})")


@graph_group.command("blast")
@click.argument("kind", type=click.Choice(
    ["clause", "document", "person", "goal", "skill"]))
@click.argument("name")
def graph_blast_cmd(kind: str, name: str) -> None:
    """KIND NAME turned out bad -- what relied on it?"""
    import json as _json

    from ..entity_graph import blast_radius
    result = blast_radius(kind, name)
    click.echo(_json.dumps(
        {k: result[k] for k in ("records", "vendors", "skills", "decisions")
         if k in result}, indent=2, default=str))


@main.command("dpia")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text",
              help="Output format.")
@click.option("--output", "-o", type=click.Path(), default=None,
              help="Write to file (default stdout).")
def dpia_cmd(fmt: str, output) -> None:
    """Generate a GDPR Art. 35 DPIA scaffold for this deployment.

    Pre-fills the processing description (consistent with 'maverick ropa') and a
    risk register of the agent-on-personal-data risks -- each mapped to the
    Lightwork control that mitigates it and whether that control is active right
    now -- leaving necessity/proportionality and residual-risk sign-off to the
    controller. A scaffold for a DPO to finish, not a completed DPIA.
    """
    from ..dpia import generate_dpia, render_dpia_json, render_dpia_text
    dpia = generate_dpia()
    payload = render_dpia_json(dpia) if fmt == "json" else render_dpia_text(dpia)
    if output:
        try:
            fd = os.open(str(output), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload + "\n")
        except OSError as e:
            raise click.ClickException(f"could not write {output}: {e}") from e
        click.echo(f"wrote {output}")
    else:
        click.echo(payload)


@main.command("ai-act")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text",
              help="Output format.")
def ai_act_cmd(fmt: str) -> None:
    """Classify this deployment under the EU AI Act (self-assessment).

    Reports the live Art. 50 transparency posture and hands you a checklist of
    the prohibited (Art. 5) and high-risk (Annex III) categories plus the
    obligations each tier triggers. A conversational agent that discloses it is
    AI is limited-risk by default -- but you must rule out those lists for your
    use case. A self-assessment aid, not a legal classification.
    """
    from ..ai_act import assess_ai_act, render_ai_act_json, render_ai_act_text
    report = assess_ai_act()
    click.echo(render_ai_act_json(report) if fmt == "json" else render_ai_act_text(report))


@main.command("controls")
@click.argument("query", nargs=-1, required=True)
@click.option("--limit", type=int, default=5, help="Max controls to return.")
def controls_cmd(query: tuple[str, ...], limit: int) -> None:
    """Find the privacy/security control(s) for a risk, with framework citations.

    Example: maverick controls vendor has no DPA
    """
    from ..controls import find_controls, render_control
    hits = find_controls(" ".join(query), limit=limit)
    if not hits:
        raise click.ClickException(f"no controls matched {' '.join(query)!r}")
    click.echo("\n".join(render_control(c) for c in hits))


@main.command("hunt")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text",
              help="Output format.")
@click.option("--since", default=None, help="Only events on/after this UTC date (YYYY-MM-DD).")
@click.option("--until", default=None, help="Only events on/before this UTC date (YYYY-MM-DD).")
@click.option("--strict", is_flag=True,
              help="Exit non-zero if any attack signal is found (gate CI / monitoring).")
def hunt_cmd(fmt: str, since: str | None, until: str | None, strict: bool) -> None:
    """Hunt the audit trail for attacks by/against agents.

    Sweeps the audit log for security signals -- shield blocks (prompt
    injection), blocked egress (exfiltration attempts), capability/governance
    denials (escalation), the kill switch -- and reports them risk-ranked. With
    --strict, exits non-zero when any signal is present, so it can gate a
    monitoring job.
    """
    from ..threat_hunt import hunt, render_report_json, render_report_text
    report = hunt(all_days=(since is None and until is None), since=since, until=until)
    click.echo(render_report_json(report) if fmt == "json" else render_report_text(report))
    if strict and report.findings:
        raise click.ClickException(
            f"{len(report.findings)} attack signal type(s) found "
            f"(risk: {report.risk_rating})"
        )


@main.command("remediate")
@click.option("--apply", "do_apply", is_flag=True,
              help="Apply the auto-fixable remediations (needs enterprise mode + "
                   "[security] auto_fix opt-in).")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text",
              help="Output format.")
def remediate_cmd(do_apply: bool, fmt: str) -> None:
    """Assess security posture and (bounded) auto-fix it.

    Reports control gaps + active breach signals and the remediation plan.
    Low-risk, reversible fixes to Lightwork's OWN config are auto-applied with
    --apply -- but only under enterprise mode + a [security] auto_fix opt-in;
    everything behaviour-changing is proposed for a human. Every applied fix is
    audited and reports how to undo it.
    """
    from ..remediation import (
        apply_remediation,
        plan,
        render_plan_json,
        render_plan_text,
    )
    p = plan()
    click.echo(render_plan_json(p) if fmt == "json" else render_plan_text(p))
    if not do_apply:
        return
    click.echo("")
    applied_any = False
    for g in p.gaps:
        if not g.auto:
            continue
        res = apply_remediation(g, dry_run=False)
        if res.applied:
            applied_any = True
            click.echo(f"  applied: {g.title}  (undo: {res.undo})")
        else:
            click.echo(f"  skipped: {g.title}  ({res.reason})")
    if not applied_any:
        click.echo("  (nothing auto-applied)")


# ----- Compliance assessments (PIA / AIRA / vendor risk) ---------------

@main.group("assess")
def assess_group() -> None:
    """Conduct compliance assessments of a subject.

    Privacy: PIA, AIRA, vendor risk. Security: HIPAA, SOC 2, PCI DSS.
    Run 'maverick assess templates' for the full set.
    """


@assess_group.command("templates")
def assess_templates() -> None:
    """List the available assessment types."""
    from ..assessment import list_templates
    for t in list_templates():
        click.echo(
            f"  {t.type:12} {t.title}  "
            f"({t.framework}; {len(t.questions)} questions)"
        )


@assess_group.command("questions")
@click.argument("assessment_type")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text",
              help="Output format.")
def assess_questions(assessment_type: str, fmt: str) -> None:
    """Print an assessment's questionnaire (so you -- or the agent -- can answer it)."""
    from ..assessment import get_template, render_questions_json, render_questions_text
    tpl = get_template(assessment_type)
    if tpl is None:
        raise click.ClickException(
            f"unknown assessment type {assessment_type!r}; see 'maverick assess templates'"
        )
    click.echo(render_questions_json(tpl) if fmt == "json" else render_questions_text(tpl))


@assess_group.command("score")
@click.argument("assessment_type")
@click.option("--subject", required=True,
              help="What is being assessed (the vendor / system / activity).")
@click.option("--answers", "answers_file", required=True,
              type=click.Path(exists=True),
              help="JSON {question_id: answer}; answer is yes/no/na/unknown "
                   "or {\"answer\":..., \"note\":...}.")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text",
              help="Output format.")
@click.option("--no-save", is_flag=True, help="Don't persist the assessment.")
def assess_score(assessment_type: str, subject: str, answers_file: str,
                 fmt: str, no_save: bool) -> None:
    """Score a completed answer set into findings + a risk rating, and save it."""
    import json as _json

    from ..assessment import (
        AssessmentSession,
        get_template,
        render_result_json,
        render_result_text,
        save_session,
    )
    if get_template(assessment_type) is None:
        raise click.ClickException(
            f"unknown assessment type {assessment_type!r}; see 'maverick assess templates'"
        )
    try:
        raw = _json.loads(Path(answers_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise click.ClickException(f"could not read answers: {e}") from e
    if not isinstance(raw, dict):
        raise click.ClickException("answers file must be a JSON object {question_id: answer}")
    session = AssessmentSession(type=assessment_type, subject=subject)
    for qid, val in raw.items():
        answer = val.get("answer") if isinstance(val, dict) else val
        note = val.get("note", "") if isinstance(val, dict) else ""
        try:
            session.record(str(qid), str(answer), str(note))
        except (KeyError, ValueError) as e:
            raise click.ClickException(str(e)) from e
    result = session.evaluate()
    click.echo(render_result_json(result) if fmt == "json" else render_result_text(result))
    if not no_save:
        click.echo(f"\nsaved: {save_session(session)}")


@assess_group.command("list")
def assess_list() -> None:
    """List saved assessments, newest first."""
    from ..assessment import list_saved
    rows = list_saved()
    if not rows:
        click.echo("No saved assessments.")
        return
    for r in rows:
        click.echo(
            f"  {r['id']:20} {r['type']:12} {r['risk_rating']:8} "
            f"{r['findings']} finding(s)  {r['subject']}"
        )


@assess_group.command("show")
@click.argument("assessment_id")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text",
              help="Output format.")
def assess_show(assessment_id: str, fmt: str) -> None:
    """Show a saved assessment by id."""
    import json as _json

    from ..assessment import AssessmentResult, Finding, load_saved, render_result_text
    data = load_saved(assessment_id)
    if data is None:
        raise click.ClickException(f"no saved assessment {assessment_id!r}")
    if fmt == "json":
        click.echo(_json.dumps(data, indent=2, default=str))
        return
    res = data["result"]
    result = AssessmentResult(
        type=res["type"], subject=res["subject"], risk_rating=res["risk_rating"],
        findings=[Finding(**f) for f in res["findings"]],
        answered=res["answered"], total=res["total"],
    )
    click.echo(render_result_text(result))


# ----- DSAR subject-data export ----------------------------------------

@main.group("dsar")
def dsar_group() -> None:
    """Data-subject access requests (GDPR Art. 15 / 20)."""


@dsar_group.command("export")
@click.option("--user", "user_id", required=True,
              help="The channel user_id (subject) to export.")
@click.option("--tenant", default=None, help="Tenant to read from (default: active).")
@click.option("--output", "-o", type=click.Path(), default=None,
              help="Write JSON to file (default stdout).")
@click.option("--json", "compact", is_flag=True,
              help="Emit compact single-line JSON (default: pretty, indent=2).")
def dsar_export(user_id: str, tenant: str | None, output, compact: bool) -> None:
    """Export everything Lightwork holds for a subject as a JSON bundle.

    Serializes ``export_subject_data()`` -- the subject's conversations, the
    turns/goals/episodes those reference, and their audit rows -- for the
    right of access / portability. The exporter is fail-soft (an unknown
    subject or empty install yields a structured, empty bundle), so this
    command always emits a JSON object and exits 0.
    """
    import json as _json

    from ..dsar import export_subject_data
    bundle = export_subject_data(user_id, tenant=tenant)
    payload = (
        _json.dumps(bundle, default=str)
        if compact
        else _json.dumps(bundle, default=str, indent=2)
    )
    if output:
        # A DSAR export carries the subject's full conversation content.
        # Create it 0o600 (not the umask's world-readable 0644) so a
        # co-tenant can't read it, and fail cleanly instead of dumping a
        # traceback on a bad/unwritable path.
        try:
            fd = os.open(str(output), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
        except OSError as e:
            raise click.ClickException(f"could not write {output}: {e}") from e
        click.echo(f"exported to {output}")
    else:
        click.echo(payload)


@main.group("ai-incidents")
def ai_incidents() -> None:
    """EU AI Act Art. 73 serious-incident clocks and report drafts.

    Classify an assurance incident under Art. 3(49), watch the tiered
    2/10/15-day awareness clock, draft the report for the routed Member-State
    market surveillance authority, and record human-gated submissions. Clocks
    and drafts only -- nothing is ever transmitted by the platform.
    """


@ai_incidents.command("declare")
@click.argument("incident_id")
@click.option("--category", required=True,
              help="Art. 3(49) category (see `maverick ai-incidents categories`).")
@click.option("--widespread", is_flag=True,
              help="Art. 3(61) widespread infringement (forces the 2-day clock).")
@click.option("--aware-at", type=float, required=True,
              help="Unix timestamp when the provider became aware.")
@click.option("--causal-link", default="under_investigation",
              show_default=True,
              help="established | reasonably_likely | under_investigation.")
@click.option("--member-state", required=True,
              help="EU-27 code of the Member State where the incident occurred.")
@click.option("--authority", required=True,
              help="The market surveillance authority the report routes to.")
@click.option("--by", "declared_by", required=True, help="Declaring human.")
def ai_incidents_declare(incident_id: str, category: str, widespread: bool,
                         aware_at: float, causal_link: str, member_state: str,
                         authority: str, declared_by: str) -> None:
    """Declare one assurance incident as an Art. 3(49) serious incident."""
    from ..ai_incident_reporting import declare_serious_incident
    try:
        record = declare_serious_incident(
            incident_id, category=category, widespread=widespread,
            became_aware_at=aware_at, causal_link=causal_link,
            member_state=member_state, authority=authority,
            declared_by=declared_by,
        )
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"declared {record['id']}: report due at "
               f"{record['report_due_at']} ({record['deadline_citation']})")


@ai_incidents.command("categories")
def ai_incidents_categories() -> None:
    """The Art. 3(49) categories and the clock each one starts."""
    from ..ai_incident_reporting import SERIOUS_INCIDENT_CATEGORIES
    for token, entry in sorted(SERIOUS_INCIDENT_CATEGORIES.items()):
        click.echo(f"  {token:38s} {entry['window_days']:>2d} days "
                   f"({entry['deadline_citation']}; {entry['category_citation']})")


@ai_incidents.command("list")
@click.option("--status", default="", help="Filter by lifecycle status.")
@click.option("--overdue", "overdue_only", is_flag=True,
              help="Only declarations past their Art. 73 deadline.")
def ai_incidents_list(status: str, overdue_only: bool) -> None:
    """Declarations with their live clocks, most urgent first."""
    from ..ai_incident_reporting import list_declarations, overdue_declarations
    try:
        rows = (overdue_declarations() if overdue_only
                else list_declarations(status=status.strip() or None))
    except (ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    if not rows:
        click.echo("no serious-incident declarations.")
        return
    for row in rows:
        clock = row["clock"]
        marker = "OVERDUE" if clock["overdue"] else f"{clock['seconds_remaining'] / 86400:.1f}d left"
        click.echo(f"  {row['id']}  {row['status']:18s} {row['category']:38s} "
                   f"{row['member_state']}  {marker}")


@ai_incidents.command("submit")
@click.argument("record_id")
@click.option("--kind", type=click.Choice(["initial", "complete"]), required=True,
              help="Art. 73(5): an incomplete initial report may precede the "
                   "complete one.")
@click.option("--by", "submitted_by", required=True, help="Submitting human.")
@click.option("--at", "submitted_at", type=float, required=True,
              help="Unix timestamp of the filing.")
@click.option("--reference", required=True,
              help="The authority's ticket / filing reference.")
@click.option("--revision", type=int, required=True,
              help="Expected record revision (CAS).")
def ai_incidents_submit(record_id: str, kind: str, submitted_by: str,
                        submitted_at: float, reference: str, revision: int) -> None:
    """Record that a human filed the report with the routed authority."""
    from ..ai_incident_reporting import record_submission
    try:
        record = record_submission(
            record_id, kind=kind, submitted_by=submitted_by,
            submitted_at=submitted_at, reference=reference,
            expected_revision=revision,
        )
    except (ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    if record is None:
        raise click.ClickException(f"no declaration {record_id!r}")
    late = record["submissions"][-1]["late"]
    click.echo(f"{record['status']}{' (late filing recorded)' if late else ''}")


@ai_incidents.command("report")
@click.argument("record_id")
def ai_incidents_report(record_id: str) -> None:
    """Render the bounded Art. 73 report draft for one declaration."""
    from ..ai_incident_reporting import render_report
    try:
        click.echo(render_report(record_id))
    except (KeyError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e


@main.group("red-team")
def red_team() -> None:
    """Continuous red-team assurance schedules and their dispatcher.

    A schedule is a standing, human-created authority that materializes
    push-based campaigns on a cadence; `run-due` is the dispatch step an
    operator or cron invokes to turn due slots into campaigns. Enrolled
    agents still pull and lease each case -- nothing here executes attacks.
    """


@red_team.command("schedules")
@click.option("--limit", type=int, default=100, show_default=True,
              help="Maximum schedules to list.")
def red_team_schedules_cli(limit: int) -> None:
    """Schedules with their status, cadence, and next due slot."""
    from ..red_team_assurance import list_schedules
    try:
        page = list_schedules(limit=limit)
    except (ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    rows = page["items"]
    if not rows:
        click.echo("no red-team schedules.")
        return
    for row in rows:
        click.echo(f"  {row['id']}  {row['status']:10s} "
                   f"every {int(row['cadence_seconds'])}s  "
                   f"next {row.get('next_run_at')}  runs {row.get('run_count', 0)}  "
                   f"target {row.get('target_asset_id')}")
    if page["truncated"]:
        click.echo(f"  ... truncated at {page['limit']}")


@red_team.command("schedule")
@click.option("--asset", "asset_id", required=True,
              help="Observed target asset id (MRA-...).")
@click.option("--agent", "external_agent_id", required=True,
              help="Enrolled external agent that pulls the cases.")
@click.option("--key", "idempotency_key", required=True,
              help="Idempotency key; re-running with the same key is a no-op.")
@click.option("--cadence", "cadence_seconds", type=int, required=True,
              help="Seconds between materialized campaigns (min 300).")
@click.option("--first-run-at", type=float, default=None,
              help="Unix timestamp of the first slot (default: now).")
@click.option("--ttl", "campaign_ttl_seconds", type=int, default=None,
              help="Per-campaign TTL seconds (default: configured TTL).")
@click.option("--case", "case_keys", multiple=True,
              help="Restrict to specific case keys (repeatable).")
@click.option("--safe-target-environment", is_flag=True,
              help="Attest the target runs in an isolated non-production "
                   "environment. Required.")
@click.option("--by", "actor", required=True, help="Creating human.")
def red_team_schedule_create(asset_id: str, external_agent_id: str,
                             idempotency_key: str, cadence_seconds: int,
                             first_run_at: float | None,
                             campaign_ttl_seconds: int | None,
                             case_keys: tuple[str, ...],
                             safe_target_environment: bool, actor: str) -> None:
    """Create a recurring schedule against one observed asset."""
    from ..red_team_assurance import create_schedule
    try:
        record = create_schedule(
            asset_id=asset_id, external_agent_id=external_agent_id,
            idempotency_key=idempotency_key, actor=actor,
            safe_target_environment=safe_target_environment,
            cadence_seconds=cadence_seconds, first_run_at=first_run_at,
            case_keys=list(case_keys) or None,
            campaign_ttl_seconds=campaign_ttl_seconds,
        )
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"schedule {record['id']}: next run at {record['next_run_at']}")


@red_team.command("run-due")
@click.option("--limit", type=int, default=20, show_default=True,
              help="Maximum due schedules to materialize this pass.")
@click.option("--by", "actor", required=True, help="Dispatching human or cron identity.")
def red_team_run_due(limit: int, actor: str) -> None:
    """Materialize campaigns for every due schedule (the dispatch step)."""
    from ..red_team_assurance import run_due_schedules
    try:
        result = run_due_schedules(actor=actor, limit=limit)
    except (ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"materialized {result['count']} of {result['due_count']} due"
               f"{' (truncated)' if result['truncated'] else ''}")
    for row in result["campaigns"]:
        target = (row.get("target_binding") or {}).get("asset_id")
        click.echo(f"  campaign {row['id']} against {target}")
    for failure in result["failures"]:
        click.echo(f"  FAILED {failure['schedule_id']}: {failure['reason']}")
    if result["failure_count"]:
        raise click.ClickException(
            f"{result['failure_count']} schedule(s) failed to materialize"
        )


@red_team.command("cancel-schedule")
@click.argument("schedule_id")
@click.option("--revision", type=int, required=True,
              help="Expected schedule revision (CAS).")
@click.option("--rationale", required=True, help="Why the cadence ends.")
@click.option("--by", "actor", required=True, help="Cancelling human.")
def red_team_cancel_schedule(schedule_id: str, revision: int,
                             rationale: str, actor: str) -> None:
    """Cancel future slots; an already-claimed run stays in flight."""
    from ..red_team_assurance import cancel_schedule
    try:
        record = cancel_schedule(
            schedule_id, expected_revision=revision, actor=actor,
            rationale=rationale,
        )
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"{record['id']} {record['status']}")


@main.group("regulatory-feed")
def regulatory_feed() -> None:
    """Bridge reviewed regulatory alerts into AI-governance impact records.

    The finance regulatory-change engine stays the source/version authority
    and its human review queue stays the disposition authority. `sync` runs
    reviewed alerts through operator-configured impact rules to create
    impact, evidence-staleness, and customer-notice records. Decision
    support only -- never a legal applicability opinion.
    """


@regulatory_feed.command("sync")
@click.option("--rules", "rules_path", required=True,
              type=click.Path(exists=True, dir_okay=False),
              help="JSON file: a list of explicit impact-rule mappings.")
@click.option("--status", default="accepted", show_default=True,
              type=click.Choice(["open", "in_review", "accepted"]),
              help="Which alert queue state feeds the bridge.")
@click.option("--limit", type=int, default=100, show_default=True,
              help="Maximum alerts to bridge this pass.")
@click.option("--deliver", is_flag=True,
              help="Also deliver each created notice through the configured "
                   "notification backends.")
@click.option("--by", "actor", required=True,
              help="Dispatching human or cron identity.")
def regulatory_feed_sync(rules_path: str, status: str, limit: int,
                         deliver: bool, actor: str) -> None:
    """Run each reviewed alert through the impact bridge, idempotently."""
    import json

    from ..ai_regulatory_feed import sync_alert
    from ..finance.regulatory_change import RegulatoryChangeEngine
    from ..paths import data_dir

    try:
        rules = json.loads(Path(rules_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise click.ClickException(f"could not load rules: {e}") from e
    if not isinstance(rules, list) or not rules:
        raise click.ClickException("rules file must be a non-empty JSON list")
    try:
        engine = RegulatoryChangeEngine(
            data_dir("finance_operations", "regulatory_change.sqlite3")
        )
        alerts = engine.list_alerts(status=status, limit=limit)
    except (OSError, ValueError, RuntimeError) as e:
        raise click.ClickException(f"could not list alerts: {e}") from e
    if not alerts:
        click.echo(f"no {status} regulatory alerts.")
        return
    matched = 0
    failures: list[str] = []
    for alert in alerts:
        try:
            result = sync_alert(alert, rules=rules, actor=actor, deliver=deliver)
        except (KeyError, ValueError, RuntimeError) as e:
            failures.append(f"{getattr(alert, 'alert_id', '?')}: {e}")
            continue
        impact = result.get("impact")
        if impact is None:
            continue
        matched += 1
        notice = result.get("notice") or {}
        staleness = result.get("staleness") or []
        click.echo(f"  {getattr(alert, 'alert_id', '?')}: impact "
                   f"{impact.get('id')}  stale-evidence {len(staleness)}  "
                   f"notice {notice.get('id')} [{notice.get('status')}]")
    click.echo(f"bridged {matched} of {len(alerts)} {status} alert(s)")
    if failures:
        for line in failures:
            click.echo(f"  FAILED {line}")
        raise click.ClickException(f"{len(failures)} alert(s) failed to bridge")


@regulatory_feed.command("staleness")
@click.option("--evidence", "evidence_id", default=None,
              help="Only staleness rows naming this evidence id.")
@click.option("--limit", type=int, default=100, show_default=True)
def regulatory_feed_staleness(evidence_id: str | None, limit: int) -> None:
    """Active evidence-staleness records, oldest first."""
    from ..ai_regulatory_feed import list_active_staleness
    try:
        rows = list_active_staleness(evidence_id=evidence_id, limit=limit)
    except (ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    if not rows:
        click.echo("no active regulatory staleness.")
        return
    for row in rows:
        click.echo(f"  {row['id']}  rev {row.get('revision')}  "
                   f"evidence {row.get('evidence_id')}  "
                   f"control {row.get('control_id')}  {row.get('created_at')}")


@regulatory_feed.command("resolve")
@click.argument("staleness_id")
@click.option("--revision", type=int, required=True,
              help="Expected record revision (CAS).")
@click.option("--resolution", required=True,
              help="What was refreshed or re-attested, and why it suffices.")
@click.option("--by", "actor", required=True, help="Resolving human.")
def regulatory_feed_resolve(staleness_id: str, revision: int,
                            resolution: str, actor: str) -> None:
    """Resolve one staleness record after the evidence was refreshed."""
    from ..ai_regulatory_feed import resolve_staleness
    try:
        row = resolve_staleness(
            staleness_id, expected_revision=revision,
            resolution=resolution, actor=actor,
        )
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"{row['id']} {row.get('status')}")


@regulatory_feed.command("notices")
@click.option("--limit", type=int, default=100, show_default=True)
def regulatory_feed_notices(limit: int) -> None:
    """Customer notices with their delivery state."""
    from ..ai_regulatory_feed import list_notices
    try:
        rows = list_notices(limit=limit)
    except (ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    if not rows:
        click.echo("no regulatory customer notices.")
        return
    for row in rows:
        click.echo(f"  {row['id']}  {row.get('status'):10s} rev "
                   f"{row.get('revision')}  {row.get('title')}")


@regulatory_feed.command("deliver")
@click.argument("notice_id")
@click.option("--revision", type=int, required=True,
              help="Expected record revision (CAS).")
@click.option("--by", "actor", required=True, help="Delivering human or cron identity.")
def regulatory_feed_deliver(notice_id: str, revision: int, actor: str) -> None:
    """Deliver one notice through the configured notification backends."""
    from ..ai_regulatory_feed import deliver_notice
    try:
        row = deliver_notice(notice_id, expected_revision=revision, actor=actor)
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"{row['id']} {row.get('status')}")


@main.group("ai-inventory")
def ai_inventory_group() -> None:
    """Read-only AI asset discovery sweeps across configured providers.

    `sync` runs every configured connector (or the ones named) through the
    governed discovery service -- credentials stay environment references,
    listings are bounded, and each discovered asset lands in the model-risk
    inventory. Cron `sync` for recurring sweeps; `status` shows each
    connector's last outcome.
    """


@ai_inventory_group.command("sync")
@click.option("--connector", "connector_names", multiple=True,
              help="Sync only this connector (repeatable; default: all "
                   "configured connectors).")
@click.option("--by", "actor", required=True,
              help="Dispatching human or cron identity.")
def ai_inventory_sync(connector_names: tuple[str, ...], actor: str) -> None:
    """Sweep configured providers into the governed inventory."""
    from ..ai_inventory import (
        AIInventoryError,
        get_ai_inventory_config,
        sync_connector,
    )
    try:
        config = get_ai_inventory_config()
    except AIInventoryError as e:
        raise click.ClickException(str(e)) from e
    targets = list(connector_names) or list(config.get("connectors") or [])
    if not targets:
        raise click.ClickException(
            "no connectors configured ([ai_inventory] connectors) and none named"
        )
    failures: list[str] = []
    for name in targets:
        try:
            run = sync_connector(name, actor=actor)
        except (AIInventoryError, ValueError, RuntimeError) as e:
            failures.append(f"{name}: {e}")
            continue
        click.echo(f"  {name}: {run.get('status')}  "
                   f"assets {run.get('asset_count', 0)}")
    click.echo(f"synced {len(targets) - len(failures)} of {len(targets)} connector(s)")
    if failures:
        for line in failures:
            click.echo(f"  FAILED {line}")
        raise click.ClickException(f"{len(failures)} connector(s) failed to sync")


@ai_inventory_group.command("status")
def ai_inventory_status() -> None:
    """Each configured connector's last discovery outcome."""
    from ..ai_inventory import (
        AIInventoryError,
        connector_status,
        get_ai_inventory_config,
    )
    try:
        config = get_ai_inventory_config()
    except AIInventoryError as e:
        raise click.ClickException(str(e)) from e
    connectors = list(config.get("connectors") or [])
    if not connectors:
        click.echo("no connectors configured ([ai_inventory] connectors).")
        return
    for name in connectors:
        try:
            row = connector_status(name)
        except (AIInventoryError, ValueError, RuntimeError) as e:
            click.echo(f"  {name}: status unavailable ({e})")
            continue
        if row is None:
            click.echo(f"  {name}: never synced")
        else:
            click.echo(f"  {name}: {row.get('state')}  "
                       f"completed {row.get('completed_sequence', 0)} run(s)")


@main.group("grc-sync")
def grc_sync_group() -> None:
    """Idempotent outbound sync of governance records into a GRC system.

    `run` maps model-risk findings, accepted risks, and incidents into the
    configured system of record (ServiceNow IRM tables, Archer, any REST GRC)
    with a stable outbound ledger: unchanged sources send nothing, changed
    sources update the same remote record, and an unconfirmed create is
    surfaced instead of duplicated. Evidence never leaves Lightwork -- each
    remote record is a governed pointer, not the record of truth.
    """


@grc_sync_group.command("run")
@click.option("--limit", type=int, default=50, show_default=True,
              help="Maximum records to deliver this pass.")
@click.option("--by", "actor", required=True,
              help="Dispatching human or cron identity.")
def grc_sync_run(limit: int, actor: str) -> None:
    """Deliver pending and changed records to the configured GRC system."""
    from ..grc_sync import GRCSyncError, run_sync
    try:
        result = run_sync(actor=actor, limit=limit)
    except (GRCSyncError, ValueError) as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"delivered {result['delivered_count']}  "
               f"unchanged {result['unchanged_count']}  "
               f"blocked {result['blocked_count']}  "
               f"failed {result['failure_count']}  "
               f"via {result['connector']}")
    for row in result["blocked"]:
        click.echo(f"  BLOCKED {row['source_id']}: {row['reason']}")
    for row in result["failures"]:
        click.echo(f"  FAILED {row['source_id']}: {row['reason']}")
    if result["failure_count"]:
        raise click.ClickException(
            f"{result['failure_count']} record(s) failed to deliver"
        )


@grc_sync_group.command("status")
def grc_sync_status() -> None:
    """Outbound ledger counts by delivery state."""
    from ..grc_sync import sync_status
    state = sync_status()
    counts = state["outbound"]
    click.echo(f"enabled: {state['enabled']}")
    for key in ("pending", "delivered", "delivered_unconfirmed", "failed"):
        click.echo(f"  {key:22s} {counts[key]}")


@grc_sync_group.command("outbound")
@click.option("--limit", type=int, default=100, show_default=True)
def grc_sync_outbound(limit: int) -> None:
    """The outbound ledger: every record's remote id and delivery state."""
    from ..grc_sync import list_outbound
    try:
        rows = list_outbound(limit=limit)
    except (ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    if not rows:
        click.echo("no outbound GRC records.")
        return
    for row in rows:
        click.echo(f"  {row['id']}  {row['kind']:16s} {row['status']:22s} "
                   f"{row['source_id']}  ->  {row['remote_id'] or '-'}")


@main.group("ai-literacy")
def ai_literacy_group() -> None:
    """EU AI Act Art. 4 AI-literacy register.

    Named humans set which trainings each role requires and record who
    completed what; `gaps` derives who is missing or expired, and `export`
    emits one bounded JSON evidence snapshot. Decision support for the
    Art. 4 obligation -- never a certification of competence.
    """


@ai_literacy_group.command("require")
@click.argument("role")
@click.option("--training", "trainings", multiple=True, required=True,
              help="key:title[:validity_days] (repeatable).")
@click.option("--revision", type=int, default=0, show_default=True,
              help="Expected revision when updating an existing role (CAS).")
@click.option("--by", "actor", required=True, help="Requiring human.")
def ai_literacy_require(role: str, trainings: tuple[str, ...],
                        revision: int, actor: str) -> None:
    """Set the training requirement for one role."""
    from ..ai_literacy import set_role_requirement
    parsed = []
    for raw in trainings:
        parts = raw.split(":", 2)
        if len(parts) < 2:
            raise click.ClickException(
                f"--training must be key:title[:validity_days], got {raw!r}"
            )
        entry: dict = {"key": parts[0], "title": parts[1]}
        if len(parts) == 3 and parts[2]:
            try:
                entry["validity_days"] = int(parts[2])
            except ValueError as e:
                raise click.ClickException(
                    f"validity_days must be an integer, got {parts[2]!r}"
                ) from e
        else:
            entry["validity_days"] = None
        parsed.append(entry)
    try:
        record = set_role_requirement(
            role, parsed, actor=actor, expected_revision=revision,
        )
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"{record['role']}: {len(record['trainings'])} training(s) "
               f"required (rev {record['revision']})")


@ai_literacy_group.command("assign")
@click.argument("person")
@click.option("--role", "roles", multiple=True, required=True,
              help="Role with a recorded requirement (repeatable).")
@click.option("--revision", type=int, default=0, show_default=True,
              help="Expected revision when updating an assignment (CAS).")
@click.option("--by", "actor", required=True, help="Assigning human.")
def ai_literacy_assign(person: str, roles: tuple[str, ...],
                       revision: int, actor: str) -> None:
    """Assign one person to one or more required roles."""
    from ..ai_literacy import assign_roles
    try:
        record = assign_roles(
            person, list(roles), actor=actor, expected_revision=revision,
        )
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"{record['person']}: {', '.join(record['roles'])}")


@ai_literacy_group.command("complete")
@click.argument("person")
@click.argument("training")
@click.option("--at", "completed_at", type=float, required=True,
              help="Unix timestamp of the completion.")
@click.option("--evidence", "evidence_ref", required=True,
              help="Where the completion evidence lives (LMS ref, ticket).")
@click.option("--by", "actor", required=True,
              help="Recording human or feed identity.")
def ai_literacy_complete(person: str, training: str, completed_at: float,
                         evidence_ref: str, actor: str) -> None:
    """Record (or refresh) one training completion."""
    from ..ai_literacy import record_completion
    try:
        record = record_completion(
            person, training, completed_at=completed_at,
            evidence_ref=evidence_ref, actor=actor,
        )
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    click.echo(f"{record['person']} completed {record['training']} "
               f"(history {len(record.get('history') or [])})")


@ai_literacy_group.command("gaps")
def ai_literacy_gaps_cli() -> None:
    """Who is missing or expired on a required training."""
    from ..ai_literacy import literacy_gaps
    try:
        gaps = literacy_gaps()
    except (ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    counts = gaps["counts"]
    click.echo(f"persons {gaps['person_count']}  current {counts['current']}  "
               f"expired {counts['expired']}  missing {counts['missing']}")
    for person in gaps["persons"]:
        if not person["gap_count"]:
            continue
        stale = [row["training"] for row in person["trainings"]
                 if row["status"] != "current"]
        click.echo(f"  {person['person']}: {', '.join(stale)}")


@ai_literacy_group.command("export")
@click.option("--output", "-o", type=click.Path(), default=None,
              help="Write the evidence snapshot here (default stdout).")
def ai_literacy_export(output) -> None:
    """Export the bounded JSON evidence snapshot for an auditor."""
    from ..ai_literacy import export_evidence
    try:
        payload = export_evidence()
    except (ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    if output:
        try:
            fd = os.open(str(output), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload + "\n")
        except OSError as e:
            raise click.ClickException(f"could not write {output}: {e}") from e
        click.echo(f"wrote {output}")
    else:
        click.echo(payload)


@main.group("eu-conformity")
def eu_conformity_group() -> None:
    """EU AI Act conformity workflows: FRIA, Annex IV, Art. 43 route.

    Human-gated registers: the Art. 27 fundamental-rights impact assessment
    as six named assertions, the Art. 11(1)/Annex IV technical-documentation
    checklist, and the Art. 43 conformity-assessment route with its
    forbidden combinations refused at the boundary. Drafting and tracking
    assistance only -- never a conformity assessment, CE marking, or legal
    advice.
    """


@eu_conformity_group.command("fria")
@click.argument("action", type=click.Choice(
    ["open", "assert", "complete", "outdate", "notify", "render", "list"]))
@click.option("--asset", "asset_id", default=None, help="Governed asset id.")
@click.option("--context", default=None, help="Deployment/use context key.")
@click.option("--record", "record_id", default=None, help="FRIA record id (ECF-...).")
@click.option("--element", default=None,
              help="Art. 27(1) element key (see the module's FRIA_ELEMENTS).")
@click.option("--content", default=None, help="The asserted element text.")
@click.option("--reason", default=None, help="Why the assessment is outdated.")
@click.option("--authority", default=None,
              help="Market surveillance authority notified (Art. 27(3)).")
@click.option("--reference", default=None, help="The authority's filing reference.")
@click.option("--at", "submitted_at", type=float, default=None,
              help="Unix timestamp of the notification filing.")
@click.option("--revision", type=int, default=0, help="Expected revision (CAS).")
@click.option("--status", default=None, help="list: filter by lifecycle status.")
@click.option("--by", "actor", default=None, help="Acting human.")
def eu_conformity_fria(action, asset_id, context, record_id, element, content,
                       reason, authority, reference, submitted_at, revision,
                       status, actor) -> None:
    """Drive one FRIA record through its Art. 27 lifecycle."""
    from .. import eu_conformity as euc

    def need(value, name):
        if value in (None, ""):
            raise click.ClickException(f"--{name} is required for {action}")
        return value

    try:
        if action == "open":
            record = euc.open_fria(
                need(asset_id, "asset"), context=need(context, "context"),
                actor=need(actor, "by"))
            click.echo(f"{record['id']} {record['status']} (rev {record['revision']})")
        elif action == "assert":
            record = euc.assert_fria_element(
                need(record_id, "record"), need(element, "element"),
                content=need(content, "content"), actor=need(actor, "by"),
                expected_revision=revision)
            done = sum(1 for value in record["elements"].values() if value)
            click.echo(f"{record['id']}: {done}/6 elements (rev {record['revision']})")
        elif action == "complete":
            record = euc.complete_fria(
                need(record_id, "record"), assessor=need(actor, "by"),
                expected_revision=revision)
            click.echo(f"{record['id']} completed by {record['assessor']}")
        elif action == "outdate":
            record = euc.mark_fria_outdated(
                need(record_id, "record"), reason=need(reason, "reason"),
                actor=need(actor, "by"), expected_revision=revision)
            click.echo(f"{record['id']} outdated (Art. 27(2))")
        elif action == "notify":
            record = euc.record_fria_notification(
                need(record_id, "record"), authority=need(authority, "authority"),
                submitted_by=need(actor, "by"),
                submitted_at=need(submitted_at, "at"),
                reference=need(reference, "reference"), expected_revision=revision)
            click.echo(f"{record['id']} notification recorded (Art. 27(3))")
        elif action == "render":
            click.echo(euc.render_fria(need(record_id, "record")))
        else:
            rows = euc.list_frias(status=status)
            if not rows:
                click.echo("no FRIA records.")
            for row in rows:
                click.echo(f"  {row['id']}  {row['status']:10s} "
                           f"{row['asset_id']}  {row['context']}")
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e


@eu_conformity_group.command("annex4")
@click.argument("action", type=click.Choice(["open", "assert", "status", "render"]))
@click.option("--asset", "asset_id", default=None, help="Governed asset id.")
@click.option("--record", "record_id", default=None, help="Checklist id (ECD-...).")
@click.option("--item", default=None, help="Annex IV item key.")
@click.option("--reference", default=None,
              help="Where the item's documentation lives.")
@click.option("--revision", type=int, default=0, help="Expected revision (CAS).")
@click.option("--by", "actor", default=None, help="Acting human.")
def eu_conformity_annex4(action, asset_id, record_id, item, reference,
                         revision, actor) -> None:
    """Track the Art. 11(1)/Annex IV technical-documentation checklist."""
    from .. import eu_conformity as euc

    def need(value, name):
        if value in (None, ""):
            raise click.ClickException(f"--{name} is required for {action}")
        return value

    try:
        if action == "open":
            record = euc.open_annex_iv(need(asset_id, "asset"), actor=need(actor, "by"))
            click.echo(f"{record['id']} 0/9 (rev {record['revision']})")
        elif action == "assert":
            record = euc.assert_annex_iv_item(
                need(record_id, "record"), need(item, "item"),
                reference=need(reference, "reference"), actor=need(actor, "by"),
                expected_revision=revision)
            state = euc.annex_iv_status(record["id"])
            click.echo(f"{record['id']} {state['asserted']}/{state['total']}")
        elif action == "status":
            state = euc.annex_iv_status(need(record_id, "record"))
            click.echo(f"{state['id']} {state['asserted']}/{state['total']}"
                       f"{'  COMPLETE' if state['complete'] else ''}")
            for missing in state["missing"]:
                click.echo(f"  missing {missing}")
        else:
            click.echo(euc.render_annex_iv(need(record_id, "record")))
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e


@eu_conformity_group.command("route")
@click.argument("action", type=click.Choice(["set", "modify", "show"]))
@click.option("--asset", "asset_id", required=True, help="Governed asset id.")
@click.option("--route", default=None,
              help="internal_control_annex_vi | notified_body_annex_vii | "
                   "sectoral_annex_i")
@click.option("--point", "annex_iii_point", type=int, default=None,
              help="Annex III point (1-8); omit for sectoral Annex I.")
@click.option("--harmonised-standards", is_flag=True,
              help="Harmonised standards (Art. 40) applied in full.")
@click.option("--common-specifications", is_flag=True,
              help="Common specifications (Art. 41) applied.")
@click.option("--rationale", default=None, help="Why this route.")
@click.option("--reason", default=None, help="modify: what changed.")
@click.option("--predetermined", is_flag=True,
              help="modify: the change was pre-determined and documented "
                   "(not a substantial modification).")
@click.option("--revision", type=int, default=0, help="Expected revision (CAS).")
@click.option("--by", "actor", default=None, help="Acting human.")
def eu_conformity_route(action, asset_id, route, annex_iii_point,
                        harmonised_standards, common_specifications,
                        rationale, reason, predetermined, revision, actor) -> None:
    """Record the Art. 43 conformity-assessment route and its changes."""
    from .. import eu_conformity as euc

    def need(value, name):
        if value in (None, ""):
            raise click.ClickException(f"--{name} is required for {action}")
        return value

    try:
        if action == "set":
            record = euc.record_assessment_route(
                asset_id, route=need(route, "route"),
                annex_iii_point=annex_iii_point,
                harmonised_standards_applied=harmonised_standards,
                common_specifications_applied=common_specifications,
                rationale=need(rationale, "rationale"), actor=need(actor, "by"))
            click.echo(f"{record['id']} {record['route']} (rev {record['revision']})")
        elif action == "modify":
            record = euc.mark_substantial_modification(
                asset_id, reason=need(reason, "reason"), actor=need(actor, "by"),
                expected_revision=revision, predetermined=predetermined)
            click.echo(f"{record['id']} {record['status']}")
        else:
            record = euc.get_assessment_route(asset_id)
            if record is None:
                raise click.ClickException(f"no route recorded for {asset_id!r}")
            click.echo(f"{record['id']}  {record['route']}  {record['status']}  "
                       f"point {record.get('annex_iii_point') or '-'}")
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e


@eu_conformity_group.command("pmm")
@click.argument("action", type=click.Choice(["record", "review", "show", "list"]))
@click.option("--asset", "asset_id", default=None, help="Governed asset id.")
@click.option("--plan", "plan_reference", default=None,
              help="Where the documented monitoring plan lives.")
@click.option("--outcome", default=None, help="review: no_change | plan_updated.")
@click.option("--revision", type=int, default=0, help="Expected revision (CAS).")
@click.option("--by", "actor", default=None, help="Acting human.")
def eu_conformity_pmm(action, asset_id, plan_reference, outcome,
                      revision, actor) -> None:
    """Track the Art. 72 post-market monitoring plan per asset."""
    from .. import eu_conformity as euc

    def need(value, name):
        if value in (None, ""):
            raise click.ClickException(f"--{name} is required for {action}")
        return value

    try:
        if action == "record":
            record = euc.record_pmm_plan(
                need(asset_id, "asset"),
                plan_reference=need(plan_reference, "plan"),
                actor=need(actor, "by"))
            click.echo(f"{record['id']} {record['citation']} (rev {record['revision']})")
        elif action == "review":
            record = euc.record_pmm_review(
                need(asset_id, "asset"), outcome=need(outcome, "outcome"),
                actor=need(actor, "by"), expected_revision=revision,
                plan_reference=plan_reference)
            click.echo(f"{record['id']} reviews {len(record['reviews'])}")
        elif action == "show":
            record = euc.get_pmm_plan(need(asset_id, "asset"))
            if record is None:
                raise click.ClickException(f"no monitoring plan for {asset_id!r}")
            click.echo(f"{record['id']}  {record['plan_reference']}  "
                       f"reviews {len(record.get('reviews') or [])}")
        else:
            for record in euc.list_pmm_plans():
                click.echo(f"{record['asset_id']}  {record['plan_reference']}")
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e


@eu_conformity_group.command("registration")
@click.argument("action", type=click.Choice(["record", "show", "list"]))
@click.option("--asset", "asset_id", default=None, help="Governed asset id.")
@click.option("--basis", default=None,
              help="provider_high_risk | provider_not_high_risk | "
                   "public_authority_deployer | annex_iii_point_2_national")
@click.option("--registry", default=None, help="eu_database | national.")
@click.option("--reference", default=None, help="The registry's reference.")
@click.option("--by", "actor", default=None, help="Acting human.")
def eu_conformity_registration(action, asset_id, basis, registry,
                               reference, actor) -> None:
    """Record completed Art. 49 registrations; wrong registries are refused."""
    from .. import eu_conformity as euc

    def need(value, name):
        if value in (None, ""):
            raise click.ClickException(f"--{name} is required for {action}")
        return value

    try:
        if action == "record":
            record = euc.record_registration(
                need(asset_id, "asset"), basis=need(basis, "basis"),
                registry=need(registry, "registry"),
                reference=need(reference, "reference"), actor=need(actor, "by"))
            click.echo(f"{record['id']} {record['citation']} {record['registry']}")
        elif action == "show":
            record = euc.get_registration(need(asset_id, "asset"))
            if record is None:
                raise click.ClickException(f"no registration for {asset_id!r}")
            click.echo(f"{record['id']}  {record['basis']}  {record['registry']}  "
                       f"{record['reference']}")
        else:
            for record in euc.list_registrations():
                click.echo(f"{record['asset_id']}  {record['basis']}  "
                           f"{record['registry']}")
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e


@eu_conformity_group.command("gpai")
@click.argument("action", type=click.Choice(["open", "assert", "status", "list"]))
@click.option("--asset", "asset_id", default=None, help="Governed asset id.")
@click.option("--record", "record_id", default=None, help="Register id (ECG-...).")
@click.option("--systemic-risk", is_flag=True,
              help="open: the model has systemic risk (adds Art. 55(1)).")
@click.option("--open-source-exempt", is_flag=True,
              help="open: apply the Art. 53(2) exemption for (a)/(b).")
@click.option("--obligation", default=None, help="Art. 53/55 obligation key.")
@click.option("--reference", default=None,
              help="Where the obligation's evidence lives.")
@click.option("--revision", type=int, default=0, help="Expected revision (CAS).")
@click.option("--by", "actor", default=None, help="Acting human.")
def eu_conformity_gpai(action, asset_id, record_id, systemic_risk,
                       open_source_exempt, obligation, reference,
                       revision, actor) -> None:
    """Track the Art. 53/55 general-purpose AI provider obligations."""
    from .. import eu_conformity as euc

    def need(value, name):
        if value in (None, ""):
            raise click.ClickException(f"--{name} is required for {action}")
        return value

    try:
        if action == "open":
            record = euc.open_gpai(
                need(asset_id, "asset"), systemic_risk=systemic_risk,
                open_source_exempt=open_source_exempt, actor=need(actor, "by"))
            click.echo(f"{record['id']} systemic {record['systemic_risk']} "
                       f"(rev {record['revision']})")
        elif action == "assert":
            record = euc.assert_gpai_obligation(
                need(record_id, "record"), need(obligation, "obligation"),
                reference=need(reference, "reference"), actor=need(actor, "by"),
                expected_revision=revision)
            state = euc.gpai_status(record["id"])
            click.echo(f"{state['asserted']}/{state['total']} evidenced")
        elif action == "status":
            state = euc.gpai_status(need(record_id, "record"))
            click.echo(f"{state['id']}  {state['asserted']}/{state['total']}  "
                       f"missing: {', '.join(state['missing']) or '-'}"
                       + (f"  exempt: {', '.join(state['exempt'])}"
                          if state['exempt'] else ""))
        else:
            for record in euc.list_gpai_registers():
                state = euc.gpai_status(record["id"])
                click.echo(f"{record['asset_id']}  "
                           f"{state['asserted']}/{state['total']}  "
                           f"systemic {state['systemic_risk']}")
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e


@eu_conformity_group.command("ifu")
@click.argument("action", type=click.Choice(["open", "assert", "status", "list"]))
@click.option("--asset", "asset_id", default=None, help="Governed asset id.")
@click.option("--record", "record_id", default=None, help="Checklist id (ECI-...).")
@click.option("--item", default=None, help="Art. 13(3) item key.")
@click.option("--reference", default=None,
              help="Where the item's documentation lives.")
@click.option("--revision", type=int, default=0, help="Expected revision (CAS).")
@click.option("--by", "actor", default=None, help="Acting human.")
def eu_conformity_ifu(action, asset_id, record_id, item, reference,
                      revision, actor) -> None:
    """Track the Art. 13(3) instructions-for-use content checklist."""
    from .. import eu_conformity as euc

    def need(value, name):
        if value in (None, ""):
            raise click.ClickException(f"--{name} is required for {action}")
        return value

    try:
        if action == "open":
            record = euc.open_ifu(need(asset_id, "asset"), actor=need(actor, "by"))
            click.echo(f"{record['id']} (rev {record['revision']})")
        elif action == "assert":
            record = euc.assert_ifu_item(
                need(record_id, "record"), need(item, "item"),
                reference=need(reference, "reference"), actor=need(actor, "by"),
                expected_revision=revision)
            state = euc.ifu_status(record["id"])
            click.echo(f"{state['asserted']}/{state['total']} documented")
        elif action == "status":
            state = euc.ifu_status(need(record_id, "record"))
            click.echo(f"{state['id']}  {state['asserted']}/{state['total']}  "
                       f"missing: {', '.join(state['missing']) or '-'}")
        else:
            for record in euc.list_ifu_checklists():
                state = euc.ifu_status(record["id"])
                click.echo(f"{record['asset_id']}  "
                           f"{state['asserted']}/{state['total']}")
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e


@main.group("governance-alerts")
def governance_alerts_group() -> None:
    """The needs-attention digest across the governance registers.

    Read-side aggregation of Art. 73 clocks, Art. 4 literacy gaps, and
    Art. 27(2)/43(4) staleness. Cron this around your own delivery channel;
    nothing is transmitted anywhere by the platform.
    """


@governance_alerts_group.command("due")
@click.option("--due-soon-hours", type=int, default=48,
              help="Incident clocks within this many hours count as due soon.")
@click.option("--expiring-days", type=int, default=30,
              help="Trainings expiring within this many days are flagged.")
@click.option("--json", "as_json", is_flag=True, help="Emit the digest as JSON.")
def governance_alerts_due(due_soon_hours, expiring_days, as_json) -> None:
    """Print what needs attention now; exit 1 when anything does."""
    import json as json_module

    from .. import governance_alerts

    try:
        report = governance_alerts.digest(
            due_soon_seconds=due_soon_hours * 3600.0,
            expiring_days=expiring_days,
        )
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    if as_json:
        click.echo(json_module.dumps(report, indent=2, sort_keys=True))
    else:
        sections = report["sections"]
        incidents = sections["serious_incidents"]
        if incidents.get("enabled"):
            click.echo(f"serious incidents: {len(incidents['overdue'])} overdue, "
                       f"{len(incidents['due_soon'])} due soon (Art. 73)")
        literacy = sections["ai_literacy"]
        if literacy.get("enabled"):
            click.echo(f"ai literacy: {literacy['missing']} missing, "
                       f"{literacy['expired']} expired, "
                       f"{literacy['expiring_soon']} expiring soon (Art. 4)")
        conformity = sections["eu_conformity"]
        if conformity.get("enabled"):
            click.echo(f"eu conformity: {len(conformity['outdated_frias'])} "
                       f"outdated FRIAs, {len(conformity['reassessment_required'])} "
                       "routes need reassessment (Art. 27(2)/43(4))")
        click.echo(f"attention items: {report['attention_count']}")
    if report["attention_count"]:
        raise SystemExit(1)


@main.group("explainability")
def explainability_group() -> None:
    """Model-agnostic explainability over logged feature/score data.

    Spearman global sensitivity and standardized-deviation reason codes run
    headless over a JSON file; permutation importance and partial dependence
    take a scoring callable and live in the Python API. Reports can be
    recorded as governed evidence. Attribution for the operator's own
    review -- never a fairness certification or legal advice.
    """


def _explainability_payload(data_file: str) -> dict:
    import json as json_module
    from pathlib import Path

    try:
        payload = json_module.loads(Path(data_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise click.ClickException(f"could not read {data_file}: {e}") from e
    if not isinstance(payload, dict):
        raise click.ClickException("the data file must contain a JSON object")
    return payload


def _explainability_record(report: dict, asset_id, source_id, actor) -> None:
    from .. import explainability

    if asset_id in (None, ""):
        return
    if actor in (None, ""):
        raise click.ClickException("--by is required when recording evidence")
    evidence = explainability.record_explainability_evidence(
        asset_id, report, source_id=source_id, actor=actor,
    )
    click.echo(f"evidence {evidence['id']} (rev {evidence['revision']})")


@explainability_group.command("sensitivity")
@click.option("--file", "data_file", required=True,
              help='JSON file: {"features": {name: [...]}, "scores": [...]}.')
@click.option("--asset", "asset_id", default=None,
              help="Record the report as evidence for this governed asset.")
@click.option("--by", "actor", default=None, help="Acting human.")
def explainability_sensitivity(data_file, asset_id, actor) -> None:
    """Rank features by |Spearman| against logged scores."""
    from .. import explainability

    payload = _explainability_payload(data_file)
    try:
        report = explainability.global_sensitivity(
            payload.get("features") or {}, payload.get("scores") or [],
        )
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    for row in report["features"]:
        click.echo(f"{row['rank']:>3}  {row['feature']}  spearman {row['spearman']:+.4f}")
    _explainability_record(report, asset_id, "sensitivity", actor)


@explainability_group.command("reasons")
@click.option("--file", "data_file", required=True,
              help='JSON file: {"features": {name: [...]}, "instance": {name: value}}.')
@click.option("--top", "top_k", type=int, default=4, help="Reason codes to emit.")
@click.option("--asset", "asset_id", default=None,
              help="Record the report as evidence for this governed asset.")
@click.option("--by", "actor", default=None, help="Acting human.")
def explainability_reasons(data_file, top_k, asset_id, actor) -> None:
    """Standardized-deviation reason codes for one decision."""
    from .. import explainability

    payload = _explainability_payload(data_file)
    try:
        report = explainability.reason_codes(
            payload.get("features") or {},
            payload.get("instance") or {},
            top_k=top_k,
        )
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    for row in report["reasons"]:
        click.echo(row["statement"])
    _explainability_record(report, asset_id, "reasons", actor)


@main.group("gateway")
def gateway_group() -> None:
    """The governed LLM gateway: status and the shadow-caller ledger.

    Agents built anywhere point their OpenAI-compatible base URL at the
    dashboard's /gateway/v1/chat/completions; every call crosses a governed
    checkpoint and unknown callers land here. Bodies are never stored.
    """


@gateway_group.command("status")
def gateway_status_command() -> None:
    """One-line gateway state and aggregate call counts."""
    from .. import llm_gateway

    state = llm_gateway.gateway_status()
    click.echo(
        f"enabled {state['enabled']}  callers {state['callers']}  "
        f"caller/model pairs {state['caller_model_pairs']}  "
        f"requests {state['request_count']}"
    )
    for status, count in sorted(state["last_status_counts"].items()):
        click.echo(f"  {status}: {count}")


@gateway_group.command("callers")
@click.option("--limit", type=int, default=50, help="Rows to list (1-500).")
def gateway_callers(limit) -> None:
    """The shadow-caller ledger, most recent first."""
    from .. import llm_gateway

    try:
        rows = llm_gateway.list_callers(limit=limit)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    for row in rows:
        click.echo(
            f"{row['caller_digest']}  {row['model']}  "
            f"calls {row['request_count']}  last {row['last_status']}"
        )
    if not rows:
        click.echo("no gateway callers recorded")


@main.group("content-provenance")
def content_provenance_group() -> None:
    """Signed, machine-readable provenance manifests (EU AI Act Art. 50(2)).

    Sidecar manifests bind exact content bytes to the generating asset and
    verify against an EXTERNAL key registry. Marking assistance for the
    operator's own content -- never a content-authenticity certification.
    """


@content_provenance_group.command("sign")
@click.option("--file", "content_file", required=True,
              help="The generated content file to sign.")
@click.option("--asset", "asset_id", required=True, help="Generating asset id.")
@click.option("--modality", required=True,
              type=click.Choice(["text", "image", "audio", "video"]))
@click.option("--receipt", "receipt_id", default=None,
              help="Optional delivery receipt id to bind.")
@click.option("--out", "out_file", default=None,
              help="Manifest path (default: <file>.provenance.json).")
@click.option("--by", "actor", default=None, help="Acting human.")
def content_provenance_sign(content_file, asset_id, modality, receipt_id,
                            out_file, actor) -> None:
    """Sign one sidecar provenance manifest for a content file."""
    import json as json_module
    from pathlib import Path

    from .. import content_provenance

    if actor in (None, ""):
        raise click.ClickException("--by is required")
    try:
        content = Path(content_file).read_bytes()
    except OSError as e:
        raise click.ClickException(f"could not read {content_file}: {e}") from e
    try:
        manifest = content_provenance.create_manifest(
            content, asset_id=asset_id, modality=modality,
            actor=actor, receipt_id=receipt_id,
        )
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    destination = Path(out_file) if out_file else Path(f"{content_file}.provenance.json")
    destination.write_text(
        json_module.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8",
    )
    click.echo(f"{destination} manifest {manifest['manifest_sha256'][:16]}... "
               f"key {manifest['attestation']['key_id']}")


@content_provenance_group.command("verify")
@click.option("--file", "content_file", required=True,
              help="The content file the manifest claims.")
@click.option("--manifest", "manifest_file", required=True,
              help="The sidecar manifest to verify.")
@click.option("--keys", "keys_file", required=True,
              help='External key registry JSON: {"<key_id>": "<pubkey hex>"}.')
def content_provenance_verify(content_file, manifest_file, keys_file) -> None:
    """Verify a manifest and content against an external key registry."""
    import json as json_module
    from pathlib import Path

    from .. import content_provenance

    try:
        content = Path(content_file).read_bytes()
        manifest = json_module.loads(Path(manifest_file).read_text(encoding="utf-8"))
        keys = json_module.loads(Path(keys_file).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise click.ClickException(f"could not read inputs: {e}") from e
    valid = content_provenance.verify_manifest(
        manifest, trusted_pubkeys=keys, content=content,
    )
    click.echo("VALID" if valid else "INVALID")
    if not valid:
        raise SystemExit(1)


@main.group("underwriting-pack")
def underwriting_pack_group() -> None:
    """The AI-insurance underwriting evidence pack.

    A bounded governance-posture summary wrapped around the signed,
    externally verifiable assurance pack. Decision support for the
    operator's own submission -- never an insurance application or rating.
    """


@underwriting_pack_group.command("export")
@click.option("--out", "out_file", required=True, help="Pack output path.")
@click.option("--asset", "asset_id", default=None,
              help="Limit the pack to one governed asset.")
@click.option("--by", "actor", default=None, help="Acting human.")
def underwriting_pack_export(out_file, asset_id, actor) -> None:
    """Export one signed underwriting evidence pack."""
    import json as json_module
    from pathlib import Path

    from .. import underwriting_pack

    if actor in (None, ""):
        raise click.ClickException("--by is required")
    try:
        pack = underwriting_pack.export_underwriting_pack(
            actor=actor, asset_id=asset_id,
        )
    except (KeyError, ValueError, RuntimeError) as e:
        raise click.ClickException(str(e)) from e
    Path(out_file).write_text(
        json_module.dumps(pack, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    summary = pack["summary"]
    click.echo(f"{out_file}  assets {summary['inventory_count']}  "
               f"pack {summary['assurance_pack_sha256'][:16]}...")
