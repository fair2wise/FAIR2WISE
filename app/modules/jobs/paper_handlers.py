"""Real handlers for the paper job kinds ``enrichment``, ``topic_sweep`` and ``pdf_parse`` (B5/J-11, RT-27..RT-32).

:mod:`.paper_jobs` owns the kinds and the lifecycle; this module is the work, ported from the donor enrichment agent
(``graph/enrichment_agent.py``: ``run_enrichment``, ``run_topic_sweep``, ``run_pdf_parse``). Differences that matter:

* **A handler never writes a graph.** Every node and edge the donor upserted is a :class:`Proposal` handed to a
  :class:`ProposalSink` (production: :class:`CurationProposalSink`, one case per proposal in the ``needs_review``
  queue, approved with ``curation.approve``). Proposals are idempotent: re-running a job re-proposes the same
  content and the store deduplicates it.
* **Ports, not globals.** The Tiled catalog, OpenAlex, the corpus publication list, PDF acquisition and the parser
  are constructor arguments bound by trusted wiring; nothing here reads the environment, opens a network connection
  or runs a model. The donor's process-wide stop flags are replaced by the job's cancel (checked between units) and
  its in-process "already scanned/parsed" probes by settled units (resume skips them).
* **Units.** Each paper, topic, search combination or analysis group is one unit with a content key; a resumed job
  skips units whose key is unchanged and retries failed ones.

What is deliberately not ported is listed in ``docs/migration/reviews/H8/handler_port_notes.md``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
from itertools import combinations
import json
import logging
import re
from typing import Any, Protocol

from ..contracts.context import RequestContext
from ..contracts.errors import ContractError, ErrorCode
from ..curation.models import UncertaintyCase
from ..curation.store import CurationStore
from ..literature.acquire import AcquireResult
from ..literature.citations import link_citations_from_referenced_works, link_citations_from_sections
from ..literature.ingested import fold_key
from ..literature.licence import from_openalex
from ..literature.openalex import (
    _BULK_WORK_FIELDS,
    MAX_PER_PAGE,
    OpenAlexClient,
    OpenAlexQuotaPaused,
    abstract_from_inverted_index,
    raise_for_no_data,
)
from ..literature.parse_service import ParseRecord
from ..literature.parsed_document import ParsedDocument
from .paper_jobs import ENRICHMENT_KIND, PDF_PARSE_KIND, TOPIC_SWEEP_KIND, JobControl, PaperJobHandler
from .pipeline import JobInterrupted, JobStopped, QuarantineStage, RestartStages, StagePause
from .store import LeaseLost

logger = logging.getLogger(__name__)

#: Lifecycle signals (stop, shutdown, lost lease, pause) end the run; they are never a failed unit.
_LIFECYCLE = (JobStopped, JobInterrupted, LeaseLost, StagePause, RestartStages, QuarantineStage)

PROPOSAL_CASE_TYPE = "graph_proposal"
PROPOSAL_SOURCE_KIND = "paper_job"
_OA_PREFIX = "https://openalex.org/"

#: Donor defaults (``_run_enrichment_body``, ``run_topic_sweep``).
DEFAULT_ENRICHMENT_LIMIT = 5000
DEFAULT_TOPIC_PAPERS = 50
MAX_TOPIC_PAPERS = 200
MAX_PAIRS_PER_BIN = 300
ANGLE_TOLERANCE = 0.15
TEMPERATURE_TOLERANCE = 10.0

#: Metadata keys a sample may declare its material under, in precedence order (bare and ``thinfilm_`` spellings).
MATERIAL_METADATA_KEYS = ("material", "thinfilm_material", "Copolymer", "thinfilm_Copolymer")
#: Other named materials a sample declares. ``Blend`` is absent on purpose: a blend is a composition, not a material.
MATERIAL_COMPONENT_KEYS = (
    "Donor", "thinfilm_Donor", "Acceptor", "thinfilm_Acceptor", "Solvent", "thinfilm_Solvent",
    "Additive", "thinfilm_Additive", "Substrate", "thinfilm_Substrate",
)
#: ``ITO/PEDOT:PSS`` is a stack of two materials; ``:`` is NOT a separator (blend ratio, ``PEDOT:PSS``).
_COMPONENT_SPLIT = re.compile(r"\s*[/+]\s*")


# ---------------------------------------------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Proposal:
    """One reviewable change set a handler wants: nodes and edges (or statements) with their evidence.

    Attributes:
        kind: ``dataset_material_links`` | ``chemical_similarity`` | ``cross_beamline`` | ``similar_conditions`` |
            ``publication_links`` | ``publication`` | ``citations`` | ``paper_parse``.
        key: Stable identity within the corpus (the same content proposed twice is one case).
        subject: The node the proposal is about (a publication, a material, a sample).
        reason: One line for the reviewer.
        content: JSON-able ``{"nodes": [...], "edges": [...], ...}``.
        priority: Review priority (higher first).
    """

    kind: str
    key: str
    subject: str
    reason: str
    content: Mapping[str, Any]
    priority: int = 40


class ProposalSink(Protocol):
    """Where proposals go. Never a graph writer."""

    def propose(self, ctx: RequestContext, corpus: str, proposal: Proposal) -> bool:
        """Record ``proposal`` for review; ``True`` when it is new or changed."""
        ...


class CurationProposalSink:
    """Proposals as ``graph_proposal`` cases in the curation review queue (``curation.approve`` decides them).

    The identity of a case is ``(corpus, subject, kind:key)``; the content is the case evidence, so a changed
    proposal resets the case to review and discards a stale decision (the store's revision rule).
    """

    def __init__(self, store: CurationStore, *, schema_revision: str = "graph-proposal-v1",
                 job_kind: str = "") -> None:
        self.store = store
        self.schema_revision = schema_revision
        self.job_kind = job_kind

    def propose(self, ctx: RequestContext, corpus: str, proposal: Proposal) -> bool:
        """Enqueue the proposal; the run id travels in the payload (provenance of who proposed it)."""
        locator = f"{proposal.kind}:{proposal.key}"
        case = UncertaintyCase(
            corpus=corpus,
            publication_id=proposal.subject,
            document_hash=hashlib.sha256(locator.encode()).hexdigest(),
            source_kind=PROPOSAL_SOURCE_KIND,
            source_locator=locator,
            case_type=PROPOSAL_CASE_TYPE,
            reason=f"proposal:{proposal.kind}",   # identity: a changed summary must not mint a second case
            evidence=json.dumps({"summary": proposal.reason[:300], **proposal.content}, sort_keys=True,
                                ensure_ascii=False, default=str),
            payload={"proposal_kind": proposal.kind},
            priority=proposal.priority,
            schema_revision=self.schema_revision,
        )
        _, changed = self.store.enqueue_for_review(case)
        return changed


# ---------------------------------------------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------------------------------------------


class DatasetCatalog(Protocol):
    """Read-only view of the dataset catalog (Tiled on ALS); the adapter lives in trusted wiring."""

    def datasets(self, ctx: RequestContext, corpus: str, *, entity_ids: Sequence[str] | None,
                 limit: int) -> Iterable[tuple[str, Mapping[str, Any]]]:
        """``(catalog path, metadata)`` of up to ``limit`` datasets (restricted to ``entity_ids`` when given)."""
        ...


class WorkSearch(Protocol):
    """OpenAlex works search returning the raw work objects (one page)."""

    def works(self, ctx: RequestContext, *, search: str, per_page: int, page: int = 1,
              year_from: int | None = None) -> Sequence[Mapping[str, Any]]:
        """Raw OpenAlex works; raises :class:`OpenAlexQuotaPaused` / a provider error rather than returning ``[]``."""
        ...


class CorpusPublications(Protocol):
    """The Works already in the corpus."""

    def publications(self, ctx: RequestContext, corpus: str) -> Sequence[Mapping[str, Any]]:
        """Rows with ``id``, ``doi`` and, when known, ``citations``, ``openalex_id`` and ``referenced_works``."""
        ...


class PdfSource(Protocol):
    """Acquire a stored source for a DOI (:class:`~app.modules.literature.acquire.PaperAcquirer`)."""

    def acquire(self, ctx: RequestContext, doi: str, *, work: Mapping[str, Any] | None = None) -> AcquireResult:
        """A stored OA source, from cache when possible."""
        ...


class PdfParser(Protocol):
    """Versioned PDF parse (:class:`~app.modules.literature.parse_service.ParseService`)."""

    def parse(self, ctx: RequestContext, source_ref: str, *, force: bool = False) -> ParseRecord:
        """A verified parse; cached by parser version unless ``force``."""
        ...

    def load(self, ctx: RequestContext, record: ParseRecord) -> ParsedDocument:
        """The parsed document of ``record``."""
        ...


class OpenAlexWorkSearch:
    """:class:`WorkSearch` over the injected :class:`OpenAlexClient` (cache, breaker and redaction included)."""

    def __init__(self, client: OpenAlexClient) -> None:
        self.client = client

    def works(self, ctx: RequestContext, *, search: str, per_page: int, page: int = 1,
              year_from: int | None = None) -> Sequence[Mapping[str, Any]]:
        """One page of works with ``referenced_works`` selected; a non-answer raises, it is not an empty page."""
        params: dict[str, Any] = {"search": search, "per_page": min(max(1, per_page), MAX_PER_PAGE), "page": page,
                                  "select": _BULK_WORK_FIELDS}
        if year_from:
            params["filter"] = f"from_publication_date:{int(year_from)}-01-01"
        data = self.client.fetch("works", params, timeout=30.0)
        raise_for_no_data(data)
        return [w for w in data.get("results", []) if isinstance(w, dict)]


# ---------------------------------------------------------------------------------------------------------------
# Pure helpers (ported)
# ---------------------------------------------------------------------------------------------------------------


def _digest(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def dataset_node_id(path: str) -> str:
    """Stable dataset id from its catalog path (donor ``_stable_id``)."""
    return hashlib.sha256(path.encode()).hexdigest()[:12]


def material_name_from_metadata(meta: Mapping[str, Any]) -> str:
    """The material a sample declares, or ``""`` (donor ``_material_name_from_metadata``)."""
    for key in MATERIAL_METADATA_KEYS:
        value = meta.get(key)
        if value:
            return str(value).strip()
    return ""


def material_names_from_metadata(meta: Mapping[str, Any]) -> list[str]:
    """Every material name a sample declares, primary first, de-duplicated case-insensitively."""
    names: list[str] = []
    seen: set[str] = set()
    primary = material_name_from_metadata(meta)
    if primary:
        names.append(primary)
        seen.add(primary.casefold())
    for key in MATERIAL_COMPONENT_KEYS:
        raw = meta.get(key)
        if not raw:
            continue
        for part in _COMPONENT_SPLIT.split(str(raw)):
            name = part.strip()
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                names.append(name)
    return names


def build_record(path: str, meta: Mapping[str, Any]) -> dict[str, Any] | None:
    """The enrichment record of one catalog entry, or ``None`` when it has nothing to link on.

    Supports the legacy ``thinfilm_*`` schema and the ``PS_A_SS_*`` / ``PS_B_SS_*`` precursor-solution schema.
    """
    def first(*keys: str) -> str:
        for key in keys:
            if meta.get(key):
                return str(meta[key])
        return ""

    material = material_name_from_metadata(meta)
    record: dict[str, Any] = {
        "path": path,
        "node_id": dataset_node_id(path),
        "material": material,
        "materials": material_names_from_metadata(meta),
        "organic_smiles": first("thinfilm_OrganicSalt_SMILES", "PS_A_SS_OrganicSP_SMILES",
                                "PS_A_SS_OrganicSalt_SMILES").strip(),
        "metal_smiles": first("thinfilm_MetalSalt_SMILES", "PS_B_SS_MetalSP_SMILES",
                              "PS_B_SS_MetalSalt_SMILES").strip(),
        "organic_salt": first("thinfilm_OrganicSalt", "PS_A_SS_OrganicSalt_Name", "PS_A_SS_OrganicSP_Name"),
        "metal_salt": first("thinfilm_MetalSalt", "PS_B_SS_MetalSalt_Name", "PS_B_SS_MetalSP_Name",
                            "PS_B_SS_MetalSP_Abbrev"),
        "technique": first("technique", "scan_type"),
        "angle": meta.get("incident_angle_deg") or meta.get("sample_alpha_stage"),
        "temp": (meta.get("thinfilm_AnnealingTemp") or meta.get("AnnealingTemp") or meta.get("temperature")
                 or meta.get("Temp")),
        "sample_name": str(meta.get("sample_name") or meta.get("ThinFilmID") or "").strip().lower(),
        "beamline": str(meta.get("beamline") or ""),
        "energy_keV": meta.get("energy_keV"),
    }
    if record["sample_name"] or record["technique"] or record["beamline"] or any(
            record[k] for k in ("organic_smiles", "metal_smiles", "organic_salt", "metal_salt", "material")):
        return record
    return None


def condition_bin(value: Any, tolerance: float) -> str | None:
    """The tolerance bin of an angle or temperature value (``"150 C"`` and ``150`` agree), or ``None``."""
    try:
        number = float(str(value).rstrip("CcKk °").strip())
    except (TypeError, ValueError):
        return None
    return f"{round(number / tolerance) * tolerance:.2f}"


def work_record(work: Mapping[str, Any]) -> dict[str, Any]:
    """An OpenAlex work as the donor's rich paper record (authors with ids, concepts, referenced works)."""
    authors: list[dict[str, Any]] = []
    for authorship in (work.get("authorships") or [])[:10]:
        author = authorship.get("author") or {}
        institutions = authorship.get("institutions") or []
        authors.append({
            "name": author.get("display_name", "") or "",
            "openalex_id": str(author.get("id") or "").replace(_OA_PREFIX, ""),
            "affiliation": (institutions[0].get("display_name", "") if institutions else "") or "",
        })
    source = (work.get("primary_location") or {}).get("source") or {}
    refs = work.get("referenced_works") or []
    abstract = abstract_from_inverted_index(work.get("abstract_inverted_index"))
    return {
        "doi": str(work.get("doi") or "").replace("https://doi.org/", ""),
        "openalex_id": str(work.get("id") or "").replace(_OA_PREFIX, ""),
        "title": work.get("title") or "",
        "year": work.get("publication_year"),
        "citations": int(work.get("cited_by_count") or 0),
        "abstract": abstract,
        "abstract_snippet": abstract[:300],
        "authors": authors,
        "journal": source.get("display_name", "") or "",
        "concepts": [{"name": c.get("display_name", ""), "score": round(float(c.get("score") or 0.0), 3)}
                     for c in (work.get("concepts") or [])[:10]],
        "referenced_works": [str(u).replace(_OA_PREFIX, "") for u in refs[:100]],
        "referenced_works_count": int(work.get("referenced_works_count") or len(refs)),
        "is_oa": bool((work.get("open_access") or {}).get("is_oa")),
    }


def publication_node_id(record: Mapping[str, Any]) -> str:
    """``publication:<openalex id>`` (preprints lack DOIs), else ``publication:<doi>``, else ``""``."""
    if record.get("openalex_id"):
        return f"publication:{record['openalex_id']}"
    if record.get("doi"):
        return f"publication:{record['doi']}"
    return ""


def publication_nodes_and_edges(record: Mapping[str, Any], *, source: str, ingested_via: str = "") -> tuple[
        dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """``(publication node, author nodes, authored_by edges)`` for a paper record."""
    pub_id = publication_node_id(record)
    properties = {k: record.get(k) for k in ("doi", "openalex_id", "year", "citations", "abstract_snippet",
                                             "authors", "journal", "concepts", "referenced_works_count", "is_oa")}
    if ingested_via:
        properties["ingested_via"] = ingested_via
    node = {"id": pub_id, "entity_type": "publication", "name": record.get("title") or record.get("doi")
            or record.get("openalex_id"), "source": source, "properties": properties}
    authors: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for author in record.get("authors") or []:
        if not author.get("openalex_id") or not author.get("name"):
            continue
        author_id = f"author:{author['openalex_id']}"
        authors.append({"id": author_id, "entity_type": "author", "name": author["name"], "source": source,
                        "properties": {"openalex_id": author["openalex_id"],
                                       "affiliation": author.get("affiliation", "")}})
        edges.append({"source": pub_id, "target": author_id, "relation_type": "authored_by", "confidence": 1.0,
                      "evidence": f"OpenAlex authorship on {record.get('openalex_id') or record.get('doi')}"})
    return node, authors, edges


def link_confidence(record: Mapping[str, Any], material: str, technique: str) -> tuple[float, dict[str, float]]:
    """Scored confidence of a material-to-paper link: baseline .2, +.4 title, +.2 abstract, +.2 concept = technique."""
    signals = {"baseline": 0.2}
    needle = material.lower()
    if needle and needle in str(record.get("title") or "").lower():
        signals["title_match"] = 0.4
    if needle and needle in str(record.get("abstract") or "").lower():
        signals["abstract_match"] = 0.2
    tech = technique.lower()
    if tech and any(tech in str(c.get("name", "")).lower() for c in record.get("concepts") or []):
        signals["concept_technique_match"] = 0.2
    return max(0.3, min(1.0, sum(signals.values()))), signals


# ---------------------------------------------------------------------------------------------------------------
# Shared unit runner
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class _Run:
    """Accumulates counters across fresh and already-settled units of one handler run."""

    control: JobControl
    totals: dict[str, int] = field(default_factory=dict)
    incomplete: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def add(self, counts: Mapping[str, Any]) -> None:
        for name, value in counts.items():
            if isinstance(value, int) and not isinstance(value, bool):
                self.totals[name] = self.totals.get(name, 0) + value

    def unit(self, unit_id: str, key: str, work: Callable[[], Mapping[str, int]]) -> bool:
        """Run one unit unless an earlier run settled it with the same ``key``. ``False`` when it failed."""
        self.control.guard()
        done = self.control.settled(unit_id, key)
        if done is not None:
            self.add(done)
            self.add({"units_skipped_settled": 1})
            return True
        try:
            counts = dict(work())
        except (*_LIFECYCLE, OpenAlexQuotaPaused):
            raise
        except ContractError as exc:
            return self._failed(unit_id, key, str(exc.code))
        except Exception as exc:  # noqa: BLE001 - one unit must not lose the others; the type is the reason
            logger.warning("paper job unit %s failed (%s)", unit_id, type(exc).__name__)
            return self._failed(unit_id, key, type(exc).__name__)
        self.control.settle(unit_id, key, counts)
        self.add(counts)
        self.add({"units_done": 1})
        self.control.progress(**self.totals)
        return True

    def _failed(self, unit_id: str, key: str, reason: str) -> bool:
        self.control.fail(unit_id, key, reason)
        self.incomplete.append(unit_id)
        self.add({"units_failed": 1})
        return False

    def quota_stop(self, exc: OpenAlexQuotaPaused, remaining: Iterable[str]) -> None:
        """OpenAlex allowance spent: stop, keep what is proposed, report until when (donor ``paused_quota``)."""
        self.data["openalex_quota_paused_until"] = exc.resume_at_iso
        self.incomplete.extend(remaining)

    def result(self) -> dict[str, Any]:
        return {**self.totals, **self.data, "incomplete": list(dict.fromkeys(self.incomplete))}


def _submit(sink: ProposalSink, ctx: RequestContext, corpus: str, proposal: Proposal) -> dict[str, int]:
    changed = sink.propose(ctx, corpus, proposal)
    return {"proposals": 1, "proposals_new": int(changed)}


# ---------------------------------------------------------------------------------------------------------------
# enrichment
# ---------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EnrichmentConfig:
    """Donor knobs of the enrichment pipeline that are not job parameters."""

    chem_threshold: float = 0.5
    max_pubs_per_material: int = 3
    max_combos: int = 500


class EnrichmentHandler:
    """``enrichment``: dataset-to-material, similarity, cross-beamline, condition and publication proposals.

    Ported stages (donor numbering): 1 catalog sweep (read through :class:`DatasetCatalog`), 2 chemical similarity
    and named-material links, 3 cross-beamline, 4 publication linker (+4.5 citation linker), 6 condition
    correlator. Vision, paper simulation, the LLM scaffold stages and web enrichment are not ported (see notes).

    Args:
        catalog: Dataset catalog port.
        sink: Proposal sink.
        vocabulary_match: ``(term, category) -> (slug, display name) | None``; a name the vocabulary does not know is
            counted and skipped, never proposed as a new material.
        works: OpenAlex works search for the publication linker (``None`` skips that stage and says so).
        corpus: Works already in the corpus (duplicate suppression and the citation linker).
        tanimoto: ``(smiles, smiles) -> similarity | None``; ``None`` (no RDKit) skips chemical similarity.
        config: Donor knobs.
    """

    def __init__(self, catalog: DatasetCatalog, sink: ProposalSink, *,
                 vocabulary_match: Callable[[str, str], tuple[str, str] | None],
                 works: WorkSearch | None = None, corpus: CorpusPublications | None = None,
                 tanimoto: Callable[[str, str], float | None] | None = None,
                 config: EnrichmentConfig = EnrichmentConfig()) -> None:  # noqa: B008 - frozen dataclass
        self.catalog = catalog
        self.sink = sink
        self.vocabulary_match = vocabulary_match
        self.works = works
        self.corpus = corpus
        self.tanimoto = tanimoto
        self.config = config

    def __call__(self, ctx: RequestContext, params: Mapping[str, Any], control: JobControl) -> Mapping[str, Any]:
        """Run the stages; each group below is one resumable unit."""
        corpus = str(params["corpus"])
        limit = int(params.get("limit") or DEFAULT_ENRICHMENT_LIMIT)
        entity_ids = params.get("entity_ids")
        run = _Run(control)
        records: list[dict[str, Any]] = []
        for path, meta in self.catalog.datasets(ctx, corpus, entity_ids=entity_ids, limit=limit):
            control.guard()
            record = build_record(path, meta)
            if record is not None:
                records.append(record)
            if len(records) >= limit:
                break
        run.add({"datasets_scanned": len(records)})
        control.progress(**run.totals)
        self._materials(ctx, corpus, records, run)
        self._chemistry(ctx, corpus, records, run)
        self._cross_beamline(ctx, corpus, records, run)
        self._conditions(ctx, corpus, records, run)
        found = self._publications(ctx, corpus, records, run)
        self._citations(ctx, corpus, found, run)
        return run.result()

    def _edge(self, source: str, target: str, relation: str, evidence: str, confidence: float,
              **props: Any) -> dict[str, Any]:
        edge: dict[str, Any] = {"source": source, "target": target, "relation_type": relation,
                                "confidence": confidence, "evidence": evidence}
        if props:
            edge["properties"] = props
        return edge

    def _materials(self, ctx: RequestContext, corpus: str, records: list[dict[str, Any]], run: _Run) -> None:
        """Named materials resolved through the vocabulary only (one proposal per material)."""
        by_material: dict[str, list[dict[str, Any]]] = defaultdict(list)
        display: dict[str, str] = {}
        unmatched = 0
        for record in records:
            names = record["materials"] or ([record["material"]] if record["material"] else [])
            seen: set[str] = set()
            for index, name in enumerate(names):
                entry = self.vocabulary_match(name, "material")
                if entry is None:
                    unmatched += 1
                    continue
                slug, shown = entry
                if slug in seen:
                    continue
                seen.add(slug)
                display[slug] = shown
                by_material[slug].append(self._edge(
                    record["node_id"], f"material:{slug}", "synthesized_from",
                    f"sample metadata material: {name}", 1.0, role="named" if index == 0 else "component",
                    surface_form=name))
        run.add({"material_names_unmatched": unmatched})
        for slug, edges in sorted(by_material.items()):
            key = _digest(slug, edges)
            content = {"nodes": [{"id": f"material:{slug}", "entity_type": "material", "name": display[slug]}],
                       "edges": edges}
            run.unit(f"materials:{slug}", key, lambda s=slug, c=content, e=edges: {  # type: ignore[misc]
                **_submit(self.sink, ctx, corpus, Proposal(
                    "dataset_material_links", s, f"material:{s}",
                    f"{len(e)} dataset(s) declare material {display[s]}", c)),
                "dataset_material_edges": len(e)})

    def _chemistry(self, ctx: RequestContext, corpus: str, records: list[dict[str, Any]], run: _Run) -> None:
        """Pairwise SMILES similarity above the threshold (needs the injected Tanimoto function)."""
        if self.tanimoto is None:
            run.data["chemical_similarity"] = "skipped: no similarity function (RDKit) configured"
            return
        tanimoto = self.tanimoto
        for role in ("organic", "metal"):
            smiles = sorted({r[f"{role}_smiles"] for r in records if r[f"{role}_smiles"]})
            for i, a in enumerate(smiles):
                def work(a: str = a, i: int = i, role: str = role) -> dict[str, int]:
                    edges = []
                    for b in smiles[i + 1:]:
                        score = tanimoto(a, b)
                        if score is not None and score >= self.config.chem_threshold:
                            edges.append(self._edge(f"material:{role}:{_digest(a)}", f"material:{role}:{_digest(b)}",
                                                    "chemical_similarity", f"Tanimoto={score:.3f} (Morgan r=2)",
                                                    score, tanimoto=score, smiles=[a, b]))
                    if not edges:
                        return {"proposals": 0}
                    return {**_submit(self.sink, ctx, corpus, Proposal(
                        "chemical_similarity", f"{role}:{_digest(a)}", f"material:{role}:{_digest(a)}",
                        f"{len(edges)} structure(s) similar to {a[:60]}", {"edges": edges}, priority=30)),
                        "chem_edges": len(edges)}
                run.unit(f"chem:{role}:{_digest(a)}", _digest(a, smiles[i + 1:], self.config.chem_threshold), work)

    def _cross_beamline(self, ctx: RequestContext, corpus: str, records: list[dict[str, Any]], run: _Run) -> None:
        """The same named sample at two beamlines (.95) or two energies on one beamline (.9)."""
        by_sample: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            if record["sample_name"]:
                by_sample[record["sample_name"]].append(record)
        for name, group in sorted(by_sample.items()):
            if len(group) < 2:
                continue
            edges: list[dict[str, Any]] = []
            for a, b in combinations(group, 2):
                if a["beamline"] and b["beamline"] and a["beamline"] != b["beamline"]:
                    edges.append(self._edge(
                        a["node_id"], b["node_id"], "cross_beamline",
                        f"Same sample '{name}' at BL {a['beamline']} vs BL {b['beamline']}", 0.95))
                elif (a["energy_keV"] is not None and b["energy_keV"] is not None
                      and str(a["energy_keV"]) != str(b["energy_keV"]) and a["beamline"] == b["beamline"]):
                    edges.append(self._edge(
                        a["node_id"], b["node_id"], "cross_beamline",
                        f"Same sample '{name}' at different energies ({a['energy_keV']} vs {b['energy_keV']} keV)",
                        0.9))
            if edges:
                run.unit(f"cross_beamline:{_digest(name)}", _digest(edges), lambda n=name, e=edges: {  # type: ignore[misc]
                    **_submit(self.sink, ctx, corpus, Proposal(
                        "cross_beamline", _digest(n), f"sample:{_digest(n)}",
                        f"sample '{n}' measured across beamlines or energies", {"edges": e}, priority=30)),
                    "cross_beamline_edges": len(e)})

    def _conditions(self, ctx: RequestContext, corpus: str, records: list[dict[str, Any]], run: _Run) -> None:
        """Datasets in the same angle or temperature bin (at most 300 pairs per bin)."""
        bins: dict[tuple[str, str], list[str]] = defaultdict(list)
        for record in records:
            for label, value, tolerance in (("angle", record["angle"], ANGLE_TOLERANCE),
                                            ("temperature", record["temp"], TEMPERATURE_TOLERANCE)):
                value_bin = condition_bin(value, tolerance)
                if value_bin is not None:
                    bins[(label, value_bin)].append(record["node_id"])
        unit_of = {"angle": "incident_angle_deg ≈ {}°", "temperature": "temperature ≈ {}°C"}
        for (label, value_bin), node_ids in sorted(bins.items()):
            if len(node_ids) < 2:
                continue
            edges = [self._edge(a, b, "similar_conditions", unit_of[label].format(value_bin), 0.8)
                     for a, b in list(combinations(sorted(node_ids), 2))[:MAX_PAIRS_PER_BIN]]
            run.unit(f"conditions:{label}:{value_bin}", _digest(edges), lambda lb=label, vb=value_bin, e=edges: {  # type: ignore[misc]
                **_submit(self.sink, ctx, corpus, Proposal(
                    "similar_conditions", f"{lb}:{vb}", f"conditions:{lb}:{vb}",
                    f"{len(e)} dataset pair(s) share {lb} ≈ {vb}", {"edges": e}, priority=20)),
                "condition_edges": len(e)})

    def _publications(self, ctx: RequestContext, corpus: str, records: list[dict[str, Any]],
                      run: _Run) -> list[dict[str, Any]]:
        """Search OpenAlex per (material, technique) combination; known papers are skipped, links are proposals."""
        found: list[dict[str, Any]] = []
        if self.works is None:
            run.data["publication_linker"] = "skipped: no OpenAlex search configured"
            return found
        combos: set[tuple[str, str]] = set()
        for record in records:
            technique = str(record["technique"]).strip()
            if not technique:
                continue
            if salt := (record["organic_salt"] or record["metal_salt"]):
                combos.add((salt.strip(), technique))
            for name in record["materials"] or ([record["material"]] if record["material"] else []):
                if name.strip():
                    combos.add((name.strip(), technique))
        ordered = sorted(combos, key=lambda t: (t[0].lower(), t[1].lower()))[:self.config.max_combos]
        run.add({"combos_capped": max(0, len(combos) - len(ordered))})
        known: set[str] = set()
        for row in (self.corpus.publications(ctx, corpus) if self.corpus else ()):
            known |= {k for k in (fold_key(str(row.get(f) or "")) for f in ("doi", "openalex_id", "id")) if k}
        works = self.works
        for index, (material, technique) in enumerate(ordered):
            unit_id = f"publications:{_digest(material, technique)}"

            def work(m: str = material, t: str = technique) -> dict[str, int]:
                counts = {"publications": 0, "publications_skipped_existing": 0}
                entry = self.vocabulary_match(m, "material")
                material_id = f"material:{entry[0]}" if entry else ""
                edges: list[dict[str, Any]] = []
                nodes: list[dict[str, Any]] = []
                for raw in works.works(ctx, search=f"{m} {t}".strip(), per_page=self.config.max_pubs_per_material):
                    record = work_record(raw)
                    pub_id = publication_node_id(record)
                    if not pub_id:
                        continue
                    if {fold_key(record["doi"]), fold_key(record["openalex_id"])} - {""} & known:
                        counts["publications_skipped_existing"] += 1
                        continue
                    found.append({"id": pub_id, **record})
                    node, authors, author_edges = publication_nodes_and_edges(record, source="enrichment_agent")
                    nodes += [node, *authors]
                    edges += author_edges
                    if material_id:
                        confidence, signals = link_confidence(record, m, t)
                        edges.append(self._edge(material_id, pub_id, "published_in", f"OpenAlex search: {m} {t}",
                                                confidence, confidence_signals=signals))
                        counts["publications"] += 1
                if not nodes:
                    return counts
                proposal = Proposal("publication_links", _digest(m, t), material_id or f"search:{_digest(m, t)}",
                                    f"OpenAlex papers for {m} + {t}", {"nodes": nodes, "edges": edges}, priority=35)
                return {**counts, **_submit(self.sink, ctx, corpus, proposal)}

            try:
                run.unit(unit_id, _digest(material, technique, self.config.max_pubs_per_material), work)
            except OpenAlexQuotaPaused as exc:
                run.quota_stop(exc, (f"publications:{_digest(m, t)}" for m, t in ordered[index:]))
                break
        return found

    def _citations(self, ctx: RequestContext, corpus: str, found: list[dict[str, Any]], run: _Run) -> None:
        """``cites`` statements among the corpus and the papers just found (registry claims, not paper claims)."""
        rows: dict[str, dict[str, Any]] = {}
        for row in (self.corpus.publications(ctx, corpus) if self.corpus else ()):
            rows[str(row["id"])] = {"id": str(row["id"]), "openalex_id": row.get("openalex_id"),
                                    "referenced_works": row.get("referenced_works")}
        for paper in found:
            rows.setdefault(paper["id"], {"id": paper["id"], "openalex_id": paper["openalex_id"],
                                          "referenced_works": paper["referenced_works"]})
        usable = [r for r in rows.values() if r["referenced_works"] is not None]
        result = link_citations_from_referenced_works(usable)
        if not result.statements:
            return
        statements = [s.to_dict() for s in result.statements]
        run.unit("citations", _digest([s["subject"] + s["object"] for s in statements]), lambda: {
            **_submit(self.sink, ctx, corpus, Proposal(
                "citations", "referenced_works", "citations:referenced_works",
                f"{len(statements)} paper-to-paper citation(s) from OpenAlex referenced_works",
                {"statements": statements}, priority=25)),
            "citations": len(statements)})


# ---------------------------------------------------------------------------------------------------------------
# topic sweep
# ---------------------------------------------------------------------------------------------------------------


class TopicSweepHandler:
    """``topic_sweep``: for each topic, search OpenAlex and propose the paper sub-graph (papers, authors, links).

    One unit per topic. ``limit`` is the paper cap per topic (default 50, hard cap 200). After the topics, the
    citations among the swept papers and the corpus are proposed from ``referenced_works``.
    """

    def __init__(self, works: WorkSearch, sink: ProposalSink, *, corpus: CorpusPublications | None = None) -> None:
        self.works = works
        self.sink = sink
        self.corpus = corpus

    def __call__(self, ctx: RequestContext, params: Mapping[str, Any], control: JobControl) -> Mapping[str, Any]:
        """Sweep every topic; a quota pause stops the sweep and reports the resume time."""
        corpus = str(params["corpus"])
        cap = min(int(params.get("limit") or DEFAULT_TOPIC_PAPERS), MAX_TOPIC_PAPERS)
        year_from = params.get("year_from")
        topics = [t.strip() for t in params.get("topics") or [] if t.strip()]
        run = _Run(control)
        papers: list[dict[str, Any]] = []
        for index, topic in enumerate(topics):
            def work(topic: str = topic) -> dict[str, int]:
                swept: list[dict[str, Any]] = []
                page = 1
                while len(swept) < cap:
                    control.guard()
                    batch = self.works.works(ctx, search=topic, per_page=min(cap, MAX_PER_PAGE), page=page,
                                             year_from=year_from)
                    if not batch:
                        break
                    for raw in batch:
                        record = work_record(raw)
                        if publication_node_id(record) and len(swept) < cap:
                            swept.append(record)
                    page += 1
                nodes: list[dict[str, Any]] = []
                edges: list[dict[str, Any]] = []
                authors_seen: set[str] = set()
                for record in swept:
                    node, authors, author_edges = publication_nodes_and_edges(
                        record, source="topic_sweep", ingested_via=f"topic_sweep:{topic}")
                    nodes.append(node)
                    nodes += [a for a in authors if a["id"] not in authors_seen]
                    authors_seen |= {a["id"] for a in authors}
                    edges += author_edges
                    papers.append({"id": node["id"], **record})
                if not swept:
                    return {"papers_added": 0}
                return {**_submit(self.sink, ctx, corpus, Proposal(
                    "publication", _digest(topic, year_from, cap), f"topic:{_digest(topic)}",
                    f"{len(swept)} paper(s) for topic {topic!r}", {"nodes": nodes, "edges": edges}, priority=35)),
                    "papers_added": len(swept), "authors_added": len(authors_seen)}

            try:
                run.unit(f"topic:{_digest(topic)}", _digest(topic, year_from, cap), work)
            except OpenAlexQuotaPaused as exc:
                run.quota_stop(exc, (f"topic:{_digest(t)}" for t in topics[index:]))
                break
        self._citations(ctx, corpus, papers, run)
        return run.result()

    def _citations(self, ctx: RequestContext, corpus: str, papers: list[dict[str, Any]], run: _Run) -> None:
        rows: dict[str, dict[str, Any]] = {}
        for row in (self.corpus.publications(ctx, corpus) if self.corpus else ()):
            rows[str(row["id"])] = {"id": str(row["id"]), "openalex_id": row.get("openalex_id"),
                                    "referenced_works": row.get("referenced_works")}
        for paper in papers:
            rows.setdefault(paper["id"], {"id": paper["id"], "openalex_id": paper["openalex_id"],
                                          "referenced_works": paper["referenced_works"]})
        result = link_citations_from_referenced_works([r for r in rows.values() if r["referenced_works"] is not None])
        if not result.statements:
            return
        statements = [s.to_dict() for s in result.statements]
        run.unit("citations", _digest([s["subject"] + s["object"] for s in statements]), lambda: {
            **_submit(self.sink, ctx, corpus, Proposal(
                "citations", "topic_sweep", "citations:topic_sweep",
                f"{len(statements)} citation(s) among swept and corpus papers", {"statements": statements},
                priority=25)),
            "cites_added": len(statements)})


# ---------------------------------------------------------------------------------------------------------------
# pdf parse
# ---------------------------------------------------------------------------------------------------------------


class PdfParseHandler:
    """``pdf_parse``: acquire, parse and propose sections, figures and reference-list citations per paper.

    Papers are the corpus publications with a DOI, most cited first (donor order), restricted to ``paper_ids`` and
    capped by ``limit`` (default 10, the donor's ``top_k``). Acquisition is the hardened open-access path and the
    parse is cached by parser version (``force`` re-parses), so an unchanged paper costs nothing on resume.

    Args:
        corpus: Works in the corpus (rows as in :class:`CorpusPublications`).
        source: PDF acquisition.
        parser: Versioned parser.
        sink: Proposal sink.
        work_lookup: ``doi -> OpenAlex work | None``, for figure licence terms; a failure or ``None`` stamps the
            fail-closed ``unknown`` licence status (a figure is never assumed redistributable).
    """

    def __init__(self, corpus: CorpusPublications, source: PdfSource, parser: PdfParser, sink: ProposalSink, *,
                 work_lookup: Callable[[str], Mapping[str, Any] | None] | None = None) -> None:
        self.corpus = corpus
        self.source = source
        self.parser = parser
        self.sink = sink
        self.work_lookup = work_lookup

    def __call__(self, ctx: RequestContext, params: Mapping[str, Any], control: JobControl) -> Mapping[str, Any]:
        """Parse each target paper as one unit."""
        corpus = str(params["corpus"])
        rows = list(self.corpus.publications(ctx, corpus))
        wanted = set(params.get("paper_ids") or ())
        targets = [r for r in rows if str(r.get("doi") or "").strip() and (not wanted or str(r["id"]) in wanted)]
        targets.sort(key=lambda r: int(r.get("citations") or 0), reverse=True)
        targets = targets[:int(params.get("limit") or 10)]
        force = bool(params.get("force", False))
        run = _Run(control)
        run.add({"papers_targeted": len(targets)})
        reasons: list[dict[str, str]] = []
        for row in targets:
            pub_id, doi = str(row["id"]), str(row["doi"]).strip()

            def work(pub_id: str = pub_id, doi: str = doi) -> dict[str, int]:
                acquired = self.source.acquire(ctx, doi)
                if not acquired.ok:
                    reasons.append({"doi": doi, "reason": acquired.status})
                    raise ContractError(ErrorCode.UNAVAILABLE, acquired.status)
                record = self.parser.parse(ctx, acquired.ref, force=force)
                document = self.parser.load(ctx, record)
                return self._proposals(ctx, corpus, pub_id, doi, document, record, rows)

            ok = run.unit(f"paper:{_digest(pub_id)}", _digest(pub_id, doi, force), work)
            if not ok and not any(r["doi"] == doi for r in reasons):
                reasons.append({"doi": doi, "reason": "parse_error"})
        run.add({"parse_failures": len(reasons)})
        run.data["failure_reasons"] = reasons[:50]
        return run.result()

    def _licence(self, doi: str) -> dict[str, Any]:
        work: Mapping[str, Any] | None = None
        if self.work_lookup is not None:
            try:
                work = self.work_lookup(doi)
            except Exception as exc:  # noqa: BLE001 - an unlicensed figure beats no figure
                logger.warning("licence terms unavailable for %s (%s)", doi, type(exc).__name__)
        info = from_openalex(work)
        return {**info.as_dict(), "licence_status": info.licence_status,
                "licence_source": "openalex" if work else "unavailable"}

    def _proposals(self, ctx: RequestContext, corpus: str, pub_id: str, doi: str, document: ParsedDocument,
                   record: ParseRecord, rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
        parser_props = {"parser": document.parser, "parser_version": document.parser_version,
                        "parser_key": record.parser_key}
        sections = [{"id": f"paper_section:{pub_id}:{s.structural_id or _digest(s.name, s.page)}", "name": s.name,
                     "page": s.page, "level": s.level, "chars": len(s.text),
                     "text_sha256": hashlib.sha256(s.text.encode()).hexdigest(), **parser_props}
                    for s in document.sections]
        licence = self._licence(doi)
        figures = [{"id": f"paper_figure:{pub_id}:{f.figure_id or f.index}", "caption": f.caption[:2000],
                    "page": f.page, "asset_ref": f.asset_ref, **parser_props, **licence}
                   for f in document.figures]
        edges = ([{"source": pub_id, "target": s["id"], "relation_type": "has_section", "confidence": 1.0,
                   "evidence": f"Parsed from PDF of {doi}"} for s in sections]
                 + [{"source": pub_id, "target": f["id"], "relation_type": "has_figure", "confidence": 1.0,
                     "evidence": f"Extracted from PDF of {doi}"} for f in figures])
        counts = _submit(self.sink, ctx, corpus, Proposal(
            "paper_parse", pub_id, pub_id, f"{len(sections)} section(s), {len(figures)} figure(s) parsed from {doi}",
            {"document_ref": record.document_ref, "source_ref": record.source_ref, "cache_hit": record.cache_hit,
             "nodes": [{**s, "entity_type": "paper_section"} for s in sections]
             + [{**f, "entity_type": "paper_figure"} for f in figures], "edges": edges}, priority=35))
        cites = link_citations_from_sections(
            [{"id": str(r["id"]), "doi": r.get("doi")} for r in rows],
            [(pub_id, s.name, s.text) for s in document.sections])
        resolved = [s.to_dict() for s in cites.statements if s.resolved]
        if resolved:
            counts["proposals"] += 1
            counts["proposals_new"] += int(self.sink.propose(ctx, corpus, Proposal(
                "citations", f"text:{pub_id}", pub_id, f"{len(resolved)} citation(s) from reference lists of {doi}",
                {"statements": resolved}, priority=25)))
        return {**counts, "sections_created": len(sections), "figures_created": len(figures),
                "reference_citations": len(resolved), "cache_hits": int(record.cache_hit)}


# ---------------------------------------------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------------------------------------------


def build_paper_handlers(
    *,
    sink: ProposalSink,
    catalog: DatasetCatalog | None = None,
    vocabulary_match: Callable[[str, str], tuple[str, str] | None] | None = None,
    works: WorkSearch | None = None,
    corpus: CorpusPublications | None = None,
    source: PdfSource | None = None,
    parser: PdfParser | None = None,
    tanimoto: Callable[[str, str], float | None] | None = None,
    work_lookup: Callable[[str], Mapping[str, Any] | None] | None = None,
    config: EnrichmentConfig = EnrichmentConfig(),  # noqa: B008 - frozen dataclass
) -> dict[str, PaperJobHandler]:
    """The handlers whose ports are all configured, ready for :func:`~.paper_jobs.register_paper_jobs`.

    A kind whose required ports are missing is left out, so submitting it is refused (``UNSUPPORTED_CAPABILITY``)
    instead of being accepted and never run. ``enrichment`` needs ``catalog`` and ``vocabulary_match``;
    ``topic_sweep`` needs ``works``; ``pdf_parse`` needs ``corpus``, ``source`` and ``parser``.
    """
    handlers: dict[str, PaperJobHandler] = {}
    if catalog is not None and vocabulary_match is not None:
        handlers[ENRICHMENT_KIND] = EnrichmentHandler(
            catalog, sink, vocabulary_match=vocabulary_match, works=works, corpus=corpus, tanimoto=tanimoto,
            config=config)
    if works is not None:
        handlers[TOPIC_SWEEP_KIND] = TopicSweepHandler(works, sink, corpus=corpus)
    if corpus is not None and source is not None and parser is not None:
        handlers[PDF_PARSE_KIND] = PdfParseHandler(corpus, source, parser, sink, work_lookup=work_lookup)
    return handlers


__all__ = ("CorpusPublications", "CurationProposalSink", "DatasetCatalog", "EnrichmentConfig", "EnrichmentHandler",
           "OpenAlexWorkSearch", "PROPOSAL_CASE_TYPE", "PdfParseHandler", "PdfParser", "PdfSource", "Proposal",
           "ProposalSink", "TopicSweepHandler", "WorkSearch", "build_paper_handlers", "build_record",
           "condition_bin", "link_confidence", "material_names_from_metadata", "work_record")
