#!/usr/bin/env python3
"""Ensure the integration gate rejects deliberately broken temporary engines."""

import ast
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def check(function, field):
    with tempfile.TemporaryDirectory(prefix=f"sonder-broken-{function}-") as td:
        copy = Path(td) / "sonder"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.pyc", "demo-paper-revision"))
        engine = copy / "library/learn.py"
        tree = ast.parse(engine.read_text())
        original = next(n for n in tree.body
                        if isinstance(n, ast.FunctionDef) and n.name == function)
        original.name = "_fault_original_" + function
        rows = "result.values()" if function == "bias" else "result"
        wrapper = ast.parse(f"""
def {function}(*args, **kwargs):
    result = {original.name}(*args, **kwargs)
    for row in {rows}:
        row[{field!r}] = 0
    return result
""").body[0]
        tree.body.insert(tree.body.index(original) + 1, wrapper)
        engine.write_text(ast.unparse(ast.fix_missing_locations(tree)) + "\n")
        p = subprocess.run(["bash", "tests/run_e2e.sh"], cwd=copy,
                           capture_output=True, text=True, timeout=60)
        if p.returncode == 0 or "Learning integration PASS" in p.stdout:
            raise SystemExit(f"FAULT NOT DETECTED: {function}\n{p.stdout}\n{p.stderr}")
        if "FAIL:" not in p.stderr:
            raise SystemExit(f"Gate errored without an assertion failure: {p.stderr}")
        failures = [line for line in p.stderr.splitlines() if line.startswith("FAIL:")]
        print(f"Detected {function} returning zero {field}; exit={p.returncode}")
        for line in failures:
            print("  " + line)


def check_rank_reversal():
    """The phrase-ranking assertions must catch an engine that reverses rank."""
    with tempfile.TemporaryDirectory(prefix="sonder-broken-rank-") as td:
        copy = Path(td) / "sonder"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.pyc", "demo-paper-revision"))
        engine = copy / "library/learn.py"
        tree = ast.parse(engine.read_text())
        original = next(n for n in tree.body
                        if isinstance(n, ast.FunctionDef) and n.name == "rank_candidates")
        original.name = "_fault_original_rank_candidates"
        wrapper = ast.parse("""
def rank_candidates(*args, **kwargs):
    result = _fault_original_rank_candidates(*args, **kwargs)
    result.reverse()
    return result
""").body[0]
        tree.body.insert(tree.body.index(original) + 1, wrapper)
        engine.write_text(ast.unparse(ast.fix_missing_locations(tree)) + "\n")
        p = subprocess.run(["bash", "tests/run_e2e.sh"], cwd=copy,
                           capture_output=True, text=True, timeout=60)
        if p.returncode == 0 or "Learning integration PASS" in p.stdout:
            raise SystemExit("FAULT NOT DETECTED: rank_candidates reversal")
        print(f"Detected rank_candidates reversal; exit={p.returncode}")


def check_syntax_disabled():
    """The structure-learning regression must catch a disabled syntax adapter."""
    with tempfile.TemporaryDirectory(prefix="sonder-broken-syntax-") as td:
        copy = Path(td) / "sonder"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.pyc", "demo-paper-revision"))
        engine = copy / "library/learn.py"
        tree = ast.parse(engine.read_text())
        original = next(n for n in tree.body
                        if isinstance(n, ast.FunctionDef) and n.name == "_syntax_features")
        original.name = "_fault_original_syntax_features"
        wrapper = ast.parse("""
def _syntax_features(*args, **kwargs):
    return [], "off"
""").body[0]
        tree.body.insert(tree.body.index(original) + 1, wrapper)
        engine.write_text(ast.unparse(ast.fix_missing_locations(tree)) + "\n")
        p = subprocess.run(["bash", "tests/run_e2e.sh"], cwd=copy,
                           capture_output=True, text=True, timeout=60)
        if p.returncode == 0 or "Learning integration PASS" in p.stdout:
            raise SystemExit("FAULT NOT DETECTED: syntax adapter disabled")
        if "FAIL:" not in p.stderr:
            raise SystemExit(f"Gate errored without an assertion failure: {p.stderr}")
        print(f"Detected syntax adapter disabled; exit={p.returncode}")


