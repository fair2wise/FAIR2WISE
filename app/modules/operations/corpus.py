"""Corpus audit and Work identity as Knowledge Operations (task T19C, inventory OP02; H3 K1-K3).

Two parts:

**Work identity (K1, method** :data:`WORK_IDENTITY_METHOD` **).** Tool output counts
*distinct Works*, not PDF filename keys. A *paper key* is the basename a record cites
(``source_papers`` entry, ``source_metadata`` key or KG ``source_papers``/``papers``
value). Keys are grouped into Works with the rule of
``scripts/harvest_rsoxs.py:works_same_identity`` (the H3 K1 reference), applied as a
transitive closure (union-find):

0. equal content hash (sha256 of the PDF bytes, when the metadata carries one) -> same
   Work (``work-identity/2``, owner decision D1.9 / O-S3, 2026-10-30: byte-identical
   files count once even when renamed and otherwise unidentified);
1. equal DOI -> same Work; two *different* non-preprint DOIs -> never the same Work;
2. equal arXiv id (version suffix stripped) -> same Work;
3. otherwise same Work only when the titles are near-duplicates (token Jaccard >= 0.82
   after a small stopword list) and the first-author surnames do not disagree.

Identifiers come from bibliographic metadata when present (``source_metadata`` ``doi``,
``paper_title``, ``authors``; the content hash from ``sha256``, ``content_sha256``,
``source_sha256`` or a ``source_ref`` of the form ``sha256:<hex>``) and otherwise from the
key itself: F2W DOI stems (``10.1021_acs.macromol.0c01782.pdf``; the first ``_`` after
the registrant is the ``/``) and arXiv ids (``2306.07295v1.pdf``). A key with no content
hash, DOI, arXiv id or title is its own Work and is counted under
``works_identified_by_key_only``. Candidate title pairs come from an exact prefix-filter
join, so the rule is applied to every qualifying pair without an all-pairs scan.
``WorkIndex.build(..., use_content_hash=False)`` reproduces ``work-identity/1`` for parity
tests; on a population without hashes the two methods agree.

**Corpus audit (OP02,** ``curate_audit_corpus`` **).** Runs the ``scripts/corpus_qa.py``
sections over one extracted-terms file, read-only:

=========================  ==================================================================
§1 duplicate_works         legacy filename-key figures **plus** distinct Works (K1); the
                           arXiv x journal near-duplicate title check runs inline only up to
                           :data:`NEAR_DUPLICATE_INLINE_LIMIT` paper keys (owner decision
                           D1.10, O-S4) and otherwise inside a ``knowledge_operation`` job
                           (``ScanControl(job=True)``); an inline audit of a larger corpus
                           reports ``arxiv_journal_near_dupes: null`` with a warning
§2 stubs                   unchanged (term entries)
§4 numeric_values          unchanged (per-term relation dicts)
§6 aliases                 unchanged; the under-merge figure compares *adjacent* sorted
                           labels only (a heuristic sample, not all pairs)
§7 relations               unchanged (per-term relation dicts, not deduplicated edges)
§8 summary                 legacy figures, the legacy per-year histogram under its real unit
                           (one increment per (term, source_metadata entry)), **plus** the
                           versioned distinct-Work histogram ``papers_per_year/2`` (K2)
=========================  ==================================================================

§3 (technique-as-measurement) and §5 (zero-measurement papers) are **excluded** from the
Tool output (H3 K3, D20: no measurement/technique split; their "reclassification" and
"zero-measurement" figures are not defects). The legacy CLI is unchanged and still
prints them. The per-section legacy figures are computed by the same definitions as
``corpus_qa.py`` (``tests/operations/test_corpus_audit.py`` checks parity against the
script), so a Tool total and a CLI total with the same name and unit agree.

The audit never writes: no repair, merge, reclassification or file update follows from
it; repairs remain the separately approved ``scripts/repair_corpus.py`` path. Outputs
carry counts, term labels and paper keys only: no definitions, snippets, abstracts or
paths (D19). No model call.

Corpus selection: the Tool takes a corpus *name*; an injected :data:`CorpusResolver`
(trusted wiring) maps it to a terms file. The model never supplies a path.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import logging
import math
from pathlib import Path, PurePosixPath
import re
from typing import Any
import unicodedata
import uuid

from ..agent_runtime.executor import is_control_flow
from ..agent_runtime.skills import Skill
from ..contracts import ContractError, CostClass, Effect, ErrorCode, RequestContext, ToolResult, ToolSpec
from .reports import (
    Coverage,
    OperationReport,
    ScanControl,
    error_result,
    report_to_result,
    stamp,
    store_report_artifact,
    top_items,
    utcnow,
)

logger = logging.getLogger(__name__)

# =====================================================================================
# Work identity (H3 K1)
# =====================================================================================

WORK_IDENTITY_METHOD = "work-identity/2"
#: The previous method (no content-hash step); ``WorkIndex.build(use_content_hash=False)``.
WORK_IDENTITY_METHOD_V1 = "work-identity/1"
_SHA256_HEX = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$", re.IGNORECASE)
TITLE_THRESHOLD = 0.82
_STOPWORDS = frozenset({"a", "an", "the", "and", "for", "of", "in", "on", "with", "by"})
_F2W_DOI_STEM = re.compile(r"^(10\.\d{4,9})_(.+)$")
_ARXIV_NEW = re.compile(r"^(?:arxiv[_:.-]?)?(\d{4}\.\d{4,5})(?:v\d+)?$", re.IGNORECASE)
_ARXIV_OLD = re.compile(r"^([a-z][a-z.-]*)[_/](\d{7})(?:v\d+)?$", re.IGNORECASE)
_ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.IGNORECASE)


def content_hash_key(value: Any) -> str:
    """Lowercased 64-hex sha256 from ``<hex>`` or ``sha256:<hex>``; ``""`` for anything else."""
    match = _SHA256_HEX.match(str(value or "").strip())
    return match.group(1).lower() if match else ""


def paper_key(value: Any) -> str:
    """The basename a record cites (``a/b/x.pdf`` -> ``x.pdf``); ``""`` for empty values."""
    text = str(value or "").strip().replace("\\", "/")
    return PurePosixPath(text).name if text else ""


def doi_key(value: Any) -> str:
    """Lowercased bare DOI (``doi.org`` URL / ``doi:`` prefix removed), or ``""``."""
    text = str(value or "").strip()
    text = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^doi:\s*", "", text, flags=re.IGNORECASE).strip("/ ").lower()
    return text if text.startswith("10.") and "/" in text else ""


def arxiv_key(value: Any) -> str:
    """Lowercased arXiv id with the version suffix stripped (``harvest_rsoxs._arxiv_key``)."""
    if not value:
        return ""
    return re.sub(r"v\d+$", "", str(value).strip().lower())


def identifiers_from_key(key: str) -> tuple[str, str]:
    """``(doi, arxiv_id)`` implied by a paper key (F2W DOI stem or arXiv filename)."""
    stem = key[:-4] if key.lower().endswith(".pdf") else key
    match = _F2W_DOI_STEM.match(stem)
    if match:
        doi = doi_key(f"{match.group(1)}/{match.group(2)}")
        arx = _ARXIV_DOI.match(doi)
        return doi, arxiv_key(arx.group(1)) if arx else ""
    match = _ARXIV_NEW.match(stem)
    if match:
        return "", arxiv_key(match.group(1))
    match = _ARXIV_OLD.match(stem)
    if match:
        return "", arxiv_key(f"{match.group(1)}/{match.group(2)}")
    return "", ""


def _normalize_title(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def title_tokens(title: str) -> set[str]:
    """Normalized title tokens minus the stopword list (``harvest_rsoxs._title_tokens``)."""
    return set(_normalize_title(title).split()) - _STOPWORDS


def titles_near_duplicate(a: str, b: str, threshold: float = TITLE_THRESHOLD) -> bool:
    """Token Jaccard similarity >= ``threshold`` (``harvest_rsoxs.titles_near_duplicate``)."""
    ta, tb = title_tokens(a), title_tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold


def first_author_key(authors: Any) -> str:
    """Normalized surname of the first listed author (``harvest_rsoxs._first_author_key``)."""
    if isinstance(authors, list) and authors:
        first = authors[0]
        name = str(first.get("name") or "") if isinstance(first, Mapping) else str(first or "")
    elif isinstance(authors, list):
        name = ""
    else:
        name = str(authors or "")
    if not name.strip():
        return ""
    surname = name.split(",")[0] if "," in name else name.split()[-1]
    return _normalize_title(surname)


@dataclass(frozen=True)
class PaperRecord:
    """Identity evidence for one paper key."""

    key: str
    doi: str = ""
    arxiv_id: str = ""
    title: str = ""
    first_author: str = ""
    years: tuple[int, ...] = ()
    content_sha256: str = ""

    @property
    def is_preprint(self) -> bool:
        """``harvest_rsoxs._is_preprint``: the DOI looks like an arXiv identifier."""
        return "arxiv" in self.doi or "10.48550" in self.doi

    @property
    def identified(self) -> bool:
        """Has a content hash, DOI, arXiv id or title (otherwise the key alone identifies it)."""
        return bool(self.content_sha256 or self.doi or self.arxiv_id or title_tokens(self.title))


def same_work(left: PaperRecord, right: PaperRecord, *, use_content_hash: bool = True) -> bool:
    """Return whether two records are the same work.

    Pairwise rule of ``harvest_rsoxs.works_same_identity`` (without stored ``versions``),
    preceded by the content-hash step of ``work-identity/2`` unless ``use_content_hash`` is off.
    """
    if left.key == right.key:
        return True
    if use_content_hash and left.content_sha256 and left.content_sha256 == right.content_sha256:
        return True
    if left.doi and right.doi:
        if left.doi == right.doi:
            return True
        if not (left.is_preprint or right.is_preprint):
            return False
    if left.arxiv_id and right.arxiv_id and left.arxiv_id == right.arxiv_id:
        return True
    if not titles_near_duplicate(left.title, right.title):
        return False
    if left.first_author and right.first_author and left.first_author != right.first_author:
        return False
    return True


def _valid_year(value: Any) -> int | None:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1800 <= year <= 2100 else None


def record_for(key: str, metadata: Iterable[Mapping[str, Any]] = ()) -> PaperRecord:
    """Merge every metadata mapping seen for ``key`` (first non-empty value wins; all years kept).

    ``key`` is reduced to its basename first, so ``dir/x.pdf`` and ``x.pdf`` are one key.
    """
    key = paper_key(key)
    doi, arxiv = identifiers_from_key(key)
    title = author = digest = ""
    years: list[int] = []
    for meta in metadata:
        if not isinstance(meta, Mapping):
            continue
        digest = digest or next((h for h in (content_hash_key(meta.get(f)) for f in
                                             ("sha256", "content_sha256", "source_sha256", "source_ref")) if h), "")
        mdoi = doi_key(meta.get("doi"))
        if mdoi and not doi:
            doi = mdoi
            arx = _ARXIV_DOI.match(mdoi)
            arxiv = arxiv or (arxiv_key(arx.group(1)) if arx else "")
        arxiv = arxiv or arxiv_key(meta.get("arxiv_id"))
        title = title or str(meta.get("paper_title") or meta.get("title") or "")
        author = author or first_author_key(meta.get("authors") or meta.get("paper_authors"))
        year = _valid_year(meta.get("publication_year"))
        if year is not None:
            years.append(year)
    return PaperRecord(key=key, doi=doi, arxiv_id=arxiv, title=title, first_author=author, years=tuple(years),
                       content_sha256=digest)


class _UnionFind:
    def __init__(self, items: Iterable[str]) -> None:
        self.parent = {i: i for i in items}

    def find(self, item: str) -> str:
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if rb < ra:
                ra, rb = rb, ra
            self.parent[rb] = ra


@dataclass
class WorkIndex:
    """Paper keys grouped into distinct Works (method :data:`WORK_IDENTITY_METHOD`).

    Attributes:
        records: ``key -> PaperRecord``.
        work_of: ``key -> work_id``.
        members: ``work_id -> sorted keys``.
    """

    records: dict[str, PaperRecord]
    work_of: dict[str, str] = field(default_factory=dict)
    members: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def build(cls, records: Iterable[PaperRecord], *, threshold: float = TITLE_THRESHOLD,
              use_content_hash: bool = True) -> WorkIndex:
        """Group ``records`` (one per key; later duplicates of a key are ignored).

        ``use_content_hash=False`` applies ``work-identity/1`` (no step 0) for parity tests.
        """
        recs: dict[str, PaperRecord] = {}
        for rec in records:
            if rec.key and rec.key not in recs:
                recs[rec.key] = rec
        uf = _UnionFind(recs)
        by_hash: dict[str, str] = {}
        by_doi: dict[str, str] = {}
        by_arxiv: dict[str, str] = {}
        for key, rec in recs.items():
            idents: tuple[tuple[dict[str, str], str], ...] = ((by_doi, rec.doi), (by_arxiv, rec.arxiv_id))
            if use_content_hash:
                idents = ((by_hash, rec.content_sha256),) + idents
            for index, ident in idents:
                if ident:
                    if ident in index:
                        uf.union(index[ident], key)
                    else:
                        index[ident] = key
        for a, b in _title_candidates(recs, threshold):
            if uf.find(a) != uf.find(b) and same_work(recs[a], recs[b], use_content_hash=use_content_hash):
                uf.union(a, b)
        groups: dict[str, list[str]] = defaultdict(list)
        for key in recs:
            groups[uf.find(key)].append(key)
        built = cls(records=recs)
        for keys in groups.values():
            keys.sort()
            work_id = _work_id([recs[k] for k in keys], use_content_hash=use_content_hash)
            built.members[work_id] = keys
            for key in keys:
                built.work_of[key] = work_id
        return built

    def __len__(self) -> int:
        return len(self.members)

    def year_of(self, work_id: str) -> tuple[int | None, bool]:
        """``(year, conflicting)``: the most frequent recorded year (ties: earliest) over the Work's keys."""
        counts: Counter[int] = Counter()
        for key in self.members.get(work_id, ()):
            counts.update(self.records[key].years)
        if not counts:
            return None, False
        best = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        return best, len(counts) > 1

    def key_only_works(self) -> int:
        """Works none of whose keys carries a DOI, arXiv id or title."""
        return sum(1 for keys in self.members.values() if not any(self.records[k].identified for k in keys))

    def multi_version_works(self) -> int:
        """Works grouping more than one paper key (for example an arXiv preprint and its journal version)."""
        return sum(1 for keys in self.members.values() if len(keys) > 1)

    def resolve(self, ref: str) -> str | None:
        """The Work a caller's reference names: a paper key, DOI, arXiv id or work id; ``None`` if unknown."""
        ref = (ref or "").strip()
        if not ref:
            return None
        if ref in self.members:
            return ref
        key = paper_key(ref)
        if key in self.work_of:
            return self.work_of[key]
        doi, arx = doi_key(ref), arxiv_key(ref.split(":", 1)[1] if ref.lower().startswith("arxiv:") else ref)
        for k, rec in self.records.items():
            if (doi and rec.doi == doi) or (arx and rec.arxiv_id == arx):
                return self.work_of[k]
        return None


