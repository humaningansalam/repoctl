from __future__ import annotations

import ast
import json
import textwrap
from pathlib import Path

import pytest

from tests.repoctl.graph.test_graph_build import _materialize
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import init_repo
from tools.repoctl.cli import main


def _workspace(tmp_path: Path, monkeypatch) -> Path:
    write_workspace(tmp_path)
    repo = tmp_path / 'repos'
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr('tools.repoctl.cli.find_workspace_root', lambda: tmp_path)
    return repo


def _payload(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def _write(repo: Path, path: str, text: str) -> None:
    dest = repo / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding='utf-8')


def _python_route_fixture(repo: Path) -> None:
    _write(repo, 'src/api/model_routes.py', '''from src.core.model_manager import ModelManager
class Router:
    def route(self, *a, **kw):
        return lambda f: f
bp = Router()
@bp.route('/predict', methods=['POST'])
def predict():
    manager = ModelManager()
    return manager.predict()
''')
    _write(repo, 'src/core/model_manager.py', '''class ModelManager:
    def predict(self):
        return 42
''')
    _write(repo, 'tests/test_predict.py', '''def test_predict_route(client):
    response = client.post('/predict', json={})
    assert response.status_code == 200
''')
    _write(repo, 'tests/test_model_manager_concurrency.py', '''from src.core.model_manager import ModelManager

def test_predict_concurrency():
    assert ModelManager().predict() == 42
''')


def test_http_route_workflow_links_source_candidate_and_confirmed_test(tmp_path, monkeypatch, capsys) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    _python_route_fixture(repo)
    _materialize(tmp_path)
    assert main(['graph', 'workflow', '--route', '/predict', '--json']) == 0
    data = _payload(capsys)
    result = data['data']['result']
    assert result['status'] == 'partial'
    assert [x['path'] for x in result['stages']['entrypoints']] == ['src/api/model_routes.py']
    assert any(x['path'] == 'src/core/model_manager.py' and x['relation'] == 'candidate' for x in result['stages']['implementation'])
    assert any(x['path'] == 'tests/test_predict.py' and x['relation'] == 'source'
               and x['verification'] == 'python_literal_http_test' for x in result['stages']['tests'])
    assert any(x['path'] == 'tests/test_model_manager_concurrency.py' and x['relation'] == 'source'
               and x['verification'] == 'python_imported_class_method_test' for x in result['stages']['tests'])
    assert any(x['to'] == 'src/core/model_manager.py' and x['relation'] == 'candidate' for x in result['links'])
    assert any('not provider-confirmed' in w for w in result['warnings'])
    assert main(['graph', 'workflow', '--route', '/predict', '--in-file', 'src/api/model_routes.py', '--json']) == 0
    scoped = _payload(capsys)['data']['result']
    assert any(x['path'] == 'tests/test_predict.py' for x in scoped['stages']['tests'])


def test_route_ambiguity_fails_closed_without_selecting_an_arbitrary_handler(tmp_path, monkeypatch, capsys) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    for name in ('first', 'second'):
        _write(repo, f'src/api/{name}.py', "@router.get('/health')\ndef status():\n    return 'ok'\n")
    _materialize(tmp_path)
    assert main(['graph', 'workflow', '--route', '/health', '--json']) == 1
    result = _payload(capsys)['data']['result']
    assert result['status'] == 'ambiguous'
    assert {x['path'] for x in result['entrypoints']} == {'src/api/first.py', 'src/api/second.py'}
    assert main(['graph', 'workflow', '--route', '/health', '--in-file', 'src/api/first.py', '--json']) == 0
    chosen = _payload(capsys)['data']['result']
    assert chosen['stages']['entrypoints'][0]['path'] == 'src/api/first.py'


def test_rust_module_fallback_does_not_promote_similar_test_names(tmp_path, monkeypatch, capsys) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    _write(repo, 'crates/daemon/src/audio_clock.rs', 'pub struct AudioClockDriftEstimator;\n')
    _write(repo, 'crates/core/src/lib.rs', '''pub struct HostAudioClockModel;
#[test]
fn host_audio_clock_default_contract() { let _model = HostAudioClockModel; }
''')
    _materialize(tmp_path)
    assert main(['graph', 'workflow', '--symbol', 'audio_clock', '--json']) == 0
    result = _payload(capsys)['data']['result']
    assert result['status'] == 'partial'
    assert any(x['path'] == 'crates/daemon/src/audio_clock.rs' for x in result['stages']['implementation'])
    assert not any(x['path'] == 'crates/core/src/lib.rs' for x in result['stages']['implementation'])
    assert not any(x['path'] == 'crates/core/src/lib.rs' for x in result['stages']['tests'])
    assert result['completeness']['precision'] == 'partial'


def test_stale_empty_graph_uses_changed_source_and_refresh_restores_precision(tmp_path, monkeypatch, capsys) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    _write(repo, 'README.txt', 'initial repository\n')
    _materialize(tmp_path)
    _write(repo, 'src/main.py', 'def start():\n    return 42\n')
    assert main(['graph', 'workflow', '--symbol', 'start', '--json']) == 0
    stale = _payload(capsys)
    assert stale['data']['query_status'] == 'partial'
    assert stale['data']['freshness']['status'] == 'stale'
    assert any(x['path'] == 'src/main.py' and x['relation'] == 'candidate' for x in stale['data']['result']['stages']['implementation'])
    assert stale['data']['result']['stages']['tests'] == []
    _materialize(tmp_path)
    assert main(['graph', 'workflow', '--symbol', 'start', '--json']) == 0
    refreshed = _payload(capsys)
    assert any(x['path'] == 'src/main.py' and x['relation'] == 'confirmed' for x in refreshed['data']['result']['stages']['implementation'])
    assert refreshed['data']['query_status'] == 'partial'  # no test evidence is not zero-impact


def test_workflow_benchmark_measures_paths_and_provenance_not_edges(tmp_path, monkeypatch, capsys) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    _python_route_fixture(repo)
    _materialize(tmp_path)
    fixture = tmp_path / 'case.json'
    fixture.write_text(json.dumps({
        'schema': 'repoctl.graph.workflow-benchmark',
        'cases': [{
            'id': 'real-route', 'repo_id': 'main', 'selector': {'route': '/predict'},
            'expected_paths': {'entrypoints': ['src/api/model_routes.py'],
                               'implementation': ['src/core/model_manager.py'],
                               'tests': ['tests/test_predict.py', 'tests/test_model_manager_concurrency.py']},
            'expected_statuses': ['partial'],
            'required_stages': ['entrypoint', 'implementation', 'tests'],
        }],
    }), encoding='utf-8')
    assert main(['graph', 'workflow-benchmark', '--case-id', 'real-route', '--fixture', str(fixture), '--min-path-recall', '1', '--json']) == 1
    result = _payload(capsys)['data']['benchmark']
    assert result['path_recall'] == 1.0
    assert result['confirmed_path_recall'] < result['path_recall']
    assert result['status_expected'] is True
    assert result['stages']['tests']['found_count'] == 2
    assert result['stages']['tests']['confirmed_count'] == 0
    assert result['stages']['tests']['verified_count'] == 2
    assert result['required_stages_covered'] is True
    assert result['required_stages_precise'] is False
    assert result['task_navigation_complete'] is False


