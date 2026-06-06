#!/usr/bin/env python3
from abc import ABC, abstractmethod
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
from dotenv import load_dotenv
import functools
import json
import logging
import os
from pathlib import Path
import re
import requests
import sys
import threading
from typing import Dict, Any, Optional, List, Union, Tuple

import fitz  # this is exactly the same as PyMuPDF
from linkml_runtime.utils.schemaview import SchemaView
import openai
from rapidfuzz import process, fuzz  # still used by SchemaHelper for class/slot matching

# Allow `python3 app/modules/extract_terms.py ...` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.agents.chebi import ChebiOboLookup
from modules.agents.chem_checker import ChemicalFormulaValidator
from modules.agents.properties import PhysicalPropertyExtractor, PropertyNormalizer
# extract tables and images in an agentic way
# GEMMA 3 27b
# Llama 3.2 Vision

# ----------------------------------------
# LLM Client Setup
# ----------------------------------------
load_dotenv()


class ChatClient(ABC):
    """Abstract base class for synchronous LLM chat clients used by term extraction."""

    @abstractmethod
    def chat(self, prompt: str, *, temperature: float = 0.0, timeout: int = 240) -> str:
        """Send a prompt and return the LLM response text."""
        ...


class OllamaChatClient(ChatClient):
    """Synchronous Ollama chat client for local LLM inference."""

    def __init__(self, model: str, base_url: str):
        """Initialize with model name and Ollama base URL."""
        self.model = model
        self.base = base_url.rstrip("/")

    def chat(self, prompt: str, *, temperature: float = 0.0, timeout: int = 240) -> str:
        """Send a prompt to Ollama and return the response text."""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": temperature},
        }
        r = requests.post(f"{self.base}/api/chat", json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "") or ""


class CBorgChatClient(ChatClient):
    """
    OpenAI-compatible CBORG client (https://api.cborg.lbl.gov).
    Use model names like: "lbl/cborg-chat", "lbl/cborg-deepthought", etc.
    Env: CBORG_API_KEY, CBORG_BASE_URL (defaults to https://api.cborg.lbl.gov)
    """
    def __init__(self, model: str, api_key: Optional[str] = None, base_url: Optional[str] = None):
        """Initialize with CBORG model, API key, and base URL."""
        self.model = model
        self.client = openai.OpenAI(
            api_key=api_key or os.environ.get("CBORG_API_KEY"),
            base_url=(base_url or os.environ.get("CBORG_BASE_URL") or "https://api.cborg.lbl.gov").rstrip("/"),
        )

    def chat(self, prompt: str, *, temperature: float = 0.0, timeout: int = 240) -> str:
        """Send a prompt to CBORG and return the response text."""
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            timeout=timeout,
        )
        return resp.choices[-1].message.content or ""


def make_chat_client(
    backend: str,
    model: str,
    *,
    ollama_url: str = "http://localhost:11434",
    cborg_base: Optional[str] = None,
    cborg_api_key: Optional[str] = None,
) -> ChatClient:
    """Instantiate the appropriate synchronous chat client for the given backend."""
    b = (backend or "ollama").lower()
    if b == "ollama":
        return OllamaChatClient(model=model, base_url=ollama_url)
    if b in ("cborg", "cborg-openai"):
        return CBorgChatClient(model=model, api_key=cborg_api_key, base_url=cborg_base)
    raise ValueError(f"Unknown LLM backend: {backend}")


# ----------------------------------------
# Logging Configuration
# ----------------------------------------


class _AnsiColorFormatter(logging.Formatter):
    """Log formatter that adds ANSI colour codes based on log level."""

    _COLORS = {
        "DEBUG": "\033[36m",    # cyan
        "INFO": "\033[32m",     # green
        "WARNING": "\033[33m",  # yellow
        "ERROR": "\033[31m",    # red
        "CRITICAL": "\033[41m",  # red background
    }
    _RESET = "\033[0m"

    def format(self, record):
        """Format log record with colour-coded level and message."""
        level = record.levelname
        color = self._COLORS.get(level)
        if color:
            record.levelname = "{}{}{}".format(color, level, self._RESET)
            record.msg = "{}{}{}".format(color, record.getMessage(), self._RESET)
            record.args = ()
        return super(_AnsiColorFormatter, self).format(record)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(
    _AnsiColorFormatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
)
logger = logging.getLogger("LLMTermExtractor")
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    logger.addHandler(handler)

# ----------------------------------------
# Utility: Retriable Decorator
# ----------------------------------------