def _work_id(records: Sequence[PaperRecord], *, use_content_hash: bool = True) -> str:
    journal = sorted(r.doi for r in records if r.doi and not r.is_preprint)
    if journal:
        return f"doi:{journal[0]}"
    arxiv = sorted(r.arxiv_id for r in records if r.arxiv_id)
    if arxiv:
        return f"arxiv:{arxiv[0]}"
    dois = sorted(r.doi for r in records if r.doi)
    if dois:
        return f"doi:{dois[0]}"
    hashes = sorted(r.content_sha256 for r in records if r.content_sha256) if use_content_hash else []
    if hashes and not any(title_tokens(r.title) for r in records):
        return f"sha256:{hashes[0]}"
    return f"key:{sorted(r.key for r in records)[0]}"


def _title_candidates(recs: Mapping[str, PaperRecord], threshold: float) -> Iterable[tuple[str, str]]:
    """Pairs of keys whose title token sets *may* reach Jaccard ``threshold`` (exact prefix filter)."""
    tokens = {k: title_tokens(r.title) for k, r in recs.items()}
    freq: Counter[str] = Counter(t for toks in tokens.values() for t in toks)
    index: dict[str, list[str]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for key in sorted(tokens, key=lambda k: (len(tokens[k]), k)):
        toks = sorted(tokens[key], key=lambda t: (freq[t], t))
        if not toks:
            continue
        prefix = len(toks) - math.ceil(threshold * len(toks)) + 1
        for tok in toks[:prefix]:
            for other in index[tok]:
                pair = (other, key) if other < key else (key, other)
                if pair not in seen:
                    seen.add(pair)
                    yield pair
            index[tok].append(key)


# =====================================================================================
# corpus_qa sections (same definitions as scripts/corpus_qa.py)
# =====================================================================================

RSOXS_TECHNIQUE_KEYWORDS = ("rsoxs", "r-soxs", "rsxs", "p-rsoxs", "giwaxs", "gisaxs", "nexafs",
                            "saxs", "waxs", "xas", "exafs")
STUB_CATEGORIES = frozenset({"Thing", "owl:Thing", "Entity", "entity", "NONE", None})
ENERGY_MIN_EV = 100.0
ENERGY_MAX_EV = 2000.0
ENERGY_RELATION_SLOTS = frozenset({"photon_energy_eV", "energy_eV", "energy", "photon_energy",
                                   "photon_energy_range"})
KNOWN_BAD_PATTERNS = (
    {"description": "P3HT processed_by P-RSoXS (technique, not process)", "subject_contains": "p3ht",
     "relation": "processed_by", "object_contains": "p-rsoxs"},
    {"description": "CyRSoXS measures P-RSoXS (backward — tool measures technique?)", "subject_contains": "cyrso",
     "relation": "measures", "object_contains": "p-rsoxs"},
)
#: Example items kept per list (the legacy CLI prints 10-20).
EXAMPLES = 10
_LABEL_CHARS = 120


def _normalise(s: str) -> str:
    s = unicodedata.normalize("NFC", s).lower().strip()
    return re.sub(r"\s+", " ", s)


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb)))
        prev = curr
    return prev[-1]


