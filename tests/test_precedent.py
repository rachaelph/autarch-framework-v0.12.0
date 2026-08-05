"""PrecedentStore tests — the council remembering your rulings."""
from autarch.precedent import PrecedentStore


def test_lookup_empty_is_none(tmp_path):
    store = PrecedentStore(tmp_path / "p.db")
    assert store.lookup("file.delete") is None


def test_record_and_lookup(tmp_path):
    store = PrecedentStore(tmp_path / "p.db")
    store.record("file.delete", "overrule", "delete x")
    precedent = store.lookup("file.delete")
    assert precedent is not None
    assert precedent.decision == "overrule"
    assert precedent.count == 1
    assert "overrule" in precedent.note()


def test_count_increments(tmp_path):
    store = PrecedentStore(tmp_path / "p.db")
    store.record("file.move", "overrule", "m1")
    store.record("file.move", "overrule", "m2")
    assert store.lookup("file.move").count == 2


def test_dominant_decision_wins(tmp_path):
    store = PrecedentStore(tmp_path / "p.db")
    store.record("file.write", "ratify", "w1")
    store.record("file.write", "ratify", "w2")
    store.record("file.write", "overrule", "w3")
    precedent = store.lookup("file.write")
    assert precedent.decision == "ratify"  # higher count dominates


def test_invalid_decision_ignored(tmp_path):
    store = PrecedentStore(tmp_path / "p.db")
    store.record("file.read", "pending", "r")
    assert store.lookup("file.read") is None
