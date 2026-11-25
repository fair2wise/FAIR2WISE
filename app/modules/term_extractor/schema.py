"""LinkML-schema helper that validates and repairs extracted terms and relations."""

from collections.abc import Callable
from dataclasses import dataclass, field
import logging
import re
from typing import Any

from linkml_runtime.utils.schemaview import SchemaView
from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)


#: Longest description that reaches a prompt when ``sanitize_descriptions`` is on (the user schema validator's cap).
MAX_PROMPT_DESCRIPTION_CHARS = 400
_PROMPT_UNSAFE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069]+")


def sanitize_description(text: str, limit: int = MAX_PROMPT_DESCRIPTION_CHARS) -> str:
    """One line of plain text for a prompt: control characters (and line breaks) become one space, length capped."""
    return " ".join(_PROMPT_UNSAFE.sub(" ", str(text)).split())[:limit]


def _label_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", label.lower())


GENERAL_CATEGORY_ALIASES: dict[str, str] = {
    "anode": "Component",
    "batteryperformance": "Property",
    "chemicalelement": "Element",
    "compound": "Compound",
    "computationalmethod": "Method",
    "condition": "Condition",
    "crystallinestructure": "Structure",
    "depositionprocess": "Process",
    "electrochemicalparameter": "Parameter",
    "electrodecomponent": "Component",
    "element": "Element",
    "experimentaltechnique": "ExperimentalTechnique",
    "rsoxs": "RSoXSMeasurement",
    "rsoxsmeasurement": "RSoXSMeasurement",
    "prsoxs": "RSoXSMeasurement",
    "cyrsoxs": "RSoXSMeasurement",
    "resonantsoftxrayscattering": "RSoXSMeasurement",
    "polarizedresonantsoftxrayscattering": "RSoXSMeasurement",
    "resonantelasticsoftxrayscattering": "RSoXSMeasurement",
    "xrayphenomenon": "XRayPhenomenon",
    "xraysource": "XRaySource",
    "xraybeamproperty": "XRayBeamProperty",
    "xrayopticalconstant": "XRayOpticalConstant",
    "absorptionedge": "AbsorptionEdge",
    "secondaryprocess": "SecondaryProcess",
    "materialxrayinteraction": "MaterialXRayInteraction",
    "xraysafetyconcept": "XRaySafetyConcept",
    "cosmicxrayprocess": "CosmicXRayProcess",
    "scatteringgeometry": "ScatteringGeometry",
    "xraytechniquefamily": "XRayTechniqueFamily",
    "interactioncrosssection": "InteractionCrossSection",
    "reciprocalspaceconcept": "ReciprocalSpaceConcept",
    "waveopticsconcept": "WaveOpticsConcept",
    "atomictransition": "AtomicTransition",
    "detectionprinciple": "DetectionPrinciple",
    "externalpressure": "Condition",
    "growthdirection": "Structure",
    "interface": "Interface",
    "metal": "Material",
    "morphology": "Structure",
    "nucleation": "Phenomenon",
    "physicalcondition": "Condition",
    "pressure": "Parameter",
    "process": "Process",
    "processparameter": "Parameter",
    "separator": "Component",
    "setup": "Method",
    "structure": "Structure",
    "substrate": "Component",
    "substratesurface": "Interface",
}


GENERAL_RELATION_ALIASES: dict[str, str] = {
    "affects": "affects",
    "appliedto": "applied_to",
    "belongsto": "belongs_to",
    "causes": "causes",
    "composedof": "composed_of",
    "contains": "contains",
    "containsmaterial": "contains",
    "devicecontainsmaterial": "contains",
    "drives": "causes",
    "electrodematerial": "contains",
    "exhibitsproperty": "has_property",
    "formson": "forms_on",
    "hasapplication": "used_in",
    "haselectrodematerial": "contains",
    "hasprocess": "processed_by",
    "hasprocessingmethod": "processed_by",
    "hasproperty": "has_property",
    "materialhasproperty": "has_property",
    "materialprocessedby": "processed_by",
    "measuredby": "measures",
    "occursat": "occurs_in",
    "partof": "part_of",
    "processedby": "processed_by",
    "propertymeasuredby": "measures",
    "providessitefor": "provides_site_for",
    "relatedto": "related_to",
    "usedin": "used_in",
    "produces": "produces",
    "describedby": "described_by",
    "involves": "involves",
    "governs": "governs",
    "uses": "uses",
    "followsfrom": "follows_from",
    "samephysicsas": "same_physics_as",
}


