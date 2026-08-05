"""The Autarch CLI — preside over the council from your terminal.

  autarch do "create a file called hello.txt that says hi"
  autarch do "delete x.txt" --council mock:bold --council mock:cautious
  autarch why <why_id>
  autarch history
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

from .agent import Agent, capability
from .adapters.filesystem import FileSystemAdapter
from .contracts import HumanDecision
from .economy import Budget
from .health import health_check
from .kernel import CapabilityKernel
from .memory import WhyMemory
from .mesh import Realm, export_bundle, import_bundle
from .policy import Policy, PolicyEffect
from .guarantees import Invariant, prove_guarantees
from .provenance import NodeIdentity, available as provenance_available
from .rewind import Rewinder, parse_duration
from .substrate import Substrate

# Default grants for the CLI: read/write/move inside the sandbox, but NOT delete.
# Deletion is intentionally ungranted so the kernel denies it by default — a live
# demonstration of deny-by-default governance.
_DEFAULT_GRANTS = ["file.read", "file.write", "file.move"]


def _default_cli_policies() -> List[Policy]:
    """Illustrative policy-as-code: large writes need explicit human ratification."""
    return [
        Policy(
            name="large-write-needs-ratify",
            effect=PolicyEffect.REQUIRE_RATIFY.value,
            capability="file.write",
            when=lambda params: len(str(params.get("content", ""))) > 280,
            reason="Large writes (>280 chars) require explicit ratification.",
        ),
    ]


def _build_grants(names: List[str]):
    grants = []
    for name in names:
        scope = {"path_prefix": "."} if name.startswith("file.") else {}
        grants.append(capability(name, scope=scope))
    return grants


def _build_budget(args: argparse.Namespace):
    """Assemble a Budget from optional --budget-* flags (None if none given)."""
    limits = {}
    if getattr(args, "budget_cost", None) is not None:
        limits["cost"] = args.budget_cost
    if getattr(args, "budget_calls", None) is not None:
        limits["calls"] = args.budget_calls
    if getattr(args, "budget_risk", None) is not None:
        limits["risk"] = args.budget_risk
    return Budget(limits=limits) if limits else None


def _format_tally(tally: dict) -> str:
    return ", ".join(f"{verdict}\u00d7{count}" for verdict, count in tally.items()) or "\u2014"


def _render(deliberation, gate, policy, precedent) -> None:
    """Observer: print the council's deliberation as a fast, legible verdict."""
    d = deliberation
    print("\n\u2550\u2550\u2550 AUTARCH \u2550\u2550\u2550")
    print(f"Intent: {d.intent.text}")
    if d.rounds > 1:
        print(f"(re-deliberation, round {d.rounds})")
    print(f"Council: {', '.join(d.voices)}")

    if d.proposal_disagreement:
        print("\nThe voices propose different actions:")
        for pos in d.proposals:
            if pos.action is not None:
                print(f"  \u2022 {pos.voice}  \u2192  {pos.action.capability}  {json.dumps(pos.action.params)}")
            else:
                print(f"  \u2022 {pos.voice}  \u2192  (abstains)")

    motion = d.motion
    if motion is None:
        print("\nNo actionable motion \u2014 the council found nothing safe to do.")
        return

    print(f"\nMotion: {motion.capability}  {json.dumps(motion.params)}")
    if d.proposal.rationale:
        print(f"  rationale: {d.proposal.rationale}")

    print("Critique:")
    for pos in d.critiques:
        print(f"  \u2022 {pos.voice}  \u2192  {pos.verdict}: {pos.rationale}")

    badge = "DISAGREEMENT" if d.has_disagreement else "consensus"
    print(f"\nTally: {_format_tally(d.tally)}   [{badge}]")
    print(f"Capability gate: {'ALLOWED' if gate.allowed else 'DENIED'} \u2014 {gate.reason}")
    if policy is not None and policy.note():
        print(f"Policy: {policy.note()}")
    if precedent is not None:
        print(f"Precedent: {precedent.note()}")
    print(f"Recommendation: {d.recommendation}")


def _interactive_preside(deliberation, gate) -> str:
    policy = getattr(deliberation, "policy", None)
    if not gate.allowed or (policy is not None and policy.denies):
        print("\nThe kernel or policy forbids this action; it cannot be ratified.")
        return HumanDecision.OVERRULE.value
    try:
        answer = input("\nPreside \u25b8 [r]atify / [o]verrule / [s]end back? ").strip().lower()
    except EOFError:
        answer = "o"
    if answer.startswith("r"):
        return HumanDecision.RATIFY.value
    if answer.startswith("s"):
        return HumanDecision.SEND_BACK.value
    return HumanDecision.OVERRULE.value


