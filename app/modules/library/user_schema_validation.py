"""Validation of a user-written or model-written LinkML schema (design: ``design-schema-generation.md``).

The text is untrusted data: it is size-limited, parsed with PyYAML's *safe* machinery only (``compose``, which builds
nodes and never constructs Python objects), checked against a closed set of keys and value shapes, and only then
handed to ``SchemaView`` and ``SchemaHelper``. Nothing is executed, imported or fetched: ``imports`` may name only
``linkml:types``, which ``linkml_runtime`` ships.

What this is **not**: full LinkML conformance. It checks that the text parses, follows the structural rules below,
carries the required core and loads in ``SchemaView`` and ``SchemaHelper``; the ``linkml`` linter and validator are
not used (see the design page for what adding that package would cost).

Every problem carries a 1-based line (and column when known) so an editor can mark it. :func:`insert_required_core`
adds whatever part of the required core is missing, so the editor can offer it instead of only reporting it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
import tempfile
from typing import Any

import yaml

#: Largest accepted schema text, in bytes.
MAX_BYTES = 256 * 1024
#: Largest accepted number of classes and slots.
MAX_CLASSES = 150
MAX_SLOTS = 400
#: Longest ``description`` (also the cap applied where descriptions become prompt text) and longest other text value.
MAX_DESCRIPTION_CHARS = 400
MAX_TEXT_CHARS = 200
#: Most prefixes a schema may declare.
MAX_PREFIXES = 20
#: Largest nesting depth of the YAML document.
MAX_DEPTH = 8

#: The only import a schema may make: ``linkml_runtime`` ships it, so nothing outside the process is read.
ALLOWED_IMPORT = "linkml:types"

TOP_LEVEL_KEYS = frozenset({"id", "name", "title", "description", "version", "license", "prefixes", "default_prefix",
                            "default_range", "imports", "classes", "slots", "enums"})
CLASS_KEYS = frozenset({"description", "title", "is_a", "abstract", "slots", "slot_usage", "attributes", "mixins",
                        "aliases", "annotations", "comments", "see_also", "class_uri"})
SLOT_KEYS = frozenset({"description", "title", "domain", "range", "multivalued", "required", "is_a", "aliases",
                       "annotations", "comments", "see_also", "slot_uri", "identifier", "inverse"})
ENUM_KEYS = frozenset({"description", "title", "permissible_values"})

#: The only YAML tags a schema may use: plain text, numbers, booleans, null, lists and mappings (no ``!!python/...``).
_PLAIN_TAGS = frozenset(f"tag:yaml.org,2002:{name}" for name in ("str", "int", "float", "bool", "null", "seq", "map"))

_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_SCHEMA_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_VERSION = re.compile(r"^[0-9][A-Za-z0-9._+-]{0,31}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f  ‪-‮⁦-⁩]")

#: What was checked, in words for the editor to show next to the result.
CHECKED = ("size and encoding", "parses as YAML (no tags, anchors or duplicate keys)", "structural rules (closed set of keys, "
           "names, caps, imports)", "required core (Thing, CodeSnippet, name, description, related_to)",
           "loads in SchemaView and SchemaHelper")
NOT_CHECKED = "Not full LinkML conformance: the linkml linter and validator are not used."

#: The required core, as separate pieces so a missing part can be inserted without touching the rest.
CORE_CLASSES: dict[str, list[str]] = {
    "Thing": [
        "description: A generic grouping for any identifiable entity (the fallback class).",
        "slots: [name, description]",
    ],
    "CodeSnippet": [
        "is_a: Thing",
        "description: Code block extracted from a paper (created by the pipeline, not chosen by the model).",
    ],
}
CORE_SLOTS: dict[str, list[str]] = {
    "name": ["range: string"],
    "description": ["range: string"],
    "related_to": [
        "description: Broad fallback relation when no narrower predicate applies.",
        "domain: Thing",
        "range: Thing",
        "multivalued: true",
    ],
}
CORE_HEADER = ("prefixes:\n  linkml: https://w3id.org/linkml/\n  ex: https://example.org/user/\n"
               "default_prefix: ex\ndefault_range: string\nimports:\n  - linkml:types\n")


@dataclass
class Problem:
    """One finding: where, what and whether :func:`insert_required_core` fixes it."""

    line: int
    message: str
    code: str
    column: int | None = None
    fixable: bool = False


@dataclass
class Report:
    """Result of :func:`validate_schema_text`."""

    ok: bool
    problems: list[Problem] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)
    checked: tuple[str, ...] = CHECKED
    note: str = NOT_CHECKED

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe form for the API."""
        out = asdict(self)
        out["checked"] = list(self.checked)
        out["fixable"] = any(p.fixable for p in self.problems)
        return out