#: Classes the pipeline fills itself (``code_snippets`` become CodeSnippet nodes, ``has_code_snippet`` edges are synthesised).
#: They stay in the schema, but the extraction and preview prompts never offer them to the model as a category or relation:
#: a model that was offered CodeSnippet filed ordinary terms such as a staining method under it (pilot 2026-11-25).
PIPELINE_ONLY_CLASSES: frozenset[str] = frozenset({"CodeSnippet"})


#: Slot ranges that hold a value, not another node: a slot with one of these is an attribute of the term.
LITERAL_RANGES: frozenset[str] = frozenset({
    "string", "str", "integer", "int", "float", "double", "decimal", "boolean", "bool", "date", "datetime", "time", "uri",
    "uriorcurie", "ncname", "objectidentifier", "nodeidentifier"})


#: Slots that are the fields of every term (not relations): left out of a user schema's prompt.
CORE_FIELD_SLOTS: frozenset[str] = frozenset({"name", "description"})


@dataclass
class RelationJudgement:
    """What :meth:`SchemaHelper.judge_relations` decided for the relations of one registered term.

    Attributes:
        attributes: Relations whose slot has a literal range (``country: Brazil``): ``{"property", "value"}`` to store on
            the term itself, not as an edge.
        swapped: Relations that are invalid as given but valid with subject and object exchanged: ``{"relation",
            "subject", "object", "subject_class", "object_class"}``, to store on the *object* term (the real subject).
        invalid: ``(subject_class, relation, object_class)`` of relations left in place and marked ``verified=False``.
    """

    attributes: list[dict[str, str]] = field(default_factory=list)
    swapped: list[dict[str, str]] = field(default_factory=list)
    invalid: list[tuple[str, str, str]] = field(default_factory=list)


def judge_relations(schema: Any, term_data: dict[str, Any], category_of: Callable[[str], str]) -> RelationJudgement:  # noqa: ANN401  # Any: a SchemaHelper or a duck-typed double
    """:meth:`SchemaHelper.judge_relations` for any schema object; a schema without ``repair_relations`` changes nothing."""
    out = RelationJudgement()
    if not getattr(schema, "repair_relations", False):
        return out
    subject_class = str(term_data.get("category") or "")
    kept: list[dict[str, Any]] = []
    for rel in term_data.get("relations") or []:
        pred = str(rel.get("relation") or "")
        if rel.get("verified") and schema.is_attribute_slot(pred):
            out.attributes.append({"property": pred, "value": str(rel.get("related_term") or "")})
            continue
        if rel.get("verified") and subject_class:
            obj_name = str(rel.get("related_term") or "")
            object_class = category_of(obj_name)
            if object_class and not schema.check_relation_validity(subject_class, pred, object_class):
                if schema.check_relation_validity(object_class, pred, subject_class):
                    out.swapped.append({"relation": pred, "subject": obj_name, "object": str(term_data.get("term") or ""),
                                        "subject_class": object_class, "object_class": subject_class})
                    continue
                rel["verified"] = False
                out.invalid.append((subject_class, pred, object_class))
        kept.append(rel)
    term_data["relations"] = kept
    return out


def mark_invalid_relations(schema: Any, term_data: dict[str, Any], category_of: Callable[[str], str]) -> list[tuple[str, str, str]]:  # noqa: ANN401  # Any: a SchemaHelper or a duck-typed double
    """:meth:`SchemaHelper.mark_invalid_relations` for any schema object with ``check_relation_validity``."""
    marked: list[tuple[str, str, str]] = []
    subject_class = str(term_data.get("category") or "")
    for rel in term_data.get("relations") or []:
        if not rel.get("verified", False):
            continue
        object_class = category_of(str(rel.get("related_term") or ""))
        if subject_class and object_class and not schema.check_relation_validity(subject_class, rel["relation"], object_class):
            rel["verified"] = False
            marked.append((subject_class, str(rel["relation"]), object_class))
    return marked