def retry_on_exception(
    exceptions: Union[Tuple[type, ...], type],
    retries: int = 2,
    delay_seconds: float = 1.0,
) -> Any:
    """
    Decorator that retries a function up to `retries` times if one of `exceptions` is raised.
    Sleeps `delay_seconds * 2^attempt` between attempts.
    """
    def decorator(fn):
        """Wrap function with retry logic."""
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            """Execute fn with retry on specified exceptions."""
            last_exc = None
            for attempt in range(retries + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    logger.warning(
                        f"Retryable error in {fn.__name__} (attempt {attempt + 1}/{retries + 1}): {e}"
                    )
                    if attempt < retries:
                        sleep_time = delay_seconds * (2 ** attempt)
                        logger.debug(f"Sleeping {sleep_time:.1f}s before retry")
                        threading.Event().wait(sleep_time)
                        continue
                    break
            raise last_exc
        return wrapper
    return decorator

# ----------------------------------------
# SchemaHelper (refined)
# ----------------------------------------


class SchemaHelper:
    """
    Loads a LinkML schema and provides:
      - RapidFuzz indexes for class‐names and slot‐names
      - Exact‐match + fuzzy suggestions
      - Domain/range validation
      - Relation filtering (drop 'description'/'category')
    """
    def __init__(self, schema_path: str = "matkg_schema.yaml", fuzzy_cutoff: int = 80):
        """
        Args:
            schema_path: Path to a LinkML YAML schema.
            fuzzy_cutoff: RapidFuzz score cutoff (0–100) for fuzzy matching.
        """
        self.schema_path = schema_path
        self.fuzzy_cutoff = fuzzy_cutoff
        self.schema_view = SchemaView(schema_path)
        self._load_classes_and_slots()
        self._build_fuzzy_indexes()

    def _load_classes_and_slots(self) -> None:
        """Load classes and slots from LinkML schema."""
        self.classes: Dict[str, Dict[str, Any]] = {}
        self.class_parents: Dict[str, Optional[str]] = {}
        for name, cls in self.schema_view.all_classes().items():
            desc = cls.description or f"A {name} entity"
            parent = cls.is_a or None
            self.classes[name] = {"description": desc, "slots": []}
            self.class_parents[name] = parent

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
        logger.info(f"Loaded schema: {len(self.classes)} classes, {len(self.slots)} slots")

    def _build_fuzzy_indexes(self) -> None:
        """Build lowercase→canonical maps for classes and slots."""
        self._class_names_lower = [c.lower() for c in self.classes]
        self._class_map_lower = {c.lower(): c for c in self.classes}
        self._slot_names_lower = [s.lower() for s in self.slots]
        self._slot_map_lower = {s.lower(): s for s in self.slots}
        logger.debug("Built fuzzy indexes for classes and slots")

    def get_schema_context_for_llm(self) -> str:
        """Generate schema context string for LLM."""
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
        """Return exact or fuzzy-matched class, or None."""
        if not target:
            return None
        tl = target.strip().lower()
        if tl in self._class_map_lower:
            return self._class_map_lower[tl]
        match = process.extractOne(tl, self._class_names_lower, scorer=fuzz.QRatio, score_cutoff=self.fuzzy_cutoff)
        if match:
            found_lower, score, _ = match
            return self._class_map_lower.get(found_lower)
        return None

    def _find_closest_slot(self, target: str) -> Optional[str]:
        """Return exact or fuzzy-matched slot, or None."""
        if not target:
            return None
        tl = target.strip().lower()
        if tl in self._slot_map_lower:
            return self._slot_map_lower[tl]
        match = process.extractOne(tl, self._slot_names_lower, scorer=fuzz.QRatio, score_cutoff=self.fuzzy_cutoff)
        if match:
            found_lower, score, _ = match
            return self._slot_map_lower.get(found_lower)
        return None

    def validate_and_fix_term(self, term_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ensure 'category' is valid (fuzzy-fix if needed).
        Filter out any relations named 'description' or 'category'.
        For each remaining relation:
          - If slot exists → keep verified=True
          - Else if fuzzy-match → correct name, verified=True
          - Else keep original name, verified=False
        """
        # 1) Category
        cat = term_data.get("category", "").strip()
        if cat not in self.classes:
            fixed = self._find_closest_class(cat)
            if fixed:
                logger.warning(f"Fixed category '{cat}' → '{fixed}'")
                term_data["category"] = fixed
            else:
                logger.warning(f"Unknown category '{cat}' (left as-is)")

        # 2) Process relations
        cleaned_rels: List[Dict[str, Union[str, bool]]] = []
        for rel in term_data.get("relations", []):
            pred = rel.get("relation", "").strip()
            obj = rel.get("related_term", "").strip()
            if pred.lower() in ("description", "category"):
                logger.debug(f"Dropping relation '{pred}' as prohibited")
                continue

            if pred in self.slots:
                cleaned_rels.append({"relation": pred, "related_term": obj, "verified": True})
            else:
                fixed_slot = self._find_closest_slot(pred)
                if fixed_slot:
                    logger.warning(f"Fixed relation '{pred}' → '{fixed_slot}'")
                    cleaned_rels.append({"relation": fixed_slot, "related_term": obj, "verified": True})
                else:
                    logger.warning(f"Unknown relation '{pred}' → marking unverified")
                    cleaned_rels.append({"relation": pred, "related_term": obj, "verified": False})

        term_data["relations"] = cleaned_rels
        return term_data

    def _is_subclass_of(self, child: str, parent: str) -> bool:
        """Recursive check if child inherits from parent."""
        if child == parent:
            return True
        if child not in self.classes:
            return False
        p = self.class_parents.get(child)
        if not p:
            return False
        return self._is_subclass_of(p, parent)

    def check_relation_validity(self, subj_cls: str, pred: str, obj_cls: str) -> bool:
        """
        Verify domain/range of (subj_cls, pred, obj_cls). Return False if invalid.
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

# ----------------------------------------
# LLMTermExtractor (enhanced, frequent saves,
# with property extractor/normalizer integration + ChEBI enrichment)
# ----------------------------------------


class LLMTermExtractor:
    """
    Extracts terms from PDFs using a local Ollama LLM + LinkML schema validation.
    - Drops 'description'/'category' relations
    - Records unverified relations with {"verified": false}
    - Ensures every ChemicalEntity gets a formula_validation entry (or 'missing')
    - Enriches chemical terms via ChEBI (formula, mass, charge, InChI, InChIKey, SMILES, etc.)
    - Incorporates PhysicalPropertyExtractor and PropertyNormalizer to attach properties on the fly
    - Saves JSON after every page that yields new terms or properties (thread-safe)
    """
    def __init__(
        self,
        model_name: str = "gemma3:27b",  # or "mistral-small3.1:latest"
        ollama_base_url: str = "http://localhost:11434",
        temperature: float = 0.0,
        data_dir: str = "./polymer_papers",
        output_file: str = "./storage/terminology/extracted_terms.json",
        context_length: int = 50,
        schema_path: str = "matkg_schema.yaml",
        max_workers: int = 50,
        chat_client: ChatClient | None = None,
        chebi_obo_path: str | None = None,
    ):
        """
        Args:
            ollama_model: Ollama model specifier.
            ollama_base_url: Base URL of Ollama API.
            temperature: LLM sampling temperature.
            data_dir: Directory with PDFs.
            output_file: Path to write JSON output.
            context_length: Tokens of context snippet.
            schema_path: Path to LinkML schema.
            max_workers: Parallel workers for pages.
            chebi_obo_path: Optional ChEBI OBO path for chemical enrichment.
        """
        self.model_name = model_name
        self.ollama_base_url = ollama_base_url.rstrip("/")
        self.temperature = temperature
        self.data_dir = data_dir
        self.output_file = output_file
        self.context_length = context_length
        self.max_workers = max_workers

        mp_api_key = os.environ.get("MP_API_KEY", "")
        if not mp_api_key:
            logger.warning("MP_API_KEY not set; formula validation may be incomplete.")
        self.formula_checker = ChemicalFormulaValidator(api_key=mp_api_key)

        self.schema_helper = SchemaHelper(schema_path=schema_path)
        self.terms_dict: Dict[str, Dict[str, Any]] = {}
        self._bk_terms: Dict[str, str] = {}  # display_text → key
        self.code_snippets: List[Dict[str, Any]] = []
        self._snippet_seen: set = set()  # dedup keys for code snippets

        # If no chat_client provided, use OllamaChatClient by default
        self.chat_client = chat_client or OllamaChatClient(
            model=self.model_name, base_url=self.ollama_base_url
        )

        # Initialize property extractor + normalizer
        self.prop_extractor = PhysicalPropertyExtractor()
        self.prop_normalizer = PropertyNormalizer()

        # Attempt to load ChEBI ontology
        chebi_obo_path = chebi_obo_path or os.environ.get("CHEBI_OBO_PATH") or "storage/ontologies/chebi.obo"
        try:
            self.chebi_lookup = ChebiOboLookup(chebi_obo_path)
        except Exception as e:
            logger.error(f"Failed to load ChEBI ontology: {e}")
            self.chebi_lookup = None

        # Metadata
        self.metadata: Dict[str, Any] = {
            "extraction_date": datetime.datetime.utcnow().isoformat() + "Z",
            "processed_files": 0,
            "processed_pages_total": 0,
            "processed_pages_with_terms": 0,
            "version": "2.1",
        }

        self._save_lock = threading.Lock()
        os.makedirs(os.path.dirname(self.output_file), exist_ok=True)
        if os.path.exists(self.output_file):
            try:
                with open(self.output_file, "r") as fh:
                    prev = json.load(fh)
                    for term in prev.get("terms", []):
                        key = term["term"].strip().lower()
                        self.terms_dict[key] = term
                        self._bk_terms[term["term"]] = key
                    for snip in prev.get("code_snippets", prev.get("xray_code_snippets", [])):
                        self.code_snippets.append(snip)
                        self._snippet_seen.add(self._snippet_key(snip))
                    loaded_meta = prev.get("metadata", {})
                    self.metadata.update(loaded_meta)
                logger.info(
                    f"Loaded {len(self.terms_dict)} existing terms and "
                    f"{len(self.code_snippets)} code snippets from {self.output_file}"
                )
            except Exception as e:
                logger.warning(f"Could not load previous terms: {e}")

    @retry_on_exception((Exception,), retries=2, delay_seconds=2.0)
    def call_llm(self, prompt: str, timeout: int = 240) -> str:
        """Call the configured chat client with retry logic."""
        return self.chat_client.chat(prompt, temperature=self.temperature, timeout=timeout)

    # def call_ollama(self, prompt: str, timeout: int = 240) -> str:
    #     """Call Ollama chat endpoint with retries."""
    #     payload = {
    #         "model": self.ollama_model,
    #         "messages": [{"role": "user", "content": prompt}],
    #         "stream": False,
    #         "options": {"temperature": self.temperature},
    #     }
    #     resp = requests.post(f"{self.ollama_base_url}/api/chat", json=payload, timeout=timeout)
    #     resp.raise_for_status()
    #     return resp.json().get("message", {}).get("content", "") or ""

    @retry_on_exception((Exception,), retries=2, delay_seconds=1.0)
    def extract_json_from_text(self, text: str) -> Dict[str, Any]:
        """
        Extract largest JSON object containing "terms". Return {"terms": []} if none.
        """
        pattern = r"\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\}"
        matches = list(re.finditer(pattern, text))
        matches.sort(key=lambda m: -len(m.group(0)))
        for m in matches:
            snippet = m.group(0)
            try:
                obj = json.loads(snippet)
                if isinstance(obj, dict) and "terms" in obj:
                    return obj
            except json.JSONDecodeError:
                continue
        return {"terms": []}

    @retry_on_exception((Exception,), retries=1, delay_seconds=1.0)
    def extract_json_from_text(self, text: str) -> Dict[str, Any]:
        """
        Extract largest JSON object containing "snippets". Return {"snippets": []} if none.
        """
        pattern = r"\{(?:[^{}]|(?:\{(?:[^{}]|(?:\{[^{}]*\}))*\}))*\}"
        matches = list(re.finditer(pattern, text))
        matches.sort(key=lambda m: -len(m.group(0)))
        for m in matches:
            snippet = m.group(0)
            try:
                obj = json.loads(snippet)
                if isinstance(obj, dict) and "snippets" in obj:
                    return obj
            except json.JSONDecodeError:
                continue
        return {"snippets": []}

    @staticmethod
    def _snippet_key(snip: Dict[str, Any]) -> Tuple[str, str, str]:
        """Dedup key for a code snippet (source_paper, page, code body)."""
        return (
            str(snip.get("source_paper", "")),
            str(snip.get("page", "")),
            (snip.get("code_snippet") or "").strip(),
        )

    def _looks_like_formula(self, s: str) -> bool:
        """Heuristic: uppercase‐lowercase blocks + digits → formula."""
        return bool(re.search(r"[A-Z][a-z]?[\d]", s or ""))

    def _postprocess_term(self, term: Dict[str, Any], context: str) -> Dict[str, Any]:
        """
        Ensure every ChemicalEntity has 'formula_validation'.
        If formula string exists and looks like a formula → validate/repair.
        Otherwise set formula=None and status="missing".
        """
        formula_str = term.get("formula")
        if not formula_str or not self._looks_like_formula(formula_str):
            term["formula"] = None
            term["formula_validation"] = {"status": "missing"}
            return term

        try:
            validation = self.formula_checker.validate(formula_str)
        except Exception as e:
            logger.warning(f"Error validating formula '{formula_str}': {e}")
            validation = {"status": "error", "details": {"error": str(e)}}

        if validation.get("status") == "invalid":
            repair_prompt = (
                f"The extracted string '{formula_str}' is not a valid chemical formula.\n"
                "Based on the context below, guess the correct formula and return ONLY the formula string.\n\n"
                f"CONTEXT:\n{context}"
            )
            try:
                # candidate = self.call_ollama(repair_prompt, timeout=60).strip().split()[0]
                candidate = self.call_llm(repair_prompt, timeout=60).strip().split()[0]
            except Exception as e:
                logger.warning(f"LLM failed to fix invalid formula '{formula_str}': {e}")
                candidate = ""
            if candidate and candidate != formula_str:
                try:
                    newval = self.formula_checker.validate(candidate)
                    if newval.get("status") != "invalid":
                        term["formula"] = candidate
                        validation = newval
                        validation["status"] = "corrected"
                    else:
                        validation["status"] = "invalid"
                except Exception as e:
                    logger.warning(f"Error re‐validating candidate '{candidate}': {e}")
        term["formula_validation"] = validation
        return term

    def normalize_term(self, term: str) -> str:
        """Lowercase + strip whitespace."""
        return term.strip().lower()

    def fuzzy_merge(self, term: str) -> Optional[str]:
        """
        Ask the LLM which existing term (if any) matches semantically.
        Return that existing key, or None.
        """
        if not self._bk_terms:
            return None

        existing_display = list(self._bk_terms.keys())
        bullets = "\n".join(f"- {disp}" for disp in existing_display)

        prompt = f"""
We have just extracted a new term:    "{term}"
Below is the list of all already‐registered terms (one per line; the first time we saw each term):
{bullets}

You must decide whether "{term}" refers to exactly the same concept as one of these,
or if it is a distinct new concept. Follow these rules:

  1. **Ignore only trivial punctuation (spaces, hyphens, slashes, brackets, parentheses, capitalization)**
     when comparing.  For example, "GIWAXS" and "GI-WAXS" are the *same* technique and should be merged
     (choose the variant already in the list).  Likewise, "XRD" and "X-RD" (if it appeared) are identical.
     Anything beyond punctuation differences (letters, numbers, or added qualifiers) is not trivial.

  2. **Do NOT merge distinct instrument or method acronyms**.  Even if two acronyms share letters, if they are
     known to be different techniques or materials, keep them separate.
     Examples you must treat as always distinct:
       - "SEM" (scanning electron microscopy) vs. "TEM" (transmission electron microscopy)
       - "AFM" (atomic force microscopy)
       - "XPS" vs. "UPS"
       - "MoTe2" vs. "WTe2" (different compounds)
       - "Al2O3[0001]" (specific surface) vs. "Al2O3" (generic material)
     In other words, if two strings differ by more than punctuation—by letters, numbers
     or explicit qualifiers—they should not be merged.

  3. **Do NOT merge general vs. specific variants**.
     If one term is a broader concept (e.g. "band structure") and another is a specialized version
     (e.g. "Dirac-like band structure"), treat them as distinct.
     Similarly, if a term includes an added qualifier or context
     (e.g. surface orientation "[0001]" vs. generic material), do not merge into a more general term.

  4. **If the newly extracted term is an exact punctuation-agnostic match** to one of the existing
     terms—i.e., removing or changing only punctuation/brackets/spaces/case makes them identical—then respond
     with exactly that already-registered term (preserve its original casing/spelling).
     Otherwise, respond `"None"`.

  5. **DO merge terms if one is the acronym for the other term**, vice versa,
      or one term includes the acronym and the other doesn't
      For example, "angle-resolved photoelectric spectroscopy" and "ARPES" should merge to become:
      "angle-resolved photoemission spectroscopy (ARPES)".
      Another example: "resonant soft xray scattering" or "R-SoXS" should merge to become:
      "Resonant soft xray scattering (RSoXS)"

  6. **Your response must be exactly one line**: either the exact existing term (matching punctuation
     and case as it appears above) or the single word `None`. Don't output anything else—no quotes,
     no extra commentary.

Here are additional examples to illustrate:

  • If the new term is `"GI-WAXS"` and the list already contains `"GIWAXS"`, respond exactly `"GIWAXS"`.
  • If the new term is `"RSoXS"` and the list already contains `"R-SoXS"`,
    respond exactly `"RSoXS" as the correct term`.
  • If the new term is `"SEM"` and the list contains `"SEM"`, respond `"SEM"`, but if the list contains
    only `"TEM"`, respond `"None"` (distinct acronyms).
  • If the new term is `"MoTe2"` and the list has `"WTe2"`, respond `"None"` (different compound).
  • If the new term is `"Band-structure"` and the list has `"Dirac-like band structure"`, respond `"None"`
    (general vs. specific).
  • If the new term is `"Al2O3[0001]"` and the list has `"Al2O3"`, respond `"None"` (surface-specific vs. generic).
  • If the new term is `"photoemission"` and the list has `"angle-resolved photoemission spectroscopy (ARPES)"`,
    respond `"None"` (general process vs. specific technique).
  • If the new term is `"X-RD"` and the list has `"XRD"`, respond `"XRD"`
    (consistent acronym once punctuation is removed).
  • "organic solar cells" and "OSCs" should merge to become "Organic solar cells (OSCs)"

Now, having read the rules, please answer: which of the above existing terms is exactly the same concept
as "{term}"?  If none match, respond with `None`.
"""
        try:
            # llm_response = self.call_ollama(prompt).strip()
            llm_response = self.call_llm(prompt).strip()

        except Exception as e:
            logger.warning(f"LLM merge check failed for '{term}': {e}")
            return None

        if llm_response in self._bk_terms:
            logger.debug(f"LLM‐merge '{term}' → '{llm_response}'")
            return self._bk_terms[llm_response]

        return None

    def _register_new_term(self, display_text: str) -> str:
        """
        Register new term → key, return lowercase key.
        """
        key = self.normalize_term(display_text)
        self._bk_terms[display_text] = key
        logger.debug(f"Registered new term '{display_text}' → '{key}'")
        return key

    def get_context_snippet(
        self, full_text: str, term: str, filename: str, page_num: int
    ) -> Dict[str, Union[str, int]]:
        """
        Extract ~context_length tokens around `term`. Return snippet + source/page.
        """
        sentences = re.split(r"(?<=[\.\!\?])\s+", full_text)
        low = term.lower()
        for sent in sentences:
            if low in sent.lower():
                tokens = sent.split()
                snippet = " ".join(tokens[: self.context_length])
                return {"text": snippet, "source_paper": filename, "page": page_num + 1}
        snippet = " ".join(full_text.split()[: self.context_length])
        return {"text": snippet, "source_paper": filename, "page": page_num + 1}

    def _prepare_prompt(self, schema_ctx: str, filename: str, page_num: int, text: str) -> str:
        """
        Build terms extraction prompt. Extracts materials-science entities and
        publication metadata. Code extraction is handled separately by regex.
        """
        max_len = 8000
        page_text = text[-max_len:] if len(text) > max_len else text

        few_shot = r"""
### EXAMPLE
Input:
CONTENT:
"Poly(3-hexylthiophene) (P3HT) is a conjugated polymer used in organic photovoltaics.
Published: March 2021. DOI: 10.1021/jacs.1c00001. Authors: Smith J, Lee K."

Output:
{
  "terms": [
    {
      "term": "Poly(3-hexylthiophene) (P3HT)",
      "definition": "A conjugated polymer used in organic photovoltaics.",
      "category": "ConjugatedPolymer",
      "formula": "C10H14S",
      "publication_year": 2021,
      "paper_title": "Machine Learning for Organic Photovoltaics",
      "authors": ["Smith J", "Lee K"],
      "doi": "10.1021/jacs.1c00001",
      "relations": [
        {
          "relation": "has_application",
          "related_term": "organic photovoltaics",
          "verified": true
        }
      ]
    }
  ]
}
### END-EXAMPLE
"""

        template = r"""
=== EXTRACTION TASK ===
schema_context:
{schema_ctx}

PAPER: {filename}
PAGE: {pnum}

CONTENT:
{text}

INSTRUCTIONS:
1. Extract ALL key materials-science entities from this page: materials, chemical
   entities, experimental techniques, processing methods, devices, and properties.
   Use ONLY schema entity types and relation names.
2. Do NOT extract code snippets — code is handled separately. Do NOT output
   relations named 'description' or 'category'.
3. On EVERY term, stamp publication metadata found anywhere on this page
   (headers, footers, copyright lines, citation blocks):
   - "publication_year": integer year (e.g. 2023). Required — always extract if present.
   - "paper_title": full title of the paper this page is from.
   - "authors": list of author names in "Surname Initial" format (e.g. ["Smith J", "Lee K"]).
   - "doi": DOI string if present (e.g. "10.1021/jacs.3c00001").
   - "journal": journal name if present.
   Publication metadata should be the SAME on all terms from the same page.
4. Output JSON exactly:

{{
  "terms": [
    {{
      "term": "exact term from text",
      "definition": "brief technical definition",
      "category": "exact_entity_type_from_schema",
      "formula": "valid chemical formula or null",
      "publication_year": 2023,
      "paper_title": "Full paper title or null",
      "authors": ["Surname I", "..."],
      "doi": "10.xxxx/xxxxx or null",
      "journal": "Journal name or null",
      "relations": [
        {{
          "relation": "exact_predicate_name_from_schema",
          "related_term": "related term name",
          "verified": true
        }}
      ]
    }}
  ]
}}
"""
        return (
            f"{template.format(schema_ctx=schema_ctx, filename=filename, pnum=page_num+1, text=page_text)}\n"
            f"{few_shot}"
        )

    def _save_terms_threadsafe(self) -> None:
        """Acquire lock and write JSON to disk."""
        with self._save_lock:
            try:
                # Ensure each term has "properties" key (even if empty)
                terms_out = []
                for t in self.terms_dict.values():
                    if "properties" not in t:
                        t["properties"] = []
                    terms_out.append(t)
                out = {
                    "metadata": self.metadata,
                    "terms": terms_out,
                    "code_snippets": self.code_snippets,
                }
                with open(self.output_file, "w") as fh:
                    json.dump(out, fh, indent=2)
                logger.debug(f"Saved {len(self.terms_dict)} terms to {self.output_file}")
            except Exception as e:
                logger.error(f"Failed to save terms: {e}")

    def _save_snippets_threadsafe(self) -> None:
        """Acquire lock and write JSON to disk (terms + code snippets)."""
        with self._save_lock:
            try:
                terms_out = []
                for t in self.terms_dict.values():
                    if "properties" not in t:
                        t["properties"] = []
                    terms_out.append(t)
                out = {
                    "metadata": self.metadata,
                    "terms": terms_out,
                    "code_snippets": self.code_snippets,
                }
                with open(self.output_file, "w") as fh:
                    json.dump(out, fh, indent=2)
                logger.debug(
                    f"Saved {len(self.code_snippets)} code snippets to {self.output_file}"
                )
            except Exception as e:
                logger.error(f"Failed to save code snippets: {e}")

    def _prepare_code_snippet_prompt(self, page_text: str) -> str:
        """
        Prompt for scientific CONTEXT around code snippets.
        Code bodies are extracted by regex — not by LLM.
        Returns JSON with "snippets" keyed by function_name → domain metadata.
        """
        max_len = 6000
        text = page_text[-max_len:] if len(page_text) > max_len else page_text

        return f"""=== CODE SNIPPET CONTEXT EXTRACTION ===
This page may contain scientific analysis code (e.g. scattering, spectroscopy, simulation).
Extract domain metadata for any code functions mentioned or used on this page.
Do NOT extract code bodies — only the surrounding scientific context.

CONTENT:
{text}

For each function name or code block referenced on this page, extract:
- "function_name": the Python/MATLAB function name, or null
- "scattering_technique": one of SAXS, WAXS, GIWAXS, GISAXS, or null if not scattering-related
- "peak_positions": list of observed peak positions mentioned on the page, or []
- "d_spacing": list of d-spacing values mentioned on the page, or []
- "peak_assignments": list of crystallographic assignments mentioned on the page, or []
- "authors": authors of the library/code, or []
- "code_description": one-sentence plain-English description of what the function does

Return {{"snippets": []}} if page has no scientific analysis or code references.

Output JSON:
{{
  "snippets": [
    {{
      "function_name": "function name or null",
      "scattering_technique": "SAXS/WAXS/GIWAXS/GISAXS or null",
      "peak_positions": [],
      "d_spacing": [],
      "peak_assignments": [],
      "authors": [],
      "code_description": "what this function does"
    }}
  ]
}}"""

    def extract_code_snippets(
        self,
        page_text: str,
        client: ChatClient,
        schema_helper: SchemaHelper,
        *,
        source_paper: str = "",
        page: int = 0,
    ) -> List[Dict]:
        """
        Extract code snippets from a page.

        Strategy:
          1. Regex extracts ALL named code blocks (def/class/function) deterministically.
          2. LLM extracts scattering context (technique, peaks, d-spacing, description,
             authors) keyed by function_name — enriches regex results.
          3. Merge: regex provides code body, LLM provides scientific context.

        Returns list of snippet dicts. Empty list if no code found.
        """
        if not page_text or len(page_text.split()) < 20:
            return []

        # ── Step 1: Regex extracts all named code blocks ──────────────────────
        named_block_re = re.compile(
            r"((?:(?:import|from|library|require|using)\s+\S[^\n]*\n)*"  # optional leading imports
            r"(?:def|class|function|func)\s+(\w+)\s*[\(\[{]"             # named block keyword
            r"[^\n]*\n"                                                    # rest of header
            r"(?:[ \t]+[^\n]+\n){1,})",                                   # ≥1 indented body lines
            re.MULTILINE,
        )

        regex_results: List[Dict] = []
        seen_fn_names: set = set()
        seen_bodies: set = set()

        for dm in named_block_re.finditer(page_text):
            fn_name = dm.group(2)
            code_body = dm.group(1).rstrip()
            if fn_name.lower() in seen_fn_names or code_body.strip() in seen_bodies:
                continue
            seen_fn_names.add(fn_name.lower())
            seen_bodies.add(code_body.strip())

            lang = "python"
            if re.search(r"\bfunction\b", code_body) and not re.search(r"\bdef\b", code_body):
                lang = "matlab" if re.search(r"\bend\b", code_body) else "r"

            regex_results.append({
                "scattering_technique": None,
                "peak_positions": [],
                "d_spacing": [],
                "peak_assignments": [],
                "function_name": fn_name,
                "authors": [],
                "code_snippet": code_body,
                "code_language": lang,
                "code_description": f"{fn_name}: extracted from {source_paper} p.{page}",
                "page": page,
                "source_paper": source_paper,
            })
            logger.debug(f"Regex extracted '{fn_name}' from {source_paper} page {page}")

        # ── Step 2: LLM extracts scattering context (no code bodies) ──────────
        prompt = self._prepare_code_snippet_prompt(page_text)
        llm_context: Dict[str, Dict] = {}  # function_name.lower() → context dict
        try:
            response = client.chat(prompt, temperature=self.temperature, timeout=120)
            data = self.extract_json_from_text(response)
            for snip in data.get("snippets", []):
                if not isinstance(snip, dict):
                    continue
                fn = (snip.get("function_name") or "").strip().lower()
                if fn:
                    llm_context[fn] = snip
                else:
                    # no function name — attach to any unmatched regex result
                    llm_context["__anonymous__"] = snip
        except Exception as e:
            logger.warning(f"LLM context extraction failed ({source_paper} p.{page}): {e} — using regex only")

        # ── Step 3: Merge regex code + LLM context ────────────────────────────
        results: List[Dict] = []
        for r in regex_results:
            fn_key = (r["function_name"] or "").lower()
            ctx = llm_context.get(fn_key) or llm_context.get("__anonymous__") or {}
            r["scattering_technique"] = ctx.get("scattering_technique") or None
            r["peak_positions"]  = ctx.get("peak_positions")  or []
            r["d_spacing"]       = ctx.get("d_spacing")       or []
            r["peak_assignments"]= ctx.get("peak_assignments")or []
            r["authors"]         = ctx.get("authors")         or []
            if ctx.get("code_description"):
                r["code_description"] = ctx["code_description"]
            results.append(r)

        if results:
            logger.info(f"Extracted {len(results)} snippet(s) from {source_paper} page {page} "
                        f"({len(regex_results)} regex, {len(llm_context)} LLM context matches)")
        return results

    def _collect_code_snippets(
        self,
        page_text: str,
        filename: str,
        page_num: int,
        pub_meta: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Run code-snippet extraction for one page, dedup into
        `self.code_snippets`, and save (thread-safe). Returns True if new
        snippets were added.
        """
        snippets = self.extract_code_snippets(
            page_text,
            self.chat_client,
            self.schema_helper,
            source_paper=filename,
            page=page_num + 1,
        )
        _pm = pub_meta or {}
        updated = False
        for snip in snippets:
            # Stamp pub metadata onto snippet — authors from pub_meta only if
            # LLM didn't attribute to a library author
            if not snip.get("paper_title"):
                snip["paper_title"] = _pm.get("paper_title")
            if not snip.get("doi"):
                snip["doi"] = _pm.get("doi")
            # pub_meta authors = paper authors; keep separate from library authors
            if not snip.get("paper_authors"):
                snip["paper_authors"] = _pm.get("authors") or []
            # stamp publication_year so recency boost fires in score_prp
            if not snip.get("publication_year"):
                snip["publication_year"] = _pm.get("publication_year")
            key = self._snippet_key(snip)
            if key in self._snippet_seen:
                continue
            self._snippet_seen.add(key)
            self.code_snippets.append(snip)
            updated = True

        if updated:
            self._save_snippets_threadsafe()
        return updated

    @retry_on_exception((Exception,), retries=1, delay_seconds=1.0)
    def process_page(self, doc: fitz.Document, pdf_path: str, page_num: int, pub_year: Optional[int] = None, pub_meta: Optional[Dict[str, Any]] = None) -> bool:
        """
        Process one page:
        - Extract text
        - Call LLM for JSON
        - Validate/fix each term
        - Enrich chemical terms with ChEBI data (formula, mass, charge, InChI, InChIKey, SMILES, etc.)
        - Drop prohibited relations
        - Repair/fill formula & formula_validation
        - Merge duplicates via LLM
        - Extract & normalize properties for all known materials on this page
        - Attach properties to term entries
        - If new/updated → save immediately (thread-safe)
        Returns True if new/updated terms or properties were added.
        """
        filename = os.path.basename(pdf_path)
        logger.debug(f">> Starting process_page: {filename} (page {page_num+1})")
        try:
            page = doc.load_page(page_num)
            raw_text = page.get_text()
        except Exception as e:
            logger.error(f"Error reading page {page_num+1} of {filename}: {e}")
            return False

        if not raw_text or len(raw_text.split()) < 20:
            logger.info(f"Skipping page {page_num+1} (insufficient text).")
            return False

        schema_ctx = self.schema_helper.get_schema_context_for_llm()
        prompt = self._prepare_prompt(schema_ctx, filename, page_num, raw_text)

        start = datetime.datetime.utcnow()
        try:
            logger.debug(f"Calling LLM for {filename} page {page_num+1}")
            # response = self.call_ollama(prompt)
            response = self.call_llm(prompt)

            latency = (datetime.datetime.utcnow() - start).total_seconds()
            logger.debug(f"LLM response for {filename} page {page_num+1} in {latency:.2f}s")
        except Exception as e:
            logger.error(f"LLM failed for {filename} page {page_num+1}: {e}")
            return False

        try:
            data = self.extract_json_from_text(response)
            logger.debug(f"Parsed JSON for {filename} page {page_num+1}")
        except Exception as e:
            logger.error(f"JSON parsing failed on {filename} page {page_num+1}: {e}")
            return False

        if not data.get("terms"):
            logger.info(f"No terms found on {filename} page {page_num+1}.")
            # Even if no new terms, we may still want to extract properties if existing materials appear
            new_or_updated = self._extract_and_attach_properties(raw_text)
            # Additive: scan for x-ray scattering code snippets regardless of terms
            snippets_updated = self._collect_code_snippets(raw_text, filename, page_num, pub_meta)
            return new_or_updated or snippets_updated

        # Harvest pub metadata from LLM term responses — fill gaps in pub_meta
        # LLM now stamps paper_title/authors/doi/journal on every term it extracts.
        # Use first term that has any of these fields to enrich pub_meta for the page.
        _pm_enriched = dict(pub_meta or {})
        for raw_term in data.get("terms", []):
            llm_year = raw_term.get("publication_year")
            if not pub_year and isinstance(llm_year, int) and 1990 <= llm_year <= 2026:
                pub_year = llm_year
                _pm_enriched.setdefault("publication_year", pub_year)
                logger.debug(f"Got publication_year {pub_year} from LLM for {filename}")
            for _f in ("paper_title", "doi", "journal"):
                if raw_term.get(_f) and not _pm_enriched.get(_f):
                    _pm_enriched[_f] = raw_term[_f]
            if raw_term.get("authors") and not _pm_enriched.get("authors"):
                _pm_enriched["authors"] = raw_term["authors"]
            if all(_pm_enriched.get(f) for f in ("publication_year", "paper_title", "doi")):
                break  # have enough
        pub_meta = _pm_enriched

        added_or_updated = False
        page_terms: List[str] = []

        for raw_term in data["terms"]:
            name = raw_term.get("term", "").strip()
            if not name:
                continue

            page_terms.append(name)
            # 1) Validate/fix category & drop prohibited relations
            fixed_term = self.schema_helper.validate_and_fix_term(raw_term)

            # 2) Ensure formula & validation
            snippet = self.get_context_snippet(raw_text, name, filename, page_num)
            validated_term = self._postprocess_term(fixed_term, snippet["text"])

            # maybe only do this if it's a material or chemical...
            # it found an entry for Fill Factor (FF)
            # 3) Enrich with ChEBI, if available
            if self.chebi_lookup:
                try:
                    chebi_info = self.chebi_lookup.lookup(name)
                    if chebi_info:
                        validated_term["chebi"] = chebi_info
                        # If no formula from LLM, pull from ChEBI
                        if not validated_term.get("formula") and chebi_info.get("formula"):
                            validated_term["formula"] = chebi_info["formula"]
                            validated_term["formula_validation"] = {"status": "from_chebi"}
                        # Add SMILES, charge, InChI, InChIKey if missing
                        for key in ("smiles", "charge", "inchi", "inchikey", "mass"):
                            if chebi_info.get(key) and not validated_term.get(key):
                                validated_term[key] = chebi_info[key]
                except Exception as e:
                    logger.warning(f"ChEBI lookup failed for '{name}': {e}")

            # 4) Merge into terms_dict
            key = self.normalize_term(name)
            existing_key = self.fuzzy_merge(name)

            # Helper to compare relation tuples
            def relation_tuple(rel: Dict[str, Any]) -> Tuple[str, str]:
                """Return (predicate, object) tuple for deduplication."""
                return (rel["relation"], rel["related_term"])

            if existing_key:
                entry = self.terms_dict[existing_key]
                if (page_num + 1) not in entry.get("pages", []):
                    entry.setdefault("pages", []).append(page_num + 1)
                    entry.setdefault("source_papers", []).append(filename)
                    entry.setdefault("context_snippets", []).append(snippet)
                    added_or_updated = True
                if pub_year and not entry.get("publication_year"):
                    entry["publication_year"] = pub_year
                    added_or_updated = True
                # backfill pub_meta fields on existing entries if not yet set
                if pub_meta:
                    for _field in ("paper_title", "doi", "journal", "volume", "issue",
                                   "pages_range", "abstract_text"):
                        if pub_meta.get(_field) and not entry.get(_field):
                            entry[_field] = pub_meta[_field]
                            added_or_updated = True
                    for _list_field in ("authors", "institutions", "keywords"):
                        if pub_meta.get(_list_field) and not entry.get(_list_field):
                            entry[_list_field] = pub_meta[_list_field]
                            added_or_updated = True

                ex_rels = entry.setdefault("relations", [])
                existing_rel_tups = {relation_tuple(r) for r in ex_rels}
                for new_rel in validated_term.get("relations", []):
                    tup = relation_tuple(new_rel)
                    if tup not in existing_rel_tups:
                        subj_cat = entry.get("category", "")
                        obj_key = self.normalize_term(new_rel["related_term"])
                        obj_entry = self.terms_dict.get(obj_key)
                        obj_cat = obj_entry.get("category") if obj_entry else ""
                        if new_rel.get("verified", False) and subj_cat and obj_cat:
                            if not self.schema_helper.check_relation_validity(
                                subj_cat, new_rel["relation"], obj_cat
                            ):
                                logger.warning(
                                    f"Relation '{new_rel['relation']}' invalid for {subj_cat}→{obj_cat},marking verified=false"
                                )
                                new_rel["verified"] = False
                        ex_rels.append(new_rel)
                        added_or_updated = True

                new_def = validated_term.get("definition", "")
                if len(new_def) > len(entry.get("definition", "")):
                    entry["definition"] = new_def
                    added_or_updated = True

                # Update any missing chemical details from ChEBI
                if validated_term.get("chebi"):
                    for chem_key in (
                        "chebi",
                        "formula",
                        "formula_validation",
                        "smiles",
                        "charge",
                        "inchi",
                        "inchikey",
                        "mass"
                    ):
                        if validated_term.get(chem_key) and not entry.get(chem_key):
                            entry[chem_key] = validated_term[chem_key]
                            added_or_updated = True

            else:
                new_key = self._register_new_term(name)
                _pm = pub_meta or {}
                entry: Dict[str, Any] = {
                    "term": name,
                    "definition": validated_term.get("definition", ""),
                    "category": validated_term.get("category", "Thing"),
                    "formula": validated_term.get("formula"),
                    "formula_validation": validated_term.get("formula_validation"),
                    "relations": [],
                    "pages": [page_num + 1],
                    "source_papers": [filename],
                    "context_snippets": [snippet],
                    "publication_year": pub_year,
                    "paper_title": _pm.get("paper_title"),
                    "authors": _pm.get("authors") or [],
                    "institutions": _pm.get("institutions") or [],
                    "doi": _pm.get("doi"),
                    "journal": _pm.get("journal"),
                    "volume": _pm.get("volume"),
                    "issue": _pm.get("issue"),
                    "pages_range": _pm.get("pages_range"),
                    "abstract_text": _pm.get("abstract_text"),
                    "keywords": _pm.get("keywords") or [],
                }
                # include any ChEBI enrichment
                for chem_key in ("chebi", "smiles", "charge", "inchi", "inchikey", "mass"):
                    if validated_term.get(chem_key):
                        entry[chem_key] = validated_term[chem_key]

                # add relations
                for rel in validated_term.get("relations", []):
                    subj_cat = entry["category"]
                    obj_key = self.normalize_term(rel["related_term"])
                    obj_entry = self.terms_dict.get(obj_key)
                    obj_cat = obj_entry.get("category") if obj_entry else ""
                    if rel.get("verified", False) and subj_cat and obj_cat:
                        if not self.schema_helper.check_relation_validity(
                            subj_cat, rel["relation"], obj_cat
                        ):
                            logger.warning(
                                f"Relation '{rel['relation']}' invalid for {subj_cat}→{obj_cat}, marking verified=false"
                            )
                            rel["verified"] = False
                    entry["relations"].append(rel)

                self.terms_dict[new_key] = entry
                added_or_updated = True
                logger.info(f"Added new term '{name}' (page {page_num+1} of {filename})")

        # 5) After terms are merged, extract + normalize properties for all known materials on this page
        prop_updated = self._extract_and_attach_properties(raw_text)

        # 6) Additive: scan page for x-ray scattering peak-finding code snippets
        snippets_updated = self._collect_code_snippets(raw_text, filename, page_num, pub_meta)

        if added_or_updated or prop_updated:
            self._save_terms_threadsafe()

        return added_or_updated or prop_updated or snippets_updated

    def _extract_and_attach_properties(self, full_text: str) -> bool:
        """
        Find numeric property mentions for any known material names in full_text,
        normalize them, and merge into `self.terms_dict`.
        Returns True if any properties were added or updated.
        """
        if not self.terms_dict:
            return False

        material_names = [t["term"] for t in self.terms_dict.values()]
        raw_props = self.prop_extractor.extract(full_text, material_names)
        if not raw_props:
            return False

        normalized_props = self.prop_normalizer.normalize(raw_props)
        updated = False

        for p in normalized_props:
            mat = p["material"]
            mat_key = self.normalize_term(mat)
            if mat_key not in self.terms_dict:
                continue

            term_record = self.terms_dict[mat_key]
            props_list = term_record.setdefault("properties", [])

            existing_tuples = {
                (pr["property"], pr["value"], pr["unit"], pr["context"]) for pr in props_list
            }
            prop_tuple = (p["property"], p["normalized_value"], p["normalized_unit"], p["context"])
            if prop_tuple not in existing_tuples:
                props_list.append({
                    "property": p["property"],
                    "value": p["normalized_value"],
                    "unit": p["normalized_unit"],
                    "uncertainty": p.get("uncertainty_value"),
                    "context": p["context"],
                    "verified": not p["unit_conversion_failed"]
                })
                logger.info(f"Attached property '{p['property']}' to material '{mat}'")
                updated = True

        return updated

    def _extract_year_from_pdf(self, doc: fitz.Document, pdf_path: str) -> Optional[int]:
        """
        Attempt to extract publication year from PDF metadata or first-page text.
        Delegates to _extract_pub_metadata for the full date-aware logic.
        Returns a 4-digit year int or None.
        """
        meta = self._extract_pub_metadata(doc, pdf_path)
        return meta.get("publication_year")

    def _extract_pub_metadata(self, doc: fitz.Document, pdf_path: str) -> Dict[str, Any]:
        """
        Extract publication metadata from PDF metadata fields and first-page text.
        Returns a dict with keys: publication_year, paper_title, authors,
        institutions, doi, journal, volume, issue, pages_range, abstract_text, keywords.
        All values are None or [] if not found.

        Year extraction priority:
          1. Explicit publication/accepted/received date on first page
          2. PDF metadata creationDate / modDate (only if plausible — not future-dated)
          3. Most common year in first-page text that appears near a date-like context
          4. Most common 4-digit year on first page as last resort
        """
        import datetime as _dt
        pdf_meta = doc.metadata or {}
        filename = os.path.basename(pdf_path)
        current_year = _dt.datetime.now(_dt.timezone.utc).year

        # Month names for date pattern matching
        _MONTHS = (
            r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
            r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        )

        # --- first page text (primary source for pub dates) ---
        first_text = doc.load_page(0).get_text() if doc.page_count > 0 else ""

        # --- publication year ---
        publication_year: Optional[int] = None

        # Priority 1: explicit pub/accepted/received/revised date on first page
        # Patterns: "Received 14 March 2023", "Accepted: 2023-07-01",
        #            "Published online 5 Jan 2024", "Available online 2022"
        explicit_patterns = [
            # "Published"/"Accepted"/"Received"/"Revised" + full date
            rf"(?i)(?:published|accepted|received|revised|available\s+online)[^\n]{{0,40}}"
            rf"(?:{_MONTHS}\s+\d{{1,2}},?\s*((?:19|20)\d{{2}})"
            rf"|\d{{1,2}}\s+{_MONTHS}\s*((?:19|20)\d{{2}})"
            rf"|((?:19|20)\d{{2}})\s*[-–]\s*\d{{1,2}}\s*[-–]\s*\d{{1,2}})",
            # "Published"/"Accepted" + bare year
            r"(?i)(?:published|accepted|received|revised)[^\n]{0,30}((?:19|20)\d{2})",
            # ISO date near pub keyword: "2023-03-15"
            rf"(?i)(?:published|accepted|received|revised)[^\n]{{0,20}}"
            rf"((?:19|20)\d{{2}})-\d{{2}}-\d{{2}}",
            # "© 2023" or "Copyright 2023" — weaker signal, use only if nothing else
        ]
        for pat in explicit_patterns:
            m = re.search(pat, first_text)
            if m:
                # grab first non-None capture group
                yr_str = next((g for g in m.groups() if g and re.match(r"(19|20)\d{2}", g)), None)
                if yr_str:
                    yr = int(yr_str[:4])
                    if 1990 <= yr <= current_year:
                        publication_year = yr
                        logger.debug(f"Year {yr} from explicit date pattern for {filename}")
                        break

        # Priority 2: PDF metadata creationDate / modDate — only trust if ≤ current year
        if not publication_year:
            for key in ("creationDate", "modDate"):
                val = (pdf_meta.get(key) or "").strip()
                m = re.search(r"((?:19|20)\d{2})", val)
                if m:
                    yr = int(m.group(1))
                    if 1990 <= yr <= current_year:
                        publication_year = yr
                        logger.debug(f"Year {yr} from PDF metadata '{key}' for {filename}")
                        break

        # Priority 3: year adjacent to a month name on first page
        if not publication_year:
            month_year_m = re.findall(
                rf"(?:{_MONTHS})\s+(?:\d{{1,2}},?\s*)?((?:19|20)\d{{2}})"
                rf"|((?:19|20)\d{{2}})\s+{_MONTHS}",
                first_text,
            )
            candidates = []
            for grp in month_year_m:
                for g in grp:
                    if g and re.match(r"(19|20)\d{2}", g):
                        yr = int(g)
                        if 1990 <= yr <= current_year:
                            candidates.append(yr)
            if candidates:
                publication_year = max(set(candidates), key=candidates.count)
                logger.debug(f"Year {publication_year} from month-adjacent pattern for {filename}")

        # Priority 4: most common 4-digit year on first page (last resort)
        if not publication_year:
            all_years = [
                int(y) for y in re.findall(r"\b((?:19|20)\d{2})\b", first_text)
                if 1990 <= int(y) <= current_year
            ]
            if all_years:
                publication_year = max(set(all_years), key=all_years.count)
                logger.debug(f"Year {publication_year} from most-common fallback for {filename}")

        if not publication_year:
            logger.debug(f"Could not determine publication year for {filename}")

        # --- title ---
        paper_title: Optional[str] = (pdf_meta.get("title") or "").strip() or None
        if not paper_title and doc.page_count > 0:
            first_lines = [ln.strip() for ln in first_text.splitlines() if ln.strip()]
            # Find first substantive line (10–200 chars, not a URL/DOI/journal header)
            _skip = re.compile(r"(?i)^(https?://|10\.\d{4}|doi|vol|pp\.|©|received|accepted|published|edited|keywords)")
            for i, ln in enumerate(first_lines[:10]):
                if 10 <= len(ln) <= 200 and not _skip.search(ln):
                    # join next line if it looks like title continuation
                    # (short, no verb, no punctuation ending, not a name/email line)
                    title_parts = [ln]
                    for nxt in first_lines[i+1:i+4]:
                        if (5 <= len(nxt) <= 120
                                and not _skip.search(nxt)
                                and not re.search(r"[@,;]", nxt)
                                and not re.search(r"\.$", nxt)):
                            title_parts.append(nxt)
                        else:
                            break
                    paper_title = " ".join(title_parts)
                    break

        # --- authors ---
        authors: List[str] = []
        raw_author = (pdf_meta.get("author") or "").strip()
        if raw_author:
            parts = re.split(r";| and ", raw_author)
            authors = [p.strip() for p in parts if p.strip()]

        # --- DOI ---
        doi: Optional[str] = None
        for key in ("subject", "keywords", "identifier"):
            val = pdf_meta.get(key, "") or ""
            m = re.search(r"10\.\d{4,}/\S+", val)
            if m:
                doi = m.group(0).rstrip(".,)")
                break
        if not doi:
            for pn in range(min(2, doc.page_count)):
                text = doc.load_page(pn).get_text()
                m = re.search(r"10\.\d{4,}/\S+", text)
                if m:
                    doi = m.group(0).rstrip(".,)")
                    break

        # --- keywords from metadata ---
        keywords: List[str] = []
        raw_kw = (pdf_meta.get("keywords") or "").strip()
        if raw_kw:
            kw_parts = re.split(r"[;,]", raw_kw)
            keywords = [k.strip() for k in kw_parts if k.strip()]

        # --- journal / volume / issue / pages_range / abstract ---
        journal: Optional[str] = None
        volume: Optional[str] = None
        issue: Optional[str] = None
        pages_range: Optional[str] = None
        abstract_text: Optional[str] = None

        if first_text:
            jrnl_m = re.search(
                r"(?i)((?:journal|letters|review|advanced|nature|science|ACS|RSC|wiley|elsevier)[^\n]{0,80})",
                first_text,
            )
            if jrnl_m:
                journal = jrnl_m.group(1).strip()

            vi_m = re.search(
                r"(?i)vol(?:ume)?\.?\s*(\d+)[,\s]+(?:no|issue|iss)\.?\s*(\d+)",
                first_text,
            )
            if vi_m:
                volume = vi_m.group(1)
                issue = vi_m.group(2)

            pg_m = re.search(r"(?i)pp?\.?\s*(\d+\s*[-–]\s*\d+)", first_text)
            if pg_m:
                pages_range = pg_m.group(1).replace(" ", "")

            abs_m = re.search(
                r"(?i)abstract\s*\n([\s\S]{50,1500}?)(?:\n(?:introduction|keywords|1\.|©))",
                first_text,
            )
            if abs_m:
                abstract_text = " ".join(abs_m.group(1).split())

        result = {
            "publication_year": publication_year,
            "paper_title": paper_title,
            "authors": authors,
            "institutions": [],
            "doi": doi,
            "journal": journal,
            "volume": volume,
            "issue": issue,
            "pages_range": pages_range,
            "abstract_text": abstract_text,
            "keywords": keywords,
        }
        logger.debug(f"Pub metadata for {filename}: year={publication_year}, title={paper_title!r}, doi={doi!r}, authors={authors}")
        return result

    def process_pdf(self, pdf_path: str) -> int:
        """
        Open PDF and process all pages in parallel.
        Returns number of pages that added/updated ≥1 term or property.
        Updates metadata['processed_pages_total'] and 'processed_pages_with_terms'.
        """
        try:
            doc = fitz.open(pdf_path)
        except Exception as e:
            logger.error(f"Cannot open PDF {pdf_path}: {e}")
            return 0

        total_pages = doc.page_count
        self.metadata["processed_pages_total"] += total_pages
        pages_with_terms = 0

        pub_meta = self._extract_pub_metadata(doc, pdf_path)
        pub_year = pub_meta.get("publication_year")
        if pub_year:
            logger.debug(f"Publication year for {os.path.basename(pdf_path)}: {pub_year}")

        logger.debug(f"Processing '{pdf_path}' ({total_pages} pages) with {self.max_workers} workers")
        with ThreadPoolExecutor(max_workers=self.max_workers) as exe:
            futures = {
                exe.submit(self.process_page, doc, pdf_path, i, pub_year, pub_meta): i for i in range(total_pages)
            }
            for fut in as_completed(futures):
                page_i = futures[fut]
                try:
                    result = fut.result()
                    if result:
                        pages_with_terms += 1
                        logger.debug(
                            f"Page {page_i+1}/{total_pages} of {os.path.basename(pdf_path)} yielded terms or properties"
                        )
                    else:
                        logger.debug(
                            f"Page {page_i+1}/{total_pages} of {os.path.basename(pdf_path)} had no new terms or properties"
                        )
                except Exception as e:
                    logger.error(
                        f"Error processing page {page_i+1} of {os.path.basename(pdf_path)}: {e}"
                    )

        self.metadata["processed_files"] += 1
        self.metadata["processed_pages_with_terms"] += pages_with_terms

        # --- Post-process: propagate best pub metadata across all terms from this PDF ---
        # Threads may have enriched metadata on some pages but not others.
        # Gather the richest metadata across all terms from this file, then backfill.
        filename = os.path.basename(pdf_path)
        best_meta: Dict[str, Any] = dict(pub_meta or {})
        scalar_fields = ("paper_title", "doi", "journal", "volume", "issue",
                         "pages_range", "abstract_text", "publication_year")
        list_fields = ("authors", "institutions", "keywords")

        # First pass: collect best metadata from any term belonging to this PDF
        for entry in self.terms_dict.values():
            if filename not in (entry.get("source_papers") or []):
                continue
            for f in scalar_fields:
                if entry.get(f) and not best_meta.get(f):
                    best_meta[f] = entry[f]
            for f in list_fields:
                if entry.get(f) and not best_meta.get(f):
                    best_meta[f] = entry[f]

        # Second pass: stamp best metadata onto all terms from this PDF
        backfilled = 0
        for entry in self.terms_dict.values():
            if filename not in (entry.get("source_papers") or []):
                continue
            for f in scalar_fields:
                if best_meta.get(f) and not entry.get(f):
                    entry[f] = best_meta[f]
                    backfilled += 1
            for f in list_fields:
                if best_meta.get(f) and not entry.get(f):
                    entry[f] = best_meta[f]
                    backfilled += 1

        # Also backfill code_snippets from this PDF
        for snip in self.code_snippets:
            if snip.get("source_paper") != filename:
                continue
            for f in scalar_fields:
                if best_meta.get(f) and not snip.get(f):
                    snip[f] = best_meta[f]
            if best_meta.get("authors") and not snip.get("paper_authors"):
                snip["paper_authors"] = best_meta["authors"]

        if backfilled:
            logger.debug(f"Backfilled {backfilled} metadata fields across terms from {filename}")

        logger.info(
            f"Finished '{filename}': {pages_with_terms}/{total_pages} pages yielded terms or properties"
        )
        return pages_with_terms

    def process_directory(self) -> Dict[str, Any]:
        """
        Walk data_dir for all PDFs, process them, then compute final importance metrics.
        Saves output JSON one final time.
        """
        if not os.path.isdir(self.data_dir):
            msg = f"Directory not found: {self.data_dir}"
            logger.error(msg)
            return {"status": "error", "message": msg}

        pdfs = [f for f in os.listdir(self.data_dir) if f.lower().endswith(".pdf")]
        if not pdfs:
            logger.warning(f"No PDFs in {self.data_dir}")

        total_files = 0
        for idx, fname in enumerate(pdfs, start=1):
            fullpath = os.path.join(self.data_dir, fname)
            logger.info(f"[{idx}/{len(pdfs)}] Processing file: {fname}")
            self.process_pdf(fullpath)
            total_files += 1

        # Assign importance
        for term_data in self.terms_dict.values():
            occ = len(term_data.get("pages", []))
            papers = len(set(term_data.get("source_papers", [])))
            if papers > 1 or occ > 5:
                term_data["importance"] = "high"
            elif occ > 2:
                term_data["importance"] = "medium"
            else:
                term_data["importance"] = "low"

        # Final save
        self._save_terms_threadsafe()
        logger.info(
            f"Done! Files processed: {total_files}, Pages total: {self.metadata['processed_pages_total']}, "
            f"Pages w/ terms: {self.metadata['processed_pages_with_terms']}, Unique terms: {len(self.terms_dict)}"
        )
        return {
            "status": "success",
            "processed_files": total_files,
            "processed_pages_total": self.metadata["processed_pages_total"],
            "processed_pages_with_terms": self.metadata["processed_pages_with_terms"],
            "unique_terms": len(self.terms_dict),
            "output_file": self.output_file,
        }

    def save_terms(self) -> None:
        """Legacy method; not used. Thread-safe saving is done via `_save_terms_threadsafe`."""
        raise NotImplementedError("Use `_save_terms_threadsafe` instead.")

# ----------------------------------------
# Script entrypoint
# ----------------------------------------


def run_extraction(
    pdf_dir: Path,
    output_json: Path,
    *,
    model: str,
    backend: str = "ollama",
    ollama_url: str = "http://localhost:11434",
    cborg_base: Optional[str] = None,
    cborg_api_key: Optional[str] = None,
    schema_path: Path | str = "storage/schema/matkg_schema.yaml",
    chebi_obo_path: Path | str | None = None,
    temperature: float = 0.0,
    context_length: int = 50,
    max_workers: int = 4,
) -> dict:
    """
    Run term extraction on a folder of PDFs and write the results JSON.

    Args:
        pdf_dir: Directory containing input PDFs.
        output_json: Path where extracted terms JSON will be written.
        model: Backend model name.
        backend: LLM backend ("cborg", "cborg-openai", or "ollama").
        ollama_url: Base URL for Ollama server.
        cborg_base: Base URL for CBORG/OpenAI-compatible backend.
        cborg_api_key: API key for CBORG/OpenAI-compatible backend.
        schema_path: LinkML schema path used for validation.
        chebi_obo_path: Optional ChEBI OBO path for chemical enrichment.
        temperature: Sampling temperature for the LLM.
        context_length: Max tokens to keep in context snippets.
        max_workers: Page-level parallelism.

    Returns:
        Result dict returned by `process_directory()` including counts and output path.

    Raises:
        FileNotFoundError: if `pdf_dir` does not exist or is not a directory.
        RuntimeError: if the extractor reports an error status.
    """
    pdf_dir = Path(pdf_dir)
    output_json = Path(output_json)

    if not pdf_dir.is_dir():
        raise FileNotFoundError(f"PDF directory not found: {pdf_dir}")

    # Ensure output directory exists
    output_json.parent.mkdir(parents=True, exist_ok=True)

    chat = make_chat_client(
        backend=backend,
        model=model,
        ollama_url=ollama_url,
        cborg_base=cborg_base or os.environ.get("CBORG_BASE_URL") or "https://api.cborg.lbl.gov",
        cborg_api_key=cborg_api_key or os.environ.get("CBORG_API_KEY"),
    )

    extractor = LLMTermExtractor(
        model_name=model,
        ollama_base_url=ollama_url,
        temperature=temperature,
        data_dir=str(pdf_dir),
        output_file=str(output_json),
        context_length=context_length,
        schema_path=str(schema_path),
        max_workers=max_workers,
        chat_client=chat,
        chebi_obo_path=str(chebi_obo_path) if chebi_obo_path else None,
    )

    result = extractor.process_directory()
    if result.get("status") == "error":
        raise RuntimeError(result.get("message", "Unknown extraction error"))
    return result


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the term extraction script."""
    parser = argparse.ArgumentParser(
        description="Extract schema-aligned terms from PDFs into extracted_terms JSON."
    )
    parser.add_argument(
        "--pdf-dir",
        type=Path,
        default=Path("polymer_papers"),
        help="Directory containing PDFs to process.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("storage/terminology/extracted_terms.json"),
        help="Output extracted terms JSON path.",
    )
    parser.add_argument(
        "--backend",
        choices=["cborg", "cborg-openai", "ollama"],
        default=os.environ.get("EXTRACT_TERMS_BACKEND", "cborg"),
        help="LLM backend to use.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("EXTRACT_TERMS_MODEL", "lbl/cborg-chat"),
        help="Backend model name.",
    )
    parser.add_argument(
        "--ollama-url",
        "--ollama-base-url",
        default=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        help="Ollama base URL, used only with --backend ollama.",
    )
    parser.add_argument(
        "--cborg-base",
        default=os.environ.get("CBORG_BASE_URL", "https://api.cborg.lbl.gov"),
        help="CBORG/OpenAI-compatible base URL.",
    )
    parser.add_argument(
        "--cborg-api-key",
        default=os.environ.get("CBORG_API_KEY"),
        help="CBORG API key. Defaults to CBORG_API_KEY from environment.",
    )
    parser.add_argument(
        "--schema-path",
        type=Path,
        default=Path("storage/schema/matkg_schema.yaml"),
        help="LinkML schema path.",
    )
    parser.add_argument(
        "--chebi-obo",
        type=Path,
        default=os.environ.get("CHEBI_OBO_PATH"),
        help="Optional ChEBI OBO path. Defaults to CHEBI_OBO_PATH or storage/ontologies/chebi.obo.",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="LLM sampling temperature.",
    )
    parser.add_argument(
        "--context-length",
        type=int,
        default=50,
        help="Max tokens/chunks to keep in context snippets.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="Page-level worker count.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    result = run_extraction(
        args.pdf_dir,
        args.output,
        model=args.model,
        backend=args.backend,
        ollama_url=args.ollama_url,
        cborg_base=args.cborg_base,
        cborg_api_key=args.cborg_api_key,
        schema_path=args.schema_path,
        chebi_obo_path=args.chebi_obo,
        temperature=args.temperature,
        context_length=args.context_length,
        max_workers=args.max_workers,
    )
    if result.get("status") == "error":
        print(f"✗ Error: {result.get('message')}")
        print(f"  Expected directory: {args.pdf_dir}")
        print(f"  Current working directory: {os.getcwd()}")
        print(f"  Available directories: {[d for d in os.listdir('.') if os.path.isdir(d)]}")
    else:
        print(
            f"✓ Processed {result['processed_files']} files, "
            f"{result['processed_pages_total']} total pages, "
            f"{result['processed_pages_with_terms']} pages with terms, "
            f"{result['unique_terms']} unique terms → {result['output_file']}"
        )