# ------------------------------------------------------------------------------------------------ YAML tree helpers


def _line(node: yaml.Node | None) -> int:
    return (node.start_mark.line + 1) if node is not None else 1


def _col(node: yaml.Node | None) -> int | None:
    return (node.start_mark.column + 1) if node is not None else None


def _mapping(node: yaml.Node | None) -> dict[str, tuple[yaml.Node, yaml.Node]]:
    """``key text -> (key node, value node)`` of a mapping node (empty for anything else)."""
    out: dict[str, tuple[yaml.Node, yaml.Node]] = {}
    if isinstance(node, yaml.MappingNode):
        for key, value in node.value:
            if isinstance(key, yaml.ScalarNode):
                out.setdefault(key.value, (key, value))
    return out


def _scalar(node: yaml.Node | None) -> str | None:
    return node.value if isinstance(node, yaml.ScalarNode) else None


def _walk_safety(node: yaml.Node, depth: int, problems: list[Problem], seen: set[int]) -> None:
    """Refuse tags, anchors/aliases, duplicate keys and excessive depth anywhere in the document."""
    if id(node) in seen:
        problems.append(Problem(_line(node), "YAML aliases are not allowed", "yaml_alias", _col(node)))
        return
    seen.add(id(node))
    if depth > MAX_DEPTH:
        problems.append(Problem(_line(node), f"nesting is deeper than {MAX_DEPTH} levels", "too_deep", _col(node)))
        return
    if node.tag is not None and node.tag not in _PLAIN_TAGS:
        problems.append(Problem(_line(node), "YAML tags are not allowed", "yaml_tag", _col(node)))
    if isinstance(node, yaml.MappingNode):
        keys: set[str] = set()
        for key, value in node.value:
            text = key.value if isinstance(key, yaml.ScalarNode) else None
            if text is not None and text in keys:
                problems.append(Problem(_line(key), f"duplicate key '{text}'", "duplicate_key", _col(key)))
            if text is not None:
                keys.add(text)
            _walk_safety(key, depth + 1, problems, seen)
            _walk_safety(value, depth + 1, problems, seen)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            _walk_safety(item, depth + 1, problems, seen)


# ------------------------------------------------------------------------------------------------ the checks


def _text_value(node: yaml.Node, what: str, limit: int, problems: list[Problem]) -> str | None:
    text = _scalar(node)
    if text is None:
        problems.append(Problem(_line(node), f"{what} must be text", "not_text", _col(node)))
        return None
    if len(text) > limit:
        problems.append(Problem(_line(node), f"{what} is longer than {limit} characters", "too_long", _col(node)))
    if _CONTROL.search(text):
        problems.append(Problem(_line(node), f"{what} contains control characters", "control_chars", _col(node)))
    return text


def _check_keys(mapping: yaml.Node, allowed: frozenset[str], what: str, problems: list[Problem]) -> None:
    for name, (key, _value) in _mapping(mapping).items():
        if name not in allowed:
            problems.append(Problem(_line(key), f"{what}: key '{name}' is not allowed", "key_not_allowed", _col(key)))


