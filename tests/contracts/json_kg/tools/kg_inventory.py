"""Inventory KG JSON files without copying any text content. Lengths/counts only."""
import hashlib, json, re, sys, pathlib, statistics, collections

THRESH = (200, 500, 1000, 2000)
PATH_RE = re.compile(r"(/Users/|/home/|/private/|/tmp/|/global/|/pscratch/|/scratch/|[A-Za-z]:\\\\|file://)")
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|(?i:api[_-]?key)\s*[=:]|(?i:bearer)\s+[A-Za-z0-9._\-]{20,}|(?i:password)\s*[=:])")
CORE10 = {"id","name","category","description","pages","source_papers","context_snippets","formula","formula_validation","properties"}

def lens_summary(lengths):
    if not lengths:
        return {"count": 0}
    s = sorted(lengths)
    out = {"count": len(s), "max": s[-1], "median": int(statistics.median(s)),
           "p95": s[min(len(s)-1, int(0.95*len(s)))]}
    for t in THRESH:
        out[f"ge_{t}"] = sum(1 for x in s if x >= t)
    return out

def walk_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from walk_strings(k); yield from walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_strings(v)

def classify(g, node_keys):
    if not isinstance(g, dict):
        return "not_object"
    if "nodes" in g and "edges" in g and "things" not in g:
        return "nodes_edges_papers_prov (ingest_rsoxs_v3 style; not json2kg output)"
    things = g.get("things") or []
    if not things and not (g.get("associations") or []):
        return "empty_things_associations_placeholder"
    if set(node_keys) == CORE10:
        return "legacy_json2kg_core10 (app/modules/legacy/json2kg.py shape)"
    if "publications" in node_keys and "source_metadata" in node_keys:
        return "json2kg_publication_aware" + ("+metadata" if isinstance(g.get("metadata"), dict) else "")
    if "publication_year" in node_keys:
        return "json2kg_pre_publications_variant"
    return "other_things_associations"