def check_helper_replacement(function, body, relative_path="library/learn.py", *, test_pattern=None):
    """Mutate real behavior; require assertion failure, not an import error."""
    with tempfile.TemporaryDirectory(prefix=f"sonder-broken-{function}-") as td:
        copy = Path(td) / "sonder"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.pyc", "demo-paper-revision"))
        engine = copy / relative_path
        tree = ast.parse(engine.read_text())
        original = next(n for n in tree.body
                        if isinstance(n, ast.FunctionDef) and n.name == function)
        original.body = ast.parse(body).body
        engine.write_text(ast.unparse(ast.fix_missing_locations(tree)) + "\n")
        command = ([sys.executable, '-B', '-I', '-m', 'unittest', 'discover', '-s', 'tests', '-p', test_pattern]
                   if test_pattern else ["bash", "tests/run_e2e.sh"])
        p = subprocess.run(command, cwd=copy,
                           capture_output=True, text=True, timeout=60)
        if p.returncode == 0 or "Learning integration PASS" in p.stdout:
            raise SystemExit(f"FAULT NOT DETECTED: {function}")
        if "FAIL:" not in p.stderr:
            raise SystemExit(f"Gate errored without an assertion failure: {p.stderr}")
        print(f"Detected broken {function}; exit={p.returncode}")


def check_receipt_export_faults():
    """Focused export mutations must fail assertions against an otherwise healthy suite."""
    pattern = 'test_receipt_export.py'
    baseline = subprocess.run([sys.executable, '-B', '-I', '-m', 'unittest', 'discover',
                               '-s', 'tests', '-p', pattern], cwd=ROOT,
                              capture_output=True, text=True, timeout=60)
    if baseline.returncode:
        raise SystemExit('Receipt export baseline is not healthy.\n' + baseline.stdout + baseline.stderr)

    def mutate(function, body):
        check_helper_replacement(function, body, test_pattern=pattern)

    mutate('_preflight_receipt_export', 'return None')
    mutate('_publish_receipt_file', """
with open(output_path, 'wb') as stream:
    stream.write(content)
return False
""")
    mutate('_sync_receipt_directory', 'return None')
    tree = ast.parse((ROOT / 'library/learn.py').read_text())
    record = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_receipt_export_record')
    body = '\n'.join(ast.unparse(n) for n in record.body)
    assert body.count("'result': json.loads(row[1])") == 1
    mutate('_receipt_export_record', body.replace("'result': json.loads(row[1])", "'result': {**json.loads(row[1]), 'replayed': True}"))

    export = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'choice_file_export')
    body = '\n'.join(ast.unparse(n) for n in export.body)
    marker = 'result = _choice_data('
    assert body.count(marker) == 1
    mutate('choice_file_export', body.replace(marker, 'data, candidates = _candidate_data(path)\n' + marker))
    verification = "_receipt_export_record(db, data['round_id'], payload_hash)"
    assert body.count(verification) == 1
    mutate('choice_file_export', body.replace(verification, "_receipt_export_record(db, data['round_id'])"))
    # A correct-looking sidecar must never let a retry skip current core guards.
    shortcut = """
if os.path.isfile(output_path):
    with open(output_path, 'r', encoding='utf-8') as stream:
        saved = json.load(stream)
    return {**saved['result'], 'replayed': True, 'receipt_file': output_path, 'receipt_file_reused': True}
"""
    mutate('choice_file_export', body.replace(marker, shortcut + marker))
    mutate('_receipt_export_path', 'return os.path.abspath(os.fspath(output_path))')
    read = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_read_receipt_file')
    body = '\n'.join(ast.unparse(n) for n in read.body)
    assert body.count('os.fsync(stream.fileno())') == 1
    mutate('_read_receipt_file', body.replace('os.fsync(stream.fileno())', 'pass'))


def check_missing_parser_prerequisite():
    """A successful heuristic fallback must not mask invalid Stanza wiring."""
    tree = ast.parse((ROOT / "library/learn.py").read_text())
    loader = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_stanza_pipeline")
    body = "\n".join(ast.unparse(statement) for statement in loader.body)
    required = "tokenize,pos,lemma,depparse"
    assert body.count(required) == 1, "locate the real adapter processor list"
    check_helper_replacement("_stanza_pipeline", body.replace(required, "tokenize,pos,depparse"))


def check_adjunct_placement_disabled(relation):
    tree = ast.parse((ROOT / "library/learn.py").read_text())
    function = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "_grammar_adjunct_evidence")
    body = "\n".join(ast.unparse(statement) for statement in function.body)
    assert body.count("return evidence") == 1
    check_helper_replacement("_grammar_adjunct_evidence", body.replace(
        "return evidence", f"return [item for item in evidence if item[0] != {relation!r}]"))


