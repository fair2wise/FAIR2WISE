#!/usr/bin/env python3
import json
import os
import csv
from typing import Dict, List, Set, Tuple
from collections import defaultdict

# ---------------- Helpers ----------------
def load_kg(path: str) -> Tuple[Set[str], Set[Tuple[str,str,str]], Dict]:
    """Load KG from JSON, return entities, relations, and raw data."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    entities = {t["id"] for t in data.get("things", []) if "id" in t}
    relations = {(r.get("subject"), r.get("predicate"), r.get("object"))
                 for r in data.get("associations", [])
                 if r.get("subject") and r.get("predicate") and r.get("object")}
    return entities, relations, data

def calculate_structural_metrics(entities: Set, relations: Set) -> Dict:
    """Calculate KG structural quality metrics."""
    if not entities:
        return {
            'entity_relation_ratio': 0,
            'entities_with_relations_pct': 0,
            'avg_relations_per_entity': 0,
            'unique_predicates': 0
        }
    
    # Find entities that participate in relations
    entities_in_relations = set()
    predicates = set()
    entity_relation_count = defaultdict(int)
    
    for subj, pred, obj in relations:
        entities_in_relations.add(subj)
        entities_in_relations.add(obj)
        predicates.add(pred)
        entity_relation_count[subj] += 1
        entity_relation_count[obj] += 1
    
    connected_entities = entities_in_relations & entities
    
    return {
        'entity_relation_ratio': round(len(relations) / len(entities), 2) if entities else 0,
        'entities_with_relations_pct': round(100 * len(connected_entities) / len(entities), 1),
        'avg_relations_per_entity': round(sum(entity_relation_count.values()) / len(entities), 2),
        'unique_predicates': len(predicates)
    }

def calculate_growth_metrics(current_entities: Set, current_relations: Set, 
                           prev_entities: Set, prev_relations: Set,
                           current_papers: int, prev_papers: int) -> Dict:
    """Calculate growth and retention metrics between checkpoints."""
    if not prev_entities:
        return {
            'entity_retention': None,
            'relation_retention': None,
            'entity_growth_rate': round(len(current_entities) / current_papers, 1) if current_papers else 0,
            'relation_growth_rate': round(len(current_relations) / current_papers, 1) if current_papers else 0,
            'new_entities': len(current_entities),
            'new_relations': len(current_relations)
        }
    
    papers_added = current_papers - prev_papers
    new_entities = current_entities - prev_entities
    new_relations = current_relations - prev_relations
    
    return {
        'entity_retention': round(100 * len(prev_entities & current_entities) / len(prev_entities), 1),
        'relation_retention': round(100 * len(prev_relations & current_relations) / len(prev_relations), 1) if prev_relations else 0,
        'entity_growth_rate': round(len(new_entities) / papers_added, 1) if papers_added > 0 else 0,
        'relation_growth_rate': round(len(new_relations) / papers_added, 1) if papers_added > 0 else 0,
        'new_entities': len(new_entities),
        'new_relations': len(new_relations)
    }

# ---------------- Main ----------------
def evaluate_kgs_independent(folder: str, files_to_check: list,
                            out_csv="kg_independent_eval.csv", 
                            out_json="kg_independent_eval.json"):
    """Evaluate KGs using independent metrics (no biased reference)."""
    
    # Group files by model
    model_runs = defaultdict(list)
    
    for fname in files_to_check:
        if not os.path.exists(os.path.join(folder, fname)):
            print(f"⚠️ File not found: {fname}")
            continue
            
        # Parse model and papers from filename
        parts = fname.replace(".json", "").split("_")
        if "deepseek" in fname:
            model = f"DeepSeek-R1-{parts[2].upper()}"
            papers = int(parts[3])
        elif "qwen" in fname:
            model = "Qwen-3-235B"
            papers = int(parts[3].replace("papers", ""))
        elif "gpt-oss" in fname:
            # Handle gpt-oss files: matkg_gpt-oss_120b_25_...
            model = f"GPT-OSS-{parts[2].upper()}"  # Will be "GPT-OSS-120B"
            papers = int(parts[3])
        else:
            continue
            
        model_runs[model].append((papers, fname))
    
    # Sort runs by paper count
    for model in model_runs:
        model_runs[model].sort(key=lambda x: x[0])
    
    # Prepare output
    results = []
    csv_fields = [
        "model", "papers", "entities", "relations",
        "entity_relation_ratio", "entities_with_relations_pct", 
        "avg_relations_per_entity", "unique_predicates",
        "entity_retention", "relation_retention",
        "entity_growth_rate", "relation_growth_rate",
        "entities_per_paper", "relations_per_paper"
    ]
    
    print("\n" + "="*120)
    print("KNOWLEDGE GRAPH EVALUATION - INDEPENDENT METRICS")
    print("="*120)
    
    with open(out_csv, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=csv_fields)
        writer.writeheader()
        
        for model in sorted(model_runs.keys()):
            print(f"\n{model}")
            print("-" * 80)
            
            prev_entities = None
            prev_relations = None
            prev_papers = 0
            
            for papers, fname in model_runs[model]:
                path = os.path.join(folder, fname)
                entities, relations, data = load_kg(path)
                
                # Structural metrics
                struct_metrics = calculate_structural_metrics(entities, relations)
                
                # Growth metrics
                growth_metrics = calculate_growth_metrics(
                    entities, relations, 
                    prev_entities or set(), prev_relations or set(),
                    papers, prev_papers
                )
                
                # Coverage metrics
                entities_per_paper = round(len(entities) / papers, 1) if papers else 0
                relations_per_paper = round(len(relations) / papers, 1) if papers else 0
                
                row = {
                    "model": model,
                    "papers": papers,
                    "entities": len(entities),
                    "relations": len(relations),
                    "entity_relation_ratio": struct_metrics['entity_relation_ratio'],
                    "entities_with_relations_pct": struct_metrics['entities_with_relations_pct'],
                    "avg_relations_per_entity": struct_metrics['avg_relations_per_entity'],
                    "unique_predicates": struct_metrics['unique_predicates'],
                    "entity_retention": growth_metrics['entity_retention'],
                    "relation_retention": growth_metrics['relation_retention'],
                    "entity_growth_rate": growth_metrics['entity_growth_rate'],
                    "relation_growth_rate": growth_metrics['relation_growth_rate'],
                    "entities_per_paper": entities_per_paper,
                    "relations_per_paper": relations_per_paper
                }
                
                writer.writerow(row)
                results.append(row)
                
                # Print summary
                print(f"Papers: {papers:3} | Entities: {len(entities):6,} | Relations: {len(relations):6,}")
                print(f"  Structure: E/R ratio={struct_metrics['entity_relation_ratio']:.2f}, "
                      f"Connected={struct_metrics['entities_with_relations_pct']:.1f}%, "
                      f"Predicates={struct_metrics['unique_predicates']}")
                if growth_metrics['entity_retention'] is not None:
                    print(f"  Retention: Entities={growth_metrics['entity_retention']:.1f}%, "
                          f"Relations={growth_metrics['relation_retention']:.1f}%")
                    print(f"  Growth: +{growth_metrics['new_entities']} entities, "
                          f"+{growth_metrics['new_relations']} relations "
                          f"({growth_metrics['entity_growth_rate']:.1f} ent/paper, "
                          f"{growth_metrics['relation_growth_rate']:.1f} rel/paper)")
                
                # Update for next iteration
                prev_entities = entities
                prev_relations = relations
                prev_papers = papers
    
    # Save JSON with grouped results
    with open(out_json, "w", encoding="utf-8") as jf:
        grouped_results = defaultdict(list)
        for row in results:
            grouped_results[row['model']].append(row)
        json.dump(grouped_results, jf, indent=2)
    
    print(f"\n\nResults saved to {out_csv} and {out_json}")
    
    # Print comparative summary
    print("\n" + "="*120)
    print("COMPARATIVE SUMMARY")
    print("="*120)
    
    # Find best checkpoint for each model (highest paper count)
    best_runs = {}
    for model, runs in model_runs.items():
        papers, fname = runs[-1]  # Last run has most papers
        entities, relations, _ = load_kg(os.path.join(folder, fname))
        struct = calculate_structural_metrics(entities, relations)
        best_runs[model] = {
            'papers': papers,
            'entities': len(entities),
            'relations': len(relations),
            'e_r_ratio': struct['entity_relation_ratio'],
            'connected_pct': struct['entities_with_relations_pct'],
            'ent_per_paper': round(len(entities) / papers, 1),
            'rel_per_paper': round(len(relations) / papers, 1)
        }
    
    print(f"{'Model':<20} {'Papers':>7} {'Entities':>10} {'Relations':>10} "
          f"{'E/R Ratio':>10} {'Connected%':>11} {'Ent/Paper':>10} {'Rel/Paper':>10}")
    print("-" * 120)
    
    for model in sorted(best_runs.keys()):
        stats = best_runs[model]
        print(f"{model:<20} {stats['papers']:>7} {stats['entities']:>10,} "
              f"{stats['relations']:>10,} {stats['e_r_ratio']:>10.2f} "
              f"{stats['connected_pct']:>10.1f}% {stats['ent_per_paper']:>10.1f} "
              f"{stats['rel_per_paper']:>10.1f}")

# ---------------- Example usage ----------------
if __name__ == "__main__":
    folder = "storage/kg"
    
    files_to_check = [
        "matkg_deepseek-r1_14b_25_20250915_185643.json",
        "matkg_deepseek-r1_14b_50_20250916_162508.json",
        "matkg_deepseek-r1_14b_75_20250917_143348.json",
        "matkg_deepseek-r1_14b_100_20250918_095748.json",
        "matkg_deepseek-r1_32b_25_20250919_065133.json",
        "matkg_deepseek-r1_32b_50_20250920_125000.json",
        "matkg_deepseek-r1_32b_75_20250921_180642.json",
        "matkg_deepseek-r1_32b_100_20250922_191851.json",
        "matkg_deepseek-r1_70b_25_20250925_103657.json",
        "matkg_deepseek-r1_70b_50_20250926_144206.json",
        "matkg_deepseek-r1_70b_75_20250927_214641.json",
        "matkg_deepseek-r1_70b_100_20250929_004942.json",
        "matkg_gpt-oss_20b_25_20250930_172105.json",
        "matkg_gpt-oss_20b_50_20251001_025118.json",
        "matkg_gpt-oss_20b_75_20251001_115100.json",
        "matkg_gpt-oss_20b_100_20251001_115740.json",
        "matkg_gpt-oss_120b_25_20250923_213915.json",
        "matkg_gpt-oss_120b_50_20250924_042317.json",
        "matkg_gpt-oss_120b_75_20250924_135625.json",
        "matkg_gpt-oss_120b_100_20250925_002056.json",
        "matkg_qwen3_235b_25_20251001_120436.json",
        "matkg_qwen3_235b_50_20251002_095006.json",
        "matkg_qwen3_235b_75_20251003_084431.json",
        "matkg_qwen3_235b_100_20251004_054233.json",
        "matkg_qwen3_235b_147papers.json",
        "matkg_qwen3_235b_257papers.json",
        "matkg_qwen3_235b_333papers.json",
        "matkg_qwen3_235b_361papers.json",
        "matkg_qwen3_235b_444papers.json",
        "matkg_qwen3_235b_580papers.json"
    ]
    
    evaluate_kgs_independent(folder, files_to_check)