def inventory(path, tracked_blob=None):
    raw = path.read_bytes()
    rec = {"path": None, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    if tracked_blob: rec["git_blob"] = tracked_blob
    if not path.name.endswith((".json", ".json.bak")):
        rec["shape_family"] = "non_json (html viewer)"
        return rec
    g = json.loads(raw.decode("utf-8"))
    nodes = (g.get("things") if "things" in g else g.get("nodes")) or []
    edges = (g.get("associations") if "associations" in g else g.get("edges")) or []
    node_keys = collections.Counter(k for n in nodes for k in n)
    edge_keys = collections.Counter(k for e in edges for k in e)
    rec["top_level_keys"] = sorted(g.keys())
    rec["shape_family"] = classify(g, node_keys)
    rec["node_count"] = len(nodes); rec["edge_count"] = len(edges)
    rec["node_keys"] = dict(sorted(node_keys.items()))
    rec["edge_keys"] = dict(sorted(edge_keys.items()))
    rec["has_top_level_metadata"] = isinstance(g.get("metadata"), dict)
    rec["metadata_keys"] = sorted(g["metadata"].keys()) if isinstance(g.get("metadata"), dict) else []
    rec["has_prov_metadata"] = "prov_metadata" in g or any("prov_metadata" in n for n in nodes)
    gs = (g.get("metadata") or {}).get("graph_snapshot") if isinstance(g.get("metadata"), dict) else None
    if isinstance(gs, dict) and gs.get("sha256"):
        rec["embedded_graph_snapshot_sha256_matches_file"] = gs["sha256"] == rec["sha256"]
    cats = collections.Counter(str(n.get("category", n.get("entityType"))) for n in nodes)
    rec["codesnippet_nodes"] = cats.get("CodeSnippet", 0)
    rec["predicates_distinct"] = len({e.get("predicate") for e in edges})
    # Text-length measurements (no content copied)
    ev = [len(e["has_evidence"]) for e in edges if isinstance(e.get("has_evidence"), str)]
    ev_multi = sum(1 for e in edges if isinstance(e.get("has_evidence"), str) and "; " in e["has_evidence"])
    ctx = []
    ctx_by_paper = collections.Counter()
    ctx_distinct = set()
    for n in nodes:
        for c in (n.get("context_snippets") or []):
            text = c.get("text") if isinstance(c, dict) else (c if isinstance(c, str) else None)
            if isinstance(text, str):
                ctx.append(len(text))
                key = (str(c.get("source_paper")) if isinstance(c, dict) else "?", text)
                if key not in ctx_distinct:
                    ctx_distinct.add(key)
                    ctx_by_paper[key[0]] += len(text)
    desc = [len(n["description"]) for n in nodes if isinstance(n.get("description"), str)]
    abst = [len(n["abstract_text"]) for n in nodes if isinstance(n.get("abstract_text"), str)]
    code = [len(n["code_snippet"]) for n in nodes if isinstance(n.get("code_snippet"), str)]
    fst = [len(f["feature_source_text"]) for n in nodes for f in (n.get("domain_features") or [])
           if isinstance(f, dict) and isinstance(f.get("feature_source_text"), str)]
    rec["text_lengths_chars"] = {
        "edge.has_evidence": lens_summary(ev),
        "node.context_snippets[].text": lens_summary(ctx),
        "node.description": lens_summary(desc),
        "node.abstract_text": lens_summary(abst),
        "node.code_snippet": lens_summary(code),
        "node.domain_features[].feature_source_text": lens_summary(fst),
    }
    rec["edge_has_evidence_multi_joined"] = ev_multi
    rec["edge_has_evidence_non_null"] = len(ev)
    rec["context_text_distinct_values"] = len(ctx_distinct)
    rec["context_text_distinct_total_chars"] = sum(ctx_by_paper.values())
    rec["context_text_max_distinct_chars_for_one_source_paper"] = max(ctx_by_paper.values() or [0])
    long_desc = collections.Counter(str(n.get("category", n.get("entityType"))) for n in nodes
                                    if isinstance(n.get("description"), str) and len(n["description"]) >= 500)
    rec["description_ge_500_by_category"] = dict(long_desc)
    rec["distinct_abstract_texts"] = len({n["abstract_text"] for n in nodes if isinstance(n.get("abstract_text"), str) and n["abstract_text"]})
    sp = [s for n in nodes for s in (n.get("source_papers") or n.get("papers") or []) if isinstance(s, str)]
    rec["distinct_source_papers"] = len(set(sp))
    rec["source_paper_values_with_slash"] = sum(1 for s in set(sp) if "/" in s or "\\" in s)
    strs = list(walk_strings(g))
    rec["strings_matching_local_path_pattern"] = sum(1 for s in strs if PATH_RE.search(s))
    rec["strings_matching_secret_pattern"] = sum(1 for s in strs if SECRET_RE.search(s))
    hit_fields = collections.Counter()
    for n in nodes:
        for k, v in n.items():
            for sv in walk_strings(v):
                if PATH_RE.search(sv): hit_fields["local_path@node." + k] += 1
                if SECRET_RE.search(sv): hit_fields["secret_like@node." + k] += 1
    rec["pattern_hits_by_field"] = dict(hit_fields)
    rec["strings_with_http_url"] = sum(1 for s in strs if "http://" in s or "https://" in s)
    return rec

def main():
    root = pathlib.Path(sys.argv[1]); blobs_file = pathlib.Path(sys.argv[2]); out = pathlib.Path(sys.argv[3])
    extra_root = pathlib.Path(sys.argv[4]) if len(sys.argv) > 4 else None
    extra_names = sys.argv[5].split(",") if len(sys.argv) > 5 else []
    blobs = {}
    for line in blobs_file.read_text().splitlines():
        meta, p = line.split("\t"); blobs[p] = meta.split()[1]
    files = []
    for rel in sorted(blobs):
        rec = inventory(root / rel, blobs[rel]); rec["path"] = rel; rec["tracked_at_baseline"] = True
        files.append(rec)
    untracked = []
    for name in extra_names:
        rel = f"storage/kg/{name}"
        rec = inventory(extra_root / rel); rec["path"] = rel; rec["tracked_at_baseline"] = False
        untracked.append(rec)
    def agg(recs):
        a = {"files": len(recs), "total_bytes": sum(r["size_bytes"] for r in recs),
             "by_shape_family": dict(collections.Counter(r["shape_family"] for r in recs)),
             "with_prov_metadata": sum(1 for r in recs if r.get("has_prov_metadata")),
             "with_top_level_metadata": sum(1 for r in recs if r.get("has_top_level_metadata")),
             "duplicate_sha256_groups": [sorted(r["path"] for r in recs if r["sha256"] == h)
                 for h, c in collections.Counter(r["sha256"] for r in recs).items() if c > 1]}
        for field in ("edge.has_evidence", "node.context_snippets[].text", "node.abstract_text", "node.description", "node.code_snippet"):
            a[f"{field}_values_ge_500"] = sum(r.get("text_lengths_chars", {}).get(field, {}).get("ge_500", 0) for r in recs)
            a[f"{field}_values_ge_1000"] = sum(r.get("text_lengths_chars", {}).get(field, {}).get("ge_1000", 0) for r in recs)
            a[f"{field}_max"] = max([r.get("text_lengths_chars", {}).get(field, {}).get("max", 0) for r in recs] or [0])
            a[f"{field}_files_with_ge_500"] = sum(1 for r in recs if r.get("text_lengths_chars", {}).get(field, {}).get("ge_500", 0))
        a["context_text_distinct_total_chars"] = sum(r.get("context_text_distinct_total_chars", 0) for r in recs)
        a["context_text_max_distinct_chars_for_one_source_paper"] = max([r.get("context_text_max_distinct_chars_for_one_source_paper", 0) for r in recs] or [0])
        a["edge_has_evidence_non_null_total"] = sum(r.get("edge_has_evidence_non_null", 0) for r in recs)
        a["files_with_local_path_strings"] = sum(1 for r in recs if r.get("strings_matching_local_path_pattern"))
        a["files_with_secret_pattern_strings"] = sum(1 for r in recs if r.get("strings_matching_secret_pattern"))
        return a
    doc = {
        "inventory_version": "1",
        "task": "T23A/T26A",
        "baseline_commit": "222b1e7b130567dbf4a3242697ac2b465794a38d",
        "generated_by": "tests/contracts/json_kg/tools/kg_inventory.py (run from clean export; no text content copied, lengths/counts only)",
        "method_notes": [
            "Tracked set = `git ls-tree -r 222b1e7 storage/kg` (47 paths); bytes read from the clean baseline export.",
            "Lengths are Python str lengths (code points). 'Long' means >=500 chars. Whether a long value is verbatim source text cannot be proven without the private papers; it is an inference from length, field semantics (context_snippets, has_evidence, abstract_text) and the extractor design.",
            "Local path / secret detection is a regex heuristic over every string; counts only, values not recorded.",
            "untracked_working_tree section covers storage/kg/*.json present in the working tree but gitignored (.gitignore:182) and therefore NOT in the 47; these include the default graphs named in app/modules/f2w_agent/multi_kg.py:13-16."
        ],
        "summary_tracked": agg(files),
        "summary_untracked_working_tree": agg(untracked) if untracked else None,
        "files": files,
        "untracked_working_tree": untracked,
    }
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