def test_workflow_exact_file_precedes_two_thousand_other_entries(tmp_path, monkeypatch) -> None:
    from tools.repoctl.graph_model import GraphNode, GraphSnapshot, file_id
    from tools.repoctl.graph_workflow import query_graph_workflow
    from tools.repoctl.repositories import require_repo_target

    repo = _workspace(tmp_path, monkeypatch)
    _write(repo, 'z_special.py', "@router.post('/predict')\ndef predict():\n    return 'ok'\n")
    files = [f'a/file{i:04}.py' for i in range(2048)] + ['z_special.py']
    snap = GraphSnapshot(repository={'id': 'main', 'path': 'repos'}, sources=[], completeness={},
        nodes=[GraphNode(file_id('main', path), 'file', {'path': path}) for path in files], edges=[])
    target = require_repo_target(tmp_path, repo_id='main')
    result = query_graph_workflow(snap, root=tmp_path, target=target,
        route='/predict', in_file='z_special.py', freshness={'status': 'stale'})
    assert result['stages']['entrypoints'][0]['path'] == 'z_special.py'
    assert result['completeness']['files_scanned'] == 1
    assert result['completeness']['source_scan_bounded'] is False


def test_workflow_unscored_caller_fails_even_with_full_path_recall() -> None:
    from tools.repoctl.graph_workflow import evaluate_workflow_benchmark
    result = {'status': 'partial', 'stages': {
        'entrypoints': [{'path': 'entry.py', 'relation': 'source'}],
        'implementation': [{'path': 'engine.py', 'relation': 'confirmed'}],
        'tests': [{'path': 'test_engine.py', 'relation': 'confirmed'}],
        'callers_or_dependents': [],
    }}
    case = {'id':'unscored-caller', 'expected_statuses':['partial'],
        'required_stages':['entrypoint','implementation','callers_or_dependents','tests'],
        'expected_paths': {'entrypoints':['entry.py'], 'implementation':['engine.py'], 'tests':['test_engine.py']}}
    score = evaluate_workflow_benchmark(result, case)
    assert score['path_recall'] == 1.0
    assert score['required_stages']['callers_or_dependents']['status'] == 'unscored_no_ground_truth'
    assert score['required_stages_covered'] is False
    assert score['task_navigation_complete'] is False


def test_workflow_full_required_relation_evidence_can_pass() -> None:
    from tools.repoctl.graph_workflow import evaluate_workflow_benchmark
    result = {'status':'found', 'stages': {
        'entrypoints': [{'path':'route.py','relation':'source'}],
        'implementation':[{'path':'engine.py','relation':'confirmed'}],
        'callers_or_dependents':[{'path':'caller.py','relation':'confirmed'}],
        'tests':[{'path':'test_engine.py','relation':'confirmed'}],
    }}
    case = {'id':'verified', 'expected_statuses':['found'],
        'required_stages':['entrypoint','implementation','callers_or_dependents','tests'],
        'expected_paths': {'entrypoints':['route.py'], 'implementation':['engine.py'],
                           'callers_or_dependents':['caller.py'], 'tests':['test_engine.py']}}
    score = evaluate_workflow_benchmark(result, case)
    assert score['task_navigation_complete'] is True
    assert score['required_stages_precise'] is True
    assert score['false_positive_count'] == 0
    assert score['confirmed_path_recall'] == 0.75


def test_benchmark_requires_zero_false_positive_paths_for_completion() -> None:
    from tools.repoctl.graph_workflow import evaluate_workflow_benchmark
    result = {'status': 'found', 'stages': {
        'entrypoints': [{'path': 'route.py', 'relation': 'source'}],
        'implementation': [{'path': 'manager.py', 'relation': 'confirmed'}],
        'tests': [
            {'path': 'tests/test_predict.py', 'relation': 'confirmed'},
            {'path': 'tests/test_similar_name.py', 'relation': 'candidate'},
        ],
    }}
    case = {
        'id': 'extra-test', 'required_stages': ['entrypoint', 'implementation', 'tests'],
        'expected_statuses': ['found'],
        'expected_paths': {
            'entrypoints': ['route.py'], 'implementation': ['manager.py'],
            'tests': ['tests/test_predict.py'],
        },
    }
    score = evaluate_workflow_benchmark(result, case)
    assert score['path_recall'] == 1.0
    assert score['required_stages_precise'] is True
    assert score['false_positive_count'] == 1
    assert score['task_navigation_complete'] is False


def test_gakza_fixture_distinguishes_direct_test_and_proven_part_dependents() -> None:
    from tools.repoctl.graph_workflow import evaluate_workflow_benchmark

    cases = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/graph-workflow-benchmark/cases.json').read_text())['cases']
    case = next(item for item in cases if item['id'] == 'gakza-dart-precision')
    direct = case['expected_paths']['tests'][0]
    related = case['expected_related_paths']['tests'][0]
    provenance = 'TESTS_FILE through exact Dart library/part chain'
    result = {'status': 'found', 'stages': {
        'implementation': [{'path': case['expected_paths']['implementation'][0], 'relation': 'confirmed'}],
        'callers_or_dependents': [{'path': case['expected_paths']['callers_or_dependents'][0], 'relation': 'confirmed'}],
        'tests': [{'path': direct, 'relation': 'confirmed', 'evidence': provenance},
                  {'path': related, 'relation': 'confirmed', 'evidence': provenance}],
    }}
    good = evaluate_workflow_benchmark(result, case)
    assert good['task_navigation_complete'] is True
    assert good['stages']['tests']['verified_related_count'] == 1
    assert good['false_positive_count'] == 0

    # A string match or an unrelated test must not gain credit merely because
    # the corpus has verified secondary compile-time dependencies.
    result['stages']['tests'][1]['relation'] = 'candidate'
    unproven = evaluate_workflow_benchmark(result, case)
    assert unproven['false_positive_count'] == 1
    assert unproven['task_navigation_complete'] is False
    result['stages']['tests'][1]['relation'] = 'confirmed'
    result['stages']['tests'].append({'path': 'test/unrelated_test.dart', 'relation': 'confirmed'})
    extra = evaluate_workflow_benchmark(result, case)
    assert extra['false_positive_count'] == 1
    assert extra['task_navigation_complete'] is False


