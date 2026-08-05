"""Enterprise security: encrypted keys at rest + RBAC over capabilities.

Two gates a security team checks before anything ships:
  1. Secrets at rest \u2014 the signing private key is encrypted, never plaintext on disk.
  2. RBAC \u2014 *who* may wield which capability, enforced before the kernel ever runs.

Run from the repo root:
    python examples/security.py
"""
import json
import shutil
from pathlib import Path

from autarch import Agent, NodeIdentity, SecretError, capability
from autarch.provenance import available
from autarch.rbac import AccessControl, Principal, Role, RoleRegistry


def banner(title):
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def main():
    if not available():
        print("This demo needs the `cryptography` package: pip install autarch[crypto]")
        return

    root = Path("./sandbox/_security")
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    vault = root / "vault"

    banner("1) Signing key encrypted at rest (no plaintext on disk)")
    ident = NodeIdentity.create()
    ident.save(vault, passphrase="correct horse battery staple")
    raw = (vault / ".autarch" / "identity.json").read_text()
    print(f"  private key present in file: {ident._private_hex in raw}  (False = safe)")
    print(f"  on-disk shape: {sorted(json.loads(raw).keys())}")
    print(f"  decrypt with right passphrase -> can_sign: "
          f"{NodeIdentity.load(vault, passphrase='correct horse battery staple').can_sign}")
    try:
        NodeIdentity.load(vault, passphrase="guess")
    except SecretError as exc:
        print(f"  wrong passphrase -> {exc.code}: refused")

    banner("2) RBAC \u2014 roles decide who may wield which capability")
    access = AccessControl(RoleRegistry([
        Role("finance-admin", grantable=["file.*", "payment.*"]),
        Role("analyst", grantable=["file.read"]),
    ]))

    requested = [capability("payment.send"), capability("file.read")]

    admin = Agent(
        intent="pay the supplier invoice", council=["mock"],
        grants=list(requested), workspace=root / "admin",
        principal=Principal("alice", ["finance-admin"]), access=access, sign=False,
    )
    analyst = Agent(
        intent="pay the supplier invoice", council=["mock"],
        grants=list(requested), workspace=root / "analyst",
        principal=Principal("bob", ["analyst"]), access=access, sign=False,
    )

    print(f"  alice (finance-admin): wields {[g.name for g in admin.grants]}")
    print(f"  bob   (analyst):       wields {[g.name for g in analyst.grants]} "
          f"| denied {[g.name for g in analyst.denied_grants]}")

    banner("3) The denial is structural, not cosmetic")
    from autarch.contracts import Action
    gate = analyst.kernel.authorize(Action("payment.send", {"amount": 100}))
    print(f"  bob attempts payment.send -> gate allowed: {gate.allowed}")
    print(f"  reason: {gate.reason}")
    print("\nWho-may-act (RBAC) + what-may-happen (kernel): two locks, both enforced.")


if __name__ == "__main__":
    main()
