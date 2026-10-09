#!/usr/bin/env python3
"""Validate the handoff against extracted source snapshots without importing them.

Usage:
    python scripts/verify_handoff.py --f2w-root /path/FAIR2WISE-rsoxs \
        --forge-root /path/forge_rsoxs_analysis-main

Checks hashes, qualified AST symbols, route records, task dependencies and gate
requirements. It does not test application behavior, network services, Git
ancestry or scientific validity. Returns exit code 1 when any check fails.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any


def symbols(path: Path) -> dict[str, tuple[int, int]]:
    """Return qualified function/class locations in one Python source file."""
    tree = ast.parse(path.read_text(encoding='utf-8'))
    found: dict[str, tuple[int, int]] = {}
    def walk(node: ast.AST, prefix: str = '') -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                found[name] = (child.lineno, child.end_lineno or child.lineno)
                walk(child, name + '.')
            else:
                walk(child, prefix)
    walk(tree)
    return found


def verify(bundle: Path, f2w: Path, forge: Path) -> dict[str, Any]:
    """Check static source evidence and plan consistency; return a JSON report."""
    errors: list[str] = []
    cache: dict[Path, dict[str, tuple[int, int]]] = {}
    def load(name: str) -> Any:
        return json.loads((bundle / name).read_text(encoding='utf-8'))
    def check(ok: bool, message: str) -> None:
        if not ok:
            errors.append(message)
    def source(root: Path, rel: str, sha: str, records: list[dict[str, Any]]) -> None:
        path = root / rel
        if not path.is_file():
            errors.append('Missing source: ' + str(path))
            return
        check(hashlib.sha256(path.read_bytes()).hexdigest() == sha, 'Hash mismatch: ' + rel)
        try:
            if path not in cache:
                cache[path] = symbols(path)
            known = cache[path]
            for r in records:
                expected = (r['line'], r.get('end_line', r.get('end')))
                check(known.get(r['name']) == expected, f"AST span mismatch: {rel}:{r['name']} {expected} != {known.get(r['name'])}")
        except (SyntaxError, UnicodeError) as exc:
            errors.append(f'Cannot parse {rel}: {exc}')
    donor = load('port_manifest.json')['entries']
    baselines = load('f2w_baseline_manifest.json')
    ids = {e['id'] for e in donor}
    bids = {e['id'] for e in baselines['entries']}
    for e in donor:
        source(forge, e['source_file'], e['source_sha256'], e['verified_symbols'])
        check(e['proposed_target'].startswith(('app/', 'tests/', 'scripts/', 'evals/', 'storage/')), 'Unexpected target root: '+e['id'])
        check('src/f2w/' not in e['proposed_target'], 'Unapproved src-layout: '+e['id'])
        check('backends/splash.py' not in e['proposed_target'], 'New Splash backend target: '+e['id'])
        for b in e.get('f2w_anchor_ids', []): check(b in bids, 'Unknown F2W anchor: '+b)
    tests = {p for e in donor for p in e['donor_test_candidates']}
    for p in tests: check((forge / p).is_file(), 'Missing donor test candidate: '+p)
    for e in baselines['entries']:
        source(f2w,e['source_file'],e['source_sha256'],e['verified_symbols'])
        for p in e['existing_tests']: check((f2w/p).is_file(),'Missing F2W test candidate: '+p)
        for i in e['donor_port_ids']: check(i in ids,'Unknown donor anchor: '+i)
    for e in baselines['supporting_files']:
        p=f2w/e['path']; check(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==e['sha256'],'Supporting hash mismatch: '+e['path'])
    inventory=load('f2w_source_inventory.json')
    for e in inventory['files']:
        source(f2w,e['path'],e['sha256'],e['symbols'])
    ui=load('f2w_ui_contracts.json')
    for p in ui['vitest_test_files']+ui['playwright_spec_files']:check((f2w/p).is_file(),'Missing UI test: '+p)
    routes=load('f2w_http_routes.json')
    for r in routes:
        p=f2w/r['file']
        if p not in cache:cache[p]=symbols(p)
        check(cache[p].get(r['symbol'])==(r['line'],r['end_line']),'Route symbol/span mismatch: '+r['symbol'])
        node=next((n for n in ast.walk(ast.parse(p.read_text())) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.lineno==r['line']),None)
        check(node is not None and r['decorator'] in [ast.unparse(d) for d in node.decorator_list], 'Route decorator mismatch: '+r['method']+' '+r['path'])
    graph=load('task_graph.json'); nodes={n['id']:n for n in graph['tasks']+graph['human_gates']}
    check(len(nodes)==len(graph['tasks'])+len(graph['human_gates']),'Duplicate task/gate IDs')
    for n in nodes.values():
        for d in n['depends_on']:check(d in nodes,'Unknown dependency: '+d)
        for i in n.get('port_ids',[]):check(i in ids,'Unknown task donor ID: '+i)
    visiting: set[str]=set(); done: set[str]=set()
    def visit(name: str) -> None:
        if name in done:return
        if name in visiting:raise ValueError('Dependency cycle at '+name)
        visiting.add(name)
        for parent in nodes[name]['depends_on']:
            if parent in nodes:visit(parent)
        visiting.remove(name);done.add(name)
    try:
        for name in nodes:visit(name)
    except ValueError as exc:errors.append(str(exc))
    for h in graph['human_gates']:
        check(h['reviewer']=='project_owner' and not h['agent_may_self_approve'],'Invalid owner gate: '+h['id'])
        check(h['status']=='not_approved','Draft must not fabricate approval: '+h['id'])
    for h,t in [('H1','T18A'),('H2','T18B'),('H6','T18C'),('H6','T17'),('T12','T09'),('T12','T08B')]:
        check(t in nodes[h]['depends_on'],'Missing required predecessor: '+h+' <- '+t)
    for child, parent in [('H1','T19A'),('H1','T20A'),('H1','T21A'),('H1','T22A'),('H2','T19B'),('H2','T21B'),('H3','T22B'),('T08A','T20B'),('T12','T19D'),('H6','T19E')]:
        check(parent in nodes[child]['depends_on'], 'Missing v4 predecessor: '+child+' <- '+parent)
    cases = load('binary_case_inventory.json')['cases']
    check(len({c['case_id'] for c in cases}) == len(cases), 'Duplicate binary case IDs')
    for case in cases:
        check(case['execution_status'] == 'not_run' and case['verdict'] is None, 'Draft fabricates binary result: '+case['case_id'])
    for item in load('v4_source_notes.json')['items']:
        path=f2w/item['path']
        check(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256'], 'v4 source hash mismatch: '+item['path'])
    check(graph['integration_branch']=='forge_merge','Wrong integration branch')
    lock=load('source_lock.json')
    check(lock['f2w']['migration_branch']=='forge_merge','Wrong locked migration branch')
    check(lock['tiled_validation']['exact_build']=='0.2.19', 'Owner Tiled pin missing')
    check(lock['schema_semantic_change']['authorized'] is False, 'Unapproved schema semantic change')
    check(lock['kg_artifacts']['creation']=='versioned_json', 'Versioned JSON build contract missing')
    check(lock['kg_artifacts']['json_creation_requires_tiled'] is False, 'JSON build wrongly requires Tiled')
    check(graph['open_owner_questions']==['O03','O06','O07','O08'], 'Wrong unresolved policy set')
    for child,parent in [('H1','T23A'),('T08B','T23B'),('T12','T23B')]:
        check(parent in nodes[child]['depends_on'], 'Missing v5 predecessor: '+child+' <- '+parent)
    job_audit=load('forge_job_settings_source_map.json')
    for item in job_audit['items']:
        path=forge/item['path']
        if not path.is_file():
            errors.append('Missing audit source: '+item['path']); continue
        data=path.read_bytes()
        check(hashlib.sha256(data).hexdigest()==item['sha256'], 'Audit hash mismatch: '+item['id'])
        lines=data.decode('utf-8').splitlines()
        excerpt='\n'.join(lines[item['start_line']-1:item['end_line']])
        check(excerpt==item['excerpt'], 'Audit excerpt mismatch: '+item['id'])
    profiles = load('deployment_profiles.json')
    check(profiles['base_path']=='/fair2wise', 'Wrong public base path')
    pmap={x['tag']:x for x in profiles['profiles']}
    check(set(pmap)=={'local','als-dev','als-prod'}, 'Wrong deployment tags')
    check(pmap['local']['bundles_tiled_server'] is True, 'Local must bundle Tiled')
    for tag,expected in [('als-dev','https://tiled-staging.als.lbl.gov/'),('als-prod','https://tiled.als.lbl.gov/')]:
        check(pmap[tag]['bundles_tiled_server'] is False and pmap[tag]['tiled_url']==expected, 'Wrong ALS target: '+tag)
    check(lock['owner_confirmed_requirements']['tiled_write_identity']=='admin_account', 'Writer identity not updated')
    check(lock['user_tiled_writes']['owner_decided'] is True, 'D30 admin-only role decision missing')
    check(lock['owner_confirmed_requirements']['authentication']=='profile_specific', 'Wrong profile-specific authentication contract')
    check(lock['kg_artifacts']['version_store']=='F2W_git_repository', 'Committed JSON version store missing')
    check(lock['kg_artifacts']['production_reextraction_for_promotion'] is False, 'Promotion wrongly re-extracts')
    check(profiles['user_tiled_write_policy']=='profile_specific_local_full_als_admin', 'Wrong user write policy')
    check(pmap['local']['authorization_mode']=='local_trusted' and pmap['local']['user_sign_in_required'] is False and pmap['local']['user_role_checks_required'] is False, 'Local must not require user login/roles')
    check(pmap['local']['audit_actor']=='local-operator', 'Local audit actor must not invent a Tiled user')
    for tag in ['als-dev','als-prod']:
        check(pmap[tag]['authorization_mode']=='tiled_authenticated' and pmap[tag]['user_sign_in_required'] is True and pmap[tag]['trusted_local_override_allowed'] is False, 'ALS auth isolation missing: '+tag)
    check(profiles['authentication']['request_selectable'] is False and profiles['authentication']['als_auth_failure_fallback_to_local'] is False, 'Unsafe authentication selection/fallback')
    case_map={c['case_id']:c for c in cases}
    for i in range(1,11):
        check(f'LOCAL-{i:02d}' in case_map, 'Missing local-access test proposal')
    for i in range(1,12):
        c=case_map.get(f'AUTH-{i:02d}',{})
        check(c.get('deployment_profiles')==['als-dev','als-prod'], 'Old auth case not scoped to ALS')
    for child,parent in [('H1','T25A'),('H1','T26A'),('H2','T25B'),('H4','T25C'),('H4','T26B'),('H5','T26C'),('H6','T26C')]:
        check(parent in nodes[child]['depends_on'], 'Missing v7 predecessor: '+child+' <- '+parent)
    for child,parent in [('H1','T24A'),('H4','T24B'),('H4','T24C'),('H6','T24D'),('H6','T24E')]:
        check(parent in nodes[child]['depends_on'], 'Missing deployment dependency: '+child+' <- '+parent)
    for item in load('deployment_source_map.json')['files']:
        path=f2w/item['path'];data=path.read_bytes()
        check(hashlib.sha256(data).hexdigest()==item['sha256'], 'Deployment source hash mismatch: '+item['path'])
        lines=data.decode('utf-8').splitlines()
        for span in item['spans']:
            check('\n'.join(lines[span['start_line']-1:span['end_line']])==span['excerpt'], 'Deployment excerpt mismatch: '+item['path'])
    return {'draft_version':'8.0','checked_on':'2026-10-09','source_validation':'static_only',
            'f2w_archive_sha256':lock['f2w']['sha256'],'forge_archive_sha256':lock['forge']['sha256'],
            'f2w_archive_recorded_commit':lock['f2w']['archive_recorded_commit'],
            'f2w_nonvendored_python_files':len(inventory['files']),
            'f2w_baseline_crosswalk_entries':len(baselines['entries']),
            'f2w_ui_agent_route_records':sum(r['file']=='app/modules/f2w_agent/api.py' for r in routes),
            'f2w_model_api_route_records':sum(r['file']=='app/modules/kg_rag_api.py' for r in routes),
            'f2w_wire_model_records':len(ui['models']),'existing_ui_unit_test_files':len(ui['vitest_test_files']),
            'existing_playwright_spec_files':len(ui['playwright_spec_files']),
            'donor_file_records':len(donor),'donor_symbol_records':sum(len(e['verified_symbols']) for e in donor),
            'distinct_donor_test_candidates':len(tests),'tasks':len(graph['tasks']),'human_gates':len(graph['human_gates']),
            'binary_case_proposals':len(cases),'job_settings_audit_spans':len(job_audit['items']),'static_checks_passed':not errors,'errors':errors,'runtime_tests_executed':False,
            'git_object_verified':False,'remote_branch_created':False,'application_repositories_modified':False}


def main() -> int:
    """Parse paths, write/print a static verification report and return status."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--f2w-root',type=Path,required=True)
    parser.add_argument('--forge-root',type=Path,required=True)
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    try:result=verify(args.bundle.resolve(),args.f2w_root.resolve(),args.forge_root.resolve())
    except (OSError,KeyError,json.JSONDecodeError) as exc:
        print(json.dumps({'static_checks_passed':False,'error':str(exc)},indent=2));return 1
    text=json.dumps(result,indent=2)+'\n'
    if args.report:args.report.write_text(text,encoding='utf-8')
    print(text,end='')
    return 0 if result['static_checks_passed'] else 1

if __name__=='__main__':raise SystemExit(main())