def test_cops_fixture_accepts_only_proven_current_refresh_found() -> None:
    from tools.repoctl.graph_workflow import evaluate_workflow_benchmark

    cases = json.loads((Path(__file__).resolve().parents[2] / 'fixtures/graph-workflow-benchmark/cases.json').read_text())['cases']
    case = next(item for item in cases if item['id'] == 'cops-and-robbers-stale-empty')
    result = {'status': 'found', 'stages': {
        'implementation': [{'path': 'lib/main.dart', 'relation': 'confirmed'}],
    }, 'refresh': {'status': 'current', 'source_rechecked': True}}
    verified = evaluate_workflow_benchmark(result, case)
    assert verified['status_expected'] is True
    assert verified['task_navigation_complete'] is True
    result['refresh']['source_rechecked'] = False
    missing_proof = evaluate_workflow_benchmark(result, case)
    assert missing_proof['task_navigation_complete'] is False
    assert missing_proof['required_stages']['refresh']['precise'] is False


def test_dart_exact_seed_recovers_live_sibling_importer_without_inventing_a_call(tmp_path, monkeypatch) -> None:
    from tools.repoctl.graph_model import GraphNode, GraphSnapshot, file_id
    from tools.repoctl.graph_workflow import query_graph_workflow
    from tools.repoctl.repositories import require_repo_target

    repo = _workspace(tmp_path, monkeypatch)
    owner = 'lib/runtime/local_store/local_restaurant_order_authority.dart'
    consumer = 'lib/runtime/local_store/local_waitlist_authority.dart'
    _write(repo, owner, 'extension LocalRestaurantOrderAuthority on LocalWaitlistAuthority {}\n')
    _write(repo, consumer, "import 'local_restaurant_order_authority.dart';\nclass LocalWaitlistAuthority {}\n")
    files = [owner, consumer]
    snapshot = GraphSnapshot(repository={'id': 'main', 'path': 'repos'}, sources=[], completeness={},
        nodes=[GraphNode(file_id('main', path), 'file', {'path': path}) for path in files], edges=[])
    result = query_graph_workflow(snapshot, root=tmp_path, target=require_repo_target(tmp_path, repo_id='main'),
        symbol='LocalRestaurantOrderAuthority', in_file=owner,
        freshness={'status':'stale', 'changed_paths':files})
    assert result['status'] == 'partial'
    assert result['stages']['implementation'][0]['path'] == owner
    assert any(item['path'] == consumer and item['relation'] == 'source'
               and item['evidence'] == 'current Dart module import/export literal'
               for item in result['stages']['callers_or_dependents'])
    assert not any(edge['relation'] == 'confirmed' for edge in result['links'])


def test_stale_dart_entrypoint_has_dart_source_provenance_not_rust(tmp_path, monkeypatch, capsys) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    _write(repo, 'README.txt', 'empty initial repo\n')
    _materialize(tmp_path)
    _write(repo, 'lib/main.dart', 'void main() => boot();\nvoid boot() {}\n')
    assert main(['graph', 'workflow', '--symbol', 'main', '--in-file', 'lib/main.dart', '--json']) == 0
    result = _payload(capsys)['data']['result']
    assert result['status'] == 'partial'
    impl = next(item for item in result['stages']['implementation'] if item['path'] == 'lib/main.dart')
    assert impl['relation'] == 'source'
    assert impl['evidence'] == 'Dart top-level function declaration'
    assert 'Rust' not in impl['evidence']
    assert result['stages']['tests'] == []


def test_rust_module_precision_verifies_owned_test_and_rejects_other_crate(tmp_path, monkeypatch, capsys) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    _write(repo, 'crates/daemon/src/main.rs', 'mod audio_clock;\nfn main(){}\n')
    _write(repo, 'crates/daemon/src/audio_clock.rs', '''pub struct AudioClockDriftEstimator;
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn drift_estimator_contract() {
        let _estimator = AudioClockDriftEstimator;
    }
}
''')
    _write(repo, 'crates/core/src/lib.rs', '''#[test]
fn host_audio_clock_default_contract_matches_values() { assert!(true); }
''')
    _materialize(tmp_path)
    assert main(['graph', 'workflow', '--symbol', 'audio_clock', '--repo-id', 'main', '--json']) == 0
    result = _payload(capsys)['data']['result']
    assert any(item['path'] == 'crates/daemon/src/audio_clock.rs' and item['relation'] == 'confirmed'
               for item in result['stages']['implementation'])
    assert any(item['path'] == 'crates/daemon/src/main.rs' and item['relation'] == 'confirmed'
               for item in result['stages']['callers_or_dependents'])
    assert any(item['path'] == 'crates/daemon/src/audio_clock.rs'
               and item['verification'] == 'rust_embedded_test_decl'
               for item in result['stages']['tests'])
    assert not any(item['path'] == 'crates/core/src/lib.rs' for item in result['stages']['tests'])
    assert result['status'] == 'partial'


def test_dart_exact_part_of_chain_connects_library_caller_and_test(tmp_path,monkeypatch,capsys) -> None:
    repo=_workspace(tmp_path,monkeypatch)
    _write(repo,'pubspec.yaml','name: app\n')
    _write(repo,'lib/src/authority.dart',"part 'authority_part.dart';\nclass Authority {}\n")
    _write(repo,'lib/src/authority_part.dart',"part of 'authority.dart';\nclass AuthorityPart {}\n")
    _write(repo,'test/authority_test.dart',"import 'package:app/src/authority.dart';\nvoid main() {}\n")
    _materialize(tmp_path)
    assert main(['graph','workflow','--symbol','AuthorityPart','--in-file','lib/src/authority_part.dart','--json'])==0
    payload=_payload(capsys)
    result=payload['data']['result']
    assert payload['data']['freshness']['status']=='current'
    implementation = next(item for item in result['stages']['implementation']
                          if item['path']=='lib/src/authority_part.dart')
    assert implementation['relation'] in {'confirmed','candidate','source'}
    if implementation['relation'] != 'confirmed':
        # CI with no Dart SDK still provides the exact library/part path,
        # but cannot claim the missing analyzer proved the class symbol.
        assert result['status']=='partial'
    assert any(item['path']=='lib/src/authority.dart' and item['relation']=='confirmed'
               for item in result['stages']['callers_or_dependents'])
    assert any(item['path']=='test/authority_test.dart' and item['relation']=='confirmed'
               for item in result['stages']['tests'])


