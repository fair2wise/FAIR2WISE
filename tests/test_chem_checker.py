import logging

from app.modules.agents.chem_checker import ChemicalFormulaValidator


def test_mp_lookup_failure_disables_subsequent_queries(monkeypatch, caplog):
    validator = ChemicalFormulaValidator(api_key="bad-key")
    calls = 0

    def fail_lookup(formula):
        nonlocal calls
        calls += 1
        raise RuntimeError("invalid key")

    monkeypatch.setattr(validator, "_query_mp", fail_lookup)

    with caplog.at_level(logging.WARNING):
        first = validator.validate("H2O")
        second = validator.validate("Li")

    assert calls == 1
    assert first["error"] == "mp-error: invalid key"
    assert second["mp_hits"] == -1
    assert "MP lookup failed; skipping Materials Project checks for this run" in caplog.text
