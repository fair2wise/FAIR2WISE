import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from ..agents.chebi import ChebiOboLookup
from ..agents.chem_checker import ChemicalFormulaValidator
from ..agents.properties import PhysicalPropertyExtractor, PropertyNormalizer
from .models import PropertyRecord
from .store import TermStore

logger = logging.getLogger(__name__)


@dataclass
class Services:
    """Bundles the three stateful service objects needed by tools and the orchestrator."""
    formula_checker: ChemicalFormulaValidator
    chebi_lookup: Optional[ChebiOboLookup]
    prop_extractor: PhysicalPropertyExtractor = field(default_factory=PhysicalPropertyExtractor)
    prop_normalizer: PropertyNormalizer = field(default_factory=PropertyNormalizer)


def build_services(
    mp_api_key: Optional[str] = None,
    chebi_obo_path: Optional[str] = None,
) -> Services:
    """Instantiate and return a Services bundle.

    Args:
        mp_api_key: Materials Project API key for formula validation. Falls back to
            the ``MP_API_KEY`` environment variable.
        chebi_obo_path: Path to a local ``chebi.obo`` file. ChEBI lookup is disabled
            if not provided or if the file fails to load.
    """
    api_key = mp_api_key or os.environ.get("MP_API_KEY", "")
    if not api_key:
        logger.warning("MP_API_KEY not set; formula validation may be incomplete.")

    chebi_lookup: Optional[ChebiOboLookup] = None
    if chebi_obo_path and os.path.exists(chebi_obo_path):
        try:
            chebi_lookup = ChebiOboLookup(chebi_obo_path)
        except Exception as e:
            logger.warning("Failed to load ChEBI ontology from %s: %s", chebi_obo_path, e)

    return Services(
        formula_checker=ChemicalFormulaValidator(api_key=api_key or None),
        chebi_lookup=chebi_lookup,
    )


def extract_and_attach_properties(text: str, store: TermStore, services: Services) -> bool:
    """Extract physical properties from *text* and attach them to matching terms in *store*.

    Runs the full property pipeline: extract → normalize → attach.
    Returns True if at least one new property was stored.
    """
    records = store.all_records()
    if not records:
        return False

    material_names = [r.term for r in records]
    raw_props = services.prop_extractor.extract(text, material_names)
    if not raw_props:
        return False

    updated = False
    for p in services.prop_normalizer.normalize(raw_props):
        key = TermStore.normalize(p["material"])
        prop = PropertyRecord(
            property=p["property"],
            value=p["normalized_value"],
            unit=p["normalized_unit"] or "",
            context=p["context"],
            uncertainty=p.get("uncertainty_value"),
            verified=not p["unit_conversion_failed"],
        )
        if store.attach_property(key, prop):
            logger.info("Attached property '%s' to '%s'", p["property"], p["material"])
            updated = True

    return updated
