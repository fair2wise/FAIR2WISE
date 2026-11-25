"""Relevance bar for paper suggestions, common-name lookup and decline parsing for the chat's download offer.

The chat offers papers to download when the KG cannot answer a question. Pilot 2026-11-25: "tell me about pleco catfish"
(the papers say Loricariidae, never "pleco") produced offers of an ML benchmark and a paper on fake dating profiles, because
a search for "catfish" returns whatever has the word. A suggestion has to share enough of the selected KG's own vocabulary
to be about its subject, otherwise nothing is offered. Pure functions; the only I/O is reading the KG JSON once per file
version.
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

#: Distinct KG-vocabulary words a candidate's title and abstract must share with the selected KG to be offered.
MIN_SHARED_KG_WORDS = 4
#: A word is KG vocabulary when it is in the names of at least this many nodes.
MIN_NODE_COUNT = 2
#: A KG with fewer distinct vocabulary words than this has no subject to judge a paper against (a demo graph, an empty one).
MIN_VOCABULARY_WORDS = 30
#: Largest number of nodes read for the vocabulary.
MAX_VOCAB_NODES = 50_000

_WORD = re.compile(r"[a-z][a-z0-9-]{3,}")
_GENERIC = frozenset("""
this that with from have been were their there which these those about using used based study studies analysis analyses
data model models method methods result results paper papers approach between within across over under into onto than
more most also such other both each only same different novel new case review towards toward via
""".split())

#: Common names that a paper would not use for the scientific group the KG is written in. A name is added to a question
#: only when the KG's own vocabulary has the scientific name.
COMMON_NAMES: dict[str, str] = {
    "pleco": "Loricariidae", "plecos": "Loricariidae", "plecostomus": "Loricariidae", "plecostomi": "Loricariidae",
    "suckermouth": "Loricariidae", "suckermouths": "Loricariidae",
}


def words(text: str) -> set[str]:
    """Lower-case content words (four letters or more, no generic ones) of ``text``."""
    return {w for w in _WORD.findall(str(text or "").lower()) if w not in _GENERIC}


_CACHE: dict[tuple[str, float, int], Counter[str]] = {}


def kg_vocabulary(files: list[str]) -> Counter[str]:
    """Words of the node names of the KG files, counted by the number of nodes that carry them (cached per file version)."""
    total: Counter[str] = Counter()
    for name in files:
        path = Path(name)
        try:
            stat = path.stat()
        except OSError:
            continue
        key = (str(path), stat.st_mtime, stat.st_size)
        if key not in _CACHE:
            counts: Counter[str] = Counter()
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            nodes = (data.get("nodes") or data.get("things") or []) if isinstance(data, dict) else []
            for node in nodes[:MAX_VOCAB_NODES]:
                if isinstance(node, dict):
                    counts.update(words(f"{node.get('name') or node.get('label') or ''} {node.get('category') or ''}"))
            _CACHE.clear() if len(_CACHE) > 8 else None
            _CACHE[key] = counts
        total.update(_CACHE[key])
    return total


def expand_common_names(question: str, vocabulary: Counter[str]) -> str:
    """The question with the scientific name added after a common name the KG's vocabulary can answer.

    ``tell me about pleco catfish`` becomes ``tell me about pleco catfish (Loricariidae)`` when the KG has Loricariidae.
    A question with no such word, or a KG without the scientific name, is returned unchanged.
    """
    added: list[str] = []
    for token in re.findall(r"[a-z]+", question.lower()):
        scientific = COMMON_NAMES.get(token)
        if scientific and vocabulary.get(scientific.lower(), 0) and scientific not in added and scientific.lower() not in question.lower():
            added.append(scientific)
    return f"{question} ({', '.join(added)})" if added else question


def shared_kg_words(candidate: dict[str, Any], vocabulary: Counter[str]) -> set[str]:
    """KG-vocabulary words (see :data:`MIN_NODE_COUNT`) that occur in the candidate's title and abstract."""
    text = f"{candidate.get('title') or ''} {candidate.get('abstract') or ''}"
    return {w for w in words(text) if vocabulary.get(w, 0) >= MIN_NODE_COUNT}