def test_dart_invalid_part_of_never_creates_confirmed_part_dependency(tmp_path,monkeypatch,capsys) -> None:
    repo=_workspace(tmp_path,monkeypatch)
    _write(repo,'lib/authority.dart',"part 'authority_part.dart';\nclass Authority {}\n")
    _write(repo,'lib/authority_part.dart',"part of 'other_library.dart';\nclass AuthorityPart {}\n")
    _materialize(tmp_path)
    assert main(['graph','workflow','--symbol','AuthorityPart','--in-file','lib/authority_part.dart','--json'])==0
    result=_payload(capsys)['data']['result']
    assert not any(item['path']=='lib/authority.dart' and item['relation']=='confirmed'
                   for item in result['stages']['callers_or_dependents'])


def test_dart_part_of_is_not_itself_a_reverse_import(tmp_path,monkeypatch,capsys) -> None:
    repo=_workspace(tmp_path,monkeypatch)
    _write(repo,'lib/authority.dart',"part 'authority_part.dart';\nclass Authority {}\n")
    _write(repo,'lib/authority_part.dart',"part of 'authority.dart';\nclass AuthorityPart {}\n")
    _materialize(tmp_path)
    from tools.repoctl.repositories import require_repo_target
    from tools.repoctl.graph_store import load_materialized_graph
    snapshot,problems,meta=load_materialized_graph(tmp_path,target=require_repo_target(tmp_path,repo_id='main'))
    assert snapshot and not problems
    node_paths={n.id:n.identity.get('path') for n in snapshot.nodes if n.kind=='file'}
    part_edges=[edge for edge in snapshot.edges if edge.kind=='IMPORTS_FILE' and edge.source=='dart_import_resolver'
                and edge.facts.get('match_kind')=='dart_part_directive']
    assert len(part_edges)==1
    assert node_paths[part_edges[0].from_id]=='lib/authority.dart'
    assert node_paths[part_edges[0].to_id]=='lib/authority_part.dart'


def test_dart_part_inside_comment_never_makes_a_confirmed_graph_edge(tmp_path,monkeypatch,capsys) -> None:
    repo=_workspace(tmp_path,monkeypatch)
    _write(repo,'lib/authority.dart',"""/*
part 'authority_part.dart';
*/
class Authority {}
""")
    _write(repo,'lib/authority_part.dart',"part of 'authority.dart';\nclass AuthorityPart {}\n")
    _materialize(tmp_path)
    from tools.repoctl.repositories import require_repo_target
    from tools.repoctl.graph_store import load_materialized_graph
    snapshot,_,_=load_materialized_graph(tmp_path,target=require_repo_target(tmp_path,repo_id='main'))
    assert snapshot
    assert not any(edge.kind=='IMPORTS_FILE' and edge.source=='dart_import_resolver'
                   and edge.facts.get('match_kind')=='dart_part_directive'
                   for edge in snapshot.edges)


def test_dart_comment_and_multiline_string_directive_scan_is_bounded() -> None:
    from tools.repoctl.language_profiles import dart_directives,index_dart
    text="""import 'dart:convert';
/*
part 'fake_part.dart';
/* nested part 'fake_part.dart'; */
*/
// export 'fake_part.dart';
const sample = '''
part 'fake_part.dart';
''';
part 'real_part.dart';
"""
    assert dart_directives(text)==[('import','dart:convert'),('part','real_part.dart')]
    assert index_dart(text)[1]==['dart:convert','real_part.dart']


def test_flask_factory_wiring_is_source_proven_not_falsely_confirmed(tmp_path,monkeypatch,capsys) -> None:
    repo=_workspace(tmp_path,monkeypatch)
    _python_route_fixture(repo)
    _write(repo,'src/main.py', '''from flask import Flask
from src.core.model_manager import ModelManager
from src.api.model_routes import bp as model_bp

def create_app():
    app = Flask(__name__)
    app.model_manager = ModelManager()
    app.register_blueprint(model_bp)
    return app
''')
    _materialize(tmp_path)
    assert main(['graph','workflow','--route','/predict','--repo-id','main','--json'])==0
    result=_payload(capsys)['data']['result']
    impl=next(item for item in result['stages']['implementation'] if item['path']=='src/core/model_manager.py')
    # Route fixture calls `manager.predict`, not `current_app.model_manager.predict`.
    assert impl['relation']=='candidate'
    _write(repo,'src/api/model_routes.py', '''from flask import current_app
class Router:
    def route(self,*a,**kw):
        return lambda f: f
bp=Router()
@bp.route('/predict',methods=['POST'])
def predict():
    return current_app.model_manager.predict()
''')
    _materialize(tmp_path)
    assert main(['graph','workflow','--route','/predict','--repo-id','main','--json'])==0
    result=_payload(capsys)['data']['result']
    impl=next(item for item in result['stages']['implementation'] if item['path']=='src/core/model_manager.py')
    assert impl['relation']=='source'
    assert 'dispatch unconfirmed' in impl['evidence']
    assert any(edge['from']=='src/main.py' and edge['to']=='src/core/model_manager.py'
               and edge['relation']=='source' for edge in result['links'])
    assert result['status']=='partial'


def test_flask_wiring_uses_structural_imports_not_bootstrap_filenames(tmp_path, monkeypatch, capsys) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    _python_route_fixture(repo)
    _write(repo, 'src/api/model_routes.py', """from flask import current_app
class Router:
    def route(self, *a, **kw):
        return lambda f: f
bp = Router()
@bp.route('/predict', methods=['POST'])
def predict():
    return current_app.model_manager.predict()
""")
    _write(repo, 'src/infra/aaa_main.py', """from src.core.model_manager import ModelManager
from src.api.model_routes import bp as model_bp
def unrelated_assignment():
    app.model_manager = ModelManager()
def unrelated_registration():
    app.register_blueprint(model_bp)
""")
    _write(repo, 'src/infra/launch_choreography_xyz.py', """from flask import Flask
from src.core.model_manager import ModelManager
from src.api.model_routes import bp as model_bp
def assemble_application():
    app = Flask(__name__)
    app.model_manager = ModelManager()
    app.register_blueprint(model_bp)
    return app
""")
    _materialize(tmp_path)
    assert main(['graph', 'workflow', '--route', '/predict', '--repo-id', 'main', '--json']) == 0
    result = _payload(capsys)['data']['result']
    implementation = next(item for item in result['stages']['implementation']
                          if item['path'] == 'src/core/model_manager.py')
    assert implementation['relation'] == 'source'
    assert 'dispatch unconfirmed' in implementation['evidence']
    assert any(link['from'] == 'src/infra/launch_choreography_xyz.py'
               and link['to'] == 'src/core/model_manager.py'
               and link['relation'] == 'source' for link in result['links'])
    assert not any(link['from'] == 'src/infra/aaa_main.py' and link['relation'] == 'source'
                   for link in result['links'])