def _title_overlap(a: str, b: str) -> float:
    wa, wb = set(_normalise(a).split()), set(_normalise(b).split())
    return _set_overlap(wa, wb)


def _set_overlap(wa: set[str], wb: set[str]) -> float:
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _bag_distance(ca: Counter[str], cb: Counter[str]) -> int:
    """Character-multiset distance: a lower bound of the Levenshtein distance (one edit moves it by <= 1)."""
    return max(sum((ca - cb).values()), sum((cb - ca).values()))


def _levenshtein_within(a: str, b: str, limit: int) -> int | None:
    """``_levenshtein(a, b)`` when it is at most ``limit``, else ``None`` (banded DP with early exit).

    Cells farther than ``limit`` from the diagonal cannot be within ``limit``, and every alignment
    passes through each row, so a row whose minimum exceeds ``limit`` ends the computation.
    """
    m, n = len(a), len(b)
    if abs(m - n) > limit:
        return None
    cap = limit + 1
    prev = [j if j <= limit else cap for j in range(n + 1)]
    for i in range(1, m + 1):
        cur = [cap] * (n + 1)
        cur[0] = i if i <= limit else cap
        row_min = cur[0]
        ca = a[i - 1]
        for j in range(max(1, i - limit), min(n, i + limit) + 1):
            v = prev[j - 1] + (ca != b[j - 1])
            if prev[j] + 1 < v:
                v = prev[j] + 1
            if cur[j - 1] + 1 < v:
                v = cur[j - 1] + 1
            cur[j] = v if v < cap else cap
            if v < row_min:
                row_min = v
        if row_min > limit:
            return None
        prev = cur
    return prev[n] if prev[n] <= limit else None