def cmd_do(args: argparse.Namespace) -> int:
    grant_names = list(_DEFAULT_GRANTS)
    for extra in args.grant or []:
        if extra not in grant_names:
            grant_names.append(extra)

    # If this workspace has joined a realm, adopt its node identity and its
    # shared policy set so actions here are governed by the realm's rules.
    realm = Realm.load(args.workspace)
    node_id = realm.node_id if realm else "local"
    policies = _default_cli_policies()
    if realm:
        policies = realm.realm_policies() + policies

    interactive = not args.yes
    budget = _build_budget(args)
    agent = Agent(
        intent=args.intent,
        council=args.council,
        grants=_build_grants(grant_names),
        workspace=args.workspace,
        policies=policies,
        auto_preside=True,
        preside_fn=(_interactive_preside if interactive else None),
        on_round=_render,
        node_id=node_id,
        budget=budget,
    )
    if realm:
        print(f"(realm '{realm.name}' \u2014 node {realm.node_id})")

    result = agent.run()
    action = result.action

    print()
    if action is None:
        print("\u2014 Nothing to do.")
        print(f"  Why-record: {result.why_id}")
        return 0

    if args.yes:
        print(f"Auto-preside: {result.human_decision}")

    if result.executed and result.result is not None:
        print(f"\u2713 Executed: {result.result.output}")
    elif result.budget_decision is not None and not result.budget_decision.ok:
        print(f"\u2717 Refused: {result.budget_decision.reason}")
    elif result.human_decision == HumanDecision.RATIFY.value and result.result and result.result.error:
        print(f"\u2717 Failed: {result.result.error}")
    else:
        print(f"\u2014 Not executed (decision: {result.human_decision}).")
    if agent.budget is not None:
        print(f"  Budget: {', '.join(f'{k} {v}' for k, v in agent.budget.snapshot().items())}")
    print(f"  Why-record: {result.why_id}   (run `autarch why {result.why_id}`)")
    return 0


def cmd_why(args: argparse.Namespace) -> int:
    memory = WhyMemory(_db_path(args.workspace))
    record = memory.get(args.id)
    if record is None:
        print(f"No record found for {args.id}")
        return 1
    print(f"\nWHY {record.id}")
    print(f"  Intent:     {record.intent_text}")
    print(f"  Action:     {record.capability}  {json.dumps(record.params)}")
    print(f"  Proposer:   {record.proposer} \u2014 {record.rationale}")
    print(f"  Challenger: {record.challenger} \u2014 {record.critique_verdict} \u2014 {record.critique_reasons}")
    if record.voices:
        print(f"  Council:    {', '.join(record.voices)}")
    if record.tally:
        print(f"  Tally:      {_format_tally(record.tally)}  (recommendation: {record.recommendation})")
    if record.rounds and record.rounds > 1:
        print(f"  Rounds:     {record.rounds}")
    gate = "ALLOWED" if record.gate_allowed else "DENIED"
    print(f"  Gate:       {gate} \u2014 {record.gate_reason}")
    if record.policy_note:
        print(f"  Policy:     {record.policy_note}")
    if record.precedent_note:
        print(f"  Precedent:  {record.precedent_note}")
    print(f"  Decision:   {record.human_decision}")
    if record.executed:
        print(f"  Executed:   yes \u2014 {record.result_output}")
    else:
        detail = record.result_error or "not executed"
        print(f"  Executed:   no \u2014 {detail}")
    if record.undo:
        print(f"  Reversible: yes \u2014 {record.undo.get('capability')} on {record.undo.get('path')}")
    return 0


def _db_path(workspace: str) -> str:
    from pathlib import Path

    return str(Path(workspace) / ".autarch" / "why.db")


