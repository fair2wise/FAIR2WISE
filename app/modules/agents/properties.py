#!/usr/bin/env python3
"""Extract and normalize physical properties with proximity and pattern checks.

enhanced_properties_conflict_resolution.py

Further improvements to PhysicalPropertyExtractor and PropertyNormalizer to avoid
spurious property‐value extractions when properties and material mentions are semantically 
disconnected. Implements:
  - Proximity‐based matching: require “<material>” and “<property phrase>” to appear within a few words
    of one another (rather than anywhere in the sentence).
  - Context‐pattern checks: look for “<property phrase> of <material>” or “<material> <property phrase>”
    before extracting numeric values.
  - Plausibility filtering for known property domains (e.g., efficiency ∈ [0,100], density > 0).
  - Retain “measured_property” relations as before, but do not attempt numeric extraction from them.

Usage:
    extractor = PhysicalPropertyExtractor()
    raw_entries = extractor.extract(text, ["EQE", "Na3Bi"])
    raw_from_rel = extractor.extract_from_relations(term_record)
    all_raw = raw_entries + raw_from_rel

    normalizer = PropertyNormalizer()
    normalized_entries = normalizer.normalize(all_raw)

Author: David Abramov (@dabramov)
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Decimal places kept on normalized values and uncertainties.
NORMALIZED_DECIMALS = 6


class PhysicalPropertyExtractor:
    """Extracts physical property mentions from text for known materials.

    Uses proximity‐based and pattern‐based matching to reduce false positives.

    Entry points:
      - extract(text, materials): scans free text for “<material> <property> <value><unit>”
      - extract_from_relations(term_record): reads “measured_property” relations in a term dict

    Returns a list of dicts with keys:
        - material: Material name (string)
        - property: Canonical property name (e.g., “band_gap”)
        - raw_value: Extracted numeric string or None
        - raw_unit: Extracted unit string or None
        - uncertainty: Extracted uncertainty string or None
        - context: Sentence or relation context (string)
        - verified: True when a numeric value was extracted (extract), or copied from the
          relation entry (extract_from_relations); False otherwise
    """

    # Map various textual forms to canonical property names
    PROPERTY_KEYWORDS: dict[str, str] = {
        # Standard properties
        "band gap": "band_gap",
        "band-gap": "band_gap",
        "melting point": "melting_point",
        "melting_temperature": "melting_point",
        "glass transition temperature": "glass_transition_temperature",
        "t_g": "glass_transition_temperature",
        "conductivity": "conductivity",
        "electrical conductivity": "conductivity",
        "mobility": "mobility",
        "efficiency": "efficiency",
        "thermal conductivity": "thermal_conductivity",
        "dielectric constant": "dielectric_constant",
        # Materials Project properties
        "density": "density",
        "formation energy": "formation_energy",
        "formation energy per atom": "formation_energy",
        "bulk modulus": "bulk_modulus",
        "shear modulus": "shear_modulus",
        "elastic modulus": "bulk_modulus",
        "magnetic moment": "magnetic_moment",
        "energy above hull": "energy_above_hull",
    }

    # Regex to capture a numeric value (with optional uncertainty) and unit.
    # Matches: "1.9 ± 0.1 eV", "2.1eV", "2.1 eV", "250 K", "150 °C", "5.2 g/cm3", "200 GPa",
    # "85%", "12.5 %", "5 m²/g", "10 cm⁻¹", "2 W/(m*K)", "3 J·mol(-1)".
    # The value may not start inside a word or number ("v8.5", "Na3"). The unit must start with a
    # letter, µ/μ, ° or be "%", so a digit run is never split into value and unit ("85" is not
    # 8 + 5; "2024-11" is not 2024 + "-11"). After the first character a unit may contain digits,
    # superscripts (² ³ ⁻ ¹ ...), "·", "*", "^", "_", "/" and "-", plus balanced "(...)" groups, so
    # an enclosing "(1.9 eV)" does not leak its ")" into the unit.
    VALUE_UNIT_PATTERN = re.compile(
        r'(?<![\w.])(?P<value>[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'
        r'(?:\s*±\s*(?P<uncertainty>\d+(?:\.\d+)?))?\s*'
        r'(?P<unit>%|°[CF]|[A-Za-zµμ°](?:[A-Za-zµμ°/\^\-_*·0-9²³⁰¹⁴⁵⁶⁷⁸⁹⁻]|\([^()\s]*\))*)'
    )

    # Maximum number of characters between material and property phrase to be considered “proximal” 
    PROXIMITY_WINDOW = 30  # characters

    def __init__(self, property_keywords: dict[str, str] | None = None):
        """Initialize extractor with optional custom property keywords.

        Args:
            property_keywords: override for PROPERTY_KEYWORDS mapping (an empty mapping falls
                back to the default).
        """
        self.keywords = property_keywords or self.PROPERTY_KEYWORDS

    def _is_proximal(self, material: str, prop_phrase: str, sentence: str) -> bool:
        """Return True if material and prop_phrase appear within PROXIMITY_WINDOW characters."""
        mat_low = material.lower()
        prop_low = prop_phrase.lower()
        sent_low = sentence.lower()

        for m in re.finditer(re.escape(mat_low), sent_low):
            for p in re.finditer(re.escape(prop_low), sent_low):
                if abs(m.start() - p.start()) <= self.PROXIMITY_WINDOW:
                    return True
        return False

    def _matches_pattern(self, material: str, prop_phrase: str, sentence: str) -> bool:
        """Check for “<prop_phrase> of <material>” or “<material> <prop_phrase>” syntax.

        Optional words may appear between material and property phrase. Uses regex
        to detect these common syntaxes.
        """
        mat = re.escape(material)
        prop = re.escape(prop_phrase)
        # pattern1: “<prop_phrase> of <material>”
        pattern1 = rf"\b{prop}\b\s+of\s+{mat}\b"
        # pattern2: “<material> <prop_phrase>”
        pattern2 = rf"\b{mat}\b\s+.*?\b{prop}\b"
        return bool(re.search(pattern1, sentence, flags=re.IGNORECASE) or
                    re.search(pattern2, sentence, flags=re.IGNORECASE))

    def extract(self, text: str, materials: list[str]) -> list[dict[str, Any]]:
        """Extract property entries for given materials from a block of text.

        Args:
            text: The full paragraph or page text to scan.
            materials: List of material names (e.g., ["EQE", "Na3Bi"]).

        Returns:
            A list of dicts. Each dict contains:
                - material: Material name (string)
                - property: Canonical property name (e.g., "band_gap")
                - raw_value: Extracted numeric string (e.g. "1.9") or None
                - raw_unit: Extracted unit string (e.g. "eV", "°C") or None
                - uncertainty: Extracted uncertainty (e.g. "0.05") or None
                - context: The full sentence containing the mention
                - verified: True if numeric extracted and patterns matched; False otherwise
        """
        results: list[dict[str, Any]] = []
        # Split text into sentences to preserve context
        sentences = re.split(r'(?<=[\.\?\!])\s+', text)
        lower_text = text.lower()

        for material in materials:
            mat_low = material.lower()
            if mat_low not in lower_text:
                continue  # skip if material is not in text at all

            for prop_phrase, prop_name in self.keywords.items():
                prop_low = prop_phrase.lower()
                for sent in sentences:
                    sent_low = sent.lower()
                    # First check proximity and pattern before numeric extraction
                    if mat_low in sent_low and prop_low in sent_low:
                        if not (self._is_proximal(material, prop_phrase, sent) and
                                self._matches_pattern(material, prop_phrase, sent)):
                            continue  # skip if not truly about “prop_phrase of material”

                        # Attempt numeric extraction in this proximal sentence
                        found_value = False
                        # Mask the material and property phrase so digits inside names ("Na3Bi")
                        # are not read as values, and keep only the first value that follows.
                        masked = re.sub(
                            "|".join(re.escape(t) for t in (material, prop_phrase)),
                            lambda m: " " * len(m.group(0)),
                            sent,
                            flags=re.IGNORECASE,
                        )
                        for match in list(self.VALUE_UNIT_PATTERN.finditer(masked))[:1]:
                            found_value = True
                            value = match.group("value")
                            unit = match.group("unit")
                            uncertainty = match.group("uncertainty")
                            entry = {
                                "material": material,
                                "property": prop_name,
                                "raw_value": value,
                                "raw_unit": unit,
                                "uncertainty": uncertainty if uncertainty else None,
                                "context": sent.strip(),
                                "verified": True,
                            }
                            results.append(entry)

                        # If property phrase & material match patterns but no numeric found,
                        # record placeholder with raw_value/unit = None and verified=False
                        if not found_value:
                            entry = {
                                "material": material,
                                "property": prop_name,
                                "raw_value": None,
                                "raw_unit": None,
                                "uncertainty": None,
                                "context": sent.strip(),
                                "verified": False,
                            }
                            results.append(entry)

        return results

    def extract_from_relations(self, term_record: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract “measured_property” relations from a term record.

        Args:
            term_record: A dict representing a term, containing a "relations" list,
                         where each relation is a dict with keys:
                           - relation: relation name (string)
                           - related_term: the term on the right side (string)
                           - verified: bool

        Returns:
            A list of dicts, each with keys:
                - material: term_record["term"]
                - property: canonical property name if matched, else related_term lowercased
                  with spaces replaced by underscores
                - raw_value: None (no numeric in relation itself)
                - raw_unit: None
                - uncertainty: None
                - context: "measured_property:<related_term>"
                - verified: copied from relation entry
        """
        results: list[dict[str, Any]] = []
        material = term_record.get("term", "").strip()
        for rel in term_record.get("relations", []):
            if rel.get("relation") == "measured_property":
                raw_prop = rel.get("related_term", "").strip().lower()
                prop_name = self.keywords.get(raw_prop, raw_prop.replace(" ", "_"))
                entry = {
                    "material": material,
                    "property": prop_name,
                    "raw_value": None,
                    "raw_unit": None,
                    "uncertainty": None,
                    "context": f"measured_property:{rel.get('related_term')}",
                    "verified": bool(rel.get("verified", False)),
                }
                results.append(entry)
        return results