def filter_relevant(candidates: list[dict[str, Any]], vocabulary: Counter[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(kept, dropped)``: a candidate is kept when it shares at least :data:`MIN_SHARED_KG_WORDS` words with the KG.

    A KG with too small a vocabulary (see :data:`MIN_VOCABULARY_WORDS`) keeps every candidate, as before.
    """
    if sum(1 for count in vocabulary.values() if count >= MIN_NODE_COUNT) < MIN_VOCABULARY_WORDS:
        return list(candidates), []
    kept, dropped = [], []
    for candidate in candidates:
        (kept if len(shared_kg_words(candidate, vocabulary)) >= MIN_SHARED_KG_WORDS else dropped).append(candidate)
    return kept, dropped


# -- the reply to a pending download offer ------------------------------------------------------------------

_DECLINE_CLAUSE = re.compile(
    r"^(?:no|nope|nah|none|none of (?:these|them|those|the above)|neither|no thanks|no thank you|cancel|decline|skip|skip all|skip it|skip download|stop|"
    r"(?:please )?(?:do not|don't|dont) (?:download|fetch|get)(?: (?:any|a|the|anything|them|it|those|that))?(?: papers?)?|no download|no downloads|"
    r"(?:i )?(?:do not|don't|dont) want (?:any|a|the) (?:papers?|downloads?)(?: downloaded)?)\W*$")
_LEAD = re.compile(r"^(?:but|and|instead|just|now|then|so|please)\b[\s,]*")

#: Words that make a reply a decision about the offered papers rather than a new question.
ABOUT_DOWNLOAD = frozenset("""download fetch grab candidate candidates paper papers option options first second third fourth fifth
recommended another next different doi arxiv yes yep approve approved okay sure ok continue""".split())


def _is_decline(clause: str) -> bool:
    normal = re.sub(r"\s+", " ", clause.lower().replace("\u2019", "'")).strip()
    return bool(_DECLINE_CLAUSE.match(normal))


def split_decline(message: str) -> tuple[bool, str]:
    """``(declines, rest)``: whether the message refuses the offer, and what else it says.

    ``do not download. tell me about Loricariidae`` is ``(True, "tell me about Loricariidae")``; ``no`` is ``(True, "")``;
    ``tell me about them`` is ``(False, "tell me about them")``.
    """
    clauses = [c.strip() for c in re.split(r"[.;!?\n]+", message) if c and c.strip()]
    declined = False
    rest: list[str] = []
    for clause in clauses:
        head, comma, tail = clause.partition(",")
        if comma and _is_decline(head):
            declined = True
            clause = tail.strip()
            if not clause:
                continue
        elif _is_decline(clause):
            declined = True
            continue
        rest.append(_LEAD.sub("", clause).strip())
    # "do not download any paper, but what is ...": the decline clause may end in ", but".
    cleaned = []
    for part in rest:
        match = re.match(r"^(?P<d>[^,]*?),\s*(?:but|and|instead)\s+(?P<q>.+)$", part, re.I)
        if match and _is_decline(match.group("d")):
            declined = True
            part = match.group("q")
        cleaned.append(part)
    rest = cleaned
    return declined, " ".join(part for part in rest if part).strip()


def is_about_download(message: str, candidates: list[dict[str, Any]]) -> bool:
    """Whether the reply speaks about the offered papers (a word of the offer, a number, or a title it shares words with)."""
    tokens = set(re.findall(r"[a-z0-9']+", message.lower()))
    if tokens & ABOUT_DOWNLOAD or re.search(r"\b10\.\d{4,}/", message.lower()) or re.search(r"#\s*\d", message):
        return True
    asked = words(message)
    for candidate in candidates:
        title = words(str(candidate.get("title") or ""))
        if title and len(asked & title) >= 2:
            return True
    return False