def _check_top(root: yaml.MappingNode, problems: list[Problem]) -> None:
    top = _mapping(root)
    _check_keys(root, TOP_LEVEL_KEYS, "schema", problems)
    for required in ("id", "name", "version", "classes"):
        if required not in top:
            problems.append(Problem(1, f"the schema needs a top-level '{required}'", "missing_key"))
    if "id" in top:
        value = _text_value(top["id"][1], "id", MAX_TEXT_CHARS, problems)
        if value is not None and not re.match(r"^https?://[^\s]+$", value):
            problems.append(Problem(_line(top["id"][1]), "id must be an http(s) URI (it is never fetched)", "bad_id"))
    if "name" in top:
        value = _text_value(top["name"][1], "name", MAX_TEXT_CHARS, problems)
        if value is not None and not _SCHEMA_NAME.match(value):
            problems.append(Problem(_line(top["name"][1]), "name must be letters, digits, '_' or '-', starting with a letter",
                                    "bad_name"))
    if "version" in top:
        value = _text_value(top["version"][1], "version", 32, problems)
        if value is not None and not _VERSION.match(value):
            problems.append(Problem(_line(top["version"][1]), "version must look like 0.1.0", "bad_version"))
    for key in ("title", "description", "license", "default_prefix", "default_range"):
        if key in top:
            _text_value(top[key][1], key, MAX_DESCRIPTION_CHARS if key == "description" else MAX_TEXT_CHARS, problems)
    if "prefixes" in top:
        prefixes = _mapping(top["prefixes"][1])
        if not isinstance(top["prefixes"][1], yaml.MappingNode):
            problems.append(Problem(_line(top["prefixes"][1]), "prefixes must be a mapping", "bad_prefixes"))
        if len(prefixes) > MAX_PREFIXES:
            problems.append(Problem(_line(top["prefixes"][1]), f"more than {MAX_PREFIXES} prefixes", "too_many"))
        for name, (_k, prefix_node) in prefixes.items():
            _text_value(prefix_node, f"prefix '{name}'", MAX_TEXT_CHARS, problems)
    if "imports" in top:
        node = top["imports"][1]
        items = [_scalar(item) for item in node.value] if isinstance(node, yaml.SequenceNode) else [None]
        if items != [ALLOWED_IMPORT]:
            problems.append(Problem(_line(node), f"imports must be exactly [{ALLOWED_IMPORT}]: a schema is self-contained "
                                    "and nothing is read from files or URLs", "bad_import", _col(node)))
    elif "classes" in top:
        problems.append(Problem(1, f"add 'imports: [{ALLOWED_IMPORT}]'", "missing_import", fixable=True))


def _check_names_and_entries(top: dict[str, tuple[yaml.Node, yaml.Node]], problems: list[Problem]) -> None:
    classes = _mapping(top["classes"][1]) if "classes" in top else {}
    slots = _mapping(top["slots"][1]) if "slots" in top else {}
    if "classes" in top and not isinstance(top["classes"][1], yaml.MappingNode):
        problems.append(Problem(_line(top["classes"][1]), "classes must be a mapping of class name to definition", "bad_classes"))
    if "slots" in top and not isinstance(top["slots"][1], yaml.MappingNode):
        problems.append(Problem(_line(top["slots"][1]), "slots must be a mapping of slot name to definition", "bad_slots"))
    if len(classes) > MAX_CLASSES:
        problems.append(Problem(_line(top["classes"][0]), f"more than {MAX_CLASSES} classes", "too_many"))
    if len(slots) > MAX_SLOTS:
        problems.append(Problem(_line(top["slots"][0]), f"more than {MAX_SLOTS} slots", "too_many"))
    for kind, entries, allowed in (("class", classes, CLASS_KEYS), ("slot", slots, SLOT_KEYS)):
        for name, (key, value) in entries.items():
            if not _NAME.match(name):
                problems.append(Problem(_line(key), f"{kind} name '{name[:40]}' must be letters, digits and '_', "
                                        "starting with a letter", "bad_name", _col(key)))
            if value.tag == "tag:yaml.org,2002:null" or (isinstance(value, yaml.ScalarNode) and not value.value):
                continue   # an empty definition is allowed (it only names the class or slot)
            if not isinstance(value, yaml.MappingNode):
                problems.append(Problem(_line(value), f"{kind} '{name}' must be a mapping", "bad_entry", _col(value)))
                continue
            _check_keys(value, allowed, f"{kind} '{name}'", problems)
            for field_name, (_k, field_node) in _mapping(value).items():
                limit = MAX_DESCRIPTION_CHARS if field_name == "description" else MAX_TEXT_CHARS
                if field_name in ("description", "title", "is_a", "domain", "range", "class_uri", "slot_uri", "inverse"):
                    _text_value(field_node, f"{kind} '{name}' {field_name}", limit, problems)
    for name, (_key, value) in _mapping(top["enums"][1] if "enums" in top else None).items():
        _check_keys(value, ENUM_KEYS, f"enum '{name}'", problems)


def _check_core(top: dict[str, tuple[yaml.Node, yaml.Node]], problems: list[Problem]) -> None:
    classes = _mapping(top["classes"][1]) if "classes" in top else {}
    slots = _mapping(top["slots"][1]) if "slots" in top else {}
    anchor = _line(top["classes"][0]) if "classes" in top else 1
    for name in CORE_CLASSES:
        if name not in classes:
            problems.append(Problem(anchor, f"required core: class '{name}' is missing", "missing_core", fixable=True))
    for name in CORE_SLOTS:
        if name not in slots:
            problems.append(Problem(_line(top["slots"][0]) if "slots" in top else anchor,
                                    f"required core: slot '{name}' is missing", "missing_core", fixable=True))
    related = _mapping(slots["related_to"][1]) if "related_to" in slots else {}
    if related:
        for key, wanted in (("domain", "Thing"), ("range", "Thing")):
            have = _scalar(related[key][1]) if key in related else None
            if have != wanted:
                node = related[key][1] if key in related else slots["related_to"][0]
                problems.append(Problem(_line(node), f"required core: related_to needs {key}: {wanted}", "bad_core"))
    code = _mapping(classes["CodeSnippet"][1]) if "CodeSnippet" in classes else {}
    if code and (_scalar(code["is_a"][1]) if "is_a" in code else None) != "Thing":
        problems.append(Problem(_line(classes["CodeSnippet"][0]), "required core: CodeSnippet must be 'is_a: Thing'",
                                "bad_core"))