@pytest.mark.parametrize(
    ('method', 'http_test_name'),
    [
        ('predict', 'test_predict.py'),
        ('infer', 'test_predict.py'),
        ('predict', 'test_service_contract.py'),
        ('infer', 'test_service_contract.py'),
    ],
)
def test_flask_route_workflow_is_independent_of_method_and_test_filename(
    tmp_path, monkeypatch, capsys, method: str, http_test_name: str,
) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    _write(repo, 'src/api/model_routes.py', f"""from flask import current_app
class Router:
    def route(self, *args, **kwargs):
        return lambda fn: fn
bp = Router()
@bp.route('/predict', methods=['POST'])
def predict():
    current_app.metrics.record('request')
    return current_app.model_manager.{method}()
""")
    _write(repo, 'src/core/model_manager.py', f"""class ModelManager:
    def {method}(self):
        return 42
""")
    _write(repo, 'src/core/metrics.py', """class Metrics:
    def record(self, event):
        return event
""")
    _write(repo, 'src/core/logging.py', """class LogWriter:
    def log(self, message):
        return message
""")
    _write(repo, 'src/bootstrap/assembly.py', """from src.core.model_manager import ModelManager
from src.api.model_routes import bp as model_bp
def build_service():
    app = object()
    app.model_manager = ModelManager()
    app.register_blueprint(model_bp)
    return app
""")
    _write(repo, f'tests/{http_test_name}', """def test_http_request(client):
    response = client.post('/predict', json={})
    assert response.status_code == 200
""")
    _write(repo, 'tests/test_runtime_concurrency.py', f"""from src.core.model_manager import ModelManager
def test_concurrent_operation():
    assert ModelManager().{method}() == 42
""")
    _write(repo, 'tests/test_metrics_decoy.py', """from src.core.metrics import Metrics
def test_monitoring_only():
    assert Metrics().record('request') == 'request'
""")
    _materialize(tmp_path)
    assert main(['graph', 'workflow', '--route', '/predict', '--repo-id', 'main', '--json']) == 0
    result = _payload(capsys)['data']['result']
    implementation = result['stages']['implementation']
    assert [(item['path'], item['relation']) for item in implementation] == [
        ('src/core/model_manager.py', 'source'),
    ]
    tests = {item['path']: item for item in result['stages']['tests']}
    assert tests[f'tests/{http_test_name}']['verification'] == 'python_literal_http_test'
    assert tests['tests/test_runtime_concurrency.py']['verification'] == 'python_imported_class_method_test'
    assert 'tests/test_metrics_decoy.py' not in tests
    assert result['completeness']['source_bytes_scanned'] <= 8 * 1024 * 1024
    assert any(link['to'] == 'src/core/model_manager.py' and link['relation'] == 'source'
               for link in result['links'])


@pytest.mark.parametrize(
    ('body', 'expected'),
    [
        ("return current_app.engine.run(data)", {('run', 'current_app.engine')}),
        ("value = current_app.engine.run(data)\nreturn value", {('run', 'current_app.engine')}),
        (
            "value = current_app.engine.run(data)\n"
            "metrics.record('done')\n"
            "return jsonify({'data': value.to_dict()}), 200",
            {('run', 'current_app.engine')},
        ),
        (
            "value = current_app.engine.run(data)\n"
            "response = {'value': value}\n"
            "return response, 200",
            {('run', 'current_app.engine')},
        ),
        (
            "metrics.record('before')\n"
            "value = current_app.engine.run(data)\n"
            "metrics.record('after')\n"
            "return serializer(value)",
            {('run', 'current_app.engine')},
        ),
        (
            "entity = current_app.catalog.fetch(key)\n"
            "return response_builder(entity)",
            {('fetch', 'current_app.catalog')},
        ),
        (
            "first = current_app.alpha.run(data)\n"
            "second = current_app.beta.run(data)\n"
            "return {'first': first, 'second': second}",
            {('run', 'current_app.alpha'), ('run', 'current_app.beta')},
        ),
        (
            "value = current_app.engine.run(data)\n"
            "value = cached\n"
            "return value",
            set(),
        ),
    ],
)
def test_route_return_value_flow_selects_only_contributors(body, expected) -> None:
    from tools.repoctl.graph_workflow import collect_route_value_flow
    tree = ast.parse('def handler():\n' + textwrap.indent(body, '    '))
    assert collect_route_value_flow(tree.body[0]) == expected


@pytest.mark.parametrize(
    'body',
    [
        "return current_app.model_manager.predict('hash', data)",
        "result = current_app.model_manager.predict('hash', data)\nreturn result",
        "result = current_app.model_manager.predict('hash', data)\nreturn jsonify({'data': result.to_dict()}), 200",
        "result = current_app.model_manager.predict('hash', data)\nresponse = {'data': result}\nreturn response, 200",
        "metrics.record('before')\nresult = current_app.model_manager.predict('hash', data)\nreturn jsonify(result.to_dict())",
        "result = current_app.model_manager.predict('hash', data)\nmetrics.record('after')\nreturn jsonify(result.to_dict())",
    ],
)
def test_flask_route_wrapper_and_metrics_find_wired_implementation_and_tests(
    tmp_path, monkeypatch, capsys, body,
) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    _write(repo, 'src/api/service.py', """class Router:
    def route(self, *args, **kwargs):
        return lambda fn: fn
bp = Router()
""")
    _write(repo, 'src/api/model_routes.py', """from flask import current_app, jsonify
from src.api.service import bp
class Metrics:
    def record(self, event):
        return event
metrics = Metrics()
@bp.route('/predict', methods=['POST'])
def predict():
""" + textwrap.indent(body, '    ') + '\n')
    _write(repo, 'src/core/model_manager.py', """class ModelManager:
    def predict(self, model_hash, data):
        return 42
""")
    _write(repo, 'src/core/decoy.py', """class Logger:
    def predict(self, model_hash, data):
        return 13
""")
    _write(repo, 'src/launcher.py', """from src.core.model_manager import ModelManager
from src.api.model_routes import bp
def build():
    app = object()
    app.model_manager = ModelManager()
    app.register_blueprint(bp)
    return app
""")
    _write(repo, 'tests/test_service_contract.py', """def test_http(client):
    assert client.post('/predict', json={}).status_code == 200
""")
    _write(repo, 'tests/test_concurrency.py', """from src.core.model_manager import ModelManager
def test_implementation_behavior():
    result = ModelManager().predict('hash', [])
    assert result == 42
""")
    _materialize(tmp_path)
    assert main(['graph', 'workflow', '--route', '/predict', '--repo-id', 'main', '--json']) == 0
    result = _payload(capsys)['data']['result']
    impl = result['stages']['implementation']
    assert [(item['path'], item['relation']) for item in impl] == [('src/core/model_manager.py', 'source')]
    tests = {item['path']: item['verification'] for item in result['stages']['tests']}
    assert tests['tests/test_service_contract.py'] == 'python_literal_http_test'
    assert tests['tests/test_concurrency.py'] == 'python_imported_class_method_test'
    assert result['completeness']['source_bytes_scanned'] <= 8 * 1024 * 1024


