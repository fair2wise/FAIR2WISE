"""Chemical identity and vocabulary edge cases. RDKit is replaced by a tiny fake (it is optional)."""

from __future__ import annotations

from datetime import UTC, datetime
import sys
from types import SimpleNamespace

import pytest

from app.modules.canonicalization import chemical_identity as ci
from app.modules.canonicalization import vocabulary as vocab_mod
from app.modules.canonicalization.vocabulary import (
    VocabEntry,
    Vocabulary,
    VocabularyApproval,
    entity_slug,
    load_vocabulary_asset,
    vocabulary_from_mapping,
)


# ----------------------------------------------------------------------------- fake rdkit
class _Atom:
    def __init__(self, num):
        self.num, self.mapnum = num, 5

    def GetAtomicNum(self):
        return self.num

    def SetAtomMapNum(self, n):
        self.mapnum = n


class _Mol:
    def __init__(self, text):
        self.text = text
        self.atoms = [_Atom(0 if "*" in text else 6)]

    def GetAtoms(self):
        return self.atoms


@pytest.fixture
def fake_rdkit(monkeypatch):
    chem = SimpleNamespace(
        MolFromSmiles=lambda t: None if t == "invalid" else _Mol(t),
        MolToSmiles=lambda mol, canonical, isomericSmiles: mol.text.upper(),
    )

    class BlockLogs:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    pkg = SimpleNamespace(Chem=chem, rdBase=SimpleNamespace(BlockLogs=BlockLogs))
    monkeypatch.setitem(sys.modules, "rdkit", pkg)
    monkeypatch.setitem(sys.modules, "rdkit.Chem", chem)
    monkeypatch.setitem(sys.modules, "rdkit.rdBase", pkg.rdBase)
    ci.canonical_smiles.cache_clear()
    yield
    ci.canonical_smiles.cache_clear()


def test_canonical_smiles_unresolved_without_rdkit(monkeypatch):
    monkeypatch.setitem(sys.modules, "rdkit", None)  # import raises ImportError
    ci.canonical_smiles.cache_clear()
    assert ci.canonical_smiles("CCO") is None and ci.structure_slug("CCO") is None
    ci.canonical_smiles.cache_clear()


@pytest.mark.parametrize("bad", ["", "   ", "CC O", "CCO|extended", "invalid", "C*C", "x" * 10001])
def test_canonical_smiles_refuses_unsafe_or_unresolvable_input(fake_rdkit, bad):
    assert ci.canonical_smiles(bad) is None


def test_canonical_smiles_normalises_and_structure_slug_hashes_it(fake_rdkit):
    assert ci.canonical_smiles(" cco ") == "CCO"
    slug = ci.structure_slug("cco")
    assert slug.startswith("molecule_") and len(slug) == len("molecule_") + 64
    assert ci.structure_slug("CCO") == slug  # same canonical structure, same key
    assert ci.structure_slug("invalid") is None


def test_canonical_smiles_rejects_non_string(fake_rdkit):
    ci.canonical_smiles.cache_clear()
    assert ci.canonical_smiles(123) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("PbI2", {"Pb": 1, "I": 2}),
        ("(NH4)2SO4", {"N": 2, "H": 8, "S": 1, "O": 4}),
        ("Ca(OH)2", {"Ca": 1, "O": 2, "H": 2}),
        ("pbi2", None),
        ("Na-Cl", None),
        ("(NaCl", None),
        ("", None),
        ("H2O)", None),
    ],
)
def test_formula_counts(text, expected):
    assert ci.formula_counts(text) == expected


def test_looks_chemical_accepts_qualifiers_and_rejects_words():
    assert ci.looks_chemical("Al2O3[0001]") and ci.looks_chemical("alpha-Fe2O3")
    assert not ci.looks_chemical("") and not ci.looks_chemical("alpha")
    assert not ci.looks_chemical("film thickness")


@pytest.mark.parametrize(
    ("a", "b", "merge"),
    [
        ("PbI2", "PbI2", True),
        ("MoTe2", "WTe2", False),
        ("Al2O3[0001]", "Al2O3", False),
        ("alpha-Fe2O3", "gamma-Fe2O3", False),
        # same element counts in a different written order still merge (the docstring says
        # "differ at most in case/spacing", but the implementation compares counts only)
        ("(NH4)2SO4", "H8N2O4S", True),
    ],
)
def test_chemical_verdict(a, b, merge):
    verdict = ci.chemical_verdict(a, b)
    assert verdict is not None and verdict.allowed is merge


def test_chemical_verdict_has_no_opinion_unless_both_are_formulas():
    assert ci.chemical_verdict("PbI2", "lead iodide") is None
    assert ci.chemical_verdict("lead iodide", "tin iodide") is None


# ----------------------------------------------------------------------------- vocabulary
def _entry(slug, category="technique", **kw):
    return VocabEntry(slug=slug, display_name=kw.pop("display_name", slug.replace("_", " ").title()),
                      category=category, **kw)


def test_entity_slug_folds_accents_case_and_punctuation():
    assert entity_slug("Café  -- X/ray!") == "cafe_x_ray"
    assert entity_slug("") == "" and entity_slug(None) == ""