def _find_line(top: dict[str, tuple[yaml.Node, yaml.Node]], name: str) -> int:
    for section in ("classes", "slots"):
        entries = _mapping(top[section][1]) if section in top else {}
        if name in entries:
            return _line(entries[name][0])
    return 1


def _check_loads(text: str, top: dict[str, tuple[yaml.Node, yaml.Node]], problems: list[Problem]) -> None:
    """Load the text in ``SchemaView`` and ``SchemaHelper`` and check that every reference resolves."""
    from linkml_runtime.utils.schemaview import SchemaView

    try:
        view = SchemaView(text)
        classes = view.all_classes()
        slots = view.all_slots()
        types = set(view.all_types())
        enums = set(view.all_enums())
    except Exception as exc:  # noqa: BLE001 - any load failure of untrusted text is one finding, never a crash
        problems.append(Problem(1, f"does not load in SchemaView: {type(exc).__name__}: {str(exc)[:160]}", "load_failed"))
        return
    for name, cls in classes.items():
        if cls.is_a and cls.is_a not in classes:
            problems.append(Problem(_find_line(top, name), f"class '{name}': is_a '{cls.is_a}' is not a class of this schema",
                                    "unknown_reference"))
        for slot in cls.slots or []:
            if slot not in slots:
                problems.append(Problem(_find_line(top, name), f"class '{name}': slot '{slot}' is not defined under slots",
                                        "unknown_reference"))
    for name, slot in slots.items():
        if slot.domain and slot.domain not in classes:
            problems.append(Problem(_find_line(top, name), f"slot '{name}': domain '{slot.domain}' is not a class",
                                    "unknown_reference"))
        if slot.range and slot.range not in classes and slot.range not in types and slot.range not in enums:
            problems.append(Problem(_find_line(top, name), f"slot '{name}': range '{slot.range}' is not a class, type or enum",
                                    "unknown_reference"))
    if problems:
        return
    try:
        for name in classes:
            view.class_induced_slots(name)
    except Exception as exc:  # noqa: BLE001 - reported against the class, with the exception type only
        problems.append(Problem(1, f"slots of a class cannot be resolved: {type(exc).__name__}: {str(exc)[:160]}",
                                "load_failed"))
        return
    from ..term_extractor.schema import SchemaHelper

    with tempfile.TemporaryDirectory(prefix="f2w-schema-check-") as directory:
        path = Path(directory) / "schema.yaml"
        path.write_text(text, encoding="utf-8")
        try:
            SchemaHelper(str(path))
        except Exception as exc:  # noqa: BLE001
            problems.append(Problem(1, f"does not load in SchemaHelper: {type(exc).__name__}: {str(exc)[:160]}",
                                    "load_failed"))


def validate_schema_text(text: str) -> Report:
    """Check ``text`` and return every finding with its line; ``ok`` only when there is none.

    Args:
        text: The schema as the editor holds it.

    Returns:
        A :class:`Report`; it never raises for bad input.
    """
    problems: list[Problem] = []
    raw = text.encode("utf-8", errors="replace") if isinstance(text, str) else b""
    if not isinstance(text, str) or len(raw) > MAX_BYTES:
        return Report(False, [Problem(1, f"the schema is larger than {MAX_BYTES // 1024} KiB", "too_large")])
    match = _CONTROL.search(text)
    if match:
        return Report(False, [Problem(text.count("\n", 0, match.start()) + 1, "the text contains control characters",
                                      "control_chars")])
    try:
        documents = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        return Report(False, [Problem((mark.line + 1) if mark else 1, f"YAML: {exc.problem or 'syntax error'}", "yaml_syntax",
                                      (mark.column + 1) if mark else None)])
    except yaml.YAMLError:
        return Report(False, [Problem(1, "YAML: the text does not parse", "yaml_syntax")])
    if len(documents) != 1 or documents[0] is None:
        return Report(False, [Problem(1, "the text must hold exactly one YAML document", "yaml_documents")])
    root = documents[0]
    _walk_safety(root, 0, problems, set())
    if problems:
        return Report(False, problems)
    if not isinstance(root, yaml.MappingNode):
        return Report(False, [Problem(1, "the schema must be a mapping at the top level", "not_mapping")])
    top = _mapping(root)
    _check_top(root, problems)
    _check_names_and_entries(top, problems)
    _check_core(top, problems)
    stats = {"bytes": len(raw), "classes": len(_mapping(top["classes"][1])) if "classes" in top else 0,
             "slots": len(_mapping(top["slots"][1])) if "slots" in top else 0}
    if not problems:
        _check_loads(text, top, problems)
    problems.sort(key=lambda p: (p.line, p.message))
    return Report(not problems, problems, stats)


