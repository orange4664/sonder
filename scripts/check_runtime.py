#!/usr/bin/env python3
"""Check this installed Skill's engine before creating or resuming a revision."""

import argparse
import json
from pathlib import Path
import subprocess
import sys


def _validate_runtime_info(info):
    """Requirements belong to this Skill, independently of the engine's code."""
    if not isinstance(info, dict):
        raise ValueError('engine-info must return a JSON object')
    errors = []
    if info.get('engine') != 'sonder':
        errors.append('expected the Sonder engine')
    for key, required in (('info_version', 1), ('database_schema', 9), ('context_key_version', 2), ('scoring_version', 2), ('exemplar_index_version', 1), ('receipt_export_version', 1)):
        if type(info.get(key)) is not int or info[key] != required:
            errors.append(f'{key} must be {required}')
    capabilities = info.get('capabilities')
    required = {'injective_context_keys', 'coextensive_grammar_frames', 'checked_candidate_preparation', 'local_opposition_learning', 'independent_adjunct_feedback', 'unanchored_grammar_guard', 'literal_text_files', 'phrase_memory', 'author_exemplars', 'choice_receipts', 'adjunct_position', 'annotation_binding', 'syntax_fallback_diagnostics', 'syntax_model_identity', 'choice_id_guard', 'grounded_grammar_precedence', 'text_first_ranking', 'choice_learning_control', 'choice_context_guard', 'analysis_uncertainty_guard', 'phrase_literal_guard', 'choice_edit_scope_guard', 'independent_analysis_scoring', 'grammar_link_order', 'grammar_link_identity', 'grammar_observation_identity', 'grammar_binding_capacity', 'compact_ranking', 'rank_summary_input_guard', 'feedback_commit_time', 'complete_phrase_features', 'consistent_rank_snapshot', 'formatting_text_continuity', 'markdown_text_format', 'exemplar_format', 'choice_receipt_export'}
    if not isinstance(capabilities, list) or not all(isinstance(x, str) for x in capabilities):
        errors.append('capabilities must be an array of names')
    elif not required.issubset(capabilities):
        errors.append('missing capabilities: ' + ', '.join(sorted(required - set(capabilities))))
    if not isinstance(capabilities, list) or 'adaptive_preference_learning' not in capabilities:
        errors.append('missing capability: adaptive_preference_learning')
    for capability in ['relation_coverage', 'relative_preference_learning']:
        if not isinstance(capabilities, list) or capability not in capabilities:
            errors.append('missing capability: ' + capability)
    supported = info.get('supported_scoring_versions')
    if (not isinstance(supported, list) or not all(type(x) is int for x in supported)
            or not {2, 3, 4, 5}.issubset(supported)):
        errors.append('supported_scoring_versions must include 2, 3, 4 and 5')
    policies = info.get('learning_policies')
    if (not isinstance(policies, list) or not all(isinstance(x, str) for x in policies)
            or not {'legacy', 'adaptive-v1', 'adaptive-v2', 'adaptive-v3'}.issubset(policies)):
        errors.append('learning_policies must include legacy, adaptive-v1, adaptive-v2 and adaptive-v3')
    versions = info.get('input_versions')
    for name, required in (('analysis', {1, 2}), ('grammar', {1, 2, 3, 4, 5, 6, 7}), ('relation_coverage', {1})):
        supported = versions.get(name) if isinstance(versions, dict) else None
        if (not isinstance(supported, list) or not all(type(x) is int for x in supported)
                or not required.issubset(supported)):
            errors.append(f'{name} input versions must include {sorted(required)}')
    features = info.get('feature_versions')
    for name, required in (('syntax', 'v3'), ('analysis', 'v2'), ('analysis_topology', 'v1'), ('grammar', 'v1'), ('relations', 'v1'), ('syntax_model_identity', 'v1')):
        if not isinstance(features, dict) or features.get(name) != required:
            errors.append(f'{name} feature version must be {required}')
    limits = info.get('annotation_limits')
    for name, required in (('grammar_frames', 64), ('analysis_parts', 16), ('grammar_expression_slots', 5)):
        if (not isinstance(limits, dict) or type(limits.get(name)) is not int
                or limits[name] != required):
            errors.append(f'{name} annotation limit must be {required}')
    if errors:
        raise ValueError('; '.join(errors))


def check_runtime():
    # Resolve relative to this file, never cwd, PATH, another checkout or a DB.
    engine = Path(__file__).resolve().parents[1] / 'library/learn.py'
    if not engine.is_file():
        raise ValueError(f'learning engine is missing: {engine}')
    try:
        result = subprocess.run([sys.executable, '-B', '-I', str(engine), 'engine-info'],
                                capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired as error:
        raise ValueError(f'engine-info timed out: {engine}') from error
    except OSError as error:
        raise ValueError(f'cannot run engine-info: {error}') from error
    if result.returncode:
        detail = result.stderr.strip()[:1000] or f'exit {result.returncode}'
        raise ValueError(f'engine-info failed at {engine}: {detail}')
    try:
        info = json.loads(result.stdout)
    except ValueError as error:
        raise ValueError(f'engine-info did not return valid JSON: {engine}') from error
    _validate_runtime_info(info)
    return {'status': 'compatible', 'engine_path': str(engine), 'engine_info': info}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        result = check_runtime()
    except (OSError, ValueError) as error:
        parser.exit(1, f'Sonder runtime mismatch: {error}. Reinstall the complete Skill before learning.\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
