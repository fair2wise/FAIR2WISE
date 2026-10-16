"""A small static model of the Dockerfile and ``.dockerignore`` for the T24B image tests.

It parses instructions (comments, ``\\`` continuations, JSON-form arguments), splits them
into stages, resolves ``FROM <stage>`` inheritance, and evaluates ``.dockerignore``
patterns closely enough for the paths this repository uses (``**``, ``*``, ``?``, ``!``
re-includes, directory prefixes). No Docker is needed.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass
class Instruction:
    keyword: str
    args: str
    line: int

    def words(self) -> List[str]:
        text = self.args.strip()
        if text.startswith("["):
            try:
                return [str(w) for w in json.loads(text)]
            except ValueError:
                pass
        return shlex.split(text, posix=True)


@dataclass
class Stage:
    name: str
    base: str
    instructions: List[Instruction] = field(default_factory=list)

    def of(self, keyword: str) -> List[Instruction]:
        return [i for i in self.instructions if i.keyword == keyword]

    def env(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for ins in self.of("ENV"):
            for word in ins.words():
                if "=" in word:
                    key, value = word.split("=", 1)
                    out[key] = value
        return out

    def labels(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for ins in self.of("LABEL"):
            for word in ins.words():
                if "=" in word:
                    key, value = word.split("=", 1)
                    out[key] = value
        return out

    def copies(self) -> List[Tuple[Optional[str], List[str], str]]:
        """``(from_stage, sources, dest)`` for each COPY/ADD."""
        out = []
        for ins in self.instructions:
            if ins.keyword not in ("COPY", "ADD"):
                continue
            words = ins.words()
            source_stage = None
            plain = []
            for word in words:
                if word.startswith("--from="):
                    source_stage = word.split("=", 1)[1]
                elif word.startswith("--"):
                    continue
                else:
                    plain.append(word)
            out.append((source_stage, plain[:-1], plain[-1]))
        return out


@dataclass
class Dockerfile:
    global_args: Dict[str, str]
    stages: List[Stage]

    @property
    def names(self) -> List[str]:
        return [s.name for s in self.stages]

    def stage(self, name: str) -> Stage:
        for stage in self.stages:
            if stage.name == name:
                return stage
        raise KeyError(name)

    def lineage(self, name: str) -> List[Stage]:
        """The stage and every stage it is built ``FROM``, nearest first."""
        chain = [self.stage(name)]
        while chain[-1].base in self.names:
            chain.append(self.stage(chain[-1].base))
        return chain

    def effective(self, name: str, keyword: str) -> List[Instruction]:
        """Instructions of ``keyword`` from the whole lineage, oldest first."""
        return [i for s in reversed(self.lineage(name)) for i in s.of(keyword)]

    def effective_env(self, name: str) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for stage in reversed(self.lineage(name)):
            out.update(stage.env())
        return out

    def last(self, name: str, keyword: str) -> Optional[Instruction]:
        found = self.effective(name, keyword)
        return found[-1] if found else None

    def copied_from_stages(self, name: str) -> List[str]:
        return [src for s in self.lineage(name) for src, _, _ in s.copies() if src]

    def context_sources(self, name: str) -> List[str]:
        """Build-context paths copied into the final image (not from other stages)."""
        return [p for s in self.lineage(name) for src, paths, _ in s.copies() if src is None for p in paths]


def parse_dockerfile(text: str) -> Dockerfile:
    logical: List[Tuple[int, str]] = []
    buffer, start = "", 0
    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not buffer and (not stripped or stripped.startswith("#")):
            continue
        if buffer and stripped.startswith("#"):
            continue
        if not buffer:
            start = number
        if stripped.endswith("\\"):
            buffer += stripped[:-1].rstrip() + " "
            continue
        logical.append((start, buffer + stripped))
        buffer = ""
    global_args: Dict[str, str] = {}
    stages: List[Stage] = []
    for number, line in logical:
        keyword, _, args = line.partition(" ")
        keyword = keyword.upper()
        if keyword == "FROM":
            words = args.split()
            base = words[0]
            name = words[2] if len(words) >= 3 and words[1].upper() == "AS" else f"stage{len(stages)}"
            stages.append(Stage(name=name, base=base))
        elif not stages:
            if keyword == "ARG" and "=" in args:
                key, value = args.strip().split("=", 1)
                global_args[key] = value
        else:
            stages[-1].instructions.append(Instruction(keyword, args, number))
    return Dockerfile(global_args, stages)


# --------------------------------------------------------------------------- dockerignore


def _pattern_regex(pattern: str) -> "re.Pattern[str]":
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    # A pattern also excludes everything below a matching directory.
    return re.compile(f"^{out}(?:/.*)?$")


def parse_dockerignore(text: str) -> List[Tuple[bool, "re.Pattern[str]", str]]:
    rules = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if negate:
            line = line[1:].strip()
        line = line.lstrip("/").rstrip("/")
        rules.append((negate, _pattern_regex(line), line))
    return rules


def ignored(path: str, rules) -> bool:
    """Docker semantics: the last matching rule wins."""
    result = False
    for negate, regex, _ in rules:
        if regex.match(path):
            result = not negate
    return result


def image_paths(dockerfile: Dockerfile, target: str, context_files: Iterable[str], rules) -> List[str]:
    """Context files that land in ``target`` through its COPY lines after .dockerignore."""
    sources = [s.rstrip("/") for s in dockerfile.context_sources(target)]
    chosen = []
    for path in context_files:
        if ignored(path, rules):
            continue
        if any(src in (".", "") or path == src or path.startswith(src + "/") for src in sources):
            chosen.append(path)
    return chosen


def load(repo: Path) -> Tuple[Dockerfile, list]:
    return (parse_dockerfile((repo / "Dockerfile").read_text(encoding="utf-8")),
            parse_dockerignore((repo / ".dockerignore").read_text(encoding="utf-8")))