# ------------------------------------------------------------------------------------------------ the required core


def required_core_text() -> str:
    """A complete minimal schema holding exactly the required core (the starting point of a new schema)."""
    lines = ["id: https://example.org/user/new_schema", "name: new_schema", "description: Describe the domain here.",
             "version: 0.1.0", *CORE_HEADER.rstrip("\n").split("\n"), "classes:"]
    for name, body in CORE_CLASSES.items():
        lines += [f"  {name}:", *[f"    {line}" for line in body]]
    lines.append("slots:")
    for name, body in CORE_SLOTS.items():
        lines += [f"  {name}:", *[f"    {line}" for line in body]]
    return "\n".join(lines) + "\n"


def _insert_after(lines: list[str], index: int, block: list[str]) -> None:
    lines[index + 1:index + 1] = block


def _section_end(root: yaml.MappingNode, name: str, total: int) -> int:
    """0-based index of the last line of top-level section ``name`` (the line before the next top-level key)."""
    keys = [key for key, _ in root.value]
    for position, key in enumerate(keys):
        if isinstance(key, yaml.ScalarNode) and key.value == name:
            if position + 1 < len(keys):
                return int(keys[position + 1].start_mark.line) - 1
            return total - 1
    return -1


def insert_required_core(text: str) -> str:
    """The text with the missing parts of the required core added; unchanged when nothing is missing or it cannot parse.

    Existing content is never rewritten: missing entries are inserted at the end of ``classes`` and ``slots`` (the
    sections are added when absent), the ``imports`` line and the prefixes are added when missing. The result is
    meant to be validated again.
    """
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        return text
    if not isinstance(root, yaml.MappingNode):
        return text
    top = _mapping(root)
    lines = text.rstrip("\n").split("\n")
    present_classes = _mapping(top["classes"][1]) if "classes" in top else {}
    present_slots = _mapping(top["slots"][1]) if "slots" in top else {}
    new_classes = [line for name, body in CORE_CLASSES.items() if name not in present_classes
                   for line in (f"  {name}:", *[f"    {b}" for b in body])]
    new_slots = [line for name, body in CORE_SLOTS.items() if name not in present_slots
                 for line in (f"  {name}:", *[f"    {b}" for b in body])]
    # Insert from the bottom up so earlier line numbers stay valid.
    edits: list[tuple[int, list[str]]] = []
    if new_slots:
        if "slots" in top:
            edits.append((_section_end(root, "slots", len(lines)), new_slots))
        else:
            edits.append((len(lines) - 1, ["slots:", *new_slots]))
    if new_classes:
        if "classes" in top:
            edits.append((_section_end(root, "classes", len(lines)), new_classes))
        else:
            edits.append((len(lines) - 1, ["classes:", *new_classes]))
    header = [line for key, line in (("prefixes", "prefixes:\n  linkml: https://w3id.org/linkml/\n  ex: https://example.org/user/"),
                                    ("default_prefix", "default_prefix: ex"), ("default_range", "default_range: string"),
                                    ("imports", "imports:\n  - linkml:types")) if key not in top
              for line in line.split("\n")]
    for index, block in sorted(edits, key=lambda e: -e[0]):
        _insert_after(lines, index, block)
    if header:
        lines[0:0] = header
    return "\n".join(lines) + "\n"


__all__ = ("ALLOWED_IMPORT", "CHECKED", "CORE_CLASSES", "CORE_SLOTS", "MAX_BYTES", "MAX_CLASSES", "MAX_DESCRIPTION_CHARS",
           "MAX_SLOTS", "NOT_CHECKED", "Problem", "Report", "insert_required_core", "required_core_text",
           "validate_schema_text")