def cmd_guarantee(args: argparse.Namespace) -> int:
    """Statically PROVE safety invariants over a configuration of grants + policies.

    Exits non-zero if any invariant is not guaranteed, so it can gate CI.
    """
    grant_names = list(_DEFAULT_GRANTS)
    for extra in args.grant or []:
        if extra not in grant_names:
            grant_names.append(extra)
    grants = _build_grants(grant_names)

    realm = Realm.load(args.workspace)
    policies = _default_cli_policies()
    if realm:
        policies = realm.realm_policies() + policies

    invariants: List[Invariant] = []
    for cap in args.forbid or []:
        invariants.append(Invariant.forbid(cap))
    for cap in args.require_approval or []:
        invariants.append(Invariant.require_approval(cap))
    for spec in args.confine or []:
        if "=" not in spec:
            print(f"--confine expects CAPABILITY=PREFIX, got '{spec}'")
            return 2
        cap, prefix = spec.split("=", 1)
        invariants.append(Invariant.confine(cap, prefix))

    if not invariants:
        print("Nothing to prove. Use --forbid, --require-approval, or --confine.")
        return 2

    report = prove_guarantees(invariants, grants, policies)
    print("\n\u2550\u2550\u2550 GUARANTEES \u2550\u2550\u2550")
    print(f"Grants:   {', '.join(g.name for g in grants)}")
    print(f"Policies: {', '.join(p.name for p in policies) or '(none)'}\n")
    for proof in report.proofs:
        mark = "\u2713 GUARANTEED" if proof.holds else "\u2717 NOT GUARANTEED"
        print(f"  {mark}  \u2014  {proof.invariant.label()}")
        print(f"        {proof.reason}")
        if not proof.holds and proof.counterexample:
            print(f"        counterexample: grant '{proof.counterexample}'")
    if report.all_hold:
        print("\nAll invariants are provably guaranteed before the agent runs.")
        return 0
    print(f"\n{len(report.failures())} invariant(s) NOT guaranteed. (CI: non-zero exit)")
    return 1


def cmd_identity(args: argparse.Namespace) -> int:
    """Show (creating if needed) this workspace's cryptographic node identity."""
    if not provenance_available():
        print("Cryptographic identity is unavailable: the `cryptography` package is not installed.")
        print("Install it with:  pip install autarch[crypto]")
        print("Without it, actions are recorded but not signed.")
        return 1
    identity = NodeIdentity.load_or_create(args.workspace)
    print("\nNode identity (this workspace):")
    print(f"  node id:    {identity.node_id}")
    print(f"  public key: {identity.public_hex}")
    print(f"  can sign:   {identity.can_sign}")
    print("\nThe node id is derived from the public key, so it cannot be impersonated")
    print("without this node's private key. Actions taken here are signed and")
    print("verifiable by anyone holding the public key.")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    """Operational health/readiness check (for container probes & monitoring)."""
    report = health_check(args.workspace)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"\nstatus:  {report['status'].upper()}   (autarch {report['version']})")
        for name, check in report["checks"].items():
            print(f"  {name:10s} {check}")
    # Non-zero exit on error so a container probe can react.
    return 0 if report["status"] != "error" else 1


def cmd_audit(args: argparse.Namespace) -> int:
    return args.audit_func(args)


def cmd_audit_export(args: argparse.Namespace) -> int:
    memory = WhyMemory(_db_path(args.workspace))
    rows = memory.export_audit(args.file)
    ok, broken = memory.verify_chain()
    print(f"\u2713 Exported {len(rows)} record(s) to {args.file}")
    print(f"  Ledger integrity: {'intact' if ok else f'BROKEN at {broken}'}")
    redacted = sum(1 for r in rows if r["redacted_fields"])
    if redacted:
        print(f"  {redacted} record(s) have redacted fields (right-to-be-forgotten applied)")
    return 0


def cmd_audit_redact(args: argparse.Namespace) -> int:
    memory = WhyMemory(_db_path(args.workspace))
    if not memory.has(args.id):
        print(f"No record found for {args.id}")
        return 1
    n = memory.redact(args.id, reason=args.reason or "")
    print(f"\u2713 Redacted {n} field(s) of {args.id} (sealed payload untouched; integrity preserved)")
    ok, _ = memory.verify_chain()
    print(f"  Ledger still verifies: {ok}")
    return 0


def cmd_substrate(args: argparse.Namespace) -> int:
    sub = Substrate.detect()
    print("\nSubstrate (the host Autarch runs on):")
    print(f"  {sub.describe()}")
    print(f"  {sub.describe()}")
    print(f"  tags:     {', '.join(sub.tags)}")
    print(f"  data dir: {sub.data_dir()}")
    print("\nThe core is portable: same kernel, council, and memory on any of these.")
    return 0