class PropertyNormalizer:
    """Normalize extracted property values into standard units and apply plausibility checks.

    Usage:
        normalizer = PropertyNormalizer()
        normalized = normalizer.normalize(extracted_entries)
    """

    # Desired target units for each property
    TARGET_UNITS: dict[str, str] = {
        "band_gap": "eV",
        "melting_point": "K",
        "glass_transition_temperature": "K",
        "conductivity": "S/m",
        "mobility": "m2/(V*s)",
        "efficiency": "%",
        "thermal_conductivity": "W/(m*K)",
        "dielectric_constant": "",  # dimensionless
        "density": "kg/m3",
        "formation_energy": "eV/atom",
        "bulk_modulus": "GPa",
        "shear_modulus": "GPa",
        "magnetic_moment": "μ_B",
        "energy_above_hull": "eV/atom",
    }

    # Plausibility ranges for certain properties; if normalized_value falls outside,
    # flag unit_conversion_failed=True
    PLAUSIBILITY_RANGES: dict[str, Any] = {
        "efficiency": (0.0, 100.0),       # percent
        "density": (0.0, float("inf")),   # density > 0 (lower bound exclusive, see EXCLUSIVE_LOWER_BOUND)
        "band_gap": (0.0, 10.0),          # reasonable eV range
        "formation_energy": (-10.0, 10.0),# eV/atom range
        "bulk_modulus": (0.0, 1000.0),    # GPa range
        "shear_modulus": (0.0, 1000.0),   # GPa range
        "magnetic_moment": (0.0, 100.0),  # μ_B range
        "energy_above_hull": (0.0, 5.0),  # eV/atom
    }

    # Properties whose plausibility range excludes its lower bound (density 0.0 is not plausible)
    EXCLUSIVE_LOWER_BOUND: frozenset[str] = frozenset({"density"})

    _SUPERSCRIPTS = str.maketrans({
        "²": "2", "³": "3", "¹": "1", "⁰": "0", "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7",
        "⁸": "8", "⁹": "9", "⁻": "-", "−": "-", "µ": "μ",
    })

    @classmethod
    def _canonical_unit(cls, raw_unit: str) -> str:
        """Return a spelling-independent form of a unit for comparison.

        Lower-cases, maps superscripts to plain digits ("cm⁻¹" -> "cm-1", "m²" -> "m2"), maps the
        micro sign to the Greek mu, and drops spaces, "·", "*", "^" and parentheses, so that
        "W/(m*K)", "W/(m·K)" and "W/mK" compare equal, as do "S·cm⁻¹" and "S*cm-1".
        """
        text = raw_unit.strip().lower().translate(cls._SUPERSCRIPTS)
        return re.sub(r"[\s·*^()]", "", text)

    def normalize(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert raw values and units into canonical numeric + unit form.

        Plausibility filtering is applied after conversion. Entries are copied, never
        mutated in place. An entry without ``raw_value`` or ``raw_unit`` is passed through
        with the new fields at their defaults. An unknown unit, a non-numeric value or an
        implausible result sets ``unit_conversion_failed=True`` and leaves the normalized
        fields ``None`` (the failure is logged at DEBUG). Unknown properties pass their raw
        unit through unchanged.

        Args:
            entries: List of dicts from PhysicalPropertyExtractor, each containing:
                     raw_value (str or None), raw_unit (str or None),
                     uncertainty (str or None), property (str, required), etc.

        Returns:
            List of dicts with added fields for each entry:
                - normalized_value: float rounded to NORMALIZED_DECIMALS, or None
                - normalized_unit: str or None
                - uncertainty_value: float rounded to NORMALIZED_DECIMALS, or None
                - unit_conversion_failed: bool

        Raises:
            KeyError: If an entry has no ``property`` key.
        """
        normalized_entries: list[dict[str, Any]] = []

        for e in entries:
            prop = e["property"]
            raw_val = e.get("raw_value")
            raw_unit = e.get("raw_unit")
            unc = e.get("uncertainty")

            norm_entry = dict(e)  # copy original fields
            norm_entry.update({
                "normalized_value": None,
                "normalized_unit": None,
                "uncertainty_value": None,
                "unit_conversion_failed": False
            })

            # If there is no raw_value or raw_unit, skip numeric conversion
            if raw_val is None or raw_unit is None:
                normalized_entries.append(norm_entry)
                continue

            try:
                val = float(raw_val)
                unit_lower = self._canonical_unit(raw_unit)

                # Temperature-based properties → Kelvin
                if prop in ("melting_point", "glass_transition_temperature"):
                    if unit_lower in {"°c", "degc", "c"}:
                        norm_val = val + 273.15
                        unit = "K"
                        unc_norm = float(unc) if unc else None
                    elif unit_lower in {"k", "kelvin"}:
                        norm_val = val
                        unit = "K"
                        unc_norm = float(unc) if unc else None
                    else:
                        raise ValueError(f"Unknown temperature unit '{raw_unit}'")

                # Band gap → eV
                elif prop == "band_gap":
                    if unit_lower in {"ev"}:
                        norm_val = val
                        unit = "eV"
                        unc_norm = float(unc) if unc else None
                    elif unit_lower in {"mev"}:
                        norm_val = val / 1000.0
                        unit = "eV"
                        unc_norm = (float(unc) / 1000.0) if unc else None
                    else:
                        raise ValueError(f"Unknown energy unit '{raw_unit}'")

                # Conductivity → S/m (1 S/cm = 100 S/m)
                elif prop == "conductivity":
                    if unit_lower in {"s/cm", "scm-1"}:
                        norm_val = val * 100.0
                        unit = "S/m"
                        unc_norm = (float(unc) * 100.0) if unc else None
                    elif unit_lower in {"s/m", "sm-1"}:
                        norm_val = val
                        unit = "S/m"
                        unc_norm = float(unc) if unc else None
                    else:
                        raise ValueError(f"Unknown conductivity unit '{raw_unit}'")

                # Mobility → m2/(V*s) (1 cm²/V·s = 1e-4 m²/V·s)
                elif prop == "mobility":
                    if unit_lower in {"cm2/vs", "cm2v-1s-1"}:
                        norm_val = val * 1e-4
                        unit = "m2/(V*s)"
                        unc_norm = (float(unc) * 1e-4) if unc else None
                    elif unit_lower in {"m2/vs", "m2v-1s-1"}:
                        norm_val = val
                        unit = "m2/(V*s)"
                        unc_norm = float(unc) if unc else None
                    else:
                        raise ValueError(f"Unknown mobility unit '{raw_unit}'")

                # Efficiency → percent
                elif prop == "efficiency":
                    if raw_unit.strip() == "%":
                        norm_val = val
                        unit = "%"
                        unc_norm = float(unc) if unc else None
                    else:
                        # assume fraction (e.g. 0.85 → 85%)
                        norm_val = val * 100.0
                        unit = "%"
                        unc_norm = (float(unc) * 100.0) if unc else None

                # Thermal conductivity → W/(m*K)
                elif prop == "thermal_conductivity":
                    if unit_lower in {"w/mk", "wm-1k-1"}:
                        norm_val = val
                        unit = "W/(m*K)"
                        unc_norm = float(unc) if unc else None
                    elif unit_lower in {"w/cmk", "wcm-1k-1"}:
                        # 1 W/(cm*K) = 100 W/(m*K)
                        norm_val = val * 100.0
                        unit = "W/(m*K)"
                        unc_norm = (float(unc) * 100.0) if unc else None
                    else:
                        raise ValueError(f"Unknown thermal conductivity unit '{raw_unit}'")

                # Dielectric constant → dimensionless
                elif prop == "dielectric_constant":
                    norm_val = val
                    unit = ""
                    unc_norm = float(unc) if unc else None

                # Density → kg/m3 (1 g/cm3 = 1000 kg/m3)
                elif prop == "density":
                    if unit_lower in {"g/cm3", "gcm-3"}:
                        norm_val = val * 1000.0
                        unit = "kg/m3"
                        unc_norm = (float(unc) * 1000.0) if unc else None
                    elif unit_lower in {"kg/m3", "kgm-3"}:
                        norm_val = val
                        unit = "kg/m3"
                        unc_norm = float(unc) if unc else None
                    else:
                        raise ValueError(f"Unknown density unit '{raw_unit}'")

                # Formation energy → eV/atom (1 meV/atom = 0.001 eV/atom)
                elif prop == "formation_energy":
                    if unit_lower in {"ev/atom"}:
                        norm_val = val
                        unit = "eV/atom"
                        unc_norm = float(unc) if unc else None
                    elif unit_lower in {"mev/atom"}:
                        norm_val = val / 1000.0
                        unit = "eV/atom"
                        unc_norm = (float(unc) / 1000.0) if unc else None
                    else:
                        raise ValueError(f"Unknown formation energy unit '{raw_unit}'")

                # Bulk modulus → GPa (1 MPa = 0.001 GPa)
                elif prop == "bulk_modulus":
                    if unit_lower in {"gpa"}:
                        norm_val = val
                        unit = "GPa"
                        unc_norm = float(unc) if unc else None
                    elif unit_lower in {"mpa"}:
                        norm_val = val / 1000.0
                        unit = "GPa"
                        unc_norm = (float(unc) / 1000.0) if unc else None
                    else:
                        raise ValueError(f"Unknown bulk modulus unit '{raw_unit}'")

                # Shear modulus → GPa (same conversion as bulk modulus)
                elif prop == "shear_modulus":
                    if unit_lower in {"gpa"}:
                        norm_val = val
                        unit = "GPa"
                        unc_norm = float(unc) if unc else None
                    elif unit_lower in {"mpa"}:
                        norm_val = val / 1000.0
                        unit = "GPa"
                        unc_norm = (float(unc) / 1000.0) if unc else None
                    else:
                        raise ValueError(f"Unknown shear modulus unit '{raw_unit}'")

                # Magnetic moment → μ_B (assuming raw unit "μB", "uB", "mu_B", etc.)
                elif prop == "magnetic_moment":
                    if unit_lower in {"μb", "ub", "mu_b", "mu-b", "mub"}:
                        norm_val = val
                        unit = "μ_B"
                        unc_norm = float(unc) if unc else None
                    else:
                        raise ValueError(f"Unknown magnetic moment unit '{raw_unit}'")

                # Energy above hull → eV/atom (1 meV/atom = 0.001 eV/atom)
                elif prop == "energy_above_hull":
                    if unit_lower in {"ev/atom"}:
                        norm_val = val
                        unit = "eV/atom"
                        unc_norm = float(unc) if unc else None
                    elif unit_lower in {"mev/atom"}:
                        norm_val = val / 1000.0
                        unit = "eV/atom"
                        unc_norm = (float(unc) / 1000.0) if unc else None
                    else:
                        raise ValueError(f"Unknown energy above hull unit '{raw_unit}'")

                # Fallback: unknown property → pass through raw
                else:
                    norm_val = val
                    unit = raw_unit
                    unc_norm = float(unc) if unc else None

                # Apply plausibility checks
                if prop in self.PLAUSIBILITY_RANGES:
                    low, high = self.PLAUSIBILITY_RANGES[prop]
                    low_ok = norm_val > low if prop in self.EXCLUSIVE_LOWER_BOUND else norm_val >= low
                    if not (low_ok and norm_val <= high):
                        raise ValueError(f"Plausibility check failed for {prop}: {norm_val} not in [{low},{high}]")

                norm_entry["normalized_value"] = round(norm_val, NORMALIZED_DECIMALS)
                norm_entry["normalized_unit"] = unit
                if unc_norm is not None:
                    norm_entry["uncertainty_value"] = round(unc_norm, NORMALIZED_DECIMALS)

            except Exception:
                logger.debug("unit conversion failed for %s", prop, exc_info=True)
                norm_entry["unit_conversion_failed"] = True

            normalized_entries.append(norm_entry)

        return normalized_entries