def check_additive_label_score():
    tree = ast.parse((ROOT / "library/learn.py").read_text())
    function = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "rank_candidates")
    body = "\n".join(ast.unparse(statement) for statement in function.body)
    score = "score = phrase_score + SYNTAX_SCALE * syntax_score"
    assert body.count(score) == 1
    check_helper_replacement("rank_candidates", body.replace(score, score + " + label_weight * 0.001"))


def check_optout_disabled():
    tree = ast.parse((ROOT / "library/learn.py").read_text())
    function = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "_choice_data")
    assignments = [n for n in function.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "learn_from_choice" for t in n.targets)]
    assert len(assignments) == 1
    assignments[0].value = ast.Constant(value=True)
    check_helper_replacement("_choice_data", "\n".join(ast.unparse(n) for n in function.body))


def check_optout_identity_omitted():
    tree = ast.parse((ROOT / "library/learn.py").read_text())
    function = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "_choice_payload_hash")
    conditions = [n for n in function.body if isinstance(n, ast.If)
                  and isinstance(n.test, ast.UnaryOp) and isinstance(n.test.operand, ast.Name)
                  and n.test.operand.id == "learn_from_choice"]
    assert len(conditions) == 1
    function.body.remove(conditions[0])
    check_helper_replacement("_choice_payload_hash", "\n".join(ast.unparse(n) for n in function.body))