def cmd_mesh(args: argparse.Namespace) -> int:
    return args.mesh_func(args)


def cmd_mesh_init(args: argparse.Namespace) -> int:
    existing = Realm.load(args.workspace)
    if existing and not args.force:
        print(f"This workspace already belongs to realm '{existing.name}' as node {existing.node_id}.")
        print("Use --force to re-initialize.")
        return 1

    policies = []
    if args.deny:
        for cap in args.deny:
            policies.append({
                "name": f"realm-deny-{cap}", "effect": PolicyEffect.DENY.value,
                "capability": cap, "reason": f"Realm forbids {cap}.",
            })

    if args.join:
        realm = Realm.join(args.realm, args.join, policies=policies)
        action = "joined"
    else:
        realm = Realm.create(args.realm, policies=policies)
        action = "created"
    realm.save(args.workspace)

    print(f"\n\u2713 {action.capitalize()} realm '{realm.name}' as node {realm.node_id}.")
    if action == "created":
        print("\nShare this realm key with your other devices so they can join:")
        print(f"  {realm.key_hex}")
        print("\nOn another device/workspace, run:")
        print(f"  autarch --workspace <other> mesh init --realm {realm.name} --join {realm.key_hex}")
    if policies:
        print(f"\nShared realm policies: {', '.join(p['capability'] + '=' + p['effect'] for p in policies)}")
    return 0


def cmd_mesh_status(args: argparse.Namespace) -> int:
    realm = Realm.load(args.workspace)
    if not realm:
        print("This workspace is not part of a mesh. Run `autarch mesh init` to start one.")
        return 0
    memory = WhyMemory(_db_path(args.workspace), node_id=realm.node_id)
    ok, broken = memory.verify_chain()
    print(f"\nRealm:    {realm.name}")
    print(f"Node:     {realm.node_id}")
    print(f"Records:  {len(memory.all())}  across origins: {', '.join(memory.origins()) or '(none)'}")
    print(f"Ledger:   {'intact' if ok else f'BROKEN at {broken}'}")
    if realm.policies:
        print(f"Policies: {', '.join(p['capability'] + '=' + p['effect'] for p in realm.policies)}")
    return 0


def cmd_mesh_export(args: argparse.Namespace) -> int:
    realm = Realm.load(args.workspace)
    if not realm:
        print("This workspace is not part of a mesh. Run `autarch mesh init` first.")
        return 1
    memory = WhyMemory(_db_path(args.workspace), node_id=realm.node_id)
    blob = export_bundle(memory, realm)
    out = Path(args.file)
    out.write_bytes(blob)
    print(f"\u2713 Exported {len(memory.all())} record(s) as an encrypted bundle: {out}")
    print("  Anyone without the realm key sees only ciphertext.")
    return 0


def cmd_mesh_import(args: argparse.Namespace) -> int:
    realm = Realm.load(args.workspace)
    if not realm:
        print("This workspace is not part of a mesh. Run `autarch mesh init` first.")
        return 1
    blob = Path(args.file).read_bytes()
    memory = WhyMemory(_db_path(args.workspace), node_id=realm.node_id)
    try:
        report = import_bundle(memory, realm, blob)
    except ValueError as exc:
        print(f"\u2717 Import failed: {exc}")
        return 1
    if report.policies_added:
        realm.save(args.workspace)  # adopt newly-shared realm policies
    print(f"\u2713 {report.summary()}")
    ok, broken = memory.verify_chain()
    print(f"  Ledger after merge: {'intact' if ok else f'BROKEN at {broken}'}")

    # Provenance: how many of the merged records are cryptographically authentic?
    authentic = forged = 0
    for record in memory.all():
        verdict = memory.verify_provenance(record.id)
        if verdict is True:
            authentic += 1
        elif verdict is False:
            forged += 1
    if authentic or forged:
        msg = f"  Authorship: {authentic} record(s) cryptographically verified"
        if forged:
            msg += f", {forged} FORGED"
        print(msg)
    return 0


