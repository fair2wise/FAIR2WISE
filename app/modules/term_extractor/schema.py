import logging
from typing import Any, Dict, List, Optional, Union

from linkml_runtime.utils.schemaview import SchemaView
from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)


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

    def validate_and_fix_term(self, term_data: Dict[str, Any]) -> Dict[str, Any]:
        cat = term_data.get("category", "").strip()
        if cat not in self.classes:
            fixed = self._find_closest_class(cat)
            if fixed:
                logger.warning("Fixed category '%s' → '%s'", cat, fixed)
                term_data["category"] = fixed
            else:
                logger.warning("Unknown category '%s' (left as-is)", cat)

        cleaned_rels: List[Dict[str, Union[str, bool]]] = []
        for rel in term_data.get("relations", []):
            pred = rel.get("relation", "").strip()
            obj = rel.get("related_term", "").strip()
            if pred.lower() in ("description", "category"):
                logger.debug("Dropping relation '%s' as prohibited", pred)
                continue
            if pred in self.slots:
                cleaned_rels.append({"relation": pred, "related_term": obj, "verified": True})
            else:
                fixed_slot = self._find_closest_slot(pred)
                if fixed_slot:
                    logger.warning("Fixed relation '%s' → '%s'", pred, fixed_slot)
                    cleaned_rels.append({"relation": fixed_slot, "related_term": obj, "verified": True})
                else:
                    logger.warning("Unknown relation '%s' → marking unverified", pred)
                    cleaned_rels.append({"relation": pred, "related_term": obj, "verified": False})

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