class SchemaHelper:
    """Load a LinkML schema and validate extracted terms against it.

    Provides:
      - RapidFuzz indexes for class-names and slot-names
      - Exact-match + fuzzy suggestions
      - Domain/range validation
      - Relation filtering (drop 'description'/'category')
    """

    def __init__(self, schema_path: str = "matkg_schema.yaml", fuzzy_cutoff: int = 80, *,
                 sanitize_descriptions: bool = False):
        """Load ``schema_path``.

        Args:
            schema_path: Schema file (resolved against the resource root when relative).
            fuzzy_cutoff: RapidFuzz score cutoff.
            sanitize_descriptions: Opt in for schemas whose text is not ours (user and model-written ones): every class
                and slot description is reduced to one line, stripped of control characters and capped before it can
                become prompt text. Shipped schemas leave this off and render exactly as before.
        """
        from ..resources import resolve_resource

        self.sanitize_descriptions = sanitize_descriptions
        #: Relation repairs (attributes, swapped subject and object) are for schemas whose text is not ours; a shipped
        #: schema keeps the original behaviour exactly.
        self.repair_relations = sanitize_descriptions
        self.schema_path = schema_path
        self.fuzzy_cutoff = fuzzy_cutoff
        self.schema_view = SchemaView(str(resolve_resource(schema_path)))
        self._load_classes_and_slots()
        self._build_fuzzy_indexes()

    def _load_classes_and_slots(self) -> None:
        self.classes: dict[str, dict[str, Any]] = {}
        self.class_parents: dict[str, str | None] = {}
        for name, cls in self.schema_view.all_classes().items():
            desc = cls.description or f"A {name} entity"
            if self.sanitize_descriptions:
                desc = sanitize_description(desc) or f"A {name} entity"
            self.classes[name] = {"description": desc, "slots": []}
            self.class_parents[name] = cls.is_a or None

        self.slots: dict[str, dict[str, Any]] = {}
        for slot_name, slot_def in self.schema_view.all_slots().items():
            desc = slot_def.description or f"Relationship: {slot_name}"
            if self.sanitize_descriptions:
                desc = sanitize_description(desc) or f"Relationship: {slot_name}"
            domain = slot_def.domain or None
            rng = slot_def.range or None
            mv = bool(slot_def.multivalued)
            self.slots[slot_name] = {
                "description": desc,
                "domain": domain,
                "range": rng,
                "multivalued": mv,
            }
            if domain and domain in self.classes:
                self.classes[domain]["slots"].append(slot_name)
        logger.info("Loaded schema: %d classes, %d slots", len(self.classes), len(self.slots))

    def _build_fuzzy_indexes(self) -> None:
        self._class_names_lower = [c.lower() for c in self.classes]
        self._class_map_lower = {c.lower(): c for c in self.classes}
        self._slot_names_lower = [s.lower() for s in self.slots]
        self._slot_map_lower = {s.lower(): s for s in self.slots}
        logger.debug("Built fuzzy indexes for classes and slots")

    def offered_classes(self) -> dict[str, dict[str, Any]]:
        """The classes a model may choose as a category: every class except :data:`PIPELINE_ONLY_CLASSES`."""
        return {name: info for name, info in self.classes.items() if name not in PIPELINE_ONLY_CLASSES}

    def offered_slots(self) -> dict[str, dict[str, Any]]:
        """The relations a model may use: every slot except those that belong to or point at a pipeline-only class.

        A slot belongs to such a class when it is declared in the class's own ``slots`` list (``code_snippet``,
        ``code_language`` ... have no ``domain``) or names it as its domain; it points at one when its range is the class.
        """
        own: set[str] = set()
        for cls in PIPELINE_ONLY_CLASSES & set(self.classes):
            try:
                own.update(self.schema_view.class_slots(cls, direct=True))
            except Exception:  # noqa: BLE001 - a class the view cannot resolve owns no slot we can name
                logger.debug("Could not list the slots of %s", cls, exc_info=True)
        return {name: info for name, info in self.slots.items()
                if name not in own and info["domain"] not in PIPELINE_ONLY_CLASSES and info["range"] not in PIPELINE_ONLY_CLASSES}

    def get_schema_context_for_llm(self) -> str:
        """Render the schema classes and relations as prompt text for the LLM."""
        lines: list[str] = ["=== KNOWLEDGE SCHEMA ===\n", "ENTITY TYPES (use exactly these names):"]
        for cls in sorted(self.offered_classes()):
            desc = self.classes[cls]["description"]
            parent = self.class_parents[cls]
            if parent:
                lines.append(f"- {cls}: {desc}  (inherits from: {parent})")
            else:
                lines.append(f"- {cls}: {desc}")
        offered = self.offered_slots()
        if self.repair_relations:   # name and description are the term's own fields, never relations or attributes to give
            offered = {name: info for name, info in offered.items() if name not in CORE_FIELD_SLOTS}
        attributes = sorted(name for name in offered if self.repair_relations and self.is_attribute_slot(name))
        lines.append("\nVALID RELATIONSHIPS (use exactly these names):")
        if self.repair_relations:
            lines.append("A relation is read from the term you register: the first class of its Usage line is the term you "
                         "register, the second is the related_term. Never swap them.")
        for slot in sorted(name for name in offered if name not in attributes):
            info = self.slots[slot]
            dom = info["domain"] or "Any"
            rng = info["range"] or "Any"
            mv = "(multivalued)" if info["multivalued"] else ""
            lines.append(f"- {slot}: {info['description']}  Usage: {dom} --{slot}--> {rng} {mv}")
        if attributes:
            lines.append("\nATTRIBUTES (a value of the term itself, not a link to another entity; give each as a relation whose "
                         "related_term is the value text, and do not register the value as a term):")
            for slot in attributes:
                info = self.slots[slot]
                lines.append(f"- {slot}: {info['description']}  Usage: {info['domain'] or 'Any'} --{slot}--> value ({info['range']})")
        lines.append("\nIMPORTANT: Do NOT use relations named 'description' or 'category'.")
        return "\n".join(lines)

    def bounded_schema_context(self, max_chars: int) -> tuple[str, dict[str, int] | None]:
        """The prompt text of :meth:`get_schema_context_for_llm`, kept within ``max_chars`` characters.

        A schema whose full rendering fits is returned byte for byte as :meth:`get_schema_context_for_llm` renders it
        (second item ``None``). A longer one is replaced by the deterministic condensed view the schema generator
        uses (class names with the first sentence, slots with ``domain>range``, whole lines only, a fixed
        ``... (N more classes omitted)`` line when cut). The second item then holds the sizes:
        ``{"full_chars", "used_chars", "budget"}``.
        """
        full = self.get_schema_context_for_llm()
        if len(full) <= max_chars:
            return full, None
        from ..library.schema_prompt import condensed_view

        head = "=== KNOWLEDGE SCHEMA (condensed: the full schema is too long for the prompt) ===\n"
        tail = "\nIMPORTANT: Do NOT use relations named 'description' or 'category'."
        if self.repair_relations:
            tail = ("\nA relation is read from the term you register: domain>range is subject>related_term, never swapped. A "
                    "slot whose range is string or a number is an attribute of the term (related_term is the value)." + tail)
        view = condensed_view(
            {name: info["description"] for name, info in self.offered_classes().items()},
            {slot: (info["domain"], info["range"] or "Any") for slot, info in self.offered_slots().items() if info["domain"]},
            max(max_chars - len(head) - len(tail), 0))
        text = head + view + tail
        return text, {"full_chars": len(full), "used_chars": len(text), "budget": max_chars}

    def get_code_domain_feature_context(self) -> str:
        """Return schema-defined feature names allowed for CodeSnippet domain_features."""
        excluded = {
            "id",
            "name",
            "category",
            "type",
            "description",
            "provided_by",
            "publication_year",
            "paper_title",
            "authors",
            "institutions",
            "doi",
            "journal",
            "volume",
            "issue",
            "pages_range",
            "abstract_text",
            "keywords",
            "has_code_snippet",
        }
        feature_slots: list[str] = []
        for slot_name, slot_def in self.schema_view.all_slots().items():
            annotation = (slot_def.annotations or {}).get("code_domain_feature")
            annotation_value = getattr(annotation, "value", annotation)
            if str(annotation_value).lower() in {"true", "1", "yes"} and slot_name in self.slots:
                feature_slots.append(slot_name)

        if not feature_slots:
            logger.debug("No code_domain_feature annotations found; falling back to ExperimentalTechnique slots")
            for class_name in ("ExperimentalTechnique",):
                try:
                    class_slots = self.schema_view.class_slots(class_name)
                except Exception:
                    class_slots = []
                for slot_name in class_slots:
                    if slot_name not in excluded and slot_name in self.slots:
                        feature_slots.append(slot_name)

        lines: list[str] = []
        for slot_name in sorted(set(feature_slots)):
            info = self.slots[slot_name]
            mv = "multiple values allowed" if info["multivalued"] else "single value"
            lines.append(f"- {slot_name}: {info['description']} ({mv})")
        return "\n".join(lines) or "- None"

    def _find_closest_class(self, target: str) -> str | None:
        if not target:
            return None
        tl = target.strip().lower()
        if tl in self._class_map_lower:
            return self._class_map_lower[tl]
        match = process.extractOne(tl, self._class_names_lower, scorer=fuzz.QRatio, score_cutoff=self.fuzzy_cutoff)
        if match:
            found_lower, _score, _ = match
            return self._class_map_lower.get(found_lower)
        return None

    def _find_closest_slot(self, target: str) -> str | None:
        if not target:
            return None
        tl = target.strip().lower()
        if tl in self._slot_map_lower:
            return self._slot_map_lower[tl]
        match = process.extractOne(tl, self._slot_names_lower, scorer=fuzz.QRatio, score_cutoff=self.fuzzy_cutoff)
        if match:
            found_lower, _score, _ = match
            return self._slot_map_lower.get(found_lower)
        return None

    def _general_category_for(self, category: str) -> str | None:
        broad = GENERAL_CATEGORY_ALIASES.get(_label_key(category))
        return broad if broad in self.classes else None

    def _general_relation_for(self, relation: str) -> str | None:
        broad = GENERAL_RELATION_ALIASES.get(_label_key(relation))
        return broad if broad in self.slots else None

    def validate_and_fix_term(self, term_data: dict[str, Any]) -> dict[str, Any]:
        """Coerce a raw term dict onto schema classes and relations, in place.

        Unknown categories and predicates are mapped via alias tables, then fuzzy
        matching, then fallbacks (``Thing`` / ``related_to``). Remapped values keep
        the original in ``raw_category`` / ``raw_predicate``; a relation left under
        its unknown name (no ``related_to`` slot) is marked ``verified=False``.
        ``description`` and ``category`` predicates and malformed entries are dropped.

        Args:
            term_data: Raw term dict; mutated and returned.

        Returns:
            The same dict with normalized ``category`` and ``relations``.
        """
        cat = str(term_data.get("category") or "").strip()
        if not cat:
            cat = "Thing"
            term_data["category"] = cat
        if cat not in self.classes:
            fixed = self._general_category_for(cat) or self._find_closest_class(cat)
            if fixed:
                if fixed == self._general_category_for(cat):
                    logger.info("Mapped category '%s' → '%s'", cat, fixed)
                else:
                    logger.warning("Fixed category '%s' → '%s'", cat, fixed)
                term_data["raw_category"] = cat
                term_data["category"] = fixed
            else:
                fallback = "Thing" if "Thing" in self.classes else cat
                if fallback != cat:
                    term_data["raw_category"] = cat
                    term_data["category"] = fallback
                    logger.info("Mapped unknown category '%s' → '%s'", cat, fallback)
                else:
                    logger.warning("Unknown category '%s' (left as-is)", cat)

        raw_rels = term_data.get("relations") or []
        if not isinstance(raw_rels, list):
            logger.warning("Dropping non-list relations for term '%s'", term_data.get("term", ""))
            raw_rels = []

        cleaned_rels: list[dict[str, str | bool]] = []
        for rel in raw_rels:
            if not isinstance(rel, dict):
                logger.debug("Dropping malformed relation for term '%s': %r", term_data.get("term", ""), rel)
                continue
            pred = str(rel.get("relation") or "").strip()
            obj = str(rel.get("related_term") or "").strip()
            if not pred or not obj:
                logger.debug("Dropping blank relation for term '%s': %r", term_data.get("term", ""), rel)
                continue
            if pred.lower() in ("description", "category"):
                logger.debug("Dropping relation '%s' as prohibited", pred)
                continue
            if pred in self.slots:
                cleaned_rels.append({"relation": pred, "related_term": obj, "verified": True})
            else:
                fixed_slot = self._general_relation_for(pred) or self._find_closest_slot(pred)
                if fixed_slot:
                    if fixed_slot == self._general_relation_for(pred):
                        logger.info("Mapped relation '%s' → '%s'", pred, fixed_slot)
                    else:
                        logger.warning("Fixed relation '%s' → '%s'", pred, fixed_slot)
                    cleaned_rels.append({
                        "relation": fixed_slot,
                        "related_term": obj,
                        "verified": True,
                        "raw_predicate": pred,
                    })
                else:
                    fallback_slot = "related_to" if "related_to" in self.slots else pred
                    rel_out: dict[str, str | bool] = {
                        "relation": fallback_slot,
                        "related_term": obj,
                        "verified": fallback_slot != pred,
                    }
                    if fallback_slot != pred:
                        rel_out["raw_predicate"] = pred
                        logger.info("Mapped unknown relation '%s' → '%s'", pred, fallback_slot)
                    else:
                        logger.warning("Unknown relation '%s' → marking unverified", pred)
                    cleaned_rels.append(rel_out)

        term_data["relations"] = cleaned_rels
        return term_data

    def is_attribute_slot(self, slot: str) -> bool:
        """Whether ``slot`` holds a value of the term (its range is a literal such as string or number), not another node."""
        info = self.slots.get(slot)
        return bool(info and str(info["range"] or "").lower() in LITERAL_RANGES)

    def judge_relations(self, term_data: dict[str, Any], category_of: Callable[[str], str]) -> RelationJudgement:
        """Judge the relations of a term that :meth:`validate_and_fix_term` already normalised, in place.

        The one rule used by the extraction and the schema preview, so they cannot disagree. Only for a schema with
        :attr:`repair_relations`; otherwise nothing changes. The subject is the term being registered. For each verified
        relation: a slot with a literal range is an attribute of the term (removed from ``relations``); a relation that is
        invalid as given but valid with subject and object swapped is removed and reported in ``swapped`` (the caller
        stores it on the object term); any other invalid relation stays and is marked ``verified=False``. A relation whose
        object has no known class cannot be judged and is left as it is.

        Args:
            term_data: The normalised term dict (``category``, ``relations``); ``relations`` is replaced.
            category_of: Class of a term by name, ``""`` when not known (yet).

        Returns:
            What was decided.
        """
        return judge_relations(self, term_data, category_of)

    def mark_invalid_relations(self, term_data: dict[str, Any], category_of: Callable[[str], str]) -> list[tuple[str, str, str]]:
        """Mark ``verified=False`` on each verified relation whose classes violate its slot's domain or range.

        Judged only when the subject class and the object's class (``category_of``) are known. Shared by the extraction and
        the schema preview. Returns ``(subject_class, relation, object_class)`` of each relation marked.
        """
        return mark_invalid_relations(self, term_data, category_of)

    def _is_subclass_of(self, child: str, parent: str) -> bool:
        if child == parent:
            return True
        if child not in self.classes:
            return False
        p = self.class_parents.get(child)
        if not p:
            return False
        return self._is_subclass_of(p, parent)

    def check_relation_validity(self, subj_cls: str, pred: str, obj_cls: str) -> bool:
        """Return True if ``pred`` is a schema slot whose domain/range admit the classes.

        Args:
            subj_cls: Class of the relation subject.
            pred: Relation (slot) name.
            obj_cls: Class of the relation object.
        """
        if pred not in self.slots:
            return False
        slot = self.slots[pred]
        dom = slot["domain"]
        rng = slot["range"]
        if dom and not self._is_subclass_of(subj_cls, dom):
            return False
        if rng and not self._is_subclass_of(obj_cls, rng):
            return False
        return True
