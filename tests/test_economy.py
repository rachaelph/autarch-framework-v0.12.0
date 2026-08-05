"""Economic kernel tests — budgets refuse actions that can't be afforded."""
from autarch import Agent, Budget, CostModel, EconomicKernel, capability
from autarch.contracts import Action, HumanDecision


# --- Budget ---------------------------------------------------------------

def test_remaining_and_charge():
    b = Budget(limits={"cost": 1.0})
    assert b.remaining("cost") == 1.0
    b.charge({"cost": 0.4})
    assert b.remaining("cost") == 0.6
    assert b.remaining("unmetered") is None


def test_would_exceed_detects_breach():
    b = Budget(limits={"calls": 2})
    b.charge({"calls": 2})
    breach = b.would_exceed({"calls": 1})
    assert breach is not None
    key, limit, projected = breach
    assert key == "calls" and limit == 2 and projected == 3


def test_would_exceed_ignores_unmetered_keys():
    b = Budget(limits={"calls": 2})
    assert b.would_exceed({"risk": 99}) is None  # risk isn't metered here


def test_snapshot():
    b = Budget(limits={"cost": 1.0})
    b.charge({"cost": 0.25})
    assert b.snapshot()["cost"] == "0.25/1"


# --- CostModel ------------------------------------------------------------

def test_default_costs():
    model = CostModel()
    assert model.estimate(Action("file.read", {}))["risk"] == 0
    assert model.estimate(Action("file.delete", {}))["risk"] == 5


def test_custom_prices_override():
    model = CostModel({"payment.send": {"cost": 5.0, "calls": 1, "risk": 9}})
    assert model.estimate(Action("payment.send", {}))["cost"] == 5.0


def test_wildcard_family_pricing():
    model = CostModel({"payment.*": {"cost": 2.0, "calls": 1, "risk": 7}})
    assert model.estimate(Action("payment.refund", {}))["cost"] == 2.0


def test_fallback_cost_for_unknown():
    model = CostModel()
    est = model.estimate(Action("mystery.action", {}))
    assert est["calls"] == 1 and est["risk"] == 3


# --- EconomicKernel -------------------------------------------------------

def test_kernel_allows_within_budget():
    kernel = EconomicKernel(Budget(limits={"risk": 10}))
    decision = kernel.authorize(Action("file.delete", {"path": "a"}))
    assert decision.ok is True


def test_kernel_blocks_over_budget():
    kernel = EconomicKernel(Budget(limits={"risk": 3}))
    decision = kernel.authorize(Action("file.delete", {"path": "a"}))  # risk 5
    assert decision.ok is False
    assert decision.offending_key == "risk"


# --- Agent integration ----------------------------------------------------

def test_no_budget_means_no_enforcement(tmp_path):
    agent = Agent(
        intent="create a.txt that says hi", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})], workspace=tmp_path,
    )
    result = agent.run()
    assert result.executed is True
    assert result.budget_decision is None


def test_agent_charges_budget_on_execution(tmp_path):
    agent = Agent(
        intent="create a.txt that says hi", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path, budget=Budget(limits={"calls": 5}),
    )
    result = agent.run()
    assert result.executed is True
    assert agent.budget.spent["calls"] == 1
    # The cost is recorded in the why-memory.
    assert agent.memory.get(result.why_id).cost["calls"] == 1


def test_agent_blocks_when_budget_exhausted(tmp_path):
    budget = Budget(limits={"calls": 1})
    Agent(
        intent="create a.txt that says one", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path, budget=budget,
    ).run()
    # Second action on the SAME budget -> refused before execution.
    result = Agent(
        intent="create b.txt that says two", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path, budget=budget,
    ).run()
    assert result.executed is False
    assert result.budget_decision.ok is False
    assert not (tmp_path / "b.txt").exists()


def test_budget_dict_is_accepted(tmp_path):
    agent = Agent(
        intent="create a.txt that says hi", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path, budget={"calls": 3},
    )
    assert agent.budget is not None
    agent.run()
    assert agent.budget.spent["calls"] == 1


def test_spawn_shares_budget_pool(tmp_path):
    # A parent and its sub-agents draw from one shared budget.
    parent = Agent(
        intent="coordinate", council=["mock"],
        grants=[capability("file.write", scope={"path_prefix": "."})],
        workspace=tmp_path, budget=Budget(limits={"calls": 1}),
    )
    child = parent.spawn(
        intent="create reports/x.txt that says child",
        grants=[capability("file.write", scope={"path_prefix": "reports"})],
        preside_fn=lambda d, g: HumanDecision.RATIFY.value,
    )
    assert child.budget is parent.budget  # same pool
    first = child.run()
    assert first.executed is True

    # The single shared call is now spent -> a second sub-agent is refused.
    sibling = parent.spawn(
        intent="create reports/y.txt that says sibling",
        grants=[capability("file.write", scope={"path_prefix": "reports"})],
        preside_fn=lambda d, g: HumanDecision.RATIFY.value,
    )
    second = sibling.run()
    assert second.executed is False
    assert second.budget_decision.ok is False


def test_blocked_action_does_not_charge(tmp_path):
    budget = Budget(limits={"risk": 3})
    result = Agent(
        intent="delete a.txt", council=["mock"],
        grants=[capability("file.delete", scope={"path_prefix": "."})],
        workspace=tmp_path, budget=budget,
        preside_fn=lambda d, g: HumanDecision.RATIFY.value,
    ).run()
    # Refused (delete risk 5 > 3) -> nothing spent.
    assert result.executed is False
    assert budget.spent.get("risk", 0) == 0