#: Paper keys above which the §1 near-duplicate title check is deferred to a job (D1.10, O-S4).
NEAR_DUPLICATE_INLINE_LIMIT = 2_000
NEAR_DUPLICATE_DEFERRED = ("duplicate_works.legacy.arxiv_journal_near_dupes not computed inline: the corpus has "
                           "{keys} paper keys (inline limit {limit}); submit curate_audit_corpus as a "
                           "knowledge_operation job for the near-duplicate title check")
#: corpus_qa §1: a title pair is near-duplicate when its 100-character prefixes are fewer than 10 edits apart.
_NEAR_LEV = 10
_NEAR_PREFIX = 100


def _near_duplicate_pairs(arxiv: Mapping[str, Any], journal: Mapping[str, Any]) -> list[tuple[str, str, float]]:
    """The corpus_qa §1 arXiv x journal near-duplicate list, in the same order and with the same scores.

    Same rule as ``corpus_qa.audit_duplicate_works`` (word Jaccard >= 0.80, else both titles longer
    than 30 characters and Levenshtein(prefix 100) < 10), but the word sets are computed once per
    title and the edit distance runs only for pairs that pass two exact lower bounds (length
    difference and character-bag distance below 10). The result is identical; T19D measures
    the pair loop at corpus scale (the plain loop costs about 0.5 ms per pair). The edit distance
    itself is banded (:func:`_levenshtein_within`): only distances below 10 matter.
    """
    words: dict[int, set[str]] = {}
    bags: dict[int, Counter[str]] = {}

    def word_set(title: Any) -> set[str]:
        key = id(title)
        if key not in words:
            words[key] = set(_normalise(title).split())
        return words[key]

    def bag(prefix: str) -> Counter[str]:
        key = id(prefix)
        if key not in bags:
            bags[key] = Counter(prefix)
        return bags[key]

    prefixes: dict[int, str] = {}

    def prefix_of(title: str) -> str:
        key = id(title)
        if key not in prefixes:
            prefixes[key] = title[:_NEAR_PREFIX]
        return prefixes[key]

    near: list[tuple[str, str, float]] = []
    for adoi, atitle in arxiv.items():
        if not atitle:
            continue
        for jdoi, jtitle in journal.items():
            if not jtitle:
                continue
            overlap = _set_overlap(word_set(atitle), word_set(jtitle))
            if overlap >= 0.80:
                near.append((adoi, jdoi, overlap))
            elif len(atitle) > 30 and len(jtitle) > 30:
                pa, pj = prefix_of(atitle), prefix_of(jtitle)
                if abs(len(pa) - len(pj)) >= _NEAR_LEV or _bag_distance(bag(pa), bag(pj)) >= _NEAR_LEV:
                    continue  # both are lower bounds of the edit distance: lev >= 10 for certain
                lev = _levenshtein_within(pa, pj, _NEAR_LEV - 1)
                if lev is not None:
                    near.append((adoi, jdoi, 1.0 - lev / 100))
    return near


def _doi_from_filename(filename: str) -> str:
    return filename.removesuffix(".pdf")


def _is_arxiv(key: str) -> bool:
    return "arxiv" in key.lower() or key.startswith("10.48550")


def _try_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.match(r"^\s*([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)", value)
        if m:
            return float(m.group(1))
    return None


def _label(value: Any) -> str:
    text = str(value if value is not None else "")
    return text if len(text) <= _LABEL_CHARS else text[:_LABEL_CHARS - 1] + "…"


