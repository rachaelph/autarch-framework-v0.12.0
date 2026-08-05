---------------------------- MODULE kernel ----------------------------
(***************************************************************************)
(* A formal model of Autarch's Capability Kernel.                          *)
(*                                                                         *)
(* The kernel is a deterministic function of (grants, action) -> verdict.  *)
(* Because it holds no state and no intelligence, its safety properties are *)
(* pure logical invariants over that function. This TLA+ module states them *)
(* so a model checker (TLC) can verify them exhaustively over a bounded     *)
(* domain, mirroring autarch/verification.py.                              *)
(*                                                                         *)
(* Run: model-check with TLC, INVARIANTS = DenyByDefault, NoScopeEscape,   *)
(* Attenuation.                                                            *)
(***************************************************************************)
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS
    Capabilities,   \* set of capability names, e.g. {"file.read","file.write"}
    Prefixes,       \* set of path prefixes a grant may confine to
    Paths           \* set of paths an action may target

(* A grant authorizes a capability and (optionally) confines a path prefix. *)
Grant == [cap : Capabilities, prefix : Prefixes \cup {"<none>"}]

(* An action names a capability and targets a path. *)
Action == [cap : Capabilities, path : Paths]

(* Ground-truth: is `path` within `prefix`? "." is the whole sandbox root. *)
WithinPrefix(prefix, path) ==
    \/ prefix = "<none>"
    \/ prefix = "."
    \/ prefix = path

(* The kernel's authorize function: deny by default; a matching grant       *)
(* authorizes only if the scope constraint is satisfied.                    *)
Authorize(grants, action) ==
    \E g \in grants :
        /\ g.cap = action.cap
        /\ WithinPrefix(g.prefix, action.path)

------------------------------------------------------------------------
(* INVARIANT I1 — Deny by default.                                         *)
(* If no grant names the action's capability, the action is not authorized. *)
DenyByDefault ==
    \A grants \in SUBSET Grant, action \in Action :
        (\A g \in grants : g.cap # action.cap) => ~Authorize(grants, action)

(* INVARIANT I2 — No scope escape.                                          *)
(* A confined grant never authorizes a path outside its prefix.            *)
NoScopeEscape ==
    \A grants \in SUBSET Grant, action \in Action :
        Authorize(grants, action) =>
            \E g \in grants :
                /\ g.cap = action.cap
                /\ WithinPrefix(g.prefix, action.path)

(* Attenuation: a child grant is "within" a parent iff same capability and  *)
(* its prefix is no broader.                                               *)
ChildWithinParent(parent, child) ==
    /\ parent.cap = child.cap
    /\ \/ parent.prefix = child.prefix
       \/ parent.prefix = "."
       \/ parent.prefix = "<none>"

(* INVARIANT I3 — Attenuation monotonicity.                                *)
(* A child grant never authorizes an action its parent would deny.         *)
Attenuation ==
    \A parent, child \in Grant, action \in Action :
        ChildWithinParent(parent, child) =>
            (Authorize({child}, action) => Authorize({parent}, action))

=============================================================================
