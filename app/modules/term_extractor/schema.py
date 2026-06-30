import logging
import re
from typing import Any, Dict, List, Optional, Union

from linkml_runtime.utils.schemaview import SchemaView
from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)


def _label_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", label.lower())


GENERAL_CATEGORY_ALIASES: Dict[str, str] = {
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


GENERAL_RELATION_ALIASES: Dict[str, str] = {
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
}


class SchemaHelper:
    """
    Loads a LinkML schema and provides:
      - RapidFuzz indexes for class-names and slot-names
      - Exact-match + fuzzy suggestions
      - Domain/range validation
      - Relation filtering (drop 'description'/'category')
    """

    def __init__(self, schema_path: str = "matkg_schema.yaml", fuzzy_cutoff: int = 80):
        self.schema_path = schema_path
        self.fuzzy_cutoff = fuzzy_cutoff
        self.schema_view = SchemaView(schema_path)
        self._load_classes_and_slots()
        self._build_fuzzy_indexes()

    def _load_classes_and_slots(self) -> None:
        self.classes: Dict[str, Dict[str, Any]] = {}
        self.class_parents: Dict[str, Optional[str]] = {}
        for name, cls in self.schema_view.all_classes().items():
            desc = cls.description or f"A {name} entity"
            self.classes[name] = {"description": desc, "slots": []}
            self.class_parents[name] = cls.is_a or None

        self.slots: Dict[str, Dict[str, Any]] = {}
        for slot_name, slot_def in self.schema_view.all_slots().items():
            desc = slot_def.description or f"Relationship: {slot_name}"
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

    def get_schema_context_for_llm(self) -> str:
        lines: List[str] = ["=== KNOWLEDGE SCHEMA ===\n", "ENTITY TYPES (use exactly these names):"]
        for cls in sorted(self.classes):
            desc = self.classes[cls]["description"]
            parent = self.class_parents[cls]
            if parent:
                lines.append(f"- {cls}: {desc}  (inherits from: {parent})")
            else:
                lines.append(f"- {cls}: {desc}")
        lines.append("\nVALID RELATIONSHIPS (use exactly these names):")
        for slot in sorted(self.slots):
            info = self.slots[slot]
            dom = info["domain"] or "Any"
            rng = info["range"] or "Any"
            mv = "(multivalued)" if info["multivalued"] else ""
            lines.append(f"- {slot}: {info['description']}  Usage: {dom} --{slot}--> {rng} {mv}")
        lines.append("\nIMPORTANT: Do NOT use relations named 'description' or 'category'.")
        return "\n".join(lines)

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
        feature_slots: List[str] = []
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

        lines: List[str] = []
        for slot_name in sorted(set(feature_slots)):
            info = self.slots[slot_name]
            mv = "multiple values allowed" if info["multivalued"] else "single value"
            lines.append(f"- {slot_name}: {info['description']} ({mv})")
        return "\n".join(lines) or "- None"

    def _find_closest_class(self, target: str) -> Optional[str]:
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

    def _find_closest_slot(self, target: str) -> Optional[str]:
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

    def _general_category_for(self, category: str) -> Optional[str]:
        broad = GENERAL_CATEGORY_ALIASES.get(_label_key(category))
        return broad if broad in self.classes else None

    def _general_relation_for(self, relation: str) -> Optional[str]:
        broad = GENERAL_RELATION_ALIASES.get(_label_key(relation))
        return broad if broad in self.slots else None

    def validate_and_fix_term(self, term_data: Dict[str, Any]) -> Dict[str, Any]:
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

        cleaned_rels: List[Dict[str, Union[str, bool]]] = []
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
                    rel_out: Dict[str, Union[str, bool]] = {
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