def test_route_two_return_contributors_keep_both_source_wired_producers(tmp_path, monkeypatch, capsys) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    _write(repo, 'src/api/entry.py', """from flask import current_app
class Router:
    def route(self, *args, **kwargs):
        return lambda fn: fn
bp = Router()
@bp.route('/merge')
def merged():
    first = current_app.alpha.compute()
    second = current_app.beta.compute()
    return {'left': first, 'right': second}
""")
    _write(repo, 'src/core/alpha.py', """class AlphaEngine:
    def compute(self):
        return 1
""")
    _write(repo, 'src/core/beta.py', """class BetaEngine:
    def compute(self):
        return 2
""")
    _write(repo, 'src/factory.py', """from src.api.entry import bp
from src.core.alpha import AlphaEngine
from src.core.beta import BetaEngine
def assemble():
    app = object()
    app.alpha = AlphaEngine()
    app.beta = BetaEngine()
    app.register_blueprint(bp)
    return app
""")
    _write(repo, 'tests/test_generic_contract.py', """def test_http(client):
    assert client.get('/merge').status_code == 200
""")
    _materialize(tmp_path)
    assert main(['graph', 'workflow', '--route', '/merge', '--repo-id', 'main', '--json']) == 0
    result = _payload(capsys)['data']['result']
    assert {(item['path'], item['relation']) for item in result['stages']['implementation']} == {
        ('src/core/alpha.py', 'source'),
        ('src/core/beta.py', 'source'),
    }
    assert result['status'] == 'partial'
    assert any('Multiple return-contributing' in warning for warning in result['warnings'])
    assert result['completeness']['source_bytes_scanned'] <= 8 * 1024 * 1024


def test_route_workflow_survives_six_incremental_project_changes(
    tmp_path, monkeypatch, capsys,
) -> None:
    """The same materialized repo must survive method, test, wrapper and file changes."""
    repo = _workspace(tmp_path, monkeypatch)
    observed = []
    old_http_test = None
    old_implementation = None

    for step in range(7):
        method = 'predict' if step == 0 else 'infer'
        http_test = 'tests/test_predict.py' if step <= 1 else 'tests/test_service_contract.py'
        impl_path = 'src/core/engine.py' if step < 6 else 'src/runtime/engine_impl.py'
        if old_http_test and old_http_test != http_test:
            (repo / old_http_test).unlink()
        if old_implementation and old_implementation != impl_path:
            (repo / old_implementation).unlink()
        old_http_test = http_test
        old_implementation = impl_path

        if step < 3:
            body = f"return current_app.engine.{method}(data)"
        elif step == 3:
            body = (
                f"value = current_app.engine.{method}(data)\n"
                "return jsonify({'value': value.to_dict()}), 200"
            )
        elif step == 4:
            body = (
                "metrics.record('start')\n"
                f"value = current_app.engine.{method}(data)\n"
                "metrics.record('end')\n"
                "return jsonify({'value': value.to_dict()}), 200"
            )
        else:
            body = (
                "metrics.record('start')\n"
                f"value = current_app.engine.{method}(data)\n"
                "metrics.record('end')\n"
                "return serialize_response(value), 200"
            )
        _write(repo, 'src/api/route.py', """from flask import current_app, jsonify
class Router:
    def route(self, *args, **kwargs):
        return lambda fn: fn
bp = Router()
class Metrics:
    def record(self, event):
        pass
metrics = Metrics()
def serialize_response(value):
    return jsonify({'value': value.to_dict()})
@bp.route('/predict', methods=['POST'])
def predict(data=None):
""" + textwrap.indent(body, '    ') + '\n')
        _write(repo, impl_path, f"""class ModelManager:
    def {method}(self, data):
        return 42
""")
        _write(repo, 'src/core/distractor.py', f"""class OtherManager:
    def {method}(self, data):
        return 13
""")
        _write(repo, 'src/wire.py', f"""from {impl_path.removesuffix('.py').replace('/', '.')} import ModelManager
from src.api.route import bp
def create():
    app = object()
    app.engine = ModelManager()
    app.register_blueprint(bp)
    return app
""")
        _write(repo, http_test, """def test_http_contract(client):
    assert client.post('/predict', json={{}}).status_code == 200
""".replace('{{}}', '{}'))
        _write(repo, 'tests/test_concurrency.py', f"""from {impl_path.removesuffix('.py').replace('/', '.')} import ModelManager
def test_implementation_behavior():
    assert ModelManager().{method}([]) == 42
""")

        _materialize(tmp_path)
        assert main(['graph', 'workflow', '--route', '/predict', '--repo-id', 'main', '--json']) == 0
        result = _payload(capsys)['data']['result']
        implementations = result['stages']['implementation']
        assert [(item['path'], item['relation']) for item in implementations] == [(impl_path, 'source')]
        tests = {item['path']: item for item in result['stages']['tests']}
        assert tests[http_test]['verification'] == 'python_literal_http_test'
        assert tests['tests/test_concurrency.py']['verification'] == 'python_imported_class_method_test'
        assert result['completeness']['source_bytes_scanned'] <= 8 * 1024 * 1024
        assert result['status'] == 'partial'  # runtime dispatch was not compiler-confirmed
        assert result['completeness']['graph_freshness'] == 'current'
        observed.append(result['completeness']['source_bytes_scanned'])

    assert len(observed) == 7
    assert max(observed) < 2 * min(observed)


