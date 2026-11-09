"""Parsers for the beamline blueprint HTML export (devices, stages, beam-path order, people)."""

from __future__ import annotations

from collections.abc import Sequence
from html import unescape
from pathlib import Path
import re
from typing import Any

from lxml import html as lhtml

from .common import LOGGER, _texts, _xp_elems, _xp_strs, should_skip_file, strip_tags
from .parse_als import _clean_person_name
from .paths import BLUEPRINT_PATH
from .specs import (
    BCS2SIM_PATH_GLOBS,
    BEAM_PATH_CHAMBER_TAIL,
    BEAM_PATH_PREFIX,
    BLUEPRINT_PERSON_RE,
    KIND_CATEGORY,
    STAGE_NAME_ALIASES,
    BlueprintDevice,
    BlueprintStage,
)


def parse_blueprint_people(blueprint: dict[str, Any], html_text: str = "") -> list[dict[str, str]]:
    """Names the Gabe blueprint actually writes (e.g. reviewed with Thomas Ferron)."""
    header = blueprint.get("header") or {}
    blobs = [
        header.get("sources") or "",
        " ".join(str(v) for v in header.values()),
        html_text,
    ]
    people: list[dict[str, str]] = []
    seen = set()
    for blob in blobs:
        if not blob:
            continue
        for match in BLUEPRINT_PERSON_RE.finditer(blob):
            name = _clean_person_name(match.group(1))
            if not name or name.casefold() in seen:
                continue
            seen.add(name.casefold())
            people.append(
                {
                    "name": name,
                    "email": "",
                    "role": "BeamlineScientist",
                    "source": BLUEPRINT_PATH.name,
                    "evidence": match.group(0).strip()[:240],
                }
            )
    return people


def _ophyd_name_from_handle(handle: str, fallback: str) -> tuple[str, str]:
    match = re.search(r'd\.(\w+)\[\s*["\']([^"\']+)["\']\s*\]', handle or "")
    if match:
        return match.group(2), match.group(1)
    return fallback, ""


def canonical_stage_name(name: str) -> str:
    """Map SVG / caption / prose aliases onto a stable BeamlineStage name."""
    raw = re.sub(r"\s+", " ", (name or "").strip())
    if not raw:
        return ""
    key = raw.casefold()
    key = key.replace("kirkpatrick–baez", "m103").replace("kirkpatrick-baez", "m103")
    return STAGE_NAME_ALIASES.get(key, raw)


