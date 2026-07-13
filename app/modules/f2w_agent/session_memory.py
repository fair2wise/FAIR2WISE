"""Session memory for FAIR2WISE chat and KG growth context.

This is *conversational* memory only. It is injected into the router,
question-rewrite, and direct-answer prompts to keep multi-turn chat seamless.
It is deliberately never injected into RetrievalAgent or grounded-answer
prompts, so it cannot introduce hallucinated evidence or citations.

v2 adds structure to reduce cross-topic smear:
  - each recorded turn carries structured references (node_ids, publication
    title/doi) and a topic_id
  - lightweight lexical topic segmentation groups turns; context is scoped to
    the current topic so stale, unrelated context does not leak into routing
  - a constrained LLM ``compress`` pass replaces the append-only summary once it
    grows, with an explicit no-invented-metadata rule
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional


MAX_SUMMARY_CHARS = 3000
MAX_RECENT_TURNS = 12
MAX_KG_EVENTS = 12
MAX_OPEN_QUESTIONS = 10
MAX_ENTITIES = 40
MAX_TOPICS = 12
MAX_PUB_REFS = 6

# Topic stays "current" while question entities overlap the topic's entities.
TOPIC_OVERLAP_MIN = 0.15
# Run the constrained compression pass once the raw summary crosses this size.
COMPRESS_TRIGGER_CHARS = 2400

_ENTITY_STOPWORDS = {
    "about",
    "after",
    "again",
    "agent",
    "answer",
    "assistant",
    "because",
    "before",
    "between",
    "could",
    "evidence",
    "extract",
    "from",
    "graph",
    "grounded",
    "into",
    "knowledge",
    "material",
    "materials",
    "paper",
    "papers",
    "question",
    "retrieval",
    "session",
    "should",
    "that",
    "their",
    "there",
    "these",
    "thing",
    "this",
    "those",
    "through",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip(text: Any, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _unique(values: Iterable[Any], *, limit: int) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for value in values:
        text = _clip(value, 120)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _default_memory() -> Dict[str, Any]:
    return {
        "version": 2,
        "summary": "",
        "open_questions": [],
        "important_entities": [],
        "recent_turns": [],
        "kg_growth": [],
        "topics": [],
        "current_topic_id": None,
        "updated_at": None,
    }


def _extract_entities(*texts: Any) -> List[str]:
    candidates: List[str] = []
    for text in texts:
        for token in re.findall(r"\b[A-Za-z][A-Za-z0-9_./+-]{2,}\b", str(text or "")):
            clean = token.strip(".,;:()[]{}")
            lower = clean.casefold()
            if lower in _ENTITY_STOPWORDS:
                continue
            if "_" in clean or "/" in clean or any(ch.isdigit() for ch in clean) or clean[:1].isupper():
                candidates.append(clean)
            elif len(clean) >= 8:
                candidates.append(clean)
    return _unique(candidates, limit=MAX_ENTITIES)


def _publication_refs(publications: Optional[List[Dict[str, Any]]]) -> List[Dict[str, str]]:
    refs: List[Dict[str, str]] = []
    for pub in publications or []:
        if not isinstance(pub, dict):
            continue
        title = _clip(pub.get("paper_title") or pub.get("source_paper"), 160)
        doi = _clip(pub.get("doi"), 120)
        if not title and not doi:
            continue
        refs.append({"paper_title": title, "doi": doi})
        if len(refs) >= MAX_PUB_REFS:
            break
    return refs


def _round_growth_event(
    *,
    query: str,
    round_info: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    extraction = round_info.get("extraction")
    kg = round_info.get("kg")
    if not isinstance(extraction, dict) and not isinstance(kg, dict):
        return None
    retrieval = round_info.get("retrieval") if isinstance(round_info.get("retrieval"), dict) else {}
    event = {
        "timestamp": _now(),
        "query": _clip(query, 300),
        "round": round_info.get("round"),
        "missing_topics": _unique(retrieval.get("missing_topics") or [], limit=8),
        "pdfs": _unique(round_info.get("pending_pdfs") or [], limit=8),
    }
    if isinstance(extraction, dict):
        event.update(
            {
                "extraction_mode": extraction.get("extraction_mode") or "targeted",
                "unique_terms": extraction.get("unique_terms"),
                "processed_files": extraction.get("processed_files"),
                "processed_pages_total": extraction.get("processed_pages_total"),
                "processed_pages_with_terms": extraction.get("processed_pages_with_terms"),
            }
        )
    if isinstance(kg, dict):
        event.update(
            {
                "kg_nodes": kg.get("nodes"),
                "kg_edges": kg.get("edges"),
                "kg_status": kg.get("status"),
            }
        )
    return {key: value for key, value in event.items() if value not in (None, "", [])}


def _parse_json_object(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    match = re.search(r"\{[\s\S]*\}", raw)
    if not match:
        return {}
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


class SessionMemory:
    """Small JSON-backed memory scoped to one ``workdir``."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            return _default_memory()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return _default_memory()
        if not isinstance(raw, dict):
            return _default_memory()
        data = _default_memory()
        data.update(raw)
        for key in ("open_questions", "important_entities", "recent_turns", "kg_growth", "topics"):
            if not isinstance(data.get(key), list):
                data[key] = []
        data["summary"] = _clip(data.get("summary"), MAX_SUMMARY_CHARS)
        data["version"] = 2
        return data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def clear(self) -> None:
        self.data = _default_memory()
        self.save()

    def has_context(self) -> bool:
        return bool(
            self.data.get("summary")
            or self.data.get("open_questions")
            or self.data.get("important_entities")
            or self.data.get("kg_growth")
            or self.data.get("recent_turns")
        )

    # ------------------------------------------------------------------
    # Topic segmentation
    # ------------------------------------------------------------------
    def _current_topic(self) -> Optional[Dict[str, Any]]:
        current_id = self.data.get("current_topic_id")
        if not current_id:
            return None
        for topic in self.data.get("topics") or []:
            if isinstance(topic, dict) and topic.get("id") == current_id:
                return topic
        return None

    def _assign_topic(self, question: str) -> str:
        """Return the topic id for ``question``, starting a new topic on a shift."""
        question_entities = _extract_entities(question)
        question_keys = {e.casefold() for e in question_entities}
        topics = self.data.get("topics") or []
        current = self._current_topic()

        same_topic = False
        if current is not None:
            current_keys = {str(e).casefold() for e in (current.get("entities") or [])}
            if not question_keys or not current_keys:
                # No comparable entities (e.g. "why?") -> stay on current topic.
                same_topic = True
            else:
                overlap = len(question_keys & current_keys) / len(question_keys)
                same_topic = overlap >= TOPIC_OVERLAP_MIN

        if same_topic and current is not None:
            current["entities"] = _unique(
                [*(current.get("entities") or []), *question_entities],
                limit=MAX_ENTITIES,
            )
            current["turn_count"] = int(current.get("turn_count") or 0) + 1
            self.data["current_topic_id"] = current["id"]
            return current["id"]

        topic_id = f"topic-{uuid.uuid4().hex[:12]}"
        topics.append(
            {
                "id": topic_id,
                "label": _clip(question, 80),
                "entities": question_entities,
                "started_at": _now(),
                "turn_count": 1,
            }
        )
        self.data["topics"] = topics[-MAX_TOPICS:]
        self.data["current_topic_id"] = topic_id
        return topic_id

    # ------------------------------------------------------------------
    # Prompt context (topic-scoped)
    # ------------------------------------------------------------------
    def context_block(self, *, max_chars: int = 2600) -> str:
        """Return prompt context. This is conversational memory, not evidence."""
        if not self.has_context():
            return ""
        current_id = self.data.get("current_topic_id")
        current = self._current_topic()
        lines: List[str] = []
        summary = _clip(self.data.get("summary"), 1000)
        if summary:
            lines.append(f"Summary: {summary}")
        open_questions = _unique(self.data.get("open_questions") or [], limit=5)
        if open_questions:
            lines.append("Open questions: " + "; ".join(open_questions))

        entity_source = (current or {}).get("entities") if current else None
        entities = _unique(entity_source or self.data.get("important_entities") or [], limit=16)
        if entities:
            lines.append("Important entities: " + ", ".join(entities))

        growth_lines: List[str] = []
        for event in (self.data.get("kg_growth") or [])[-3:]:
            if not isinstance(event, dict):
                continue
            parts = [
                f"query={_clip(event.get('query'), 120)}",
                f"pdfs={len(event.get('pdfs') or [])}",
            ]
            if event.get("unique_terms") is not None:
                parts.append(f"terms={event.get('unique_terms')}")
            if event.get("kg_nodes") is not None:
                parts.append(f"kg_nodes={event.get('kg_nodes')}")
            growth_lines.append(", ".join(parts))
        if growth_lines:
            lines.append("Recent KG growth: " + " | ".join(growth_lines))

        recent_turns = []
        for turn in self._current_topic_turns()[-4:]:
            role = str(turn.get("role") or "").strip()
            content = _clip(turn.get("content"), 180)
            if role and content:
                recent_turns.append(f"{role}: {content}")
        if recent_turns:
            lines.append("Recent turns: " + " | ".join(recent_turns))
        return _clip("\n".join(lines), max_chars)

    def _current_topic_turns(self) -> List[Dict[str, Any]]:
        current_id = self.data.get("current_topic_id")
        turns = [t for t in (self.data.get("recent_turns") or []) if isinstance(t, dict)]
        if not current_id:
            return turns
        scoped = [t for t in turns if t.get("topic_id") in (current_id, None)]
        # If nothing is tagged for the current topic (legacy data), fall back.
        return scoped or turns

    def memory_section(self) -> str:
        block = self.context_block()
        if not block:
            return "SESSION_MEMORY:\n(none)\n"
        return (
            "SESSION_MEMORY:\n"
            "Use this only for conversational continuity and follow-up resolution. "
            "Do not treat it as KG/paper evidence.\n"
            f"{block}\n"
        )

    def enrich_followup_question(self, question: str) -> str:
        block = self.context_block(max_chars=1800)
        if not block:
            return question
        return (
            f"{question.strip()}\n\n"
            "Session context for resolving references only; do not treat as evidence:\n"
            f"{block}"
        )

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    def record_turn(
        self,
        *,
        user_message: str,
        answer: str,
        status: str,
        sufficient: bool,
        rounds: List[Dict[str, Any]],
        effective_question: Optional[str] = None,
        node_ids: Optional[List[str]] = None,
        publications: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        user_message = _clip(user_message, 1200)
        effective_question = _clip(effective_question or user_message, 1200)
        answer = _clip(answer, 1200)
        now = _now()

        topic_id = self._assign_topic(effective_question)
        pub_refs = _publication_refs(publications)

        recent = list(self.data.get("recent_turns") or [])
        recent.extend(
            [
                {
                    "role": "user",
                    "content": user_message,
                    "effective_question": effective_question if effective_question != user_message else None,
                    "topic_id": topic_id,
                    "timestamp": now,
                },
                {
                    "role": "assistant",
                    "content": answer,
                    "status": status,
                    "sufficient": sufficient,
                    "node_ids": node_ids or [],
                    "publications": pub_refs,
                    "topic_id": topic_id,
                    "timestamp": now,
                },
            ]
        )
        self.data["recent_turns"] = recent[-MAX_RECENT_TURNS:]

        addition = (
            f"User asked: {effective_question}. "
            f"Assistant status: {status}; "
            f"{'KG evidence sufficient' if sufficient else 'KG evidence not sufficient or not used'}. "
            f"Answer: {_clip(answer, 280)}"
        )
        summary = _clip(" ".join(part for part in [self.data.get("summary"), addition] if part), MAX_SUMMARY_CHARS)
        self.data["summary"] = summary

        if not sufficient and status not in {"direct_response", "answered"}:
            self.data["open_questions"] = _unique(
                [effective_question, *(self.data.get("open_questions") or [])],
                limit=MAX_OPEN_QUESTIONS,
            )

        publication_text = " ".join(
            str(pub.get("paper_title") or pub.get("doi") or pub.get("source_paper") or "")
            for pub in (publications or [])
            if isinstance(pub, dict)
        )
        self.data["important_entities"] = _unique(
            [
                *_extract_entities(user_message, effective_question, answer, publication_text),
                *(self.data.get("important_entities") or []),
            ],
            limit=MAX_ENTITIES,
        )

        growth = list(self.data.get("kg_growth") or [])
        for round_info in rounds:
            if isinstance(round_info, dict):
                event = _round_growth_event(query=effective_question, round_info=round_info)
                if event:
                    growth.append(event)
        self.data["kg_growth"] = growth[-MAX_KG_EVENTS:]
        self.data["updated_at"] = now
        self.save()

    # ------------------------------------------------------------------
    # Constrained compression
    # ------------------------------------------------------------------
    def needs_compression(self) -> bool:
        return len(self.data.get("summary") or "") >= COMPRESS_TRIGGER_CHARS

    def compress(self, chat_fn: Callable[[str], str]) -> bool:
        """Replace the append-only summary with a constrained LLM digest.

        ``chat_fn`` takes a prompt and returns raw model text. The prompt forbids
        inventing any bibliographic metadata so compression cannot manufacture
        citations. Best-effort: on any failure the existing summary is kept.
        """
        summary = self.data.get("summary") or ""
        if not summary.strip():
            return False
        entities = ", ".join(_unique(self.data.get("important_entities") or [], limit=MAX_ENTITIES))
        open_questions = "; ".join(_unique(self.data.get("open_questions") or [], limit=MAX_OPEN_QUESTIONS))
        prompt = (
            "Compress this assistant session memory into a compact digest for "
            "conversational continuity only. This is NOT evidence.\n"
            "STRICT RULES:\n"
            "- Preserve entities under discussion and unresolved user questions.\n"
            "- NEVER invent or add authors, DOIs, publication years, journals, or any "
            "other bibliographic metadata. Only keep facts already present below.\n"
            "- Do not fabricate answers or claims.\n"
            "Return ONLY JSON with this schema:\n"
            '{"summary": string, "open_questions": [string, ...], "entities": [string, ...]}\n\n'
            f"CURRENT_SUMMARY:\n{summary}\n\n"
            f"KNOWN_ENTITIES:\n{entities}\n\n"
            f"OPEN_QUESTIONS:\n{open_questions}\n"
        )
        try:
            raw = chat_fn(prompt)
        except Exception:
            return False
        obj = _parse_json_object(str(raw or ""))
        new_summary = _clip(obj.get("summary"), MAX_SUMMARY_CHARS)
        if not new_summary:
            return False
        self.data["summary"] = new_summary
        if isinstance(obj.get("open_questions"), list):
            self.data["open_questions"] = _unique(obj["open_questions"], limit=MAX_OPEN_QUESTIONS)
        if isinstance(obj.get("entities"), list):
            self.data["important_entities"] = _unique(
                [*obj["entities"], *(self.data.get("important_entities") or [])],
                limit=MAX_ENTITIES,
            )
        self.data["updated_at"] = _now()
        self.save()
        return True