def cmd_mesh_serve(args: argparse.Namespace) -> int:
    realm = Realm.load(args.workspace)
    if not realm:
        print("This workspace is not part of a mesh. Run `autarch mesh init` first.")
        return 1
    from .transport import MeshServer

    server = MeshServer(realm, _db_path(args.workspace), workspace=args.workspace,
                        host=args.host, port=args.port)
    print(f"Serving realm '{realm.name}' (node {realm.node_id}) at {server.address}")
    if args.host not in ("127.0.0.1", "localhost"):
        print("  Note: bound beyond loopback. Bundles are AES-GCM encrypted end-to-end,")
        print("  so only realm-key holders can read or merge them.")
    print("  Peers run:  autarch --workspace <other> mesh sync " + server.address)
    print("  Press Ctrl+C to stop.")
    server.serve_forever()
    print("\nStopped.")
    return 0


def cmd_mesh_sync(args: argparse.Namespace) -> int:
    realm = Realm.load(args.workspace)
    if not realm:
        print("This workspace is not part of a mesh. Run `autarch mesh init` first.")
        return 1
    from .transport import sync as transport_sync

    memory = WhyMemory(_db_path(args.workspace), node_id=realm.node_id)
    try:
        pushed, pulled = transport_sync(args.url, memory, realm)
    except (RuntimeError, ValueError) as exc:
        print(f"\u2717 Sync failed: {exc}")
        return 1
    if pulled.policies_added:
        realm.save(args.workspace)  # adopt newly-shared realm policies
    print(f"\u2713 Pushed: peer added {pushed.get('added', 0)} record(s)"
          + (f", {pushed['rejected']} rejected" if pushed.get("rejected") else ""))
    print(f"\u2713 Pulled: {pulled.summary()}")
    ok, broken = memory.verify_chain()
    print(f"  Ledger after sync: {'intact' if ok else f'BROKEN at {broken}'}")
    return 0


def cmd_mesh_peer(args: argparse.Namespace) -> int:
    realm = Realm.load(args.workspace)
    if not realm:
        print("This workspace is not part of a mesh. Run `autarch mesh init` first.")
        return 1
    if args.peer_action == "list":
        if not realm.peers:
            print("No peers registered. Add one with `autarch mesh peer add <url>`.")
            return 0
        print("Known peers (for gossip):")
        for url in realm.peers:
            print(f"  {url}")
        return 0
    if args.peer_action == "add":
        if not args.url:
            print("Usage: autarch mesh peer add <url>")
            return 2
        changed = realm.add_peer(args.url)
        realm.save(args.workspace)
        print(f"\u2713 {'Added' if changed else 'Already present'}: {args.url.rstrip('/')}")
        return 0
    if args.peer_action == "remove":
        if not args.url:
            print("Usage: autarch mesh peer remove <url>")
            return 2
        changed = realm.remove_peer(args.url)
        realm.save(args.workspace)
        print(f"\u2713 {'Removed' if changed else 'Not found'}: {args.url.rstrip('/')}")
        return 0
    return 2