def test_benchmark_current_source_alias_requires_explicit_refresh_witness() -> None:
    from tools.repoctl.graph_workflow import evaluate_workflow_benchmark
    case={
        'id':'clean-refresh', 'required_stages':['current_source','refresh','implementation'],
        'expected_statuses':['partial'],
        'expected_paths':{'current_source':['lib/main.dart'],'implementation':['lib/main.dart']},
    }
    implementation={'path':'lib/main.dart','relation':'confirmed','evidence':'provider symbol'}
    result={'status':'partial','stages':{'implementation':[implementation],'tests':[]}}
    before=evaluate_workflow_benchmark(result,case)
    assert before['stages']['current_source']['found_count']==1
    assert before['required_stages']['current_source']['precise'] is True
    assert before['required_stages']['refresh']['status']=='not_evaluated'
    assert before['task_navigation_complete'] is False
    # A query must not fabricate that Graph was rebuilt; an independent
    # verified before/after materialization can explicitly attach this proof.
    result['refresh']={'source_rechecked':True,'status':'current'}
    after=evaluate_workflow_benchmark(result,case)
    assert after['required_stages_precise'] is True
    assert after['task_navigation_complete'] is True


def test_cops_stale_empty_then_explicit_rebuild_proves_required_refresh(tmp_path,monkeypatch,capsys) -> None:
    from tools.repoctl.graph_workflow import evaluate_workflow_benchmark
    repo=_workspace(tmp_path,monkeypatch)
    _write(repo,'README.txt','initial empty source inventory\n')
    _materialize(tmp_path)
    _write(repo,'lib/main.dart','void main() => launch();\nvoid launch() {}\n')
    assert main(['graph','workflow','--symbol','main','--in-file','lib/main.dart','--repo-id','main','--json'])==0
    before=_payload(capsys)['data']
    assert before['freshness']['status']=='stale'
    assert before['result']['status']=='partial'
    assert not any(item['relation']=='confirmed' for item in before['result']['stages']['implementation'])
    _materialize(tmp_path)
    assert main(['graph','workflow','--symbol','main','--in-file','lib/main.dart','--repo-id','main','--json'])==0
    after=_payload(capsys)['data']
    assert after['freshness']['status']=='current'
    implementation = after['result']['stages']['implementation'][0]
    assert implementation['relation'] in {'confirmed','source'}
    case={
        'id':'cops-source-refreshed',
        'expected_statuses':['partial'],
        'required_stages':['current_source','refresh','implementation'],
        'expected_paths':{'current_source':['lib/main.dart'],'implementation':['lib/main.dart']},
    }
    score_before=evaluate_workflow_benchmark(before['result'],case)
    assert score_before['required_stages_precise'] is False
    assert score_before['task_navigation_complete'] is False
    # This proof is constructed only after the test *actually* rebuilt the
    # isolated snapshot and observed a changed source become current. Normal
    # graph workflow never claims a refresh happened without that action.
    after_result=dict(after['result'])
    after_result['refresh']={
        'status':'current',
        'source_rechecked':before['freshness']['status']=='stale' and after['freshness']['status']=='current',
    }
    score_after=evaluate_workflow_benchmark(after_result,case)
    assert score_after['required_stages_covered'] is True
    assert score_after['required_stages']['refresh']['precise'] is True
    if implementation['relation']=='confirmed':
        assert score_after['required_stages_precise'] is True
        assert score_after['task_navigation_complete'] is True
    else:
        # The rebuild happened, but absent Dart semantic tooling does not
        # upgrade source navigation to a confirmed implementation symbol.
        assert score_after['required_stages_precise'] is False
        assert score_after['task_navigation_complete'] is False


def test_predict_route_omits_unrelated_cleanup_test_that_only_imports_manager(tmp_path, monkeypatch, capsys) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    _python_route_fixture(repo)
    _write(repo, 'tests/test_model_manager_cleanup.py', '''from src.core.model_manager import ModelManager

def test_cleanup_only():
    manager = ModelManager()
    assert manager is not None
''')
    _write(repo, 'tests/test_predict_fake.py', '''from src.core.model_manager import ModelManager

def test_predict_unrelated():
    fake = object()
    fake.predict()
''')
    _write(repo, 'tests/test_predictive_route.py', '''def test_predictive_route(client):
    client.post('/predictive')
    # client.post('/predict') is a comment, not a test.
''')
    _materialize(tmp_path)
    assert main(['graph','workflow','--route','/predict','--json']) == 0
    result = _payload(capsys)['data']['result']
    paths = {item['path'] for item in result['stages']['tests']}
    assert 'tests/test_model_manager_cleanup.py' not in paths
    assert 'tests/test_predict_fake.py' not in paths
    assert 'tests/test_predictive_route.py' not in paths
    assert 'tests/test_model_manager_concurrency.py' in paths
    assert 'tests/test_predict.py' in paths


def test_flask_factories_in_separate_functions_do_not_prove_one_app_wiring(tmp_path,monkeypatch,capsys) -> None:
    repo=_workspace(tmp_path,monkeypatch)
    _python_route_fixture(repo)
    _write(repo,'src/api/model_routes.py', '''from flask import current_app
class Router:
    def route(self,*a,**kw):
        return lambda f: f
bp=Router()
@bp.route('/predict',methods=['POST'])
def predict():
    return current_app.model_manager.predict()
''')
    _write(repo,'src/main.py', '''from src.core.model_manager import ModelManager
from src.api.model_routes import bp as model_bp

def factory_a():
    app=object()
    app.model_manager=ModelManager()

def factory_b():
    app=object()
    app.register_blueprint(model_bp)
''')
    _materialize(tmp_path)
    assert main(['graph','workflow','--route','/predict','--json'])==0
    result=_payload(capsys)['data']['result']
    impl=next(item for item in result['stages']['implementation'] if item['path']=='src/core/model_manager.py')
    assert impl['relation']=='candidate'
    assert not any(item['from']=='src/main.py' and item['to']=='src/core/model_manager.py'
                   for item in result['links'])


@pytest.mark.parametrize(
    "factory_body",
    [
        """def create_app():
    app = object()
    def never_called_assignment():
        app.model_manager = ModelManager()
    def never_called_registration():
        app.register_blueprint(model_bp)
    return app
""",
        """def create_app():
    app = object()
    if False:
        app.model_manager = ModelManager()
        app.register_blueprint(model_bp)
    return app
""",
        """def create_app(flag):
    app = object()
    if flag:
        app.model_manager = ModelManager()
    else:
        app.register_blueprint(model_bp)
    return app
""",
        """def create_app():
    app = object()
    app.other_manager = ModelManager()
    app.register_blueprint(model_bp)
    return app
""",
    ],
    ids=["uncalled-nested-definitions", "dead-branch", "mutually-exclusive-branches", "unrelated-receiver"],
)
def test_flask_factory_unexecutable_wiring_cannot_claim_source(
    tmp_path, monkeypatch, capsys, factory_body: str,
) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    _python_route_fixture(repo)
    _write(repo, 'src/api/model_routes.py', '''from flask import current_app
class Router:
    def route(self,*a,**kw):
        return lambda f: f
bp=Router()
@bp.route('/predict',methods=['POST'])
def predict():
    return current_app.model_manager.predict()
''')
    _write(repo, 'src/main.py',
           'from src.core.model_manager import ModelManager\n'
           'from src.api.model_routes import bp as model_bp\n\n' + factory_body)
    _write(repo, 'src/core/unrelated.py', '''class ModelManager:
    def predict(self):
        return -1
''')
    _materialize(tmp_path)
    assert main(['graph', 'workflow', '--route', '/predict', '--json']) == 0
    result = _payload(capsys)['data']['result']
    assert all(item['relation'] != 'source'
               for item in result['stages']['implementation'])
    assert not any(edge['from'] == 'src/main.py' and edge['relation'] == 'source'
                   for edge in result['links'])


