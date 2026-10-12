"""T02: tool registry, skills and explicit bootstrap."""

from __future__ import annotations

import pytest

from app.modules.contracts import Effect
from app.modules.agent_runtime.registry import ToolRegistry, ToolRegistryError
from app.modules.agent_runtime.skills import Skill, SkillRegistry, SkillRegistryError
from app.modules.tools.bootstrap import DEMO_SKILL, build_registry, build_skill_registry
from tests.runtime.runtime_fakes import Recorder, als_ctx, local_ctx, read_spec, write_spec


def _registry(*specs):
    reg = ToolRegistry()
    rec = Recorder()
    for spec in specs:
        reg.register(spec, rec.ok)
    return reg


def test_register_get_list_and_domain():
    reg = _registry(read_spec("lit_b"), read_spec("lit_a"), write_spec())
    assert [s.name for s in reg.list()] == ["kg_apply_plan", "lit_a", "lit_b"]
    assert [s.name for s in reg.list_by_domain("literature")] == ["lit_a", "lit_b"]
    assert reg.get("lit_a").spec.name == "lit_a" and reg.get("nope") is None
    assert "lit_a" in reg and len(reg) == 3
    reg.verify()


def test_duplicates_and_alias_collisions_rejected():
    reg = _registry(read_spec("lit_a", aliases=("old_a",)))
    with pytest.raises(ToolRegistryError):
        reg.register(read_spec("lit_a"), Recorder().ok)
    with pytest.raises(ToolRegistryError):
        reg.register(read_spec("lit_c", aliases=("lit_a",)), Recorder().ok)
    with pytest.raises(ToolRegistryError):
        reg.register(read_spec("old_a"), Recorder().ok)
    assert reg.resolve_alias("old_a") == "lit_a"
    assert reg.get("old_a") is None  # aliases are only resolved at compatibility boundaries


@pytest.mark.parametrize("kw", [
    {"name": "sim_run_structure"}, {"name": "analysis_fit_peaks"}, {"name": "calibration_fit_ring"},
    {"domain": "simulation"}, {"domain": "catalog"}, {"domain": "unknown_domain"},
])
def test_experimental_and_unknown_domains_rejected(kw):
    with pytest.raises(ToolRegistryError):
        _registry(read_spec(**kw))


def test_forge_implementation_rejected():
    def fn(inv):
        return {}
    fn.__module__ = "forge.agents.simulation.tools"
    with pytest.raises(ToolRegistryError, match="FORGE"):
        ToolRegistry().register(read_spec(), fn)


def test_unknown_capability_and_bad_schema_rejected():
    with pytest.raises(ToolRegistryError, match="unknown capabilities"):
        _registry(read_spec(required_capabilities=frozenset({"root.everything"})))
    with pytest.raises(ToolRegistryError, match="invalid input_schema"):
        _registry(read_spec(input_schema={"type": "not-a-type"}))
    with pytest.raises(ToolRegistryError):
        ToolRegistry().register(read_spec(), "not callable")


def test_freeze_blocks_mutation():
    reg = _registry(read_spec()).freeze()
    assert reg.frozen
    with pytest.raises(ToolRegistryError, match="frozen"):
        reg.register(read_spec("lit_other"), Recorder().ok)


def test_two_registries_are_independent():
    first = build_registry(None, demo_corpus={"p1": {"title": "A"}})
    second = build_registry(None, freeze=False)
    second.register(write_spec(), Recorder().ok)
    assert "kg_apply_plan" in second and "kg_apply_plan" not in first
    assert first.get("demo_lit_search").fn is not second.get("demo_lit_search").fn
    third = build_registry(None)
    assert third is not first and [s.name for s in third.list()] == [s.name for s in first.list()]


def test_visibility_by_capability():
    reg = _registry(read_spec(), write_spec()).freeze()
    chat = als_ctx(admin=False)
    assert [e.name for e in reg.visible_to(chat)] == ["lit_echo"]
    assert [e.name for e in reg.visible_to(als_ctx("bob", admin=True))] == ["kg_apply_plan", "lit_echo"]
    assert [e.name for e in reg.visible_to(local_ctx())] == ["kg_apply_plan", "lit_echo"]
    # a turn scope narrows, never widens
    assert [e.name for e in reg.visible_to(local_ctx(), scope=frozenset({"chat.ask"}))] == ["lit_echo"]
    assert reg.visible_to(chat, scope=frozenset({"graph.publish"})) == []


def test_skill_integrity_and_coverage():
    reg = _registry(read_spec("lit_a"), read_spec("lit_b"))
    skills = SkillRegistry(reg)
    with pytest.raises(SkillRegistryError, match="absent"):
        skills.register(Skill("find_papers", "d", "w", ("lit_a", "lit_missing")))
    with pytest.raises(SkillRegistryError, match="experimental"):
        skills.register(Skill("simulate_structure", "d", "w", ("lit_a",)))
    with pytest.raises(SkillRegistryError):
        skills.register(Skill("find_papers", "", "w", ("lit_a",)))
    with pytest.raises(SkillRegistryError):
        skills.register(Skill("find_papers", "d", "w", ("lit_a", "lit_a")))
    skills.register(Skill("find_papers", "d", "w", ("lit_a",)))
    with pytest.raises(SkillRegistryError, match="belong to no skill"):
        skills.verify()
    skills.register(Skill("read_papers", "d", "w", ("lit_b",)))
    skills.freeze()
    assert [e.name for e in skills.resolve("read_papers")] == ["lit_b"]
    with pytest.raises(SkillRegistryError):
        skills.register(Skill("late", "d", "w", ("lit_a",)))
    with pytest.raises(SkillRegistryError):
        skills.resolve("nope")


def test_skill_requires_explicit_registry():
    with pytest.raises(SkillRegistryError):
        SkillRegistry(None)


def test_skill_visibility_and_prompt_section():
    reg = _registry(read_spec(), write_spec()).freeze()
    skills = SkillRegistry(reg)
    skills.register(Skill("search_literature", "Find papers.", "Questions about papers.", ("lit_echo",)))
    skills.register(Skill("publish_graph", "Apply approved plans.", "After approval.", ("kg_apply_plan",)))
    skills.freeze()
    chat_text = skills.render_prompt_section(als_ctx())
    assert "search_literature (1 tool)" in chat_text and "publish_graph" not in chat_text
    assert "lit_echo" not in chat_text  # tool names are never rendered
    assert "publish_graph" in skills.render_prompt_section(local_ctx())
    assert skills.render_prompt_section(als_ctx(capabilities=frozenset())) == ""


def test_bootstrap_is_explicit_frozen_and_demo_only():
    reg = build_registry(local_ctx())
    assert reg.frozen and [s.name for s in reg.list()] == ["demo_lit_read", "demo_lit_search"]
    assert all(s.effects == frozenset({Effect.READ_CORPUS}) for s in reg.list())  # read-only demo tools
    assert all(s.required_capabilities == frozenset({"chat.ask"}) for s in reg.list())
    skills = build_skill_registry(reg)
    assert [s.name for s in skills.list_skills()] == [DEMO_SKILL.name]
    with pytest.raises(ToolRegistryError, match="unknown tool groups"):
        build_registry(None, include=("forge_sim",))
    with pytest.raises(TypeError):
        build_registry(42)
    assert len(build_registry(None, include=())) == 0