def check_analysis_score_fault(coupled):
    tree = ast.parse((ROOT / "library/learn.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "rank_candidates")
    replacements = {"analysis_score": "0.0"}
    if coupled:
        replacements["base_syntax_score"] = "(syntax_total - sum(item['weight'] for item in grammar_matched) - topology_total) / max(1, base_count + analysis_capacity - topology_capacity)"
    changed = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in replacements:
                    node.value = ast.parse(replacements[target.id], mode='eval').body
                    changed.add(target.id)
    assert changed == set(replacements)
    check_helper_replacement("rank_candidates", "\n".join(ast.unparse(n) for n in function.body))


def check_edit_scope_identity_omitted():
    tree = ast.parse((ROOT / "library/learn.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_choice_payload_hash")
    conditions = [n for n in function.body if isinstance(n, ast.If)
                  and any(isinstance(child, ast.Name) and child.id == "edit_scope" for child in ast.walk(n.test))]
    assert len(conditions) == 1
    function.body.remove(conditions[0])
    check_helper_replacement("_choice_payload_hash", "\n".join(ast.unparse(n) for n in function.body))


def check_topology_negative_gate_disabled():
    tree = ast.parse((ROOT / "library/learn.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_choice_data')
    assignments = [n for n in ast.walk(function) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'analysis_topology_negative_feedback' for t in n.targets)]
    assert len(assignments) == 1
    assignments[0].value = ast.Name(id='analysis_negative_feedback', ctx=ast.Load())
    check_helper_replacement('_choice_data', '\n'.join(ast.unparse(n) for n in function.body))


def check_numeric_phrase_filter_disabled():
    tree = ast.parse((ROOT / 'library/learn.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_phrase_features')
    body = '\n'.join(ast.unparse(n) for n in function.body)
    guard = ' and token[0].isalpha()'
    assert body.count(guard) == 1
    check_helper_replacement('_phrase_features', body.replace(guard, ''))


def check_slot_certainty_ignored(function_name):
    tree = ast.parse((ROOT / 'library/learn.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function_name)
    class IgnoreCertainty(ast.NodeTransformer):
        def visit_Call(self, node):
            node = self.generic_visit(node)
            if (isinstance(node.func, ast.Attribute) and node.func.attr == 'get'
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == 'slots_complete'):
                return ast.copy_location(ast.Constant(value=True), node)
            return node
    mutated = IgnoreCertainty().visit(function)
    check_helper_replacement(function_name, '\n'.join(ast.unparse(n) for n in mutated.body))


def check_link_pair_gate_disabled():
    tree = ast.parse((ROOT / 'library/learn.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_choice_data')
    assignments = [node for node in ast.walk(function) if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'link_pair_negative_feedback'
                           for target in node.targets)]
    assert len(assignments) == 1
    assignments[0].value = ast.Constant(value=True)
    check_helper_replacement('_choice_data', '\n'.join(ast.unparse(n) for n in function.body))


def check_link_pair_uncertainty_disabled():
    tree = ast.parse((ROOT / 'library/learn.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_grammar_unknown_counts')
    body = '\n'.join(ast.unparse(n) for n in function.body)
    assert body.count('return counts') == 2
    body = body.rsplit('return counts', 1)[0] + "counts['link_pair_order'] = 0\nreturn counts"
    check_helper_replacement('_grammar_unknown_counts', body)


def check_link_identity_duplicates():
    tree = ast.parse((ROOT / 'library/learn.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_grammar_observation_groups')
    body = '\n'.join(ast.unparse(n) for n in function.body)
    assert body.count("identity = ('link',") == 1
    check_helper_replacement('_grammar_observation_groups',body.replace("identity = ('link',", "identity = ('link', frame['start'], frame['end'],"))


def check_link_identity_conflict():
    tree = ast.parse((ROOT / 'library/learn.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_grammar_observations')
    class IgnoreConflict(ast.NodeTransformer):
        def visit_IfExp(self,node):
            node=self.generic_visit(node)
            if 'values' in ast.unparse(node.test):
                return ast.copy_location(ast.parse("next(iter(values))",mode='eval').body,node)
            return node
    modified=IgnoreConflict().visit(function)
    check_helper_replacement('_grammar_observations','\n'.join(ast.unparse(n) for n in modified.body))


def check_expression_identity_duplicates():
    tree = ast.parse((ROOT / 'library/learn.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_grammar_observation_groups')
    body = '\n'.join(ast.unparse(n) for n in function.body)
    assert body.count("identity = ('expression',") == 1
    check_helper_replacement('_grammar_observation_groups',body.replace("identity = ('expression',", "identity = ('expression', frame['start'], frame['end'],"))


def check_observation_role_consensus_disabled():
    tree = ast.parse((ROOT / 'library/learn.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_grammar_observations')
    body = '\n'.join(ast.unparse(n) for n in function.body)
    assert body.count('len(roles) == 1 and') == 1
    check_helper_replacement('_grammar_observations',body.replace('len(roles) == 1 and','True and'))


def check_binding_capacity_regression(kind):
    """Detect the old four-slot and sixteen-frame binder ceilings."""
    tree = ast.parse((ROOT / 'library/learn.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_bind_annotation')
    body = '\n'.join(ast.unparse(n) for n in function.body)
    target = ("len(_grammar_slot_roles(annotation['version'], entry.get('kind')))"
              if kind == 'slots' else 'else MAX_GRAMMAR_FRAMES')
    assert body.count(target) == 1
    replacement = '4' if kind == 'slots' else 'else 16'
    check_helper_replacement('_bind_annotation', body.replace(target, replacement))


def check_rank_snapshot_disabled():
    """Concurrent feedback must not split one rank across database histories."""
    tree = ast.parse((ROOT / 'library/learn.py').read_text())
    function = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == 'rank_candidates')
    body = '\n'.join(ast.unparse(n) for n in function.body)
    snapshot = "conn.execute('BEGIN')"
    assert body.count(snapshot) == 1
    check_helper_replacement('rank_candidates', body.replace(snapshot, 'pass'))


def check_formatting_continuity_disabled():
    """Removing supported wrappers must preserve shared positive evidence."""
    tree = ast.parse((ROOT / 'library/learn.py').read_text())
    function = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == '_text_feature_views')
    body = '\n'.join(ast.unparse(n) for n in function.body)
    guard = 'command in _TEXT_FORMAT_TEX_COMMANDS'
    assert body.count(guard) == 1
    check_helper_replacement('_text_feature_views', body.replace(guard, 'False'))


def check_syntax_formatting_view_disabled():
    """A phrase-only repair must still fail the base-syntax regression."""
    tree = ast.parse((ROOT / 'library/learn.py').read_text())
    function = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == '_syntax_features')
    body = '\n'.join(ast.unparse(n) for n in function.body)
    view_nodes = [node for node in ast.walk(function) if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == 'text' for target in node.targets)]
    assert len(view_nodes) == 1
    view = ast.unparse(view_nodes[0])
    assert body.count(view) == 1
    check_helper_replacement('_syntax_features', body.replace(view, 'pass'))


def check_markdown_emphasis_disabled():
    check_helper_replacement('_markdown_emphasis_positions', 'return set()')


def check_markdown_cli_format_dropped():
    tree = ast.parse((ROOT / 'library/learn.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == 'rank_candidates']
    assert len(calls) == 1 and len(calls[0].keywords) == 1
    calls[0].keywords = []
    check_helper_replacement('main', '\n'.join(ast.unparse(n) for n in function.body))


def check_markdown_choice_format_dropped():
    tree = ast.parse((ROOT / 'library/learn.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_choice_data')
    assignments = [node for node in ast.walk(function) if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'format_options' for target in node.targets)]
    assert len(assignments) == 1
    assignments[0].value = ast.Dict(keys=[], values=[])
    check_helper_replacement('_choice_data', '\n'.join(ast.unparse(n) for n in function.body))


def check_exemplar_cache_surface_disabled():
    tree = ast.parse((ROOT / 'library/learn.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_upgrade_exemplar_index')
    body = '\n'.join(ast.unparse(n) for n in function.body)
    view = '_exemplar_surface(text, text_format)'
    assert body.count(view) == 1
    check_helper_replacement('_upgrade_exemplar_index', body.replace(view, 'text'))


def check_exemplar_choice_mode_dropped():
    tree = ast.parse((ROOT / 'library/learn.py').read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_choice_data')
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == '_store_exemplar']
    assert len(calls) == 1 and any(keyword.arg is None for keyword in calls[0].keywords)
    calls[0].keywords = [keyword for keyword in calls[0].keywords if keyword.arg is not None]
    check_helper_replacement('_choice_data', '\n'.join(ast.unparse(n) for n in function.body))


if __name__ == "__main__":
    # A pre-existing failure must not make every unrelated mutation look caught.
    baseline = subprocess.run(["bash", "tests/run_e2e.sh"], cwd=ROOT,
                              capture_output=True, text=True, timeout=60)
    if baseline.returncode or "Learning integration PASS" not in baseline.stdout:
        raise SystemExit("Baseline gate is not healthy; no fault-detection result is valid.\n"
                         + baseline.stdout + baseline.stderr)
    check_receipt_export_faults()
    check("bias", "weight")
    check("nearest", "sim")
    check_rank_reversal()
    check_rank_snapshot_disabled()
    check_formatting_continuity_disabled()
    check_syntax_formatting_view_disabled()
    check_markdown_emphasis_disabled()
    check_markdown_cli_format_dropped()
    check_markdown_choice_format_dropped()
    check_exemplar_cache_surface_disabled()
    check_exemplar_choice_mode_dropped()
    phrase_tree = ast.parse((ROOT / 'library/learn.py').read_text())
    phrase = next(n for n in phrase_tree.body if isinstance(n, ast.FunctionDef) and n.name == '_phrase_features')
    phrase_body = '\n'.join(ast.unparse(statement) for statement in phrase.body)
    assert phrase_body.count('return sorted(features)') == 1
    check_helper_replacement('_phrase_features', phrase_body.replace('return sorted(features)', 'return sorted(features)[:256]'))

    time_tree = ast.parse((ROOT / 'library/learn.py').read_text())
    choice = next(n for n in time_tree.body if isinstance(n, ast.FunctionDef) and n.name == '_choice_data')
    choice_body = '\n'.join(ast.unparse(statement) for statement in choice.body)
    timestamp = 'now = _parse_time(_now_iso())'
    assert choice_body.count(timestamp) == 2
    check_helper_replacement('_choice_data', 'early_time = _parse_time(_now_iso())\n' + choice_body.replace(timestamp, 'now = early_time'))
    shared_time = 'now=now'
    assert choice_body.count(shared_time) == 1
    check_helper_replacement('_choice_data', choice_body.replace(', now=now', ''))

    tree = ast.parse((ROOT / 'library/learn.py').read_text())
    validator = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_validated_candidates')
    body = '\n'.join(ast.unparse(statement) for statement in validator.body)
    guard = "if any((field in item for field in ('summary_version', 'omitted_fields', 'matched_feature_counts'))):"
    assert body.count(guard) == 1
    check_helper_replacement('_validated_candidates', body.replace(guard, 'if False:'))

    check_helper_replacement("_rank_summary", "return ranked")
    check_helper_replacement("_rank_summary", """
omitted = {'analysis', 'grammar', 'matched_features', 'matched_syntax_features', 'matched_analysis_features', 'matched_grammar_features', 'matched_analysis_topology_features'}
return [{**{k: v for k, v in row.items() if k not in omitted}, 'summary_version': 1, 'omitted_fields': sorted(omitted.intersection(row)), 'matched_feature_counts': {'phrase': 0, 'syntax': 0, 'analysis': 0, 'grammar': 0, 'analysis_topology': 0}, 'score': 0} for row in ranked]
""")
    check_additive_label_score()
    check_optout_disabled()
    check_optout_identity_omitted()
    check_helper_replacement("_validated_edit_scope", "return value")
    check_edit_scope_identity_omitted()
    check_analysis_score_fault(coupled=True)
    check_analysis_score_fault(coupled=False)
    check_helper_replacement("_require_candidate_context", "return None")
    check_syntax_disabled()
    check_missing_parser_prerequisite()
    check_adjunct_placement_disabled('temporal')
    check_adjunct_placement_disabled('comparison')
    check_helper_replacement("_grammar_draft_version", "return 3")
    check_helper_replacement("_stanza_resource_stamp", "return ()")
    check_helper_replacement("_stanza_model_fingerprint", "return '0' * 64")
    check_helper_replacement("_syntax_feature_name", """
namespace = re.sub(r"[^A-Za-z0-9_.-]+", "_", namespace)[:48] or "unknown"
return f"syntax:{SYNTAX_FEATURE_VERSION}:{namespace}:{kind}:{value}"
""")
    check_helper_replacement("_surface_cue_allowed", "return True")
    check_helper_replacement("_round_feature_feedback_tx", """
for feature in sorted(selected):
    bump(conn, context, feature, reward, 1, now)
for feature in sorted(alternatives):
    bump(conn, context, feature, -penalty, 0, now)
""")
    check_helper_replacement("_base_syntax_comparable", "return True")
    check_helper_replacement("_choice_receipt", "return None")
    check_helper_replacement("_require_choice_round_id", "return None")
    check_helper_replacement("_choice_payload_hash", "return 'constant-decision-hash'")
    check_helper_replacement("_analysis_features", "return []")
    check_helper_replacement("_analysis_stable_features", "return _analysis_run_features(runs)")
    check_helper_replacement("_analysis_feature_capacity", "return len(_analysis_features(analysis))")
    check_helper_replacement("_analysis_topology_features", "return []")
    check_helper_replacement("_analysis_topology_capacity", "return len(_analysis_topology_features(analysis))")
    check_helper_replacement("_phrase_prose", "return text")
    check_numeric_phrase_filter_disabled()
    check_topology_negative_gate_disabled()
    check_helper_replacement("_validated_analysis", "return value")
    check_helper_replacement("_analysis_runs", "return [tuple(span[key] for key in ('move', 'polarity', 'certainty')) for span in spans]")
    check_helper_replacement("_grammar_features", "return []")
    check_helper_replacement("_grammar_link_pair_evidence", "return []")
    check_link_pair_gate_disabled()
    check_link_pair_uncertainty_disabled()
    check_link_identity_duplicates()
    check_link_identity_conflict()
    check_expression_identity_duplicates()
    check_observation_role_consensus_disabled()
    for function in ("_grammar_features", "_grammar_unknown_counts", "_grammar_status"):
        check_slot_certainty_ignored(function)
    check_helper_replacement("_argument_shapes_comparable", "return True")
    check_helper_replacement("_is_argument_shape", "return feature.startswith(GRAMMAR_PREFIX + 'expression_slots:')")
    check_helper_replacement("_surface_grammar_allowed", "return True")
    check_helper_replacement("_validated_grammar", "return value")
    check_helper_replacement("_grammar_family_scores", "return {'expression': sum(item['weight'] for item in matched) / max(1, len(features)), 'link': 0}")
    check_helper_replacement("_grammar_unknown_counts", "return {}")
    check_helper_replacement("engine_info", "return {}")
    check_helper_replacement("_validate_runtime_info", "return None", "scripts/check_runtime.py")
    check_helper_replacement("_grammar_adjunct_positions", "return []")
    check_helper_replacement("bind_candidate_data", "return data")
    check_binding_capacity_regression('slots')
    check_binding_capacity_regression('frames')
    check_helper_replacement("_bind_evidence", """
start = text.index(evidence['text'])
return {**{k: v for k, v in evidence.items() if k != 'occurrence'}, 'start': start, 'end': start + len(evidence['text'])}
""")
    check_helper_replacement("_publish_bound_file", """
with open(output_path, 'wb') as stream:
    stream.write(content)
return False
""")
