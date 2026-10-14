from pathlib import Path
root=Path("C:\\Users\\Cstep\\Documents\\Codex\\2026-09-10\\new-chat\\work\\lightwork-2529-integration-20261005")
path=root/'docs/FEATURES.md'
text=path.read_text(encoding='utf-8')
text += """
### Local rule combinations, single-step checks and reported financial alternatives

| Product control | Native path and operating boundary |
| --- | --- |
| Check local rules together | Learning → Check local rules together. Choose two owned published pure rules and permitted retained cases. All supported finite starting values, both orders and separate native references count toward one reviewed work allowance. A qualified request can run with ordinary named inputs; it remains unpublished. |
| Prepare an answer for this request | Learning → Prepare an answer for this request. Choose up to three existing taught steps, permitted original cases and explicit desired answers. Every sequence through three steps is checked by the native runner. Only complete agreement among all surviving arrangements supports an answer. No arbitrary code or connected action is generated. |
| Try one taught step | Workflow builder or Designer → select a taught local rule with AI fallback off → Try. Ordinary exact text, whole-number and Yes/No inputs lead to a complete review and one native step. AI calls and connected actions are refused; the original request is retained without automatic replay. |
| Compare additional-work economics | Promise reviews → select current additional-work qualification → enter complete financial assumptions. Exact currency amounts and all incremental expenses are compared across jointly feasible offer/addition combinations while every accepted commitment stays protected. Demand, source availability and financial premises remain team reports. |

Each capability has a default-off Features control and an installer choice.
Native ownership, current authority, source custody, learning stops and review
expiry apply. Original records are distinct from fresh calculations and remain
unconfirmed after a calculation-to-receipt gap. Historical private values require
current source permission. These local mechanisms do not publish workflows,
reserve resources, establish future accuracy or demonstrate research superiority.
"""
path.write_text(text,encoding='utf-8',newline='\n')
path=root/'docs/research/lightwork-invention-implementation-status-2026-10-01.md'
text=path.read_text(encoding='utf-8')
replacement={
'P02': '[Joint procedure checks](../../packages/maverick-core/maverick/flow/procedure_interactions.py) run two owned published pure rules over the complete supported finite starting domain, both orders, repeated local state and fresh independent native references. Current permitted retained cases and all surviving source alternatives remain bound. Conditional guards preserve both separate outputs; ambiguous orders stay unresolved. The ordinary page and protected Copilot use durable request custody. This models local read/write interactions; it does not discover remote effects, authorize approvals, establish future accuracy or show research superiority.',
'P09': '[Request-specific native arrangements](../../packages/maverick-core/maverick/flow/request_program.py) compile every declared sequence of one to three owned taught steps through three positions, preserving complete original artifact alternatives. Every permitted retained case has an explicit desired answer; surviving arrangements run the current request through the unchanged native runner. Only complete answer agreement is accepted. Ordinary controls and protected Copilot author handoff preserve current source custody and exact originals. Prose describes the request; it is not an executable specification. No arbitrary generated program, connected action or workflow publication is supported.',
'P12': '[Conditional economic alternatives](../../packages/maverick-core/maverick/commitment_economic_alternatives.py) enumerate jointly feasible offers and inclusion-minimal resource additions under every surviving current capacity explanation while protecting all accepted commitments. Complete exact entered money and permission premises bind the conditional contribution/cash-outlay comparison. Native controls and typed Copilot preserve original effects, withdrawals and unknowns without allowance refund. Costs, demand and availability remain attributed reports; no general enterprise economic model, actual revenue or optimizer advantage is inferred.',
}
lines=text.splitlines()
for i,line in enumerate(lines):
    for key,value in replacement.items():
        if line.startswith('| '+key+' |'):
            lines[i]='| '+key+' | Bounded pilot; mechanism unproven | '+value+' |'
text='\n'.join(lines)+'\n'
text=text.replace('but neither complete customer operation nor a distinct invention is qualified | 11 |','but neither complete customer operation nor a distinct invention is qualified | 14 |')
text=text.replace('without its proposed general advance | 5 |','without its proposed general advance | 3 |')
text=text.replace('a research question | 2 |','a research question | 1 |')
path.write_text(text,encoding='utf-8',newline='\n')
path=root/'docs/research/lightwork-invention-register.md'
text=path.read_text(encoding='utf-8')+"""
October 6 follow-through adds bounded P02 joint native rule checks, P09
request-specific native rule arrangements and P12 conditional economic alternatives.
The implementation-status register records their exact native scope. Full source
alternatives, separately qualified current cases, independent native comparisons,
ordinary controls and protected Copilot are retained. None supplies a general
remote-effect contract, unbounded executable intent, actual demand, realized profit,
future accuracy or an independently demonstrated research advantage. Rejected
P06/P07/P11/P14 experiments remain retained.
"""
path.write_text(text,encoding='utf-8',newline='\n')
print('Documented exact supported controls and all 28 stable invention statuses.')