def _svg_coord(el: Any, attr: str) -> float:  # noqa: ANN401  # Any: lxml element (untyped third-party object)
    try:
        return float(el.get(attr) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def parse_sheet_a_svg_order(tree: object) -> list[str]:
    """Sheet A SVG left-to-right, top-to-bottom. This is beam order, not A′ DOM."""
    labels: list[tuple[float, float, str]] = []
    for text_el in _xp_elems(tree, "//section[@id='sheet-a']//figure//svg//text[contains(@class,'big')]"):
        name = canonical_stage_name(re.sub(r"\s+", " ", " ".join(_texts(text_el))).strip())
        if not name:
            continue
        # "Sample · θ = 0" belongs to sheet B; ignore if it leaked.
        if name.casefold().startswith("sample ·"):
            continue
        labels.append((_svg_coord(text_el, "y"), _svg_coord(text_el, "x"), name))
    labels.sort(key=lambda item: (item[0], item[1]))
    ordered: list[str] = []
    seen = set()
    for _, _, name in labels:
        if name in seen:
            continue
        seen.add(name)
        ordered.append(name)
    return ordered


def parse_arrow_sequence(text: str) -> list[str]:
    """Pull a documented A → B → C beam-path chain out of prose or a figcaption."""
    blob = re.sub(r"\s+", " ", unescape(text or ""))
    blob = blob.replace("->", "→").replace("⟶", "→")
    if "→" not in blob:
        return []
    match = re.search(r"((?:[^→]{1,60}→\s*){2,}[^→.]{1,60})", blob)
    if not match:
        return []
    names: list[str] = []
    seen = set()
    for token in match.group(1).split("→"):
        name = canonical_stage_name(token.strip(" :;,"))
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def parse_figcaption_beam_path(tree: object) -> list[str]:
    """Return the beam-path chain documented in the Sheet A figcaptions, or ``[]`` if none."""
    captions = _xp_elems(tree, "//section[@id='sheet-a']//figcaption")
    text = " ".join(re.sub(r"\s+", " ", " ".join(_texts(el))) for el in captions)
    return parse_arrow_sequence(text)


def parse_bcs2sim_beam_path(root: Path | None) -> list[str]:
    """Reuse Gabe's map if bcs2sim checked in the same knowledge/blueprint text."""
    if root is None or not root.exists():
        return []
    for pattern in BCS2SIM_PATH_GLOBS:
        for path in sorted(root.glob(pattern)):
            if not path.is_file() or should_skip_file(path):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                LOGGER.debug("unreadable file skipped while scanning for M103: %s", type(exc).__name__)
                continue
            if "M103" not in text or not re.search(r"exit\s+slits?", text, re.I):
                continue
            names = parse_arrow_sequence(text)
            if names:
                LOGGER.info("bcs2sim beam path from %s (%d stages)", path, len(names))
                return names
    return []


def _index_ci(names: Sequence[str], needle: str) -> int:
    target = needle.casefold()
    for i, name in enumerate(names):
        if target in name.casefold():
            return i
    return -1


def ensure_m103_before_exit_slits(names: Sequence[str]) -> list[str]:
    """Prose puts M103 between mono and the exit slits; A.5 before A.6 is not optical order."""
    ordered = list(names)
    i_m = _index_ci(ordered, "m103")
    i_e = _index_ci(ordered, "exit slit")
    if i_m >= 0 and i_e >= 0 and i_m > i_e:
        item = ordered.pop(i_m)
        ordered.insert(i_e, item)
    return ordered


def finalize_beam_path(names: Sequence[str]) -> list[str]:
    """Prefix source/EPU/M101, replace a trailing Chamber with sample → detector."""
    ordered = ensure_m103_before_exit_slits(names)
    if not ordered:
        return []
    lowered = [n.casefold() for n in ordered]
    prefix: list[str] = []
    for name in BEAM_PATH_PREFIX:
        if name.casefold() not in lowered:
            prefix.append(name)
        else:
            break
    ordered = prefix + ordered
    if ordered and ordered[-1].casefold() == "chamber":
        ordered = ordered[:-1]
    lowered = [n.casefold() for n in ordered]
    for name in BEAM_PATH_CHAMBER_TAIL:
        if name.casefold() not in lowered:
            ordered.append(name)
    seen = set()
    unique: list[str] = []
    for name in ordered:
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(name)
    return ensure_m103_before_exit_slits(unique)


def resolve_beam_path_order(
    *,
    svg_names: Sequence[str],
    caption_names: Sequence[str],
    bcs2sim_names: Sequence[str] = (),
    stage_names: Sequence[str] = (),
) -> list[str]:
    """Prefer Sheet A SVG, then figcaption / bcs2sim. Never A′ DOM order as the path."""
    if svg_names:
        return finalize_beam_path(svg_names)
    if caption_names:
        return finalize_beam_path(caption_names)
    if bcs2sim_names:
        return finalize_beam_path(bcs2sim_names)
    # stage_names is A′ DOM (A.5 exit slits before A.6 M103). Do not use it.
    _ = stage_names
    return []


def parse_blueprint_html(html_text: str) -> dict[str, Any]:
    """Parse Gabe's BL 11.0.1.2 device blueprint (titleblock, stages, sheet E, beam path)."""
    tree = lhtml.fromstring(html_text)
    header: dict[str, str] = {}
    for block in _xp_elems(tree, "//header[contains(@class,'titleblock')]/div"):
        key = " ".join(_xp_strs(block, ".//*[contains(@class,'k')]/text()")).strip()
        val = " ".join(_xp_strs(block, ".//*[contains(@class,'v')]/text()")).strip()
        if key:
            header[key] = val
    sources = " ".join(_xp_strs(tree, "//*[contains(@class,'sources')]//text()"))
    header["sources"] = re.sub(r"\s+", " ", sources).strip()

    devices: dict[str, BlueprintDevice] = {}
    for row in _xp_elems(tree, "//table[@id='corr']//tr[@id]"):
        dev_id = (row.get("id") or "").replace("dev-", "")
        tds = _xp_elems(row, "./td")
        if len(tds) < 6:
            continue
        drawn = " ".join(_texts(tds[0])).strip()
        name = " ".join(_texts(tds[1])).strip()
        ioc = " ".join(_texts(tds[2])).strip()
        pv = " ".join(_xp_strs(tds[3], ".//span[contains(@class,'pv')]/text()")).strip()
        handle = " ".join(_xp_strs(tds[4], ".//span[contains(@class,'hd')]/text()")).strip()
        ophyd_class = " ".join(_texts(tds[5])).strip()
        mapping = " ".join(_texts(tds[6])).strip() if len(tds) > 6 else ""
        tip = unescape(" ".join(_xp_strs(tds[1], ".//*[@data-tip]/@data-tip")))
        kind = strip_tags(
            " ".join(re.findall(r'tip-k">([^<]+)', tip)) or ""
        ).lower()
        meaning = strip_tags(" ".join(re.findall(r'tip-m">([^<]+)', tip)))
        if not kind:
            if "detector" in (ophyd_class + name).lower():
                kind = "detector"
            elif "dio" in ophyd_class.lower() or "digital" in ophyd_class.lower():
                kind = "digital i/o"
            elif "ai" in ophyd_class.lower() or "analog" in ophyd_class.lower():
                kind = "analog input"
            else:
                kind = "motor"
        ophyd_name, collection = _ophyd_name_from_handle(handle, dev_id)
        devices[dev_id] = BlueprintDevice(
            dev_id=dev_id,
            name=name or ophyd_name,
            kind=kind,
            category=KIND_CATEGORY.get(kind, "Device"),
            pv=pv,
            handle=handle,
            ophyd_name=ophyd_name,
            ophyd_class=ophyd_class,
            ioc_record=ioc,
            mapping_file=mapping,
            drawn=drawn,
            meaning=meaning,
            collection=collection,
        )

    stages: list[BlueprintStage] = []
    for stage_el in _xp_elems(tree, "//*[contains(@class,'stage')]"):
        name = " ".join(_xp_strs(stage_el, ".//*[contains(@class,'stage-name')]/text()")).strip()
        if not name:
            continue
        section = " ".join(_xp_strs(stage_el, ".//*[contains(@class,'stage-id')]/text()")).strip()
        what = strip_tags(" ".join(_xp_strs(stage_el, ".//*[contains(@class,'stage-what')]//text()")))
        does = strip_tags(" ".join(_xp_strs(stage_el, ".//*[contains(@class,'stage-does')]//text()")))
        range_text = strip_tags(" ".join(_xp_strs(stage_el, ".//*[contains(@class,'stage-range')]//text()")))
        hrefs = [h.replace("#dev-", "") for h in _xp_strs(stage_el, ".//a/@href") if h.startswith("#dev-")]
        stages.append(
            BlueprintStage(
                name=name,
                section=section,
                what=what,
                does=does,
                range_text=range_text,
                device_ids=sorted(set(hrefs)),
            )
        )

    rules = []
    for rule in _xp_elems(tree, "//*[contains(@class,'rule')]"):
        key = " ".join(_xp_strs(rule, ".//*[contains(@class,'rule-k')]/text()")).strip()
        body = strip_tags(" ".join(_xp_strs(rule, ".//text()")))
        if key or body:
            rules.append({"name": key, "text": body})

    svg_names = parse_sheet_a_svg_order(tree)
    caption_names = parse_figcaption_beam_path(tree)
    beam_path = resolve_beam_path_order(
        svg_names=svg_names,
        caption_names=caption_names,
        stage_names=[stage.name for stage in stages],
    )
    parsed_bp = {
        "header": header,
        "devices": devices,
        "stages": stages,
        "rules": rules,
        "beam_note": header.get("sources", ""),
        "beam_path": beam_path,
        "beam_path_svg": svg_names,
        "beam_path_caption": caption_names,
    }
    parsed_bp["people"] = parse_blueprint_people(parsed_bp, html_text)
    return parsed_bp