def test_flask_wiring_generalizes_to_other_receiver_and_class_names(tmp_path,monkeypatch,capsys) -> None:
    repo=_workspace(tmp_path,monkeypatch)
    _write(repo,'src/api/payments.py', '''from flask import current_app
class Router:
    def route(self,*a,**kw):
        return lambda f: f
bp=Router()
@bp.route('/charge',methods=['POST'])
def charge():
    return current_app.gateway.charge()
''')
    _write(repo,'src/core/payments.py', '''class PaymentGateway:
    def charge(self):
        return 'charged'
''')
    _write(repo,'src/app.py', '''from src.api.payments import bp as payment_bp
from src.core.payments import PaymentGateway

def create_app():
    app = object()
    app.gateway = PaymentGateway()
    app.register_blueprint(payment_bp)
    return app
''')
    _materialize(tmp_path)
    assert main(['graph','workflow','--route','/charge','--json'])==0
    result=_payload(capsys)['data']['result']
    impl=next(item for item in result['stages']['implementation'] if item['path']=='src/core/payments.py')
    assert impl['relation']=='source'
    assert 'dispatch unconfirmed' in impl['evidence']
    assert any(edge['from']=='src/app.py' and edge['to']=='src/core/payments.py'
               and edge['relation']=='source' for edge in result['links'])
    assert result['status']=='partial'


def test_benchmark_explicit_verified_refresh_from_stale_source_can_pass(tmp_path,monkeypatch,capsys) -> None:
    """Only a real before/after materialization can satisfy refresh stage."""
    repo=_workspace(tmp_path,monkeypatch)
    _write(repo,'README.txt','initial repo\n')
    _materialize(tmp_path)
    _write(repo,'src/start.py','def start():\n    return 42\n')
    fixture=tmp_path/'one-refresh-fixture.json'
    fixture.write_text(json.dumps({
        'schema':'repoctl.graph.workflow-benchmark',
        'cases':[{ 'id':'stale-python', 'repo_id':'main',
            'selector':{'symbol':'start','in_file':'src/start.py'},
            'expected_paths':{'current_source':['src/start.py'],'implementation':['src/start.py']},
            'expected_statuses':['partial'],
            'required_stages':['current_source','refresh','implementation'],
        }],
    }),encoding='utf-8')
    args=['graph','workflow-benchmark','--repo-id','main','--case-id','stale-python',
          '--fixture',str(fixture),'--json']
    assert main(args)==1
    before=_payload(capsys)
    assert before['data']['freshness']['status']=='stale'
    assert before['data']['benchmark']['required_stages']['refresh']['precise'] is False
    assert main([*args[:-1],'--verify-refresh','--json'])==0
    after=_payload(capsys)
    assert after['ok'] is True
    assert after['data']['freshness']['status']=='current'
    assert after['data']['benchmark']['task_navigation_complete'] is True
    proof=after['data']['refresh']
    assert proof['source_rechecked'] is True and proof['status']=='current'
    assert proof['before_status']=='stale' and proof['after_status']=='current'
    assert proof['before_snapshot_digest']!=proof['after_snapshot_digest']
    assert 'src/start.py' in proof['changed_source_paths']
    # A second call cannot invent another stale->current transition after
    # this explicit rebuild has already finished.
    assert main([*args[:-1],'--verify-refresh','--json'])==1
    current=_payload(capsys)
    assert current['data']['refresh']['status']=='not_evaluated'
    assert current['data']['benchmark']['required_stages']['refresh']['precise'] is False


def test_benchmark_verify_refresh_requires_refresh_contract(tmp_path,monkeypatch,capsys) -> None:
    repo=_workspace(tmp_path,monkeypatch)
    _write(repo,'src/start.py','def start():\n    return 42\n')
    _materialize(tmp_path)
    fixture=tmp_path/'no-refresh-fixture.json'
    fixture.write_text(json.dumps({
        'schema':'repoctl.graph.workflow-benchmark',
        'cases':[{'id':'no-refresh','repo_id':'main','selector':{'symbol':'start'},
            'expected_paths':{'implementation':['src/start.py']},
            'expected_statuses':['partial'],'required_stages':['implementation']}],
    }),encoding='utf-8')
    assert main(['graph','workflow-benchmark','--repo-id','main','--case-id','no-refresh',
        '--fixture',str(fixture),'--verify-refresh','--json'])==2
    output=_payload(capsys)
    assert output['problems'][0]['code']=='graph_benchmark_refresh_not_required'


def test_explicit_dart_refresh_proves_change_without_fabricating_provider_precision(tmp_path,monkeypatch,capsys) -> None:
    repo=_workspace(tmp_path,monkeypatch)
    _write(repo,'README.txt','initial inventory\n')
    _materialize(tmp_path)
    _write(repo,'lib/main.dart','void main() {}\n')
    fixture=tmp_path/'dart-refresh.json'
    fixture.write_text(json.dumps({
        'schema':'repoctl.graph.workflow-benchmark',
        'cases':[{'id':'dart-current-rebuild','repo_id':'main',
            'selector':{'symbol':'main','in_file':'lib/main.dart'},
            'expected_paths':{'current_source':['lib/main.dart'],'implementation':['lib/main.dart']},
            'expected_statuses':['partial'],
            'required_stages':['current_source','refresh','implementation']}],
    }),encoding='utf-8')
    args=['graph','workflow-benchmark','--case-id','dart-current-rebuild',
          '--fixture',str(fixture),'--repo-id','main','--verify-refresh','--json']
    code=main(args)
    data=_payload(capsys)['data']
    assert data['refresh']['source_rechecked'] is True
    assert data['freshness']['status']=='current'
    assert data['benchmark']['required_stages']['refresh']['precise'] is True
    implementation_status=data['benchmark']['required_stages']['implementation']['precise']
    assert data['benchmark']['task_navigation_complete'] == implementation_status
    assert code==(0 if implementation_status else 1)