def cmd_mesh_gossip(args: argparse.Namespace) -> int:
    realm = Realm.load(args.workspace)
    if not realm:
        print("This workspace is not part of a mesh. Run `autarch mesh init` first.")
        return 1
    if not realm.peers:
        print("No peers to gossip with. Add one with `autarch mesh peer add <url>`.")
        return 1
    from .transport import gossip as transport_gossip

    memory = WhyMemory(_db_path(args.workspace), node_id=realm.node_id)
    for r in range(1, max(1, args.rounds) + 1):
        report = transport_gossip(realm.peers, memory, realm)
        prefix = f"round {r}: " if args.rounds > 1 else ""
        print(f"\u2713 {prefix}{report.summary()}")
        for entry in report.peers:
            if not entry["ok"]:
                print(f"    \u2717 {entry['url']}: {entry['error']}")
    # Adopt any realm policies that converged in via gossip.
    realm.save(args.workspace)
    ok, broken = memory.verify_chain()
    print(f"  Ledger after gossip: {'intact' if ok else f'BROKEN at {broken}'}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    memory = WhyMemory(_db_path(args.workspace))
    records = memory.recent(args.limit)
    if not records:
        print("No history yet.")
        return 0
    print("\nRecent rulings (most recent first):")
    for record in records:
        mark = "\u2713" if record.executed else "\u2014"
        disagree = "!" if (record.tally and len(record.tally) > 1) or record.proposal_disagreement else " "
        rewind = "\u21b6" if record.rewind_of else " "
        print(f"  {mark}{disagree}{rewind} {record.id}  {record.capability:<12} {record.human_decision:<8} {record.intent_text[:42]}")
    return 0


def cmd_prove(args: argparse.Namespace) -> int:
    """The accountability receipt: full evidence + verifiable integrity."""
    memory = WhyMemory(_db_path(args.workspace))

    if args.chain:
        ok, broken = memory.verify_chain()
        if ok:
            print("\u2713 Ledger intact \u2014 every sealed record verifies and links to the previous.")
            return 0
        print(f"\u2717 Ledger BROKEN at {broken} \u2014 a record was altered, removed, or reordered.")
        return 1

    record = memory.get(args.id)
    if record is None:
        print(f"No record found for {args.id}")
        return 1

    verified = memory.verify(args.id)
    seal = memory.get_seal(args.id)
    chain_ok, _ = memory.verify_chain()

    print(f"\n\u2550\u2550\u2550 RECEIPT {record.id} \u2550\u2550\u2550")
    print(f"  Intent:     {record.intent_text}")
    print(f"  Action:     {record.capability}  {json.dumps(record.params)}")
    if record.rewind_of:
        print(f"  Rewind of:  {record.rewind_of}")
    if record.voices:
        print(f"  Council:    {', '.join(record.voices)}  \u2014 tally {_format_tally(record.tally)}")
    print(f"  Proposer:   {record.proposer} \u2014 {record.rationale}")
    print(f"  Challenger: {record.challenger} \u2014 {record.critique_verdict}: {record.critique_reasons}")
    print(f"  Gate:       {'ALLOWED' if record.gate_allowed else 'DENIED'} \u2014 {record.gate_reason}")
    if record.policy_note:
        print(f"  Policy:     {record.policy_note}")
    if record.precedent_note:
        print(f"  Precedent:  {record.precedent_note}")
    print(f"  Decision:   {record.human_decision}")
    if record.executed:
        print(f"  Executed:   yes \u2014 {record.result_output}")
    else:
        print(f"  Executed:   no \u2014 {record.result_error or 'not executed'}")
    if record.cost:
        print(f"  Cost:       {', '.join(f'{k}={v:g}' for k, v in record.cost.items())}")
    if record.eval_score is not None:
        mark = "PASS" if record.eval_passed else "FAIL"
        print(f"  Evaluation: {mark} \u2014 score {record.eval_score:.2f} by {record.evaluator}"
              + (f" ({record.eval_reasons})" if record.eval_reasons else ""))
    if record.undo:
        print(f"  Reversible: yes \u2014 reverse with {record.undo.get('capability')} on {record.undo.get('path')}")

    if verified is None:
        seal_status = "unsealed (recorded before integrity sealing)"
    elif verified:
        seal_status = f"VERIFIED \u2014 seal {seal[:16]}\u2026"
    else:
        seal_status = "TAMPERED \u2014 this record does not match its seal"
    print(f"  Integrity:  {seal_status}")

    provenance = memory.verify_provenance(args.id)
    if provenance is None:
        prov_status = "unsigned (no cryptographic author)"
    elif provenance:
        prov_status = f"VERIFIED \u2014 signed by {record.signer}"
    else:
        prov_status = "FORGED \u2014 signature does not verify for the claimed author"
    print(f"  Provenance: {prov_status}")
    print(f"  Ledger:     {'intact' if chain_ok else 'BROKEN'}")
    return 0


def cmd_rewind(args: argparse.Namespace) -> int:
    """Governed, audited reversal of past actions."""
    memory = WhyMemory(_db_path(args.workspace))

    # Rewind needs to perform reversing file actions, so it grants the file
    # capabilities within the sandbox. Each reversal still passes the kernel and
    # is itself recorded — a rewind is governed, not a backdoor.
    grants = [
        capability("file.read", scope={"path_prefix": "."}),
        capability("file.write", scope={"path_prefix": "."}),
        capability("file.move", scope={"path_prefix": "."}),
        capability("file.delete", scope={"path_prefix": "."}),
    ]
    kernel = CapabilityKernel(grants)
    adapter = FileSystemAdapter(args.workspace)
    by_cap = {cap: adapter for cap in adapter.capabilities()}
    rewinder = Rewinder(memory, kernel, by_cap)

    since_seconds = parse_duration(args.since) if args.since else None
    keep_caps = set(args.keep or [])
    keep_ids = set(args.keep_id or [])
    records = rewinder.candidates(
        ids=([args.id] if args.id else None),
        last=(args.last if (args.last and not args.id and not args.since) else None),
        since_seconds=since_seconds,
        keep_capabilities=keep_caps,
        keep_ids=keep_ids,
    )

    if not records:
        print("Nothing to rewind (no reversible actions match).")
        return 0

    print("\nWill reverse (newest first):")
    for rec in records:
        print(f"  \u21b6 {rec.id}  {rec.capability:<12} {rec.intent_text[:48]}")
    if keep_caps or keep_ids:
        kept = ", ".join(sorted(keep_caps) + sorted(keep_ids))
        print(f"  keeping: {kept}")

    if not args.yes:
        try:
            answer = input("\nProceed with rewind? [y/N] ").strip().lower()
        except EOFError:
            answer = "n"
        if not answer.startswith("y"):
            print("Aborted.")
            return 0

    print()
    steps = rewinder.rewind(records)
    for step in steps:
        if step.executed:
            print(f"  \u2713 reversed {step.original_id} via {step.capability}  (new: {step.new_why_id})")
        else:
            print(f"  \u2717 could not reverse {step.original_id}: {step.error}")
    reversed_count = sum(1 for s in steps if s.executed)
    print(f"\nRewound {reversed_count}/{len(steps)} action(s). The rewind itself is recorded and auditable.")
    return 0


def _ensure_utf8_output() -> None:
    """Make stdout/stderr robust to non-ASCII output.

    On Windows the default console/pipe encoding (cp1252) cannot encode the
    box-drawing characters we render, which would crash when output is piped or
    redirected. Reconfigure to UTF-8 with a safe fallback so the CLI never dies
    on an encoding error.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autarch",
        description="An AI-native operating layer: a council of minds you preside over.",
    )
    parser.add_argument("--workspace", default="./sandbox", help="workspace / sandbox directory")
    sub = parser.add_subparsers(dest="command", required=True)

    p_do = sub.add_parser("do", help="run an intent through the council")
    p_do.add_argument("intent", help="what you want done, in plain language")
    p_do.add_argument("--council", action="append",
                      help="model spec (repeatable): mock, mock:cautious, mock:bold, ollama:llama3")
    p_do.add_argument("--grant", action="append", help="extra capability to grant (e.g. file.delete)")
    p_do.add_argument("--budget-cost", type=float, metavar="N", help="spend ceiling (currency units)")
    p_do.add_argument("--budget-calls", type=float, metavar="N", help="model/tool call ceiling")
    p_do.add_argument("--budget-risk", type=float, metavar="N", help="cumulative risk ceiling")
    p_do.add_argument("--yes", action="store_true", help="auto-preside (no interactive prompt)")
    p_do.set_defaults(func=cmd_do)

    p_why = sub.add_parser("why", help="explain a past action")
    p_why.add_argument("id", help="the why-record id")
    p_why.set_defaults(func=cmd_why)

    p_hist = sub.add_parser("history", help="list recent rulings")
    p_hist.add_argument("--limit", type=int, default=10)
    p_hist.set_defaults(func=cmd_history)

    p_prove = sub.add_parser("prove", help="show the verifiable accountability receipt for an action")
    p_prove.add_argument("id", nargs="?", help="the why-record id")
    p_prove.add_argument("--chain", action="store_true", help="verify the integrity of the entire ledger")
    p_prove.set_defaults(func=cmd_prove)

    p_rewind = sub.add_parser("rewind", help="governed, audited reversal of past actions")
    p_rewind.add_argument("--last", type=int, default=1, help="reverse the last N reversible actions")
    p_rewind.add_argument("--since", help="reverse everything since a window, e.g. '1 hour', '30m'")
    p_rewind.add_argument("--id", help="reverse a single action by its why-record id")
    p_rewind.add_argument("--keep", action="append", help="capability to keep untouched (repeatable)")
    p_rewind.add_argument("--keep-id", action="append", help="why-record id to keep untouched (repeatable)")
    p_rewind.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p_rewind.set_defaults(func=cmd_rewind)

    p_sub = sub.add_parser("substrate", help="show the host Autarch is running on")
    p_sub.set_defaults(func=cmd_substrate)

    p_guar = sub.add_parser("guarantee", help="statically PROVE safety invariants before running (CI-friendly)")
    p_guar.add_argument("--forbid", action="append", metavar="CAP",
                        help="prove a capability can never execute (repeatable)")
    p_guar.add_argument("--require-approval", action="append", metavar="CAP", dest="require_approval",
                        help="prove a capability always needs human approval (repeatable)")
    p_guar.add_argument("--confine", action="append", metavar="CAP=PREFIX",
                        help="prove a capability only ever acts within a path prefix (repeatable)")
    p_guar.add_argument("--grant", action="append", metavar="CAP",
                        help="add a granted capability to the configuration under test (repeatable)")
    p_guar.set_defaults(func=cmd_guarantee)

    p_identity = sub.add_parser("identity", help="show this workspace's cryptographic node identity")
    p_identity.set_defaults(func=cmd_identity)

    p_health = sub.add_parser("health", help="operational health/readiness check (container probes)")
    p_health.add_argument("--json", action="store_true", help="emit the report as JSON")
    p_health.set_defaults(func=cmd_health)

    p_audit = sub.add_parser("audit", help="compliance: export the audit trail or redact PII")
    audit_sub = p_audit.add_subparsers(dest="audit_command", required=True)
    p_audit.set_defaults(func=cmd_audit)
    a_export = audit_sub.add_parser("export", help="export the full audit trail (JSON lines)")
    a_export.add_argument("file", help="output path")
    a_export.set_defaults(audit_func=cmd_audit_export)
    a_redact = audit_sub.add_parser("redact", help="mask a record's PII (right-to-be-forgotten)")
    a_redact.add_argument("id", help="the why-record id")
    a_redact.add_argument("--reason", help="why the record is being redacted")
    a_redact.set_defaults(audit_func=cmd_audit_redact)

    p_mesh = sub.add_parser("mesh", help="one identity, one policy, many nodes (local-first sync)")
    mesh_sub = p_mesh.add_subparsers(dest="mesh_command", required=True)
    p_mesh.set_defaults(func=cmd_mesh)

    m_init = mesh_sub.add_parser("init", help="create or join a realm in this workspace")
    m_init.add_argument("--realm", default="home", help="realm name")
    m_init.add_argument("--join", metavar="KEY_HEX", help="join an existing realm by its shared key")
    m_init.add_argument("--deny", action="append", metavar="CAP",
                        help="shared realm policy: deny a capability (repeatable)")
    m_init.add_argument("--force", action="store_true", help="re-initialize even if a realm exists")
    m_init.set_defaults(mesh_func=cmd_mesh_init)

    m_status = mesh_sub.add_parser("status", help="show this node's realm and ledger")
    m_status.set_defaults(mesh_func=cmd_mesh_status)

    m_export = mesh_sub.add_parser("export", help="write an encrypted bundle of this node's ledger")
    m_export.add_argument("file", help="output bundle path")
    m_export.set_defaults(mesh_func=cmd_mesh_export)

    m_import = mesh_sub.add_parser("import", help="merge a peer's encrypted bundle")
    m_import.add_argument("file", help="input bundle path")
    m_import.set_defaults(mesh_func=cmd_mesh_import)

    m_serve = mesh_sub.add_parser("serve", help="serve this node's ledger over HTTP (stdlib, no broker)")
    m_serve.add_argument("--host", default="127.0.0.1", help="bind address (default loopback; use 0.0.0.0 for LAN)")
    m_serve.add_argument("--port", type=int, default=8787, help="port (default 8787)")
    m_serve.set_defaults(mesh_func=cmd_mesh_serve)

    m_sync = mesh_sub.add_parser("sync", help="bidirectional sync with a peer over HTTP")
    m_sync.add_argument("url", help="peer base URL, e.g. http://192.168.1.20:8787")
    m_sync.set_defaults(mesh_func=cmd_mesh_sync)

    m_peer = mesh_sub.add_parser("peer", help="manage gossip peers (add/remove/list)")
    m_peer.add_argument("peer_action", choices=["add", "remove", "list"])
    m_peer.add_argument("url", nargs="?", help="peer base URL (for add/remove)")
    m_peer.set_defaults(mesh_func=cmd_mesh_peer)

    m_gossip = mesh_sub.add_parser("gossip", help="sync with all known peers (epidemic convergence)")
    m_gossip.add_argument("--rounds", type=int, default=1, help="number of gossip rounds")
    m_gossip.set_defaults(mesh_func=cmd_mesh_gossip)

    return parser


def main(argv=None) -> int:
    _ensure_utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if not args.command:
        parser.print_help()
        return 1
    if getattr(args, "council", None) is None and args.command == "do":
        args.council = ["mock"]
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