def test_vocabulary_validation_errors():
    with pytest.raises(ValueError, match="unknown vocabulary status"):
        Vocabulary(1, [], status="draft")
    with pytest.raises(ValueError, match="unknown category"):
        Vocabulary(1, [_entry("a", "weird")])
    with pytest.raises(ValueError, match="duplicate vocabulary slug"):
        Vocabulary(1, [_entry("a"), _entry("a")])
    with pytest.raises(ValueError, match="claimed by both"):
        Vocabulary(1, [_entry("a", aliases=("shared",)), _entry("b", aliases=("Shared",))])


def test_revision_changes_with_content_and_approval_requires_exact_revision():
    v1 = Vocabulary(1, [_entry("a")])
    v2 = Vocabulary(1, [_entry("a", aliases=("alias",))])
    assert v1.revision != v2.revision and not v1.approved
    with pytest.raises(ValueError, match="different vocabulary revision"):
        v1.approve(VocabularyApproval("ap", v2.revision, "owner", datetime.now(UTC)))
    approved = v1.approve(VocabularyApproval("ap-1", v1.revision, "owner", datetime.now(UTC)))
    assert approved.approved and approved.approval_id == "ap-1" and approved.revision == v1.revision


def test_match_requires_exact_alias_and_respects_category():
    v = Vocabulary(1, [_entry("rsoxs", aliases=("resonant soft x-ray scattering",)),
                       _entry("gold", "material", display_name="Gold")])
    assert v.match("Resonant Soft X-ray Scattering").slug == "rsoxs"
    assert v.match("rsoxs", category="material") is None
    assert v.match("RSoXS", category="technique").slug == "rsoxs"
    assert v.match("rsox") is None and v.match("") is None
    assert v.get("gold").display_name == "Gold" and v.get("nope") is None
    assert [e.slug for e in v.entries_for("material")] == ["gold"]


def test_match_smiles_uses_canonical_structure_and_caches_index(monkeypatch):
    mapping = {"CCO": "ETHANOL", "OCC": "ETHANOL", "bad": None, "CCN": "ETHYLAMINE"}
    calls = []
    monkeypatch.setattr(ci, "canonical_smiles", lambda s: calls.append(s) or mapping.get(s, s.upper()))
    v = Vocabulary(1, [_entry("ethanol", "material", smiles="CCO"), _entry("amine", "material", smiles="CCN"),
                       _entry("nosmiles", "material"), _entry("tech", "technique", smiles="CCO")])
    assert v.match("smiles:OCC").slug == "ethanol"
    n = len(calls)
    assert v.match("SMILES:CCN").slug == "amine"
    assert len(calls) == n + 1  # index built once, only the query is canonicalised again
    assert v.match("smiles:bad") is None
    assert v.match("smiles:CCO", category="technique") is None
    assert v.match("smiles:C1CC1") is None


def test_match_smiles_flags_two_entries_with_one_structure(monkeypatch):
    monkeypatch.setattr(ci, "canonical_smiles", lambda s: "SAME")
    v = Vocabulary(1, [_entry("a", "material", smiles="x"), _entry("b", "material", smiles="y")])
    with pytest.raises(ValueError, match="duplicate chemical structure"):
        v.match_smiles("z")


def test_match_within_longest_span_wins_and_ties_and_materials_are_refused():
    v = Vocabulary(1, [
        _entry("grazing", patterns=(r"grazing[- ]incidence",)),
        _entry("giwaxs", patterns=(r"grazing[- ]incidence wide[- ]angle",)),
        _entry("tie_a", aliases=("alpha beta gamma delta",)),
        _entry("tie_b", aliases=("omega sigma kappa zeta",)),
        _entry("silicon", "material", aliases=("silicon wafer substrate",)),
        _entry("long_alias", "analysis", aliases=("peak position analysis",)),
    ])
    assert v.match_within("a grazing incidence wide angle scan").slug == "giwaxs"
    assert v.match_within("grazing-incidence scan").slug == "grazing"
    assert v.match_within("run peak  position-analysis today").slug == "long_alias"
    assert v.match_within("the silicon wafer substrate", category=None) is None  # materials never by containment
    assert v.match_within("alpha beta gamma delta and omega sigma kappa zeta") is None  # equal spans: refuse
    assert v.match_within("alpha beta gamma delta only").slug == "tie_a"
    assert v.match_within("   ") is None and v.match_within("nothing relevant") is None
    assert v.match_within("grazing incidence", category="analysis") is None


def test_match_within_ambiguous_equal_spans_refused():
    v = Vocabulary(1, [_entry("x1", patterns=(r"detector",)), _entry("x2", patterns=(r"detector",))])
    assert v.match_within("a detector") is None


def test_from_mapping_and_asset_loading(tmp_path):
    with pytest.raises(ValueError, match="not a vocabulary mapping"):
        vocabulary_from_mapping({"nope": 1})
    with pytest.raises(ValueError):
        vocabulary_from_mapping([])  # type: ignore[arg-type]
    path = tmp_path / "vocab.yaml"
    path.write_text(
        "version: 3\nentries:\n  - slug: gold\n    category: material\n    aliases: [Au]\n    smiles: '[Au]'\n"
        "  - slug: rsoxs\n    display_name: RSoXS\n    category: technique\n    patterns: ['rsoxs']\n",
        encoding="utf-8",
    )
    v = load_vocabulary_asset(path)
    assert v.version == 3 and v.status == "candidate" and v.source.startswith("vocab.yaml@sha256:")
    assert v.get("gold").display_name == "gold" and v.match("Au").slug == "gold"
    assert v.get("rsoxs").as_dict()["patterns"] == ["rsoxs"]
    assert v.get("rsoxs").compiled_patterns()[0].search("RSOXS")