def _rels(t: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [r for r in (t.get("relations") or []) if isinstance(r, dict)]


def section_duplicate_works(terms: Sequence[Mapping[str, Any]], works: WorkIndex, *,
                            near_duplicates: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    """§1: legacy filename-key figures (corpus_qa ``audit_duplicate_works``) plus distinct Works.

    ``near_duplicates=False`` skips the arXiv x journal title check (D1.10: deferred to a job);
    its count is then ``None`` and its preview list absent.
    """
    doi_to_terms: dict[str, int] = defaultdict(int)
    doi_to_title: dict[str, str] = {}
    internal: list[tuple[str, str]] = []
    work_terms: dict[str, int] = defaultdict(int)
    for t in terms:
        seen: set[str] = set()
        seen_works: set[str] = set()
        for fn in t.get("source_papers") or []:
            doi = _doi_from_filename(str(fn))
            doi_to_terms[doi] += 1
            if doi in seen:
                internal.append((str(t.get("term", "")), doi))
            seen.add(doi)
            wid = works.work_of.get(paper_key(fn))
            if wid and wid not in seen_works:
                seen_works.add(wid)
                work_terms[wid] += 1
        for fn, meta in (t.get("source_metadata") or {}).items():
            doi = _doi_from_filename(str(fn))
            if isinstance(meta, dict) and doi not in doi_to_title:
                doi_to_title[doi] = meta.get("paper_title") or ""
    arxiv = {d: ti for d, ti in doi_to_title.items() if _is_arxiv(d)}
    journal = {d: ti for d, ti in doi_to_title.items() if not _is_arxiv(d)}
    near = _near_duplicate_pairs(arxiv, journal) if near_duplicates else None
    metrics = {
        "legacy": {"unique_doi_keys": len(doi_to_terms), "internal_dup_count": len(internal),
                   "arxiv_papers": len(arxiv), "journal_papers": len(journal),
                   "arxiv_journal_near_dupes": len(near) if near is not None else None},
        "paper_keys": len(works.records),
        "distinct_works": len(works),
        "works_with_multiple_keys": works.multi_version_works(),
        "works_identified_by_key_only": works.key_only_works(),
    }
    preview = {
        "top_cited_works": [[w, n] for w, n in top_items(work_terms, EXAMPLES)],
        "internal_duplicates": [[_label(tn), d] for tn, d in internal[:EXAMPLES]],
        "multi_key_works": [[w, keys] for w, keys in sorted(works.members.items()) if len(keys) > 1][:EXAMPLES],
    }
    if near is not None:
        preview["near_duplicate_key_pairs"] = [[a, j, round(s, 2)] for a, j, s in sorted(near, key=lambda x: -x[2])[:EXAMPLES]]
    return metrics, preview


def section_stubs(terms: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """§2 (corpus_qa ``audit_stubs``): term entries with stub signals."""
    stubs: list[tuple[str, list[str]]] = []
    for t in terms:
        reasons: list[str] = []
        cat = t.get("category")
        if not (t.get("source_papers") or []):
            reasons.append("no_source_papers")
        if not (t.get("context_snippets") or []):
            reasons.append("no_snippets")
        if not (t.get("pages") or []):
            reasons.append("no_pages")
        if cat in STUB_CATEGORIES:
            reasons.append(f"untyped_category={cat!r}")
        if reasons:
            stubs.append((str(t.get("term", "")), reasons))
    hard = [(n, rs) for n, rs in stubs if {"no_source_papers", "no_snippets", "no_pages"} <= set(rs)]
    untyped = [(n, rs) for n, rs in stubs if any("untyped_category" in r for r in rs)]
    metrics = {"stub_signals_total": len(stubs), "hard_stubs": len(hard), "untyped_stubs": len(untyped)}
    preview = {"hard_stubs": [_label(n) for n, _ in hard[:20]], "untyped_stubs": [_label(n) for n, _ in untyped[:20]]}
    return metrics, preview


def section_numeric_values(terms: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """§4 (corpus_qa ``audit_numeric_values``): photon-energy slots and absorption-edge heuristics."""
    suspicious: list[list[Any]] = []
    strings: list[list[Any]] = []
    for t in terms:
        name = str(t.get("term", ""))
        for r in _rels(t):
            rel = r.get("relation", "")
            if rel not in ENERGY_RELATION_SLOTS:
                continue
            raw = r.get("related_term")
            fval = _try_float(raw)
            if fval is None:
                if isinstance(raw, str) and raw.strip():
                    strings.append([_label(name), rel, _label(raw)])
            elif fval < ENERGY_MIN_EV or fval > ENERGY_MAX_EV:
                suspicious.append([_label(name), rel, fval])
    edge_re = re.compile(r"carbon\s*k|c\s*k[-\s]?edge", re.IGNORECASE)
    carbon_re = re.compile(r"[Cc](?:[^a-z]|$)")
    mismatches: list[list[Any]] = []
    for t in terms:
        formula = t.get("formula") or ""
        for r in _rels(t):
            if r.get("relation") != "absorption_edge":
                continue
            edge = str(r.get("related_term", ""))
            if edge_re.search(edge) and formula and not carbon_re.search(formula):
                mismatches.append([_label(t.get("term", "")), _label(edge), _label(formula)])
    metrics = {"out_of_range_energy_values": len(suspicious), "non_numeric_energy_strings": len(strings),
               "edge_formula_mismatches": len(mismatches)}
    preview = {"out_of_range_energy_values": suspicious[:EXAMPLES], "non_numeric_energy_strings": strings[:EXAMPLES],
               "edge_formula_mismatches": mismatches[:EXAMPLES]}
    return metrics, preview


def section_aliases(terms: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """§6 (corpus_qa ``audit_aliases``); the under-merge figure compares adjacent sorted labels only."""
    counts: list[tuple[str, int]] = []
    total = 0
    for t in terms:
        aliases = t.get("aliases") or t.get("alternate_labels") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        total += len(aliases)
        if aliases:
            counts.append((str(t.get("term", "")), len(aliases)))
    over = [(n, c) for n, c in counts if c > 10]
    norm = sorted(((str(t.get("term", "")), _normalise(str(t.get("term", "")))) for t in terms), key=lambda x: x[1])
    pairs: list[tuple[str, str]] = []
    for i in range(len(norm) - 1):
        (a_raw, a), (b_raw, b) = norm[i], norm[i + 1]
        if a == b:
            continue
        if len(a) > 5 and abs(len(a) - len(b)) <= 3 and 0 < _levenshtein(a[:60], b[:60]) <= 2:
            pairs.append((a_raw, b_raw))
    metrics = {"total_aliases": total, "terms_with_aliases": len(counts), "over_merged_candidates": len(over),
               "under_merged_candidates": len(pairs)}
    preview = {"top_alias_counts": [[_label(n), c] for n, c in sorted(counts, key=lambda x: -x[1])[:EXAMPLES]],
               "under_merge_label_pairs": [[_label(a), _label(b)] for a, b in pairs[:20]]}
    return metrics, preview


def section_relations(terms: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    """§7 (corpus_qa ``audit_relations``): per-term relation dicts (not deduplicated KG edges)."""
    total = 0
    self_refs: list[list[str]] = []
    bad: list[list[str]] = []
    for t in terms:
        name = str(t.get("term", "") or "")
        low = _normalise(name)
        for r in _rels(t):
            total += 1
            rel = r.get("relation", "")
            obj = str(r.get("related_term", "") or "")
            obj_low = _normalise(obj)
            if low and obj_low and low == obj_low:
                self_refs.append([_label(name), str(rel)])
            for pat in KNOWN_BAD_PATTERNS:
                if pat["subject_contains"] in low and rel == pat["relation"] and pat["object_contains"] in obj_low:
                    bad.append([pat["description"], _label(name), str(rel), _label(obj)])
    cross: list[list[str]] = []
    for t in terms:
        if t.get("category", "") != "ExperimentalTechnique":
            continue
        for r in _rels(t):
            if r.get("relation") in ("used_in", "measures", "processed_by"):
                obj = str(r.get("related_term", "") or "")
                if any(kw in _normalise(obj) for kw in RSOXS_TECHNIQUE_KEYWORDS):
                    cross.append([_label(t.get("term", "")), str(r["relation"]), _label(obj)])
    metrics = {"total_edges": total, "self_referential_edges": len(self_refs), "known_bad_hits": len(bad),
               "tech_cross_edges": len(cross)}
    preview = {"known_bad_hits": bad[:20], "self_referential_edges": self_refs[:20], "tech_cross_edges": cross[:5]}
    return metrics, preview


def section_summary(terms: Sequence[Mapping[str, Any]], works: WorkIndex) -> tuple[dict[str, Any], dict[str, Any]]:
    """§8: legacy figures (corpus_qa ``audit_summary_stats``) plus the distinct-Work histogram (K2)."""
    cats: Counter[str] = Counter()
    keys: set[str] = set()
    edges = 0
    legacy_years: Counter[int] = Counter()
    unparseable = 0
    for t in terms:
        cats[t.get("category") or "UNKNOWN"] += 1
        for fn in t.get("source_papers") or []:
            keys.add(_doi_from_filename(str(fn)))
        edges += len(_rels(t))
        for _fn, meta in (t.get("source_metadata") or {}).items():
            if isinstance(meta, dict) and meta.get("publication_year"):
                try:
                    legacy_years[int(meta["publication_year"])] += 1
                except (TypeError, ValueError):
                    unparseable += 1
    per_year: Counter[int] = Counter()
    unknown = conflicting = 0
    for wid in works.members:
        year, conflict = works.year_of(wid)
        conflicting += int(conflict)
        if year is None:
            unknown += 1
        else:
            per_year[year] += 1
    metrics = {
        "legacy": {"total_entities": len(terms), "total_papers": len(keys), "total_edges": edges,
                   "top_categories": dict(cats.most_common(15)),
                   "papers_per_year": {str(k): v for k, v in sorted(legacy_years.items())}},
        "distinct_works": len(works),
        "papers_per_year_v2": {str(k): v for k, v in sorted(per_year.items())},
        "works_without_year": unknown,
        "works_with_conflicting_years": conflicting,
        "legacy_unparseable_year_entries": unparseable,
        "category_count": len(cats),
    }
    return metrics, {}


CORPUS_SECTIONS = ("duplicate_works", "stubs", "numeric_values", "aliases", "relations", "summary")
EXCLUDED_SECTIONS = {
    "technique_measurement": "corpus_qa §3 excluded from Tool output (H3 K3, D20): the schema has no "
                             "measurement/technique split, so 'reclassification candidates' are not defects",
    "zero_measurement": "corpus_qa §5 excluded from Tool output (H3 K3, D20): it counts papers lacking "
                        "measurement-typed classes that are mostly not in any schema",
    "extraction_metadata": "the terms file's metadata block is not echoed (it can carry local file names/paths)",
}
SECTION_UNITS = {
    "duplicate_works.legacy": "paper filename keys (corpus_qa §1; arXiv and journal versions counted separately)",
    "duplicate_works.distinct_works": f"distinct Works ({WORK_IDENTITY_METHOD})",
    "duplicate_works.paper_keys": "distinct paper keys (basenames) cited in source_papers or source_metadata",
    "stubs": "term entries",
    "numeric_values": "per-term relation dicts",
    "aliases": "term entries; under-merge = adjacent label pairs after sorting (heuristic sample, not all pairs)",
    "relations": "per-term relation dicts (not json2kg-deduplicated edges)",
    "summary.legacy.total_entities": "term entries (raw extracted records, not canonical entities)",
    "summary.legacy.total_papers": "paper filename keys",
    "summary.legacy.papers_per_year": "one increment per (term entry, source_metadata entry) with a year; "
                                      "NOT distinct papers (legacy CLI histogram, kept for comparison)",
    "summary.papers_per_year_v2": f"distinct Works per year (papers_per_year/2, {WORK_IDENTITY_METHOD}); a Work's "
                                  "year is its most frequent recorded publication_year, ties to the earliest",
    "summary.distinct_works": f"distinct Works ({WORK_IDENTITY_METHOD})",
}
AUDIT_METHOD = "corpus-audit/1"


def work_index_for_terms(terms: Sequence[Mapping[str, Any]]) -> WorkIndex:
    """Paper records for every key cited by ``source_papers`` or ``source_metadata``, grouped into Works."""
    meta: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    order: list[str] = []
    for t in terms:
        for fn in t.get("source_papers") or []:
            key = paper_key(fn)
            if key and key not in meta:
                order.append(key)
                meta[key] = []
        for fn, m in (t.get("source_metadata") or {}).items():
            key = paper_key(fn)
            if not key:
                continue
            if key not in meta:
                order.append(key)
                meta[key] = []
            if isinstance(m, Mapping):
                meta[key].append(m)
    return WorkIndex.build(record_for(k, meta[k]) for k in order)


# =====================================================================================
# corpus source and operation
# =====================================================================================

@dataclass(frozen=True)
class CorpusSource:
    """One extracted-terms corpus, resolved by trusted wiring (never by the model)."""

    corpus_id: str
    terms_path: str


#: ``(ctx, corpus_name) -> CorpusSource`` or ``None`` when unknown / not visible.
CorpusResolver = Callable[[RequestContext, str], CorpusSource | None]
MAX_TERMS_BYTES = 512 * 1024 * 1024


def mapping_corpus_resolver(corpora: Mapping[str, str]) -> CorpusResolver:
    """Resolver over a fixed ``{corpus_id: terms_path}`` map, honouring a non-empty ``corpus_scope``."""
    fixed = {str(k): str(v) for k, v in corpora.items()}

    def resolve(ctx: RequestContext, name: str) -> CorpusSource | None:
        if name not in fixed:
            return None
        if ctx.corpus_scope and name not in ctx.corpus_scope:
            raise ContractError(ErrorCode.DENIED, f"corpus {name!r} is outside the request scope")
        return CorpusSource(name, fixed[name])

    return resolve


def load_terms(source: CorpusSource) -> tuple[list[dict[str, Any]], str, int | None]:
    """``(terms, sha256, file_size)`` of a terms file; ``NOT_FOUND``/``UNAVAILABLE`` without a server path."""
    path = Path(source.terms_path)
    try:
        size = path.stat().st_size
        if size > MAX_TERMS_BYTES:
            raise ContractError(ErrorCode.UNAVAILABLE, f"corpus {source.corpus_id!r} exceeds the size bound")
        raw = path.read_bytes()
    except FileNotFoundError:
        raise ContractError(ErrorCode.NOT_FOUND, f"corpus {source.corpus_id!r} has no terms file") from None
    except OSError:
        raise ContractError(ErrorCode.UNAVAILABLE, f"corpus {source.corpus_id!r} could not be read") from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ContractError(ErrorCode.UNAVAILABLE, f"corpus {source.corpus_id!r} is not valid JSON") from None
    terms = data.get("terms") if isinstance(data, dict) else None
    if not isinstance(terms, list):
        terms = []
    return [t for t in terms if isinstance(t, dict)], hashlib.sha256(raw).hexdigest(), size


_SECTION_FUNCS: dict[str, Callable[..., tuple[dict[str, Any], dict[str, Any]]]] = {
    "duplicate_works": lambda terms, works, **kw: section_duplicate_works(terms, works, **kw),
    "stubs": lambda terms, works: section_stubs(terms),
    "numeric_values": lambda terms, works: section_numeric_values(terms),
    "aliases": lambda terms, works: section_aliases(terms),
    "relations": lambda terms, works: section_relations(terms),
    "summary": lambda terms, works: section_summary(terms, works),
}


def audit_corpus(source: CorpusSource, *, sections: Sequence[str] = CORPUS_SECTIONS,
                 control: ScanControl | None = None,
                 near_duplicate_limit: int | None = NEAR_DUPLICATE_INLINE_LIMIT) -> OperationReport:
    """Run the audit over ``source`` (read-only) and return a typed report.

    The terms list is scanned in full unless ``control`` stops it; a stopped scan audits
    the scanned prefix only and is ``partial`` (never presented as the corpus total).
    The §1 near-duplicate title check runs when the corpus has at most ``near_duplicate_limit``
    paper keys or the scan runs in a job (``control.job``); otherwise it is deferred with a
    warning (D1.10). ``near_duplicate_limit=None`` always runs it.
    """
    started = utcnow()
    control = control or ScanControl()
    unknown = [s for s in sections if s not in _SECTION_FUNCS]
    if unknown:
        raise ContractError(ErrorCode.INVALID_INPUT, f"unknown sections {unknown}")
    terms, sha, _size = load_terms(source)
    scanned: list[dict[str, Any]] = []
    for t in terms:
        if not control.tick():
            break
        scanned.append(t)
    complete = len(scanned) == len(terms)
    reasons = [] if complete else [control.stopped_reason or "scan stopped",
                                   f"{len(scanned)} of {len(terms)} term entries audited"]
    works = work_index_for_terms(scanned)
    deferred = (near_duplicate_limit is not None and not control.job and len(works.records) > near_duplicate_limit
                and "duplicate_works" in sections)
    metrics: dict[str, Any] = {}
    preview: dict[str, Any] = {}
    for name in sections:
        kw = {"near_duplicates": not deferred} if name == "duplicate_works" else {}
        m, p = _SECTION_FUNCS[name](scanned, works, **kw)
        metrics[name] = m
        if p:
            preview[name] = p
    units = {k: v for k, v in SECTION_UNITS.items() if k.split(".")[0] in sections}
    warnings = []
    if deferred:
        warnings.append(NEAR_DUPLICATE_DEFERRED.format(keys=len(works.records), limit=near_duplicate_limit))
    if "aliases" in sections:
        warnings.append("aliases.under_merged_candidates compares adjacent sorted labels only; it is a heuristic "
                        "sample, not an all-pairs duplicate check")
    if "summary" in sections and metrics["summary"]["legacy"]["papers_per_year"]:
        warnings.append("summary.legacy.papers_per_year counts (term, source_metadata) increments, not papers; "
                        "use summary.papers_per_year_v2 for distinct Works per year")
    report = OperationReport(
        operation=TOOL_AUDIT, operation_version=OPERATION_VERSION, method=AUDIT_METHOD,
        parameters={"sections": list(sections)}, scope={"corpus": source.corpus_id},
        source_revisions={source.corpus_id: f"sha256:{sha}"},
        population=("Every term entry in the corpus's extracted-terms file (raw extracted records); paper figures "
                    "cover every paper key those entries cite."),
        counting_units=units,
        coverage=Coverage.of(scan_complete=complete, records_scanned=len(scanned), records_expected=len(terms),
                             reasons=reasons),
        metrics=metrics, preview=preview, excluded=dict(EXCLUDED_SECTIONS), warnings=tuple(warnings),
    )
    return stamp(report, started)


# =====================================================================================
# Tool
# =====================================================================================

TOOL_AUDIT = "curate_audit_corpus"
OPERATION_VERSION = "1.0.0"
_NAME = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"}

CURATE_AUDIT_CORPUS = ToolSpec(
    name=TOOL_AUDIT,
    version=OPERATION_VERSION,
    domain="operations",
    description=(
        "Read-only quality audit of one extracted-terms corpus (no model call): duplicate Works and distinct-Work "
        "counts, stub entries, suspicious photon-energy values, alias and relation checks, and summary counts "
        "with a distinct-Work per-year histogram. Every figure names its counting unit; a stopped scan is "
        "reported as partial."
    ),
    when_to_use=(
        "The user asks how many papers/Works or terms a configured corpus holds, how papers are spread over "
        "years, or for data-quality diagnostics of a corpus. Relay counts with their units; preview lists are "
        "examples, not the totals."
    ),
    when_not_to_use=(
        "Questions about what a paper says (use retrieval), graph structure (use kg_compute_statistics), or "
        "requests to fix, merge or reclassify data: this audit never changes anything and does not propose "
        "measurement/technique reclassification."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "corpus": _NAME,
            "sections": {"type": "array", "items": {"enum": list(CORPUS_SECTIONS)}, "minItems": 1,
                         "maxItems": len(CORPUS_SECTIONS), "uniqueItems": True},
        },
        "required": ["corpus"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {
            "operation": {"const": TOOL_AUDIT}, "method": {"const": AUDIT_METHOD},
            "coverage": {"type": "object"}, "metrics": {"type": "object"}, "preview": {"type": "object"},
            "counting_units": {"type": "object"}, "excluded": {"type": "object"},
        },
        "required": ["operation", "method", "coverage", "metrics", "counting_units"],
    },
    effects=frozenset({Effect.READ_CORPUS}),
    cost_class=CostClass.FREE,
    required_capabilities=frozenset({"chat.ask"}),
    evidence_contract=("No EvidenceRef: an aggregate cites its computation report (source_revisions + method); "
                       "artifact_refs carries the private full report when an artifact store is configured."),
    idempotent=True,
)

AUDIT_LITERATURE_CORPUS_SKILL = Skill(
    name="audit_literature_corpus",
    description="Count and diagnose a configured literature corpus with explicit units, coverage and exclusions.",
    when_to_use=(
        "Corpus totals, per-year paper counts or data-quality questions. Report distinct Works as the paper "
        "count; when coverage.completeness is partial say the figures are lower bounds; never recompute totals "
        "from preview examples."
    ),
    tools=(TOOL_AUDIT,),
    required_capabilities=frozenset({"chat.ask"}),
)


class CorpusAuditTool:
    """``curate_audit_corpus`` bound to a corpus resolver and an optional artifact store."""

    spec = CURATE_AUDIT_CORPUS
    skill = AUDIT_LITERATURE_CORPUS_SKILL

    def __init__(self, resolver: CorpusResolver, *, artifact_store: Any = None, max_records: int = 2_000_000) -> None:
        self._resolver = resolver
        self._store = artifact_store
        self._max = max_records

    def registration(self) -> tuple[ToolSpec, Callable[[Any], ToolResult]]:
        """``(spec, fn)`` for ``build_registry(extra=...)``."""
        return self.spec, self.run

    def operation_names(self) -> tuple[str, ...]:
        """Operations this suite runs (job dispatch)."""
        return (TOOL_AUDIT,)

    def compute_operation(self, ctx: RequestContext, operation: str, args: Mapping[str, Any],
                          control: ScanControl | None = None) -> OperationReport:
        """Job entry point."""
        if operation != TOOL_AUDIT:
            raise ContractError(ErrorCode.INVALID_INPUT, f"unknown corpus operation {operation!r}")
        return self.compute(ctx, args, control)

    def compute(self, ctx: RequestContext, args: Mapping[str, Any], control: ScanControl | None = None
                ) -> OperationReport:
        """Resolve the corpus and audit it (raises ``ContractError``)."""
        name = str(args.get("corpus") or "")
        source = self._resolver(ctx, name)
        if source is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"no corpus named {name!r}")
        control = control or ScanControl(self._max, deadline=ctx.deadline)
        return audit_corpus(source, sections=tuple(args.get("sections") or CORPUS_SECTIONS), control=control)

    def run(self, inv: Any) -> ToolResult:
        """Executor entry point."""
        provenance_id = f"f2w-op:{uuid.uuid4().hex}"
        try:
            report = self.compute(inv.ctx, inv.args)
            store = self._store if self._store is not None else getattr(inv, "artifact_store", None)
            ref = store_report_artifact(store, inv.ctx, report)
        except ContractError as exc:
            return error_result(TOOL_AUDIT, exc, provenance_id=provenance_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("corpus audit failed", exc_info=True)
            if is_control_flow(exc):
                raise
            return error_result(TOOL_AUDIT, ContractError(ErrorCode.INTERNAL, f"corpus audit failed "
                                                                              f"({type(exc).__name__})"),
                                provenance_id=provenance_id)
        return report_to_result(TOOL_AUDIT, report, provenance_id=provenance_id, artifact_refs=(ref,) if ref else ())


__all__: tuple[str, ...] = (
    "AUDIT_LITERATURE_CORPUS_SKILL", "AUDIT_METHOD", "CORPUS_SECTIONS", "CURATE_AUDIT_CORPUS", "CorpusAuditTool",
    "CorpusResolver", "CorpusSource", "EXCLUDED_SECTIONS", "NEAR_DUPLICATE_INLINE_LIMIT", "PaperRecord", "TOOL_AUDIT",
    "WORK_IDENTITY_METHOD", "WORK_IDENTITY_METHOD_V1", "content_hash_key",
    "WorkIndex", "audit_corpus", "doi_key", "identifiers_from_key", "load_terms", "mapping_corpus_resolver",
    "paper_key", "record_for", "same_work", "title_tokens", "titles_near_duplicate", "work_index_for_terms",
)
