#!/usr/bin/env python3
"""Explicit optional gate: real SDK clients and stdio subprocesses, no mocks.

Run with the optional SDK's Python. Every store contains synthetic text.
--model-dir additionally requires actual offline Stanza success (no skip/fallback).
"""
import argparse
import asyncio
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


async def check(args, report):
    from mcp import Client, StdioServerParameters

    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix='revision-', dir=evidence)).resolve()
    report['workspace'] = str(workspace)
    root = args.server_root.resolve()
    database = workspace / 'preference.db'
    inputs = workspace / 'candidate-inputs'
    inputs.mkdir()
    sentence = '我们测得 $k_B$ = "a"，路径 C:\\tmp。\r\n样品保持稳定 \\citep{Sdk2024}。'
    data = {'round_id': 'stdio-S001-1', 'learning_context': {'section': 'results', 'role': 'support'}, 'candidates': [
        {'id': 'own', 'label': 'Other', 'text': sentence, 'analysis': {'spans': []}, 'grammar': {'frames': []}},
        {'id': 'alt', 'label': 'Alternative', 'text': '样品的稳定性得到确认。', 'analysis': {'spans': []}, 'grammar': {'frames': []}},
    ]}
    data['edit_scope'] = {'sentence_ids': ['S001', 'S002'], 'before_text': sentence, 'keep_original_id': 'own'}
    draft = inputs / 'draft.json'
    bound = inputs / 'bound.json'
    query = workspace / 'sentence.txt'
    query.write_bytes(sentence.encode())

    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

    def require(condition, message):
        if not condition:
            raise AssertionError(message)
        report['checks'].append(message)

    def snapshot():
        if not database.exists():
            return None
        with sqlite3.connect(database) as conn:
            return list(conn.iterdump())

    def learning_rows():
        with sqlite3.connect(database) as conn:
            return {table: conn.execute('SELECT * FROM ' + table + ' ORDER BY 1,2').fetchall()
                    for table in ('prefs', 'phrase_prefs', 'syntax_prefs', 'exemplars', 'seen', 'sqlite_sequence')}

    def cli(*argv):
        started = time.perf_counter()
        proc = subprocess.run([sys.executable, '-B', '-I', str(root / 'library/learn.py'),
                               *map(str, argv)], capture_output=True, text=True, timeout=90)
        report['cli'].append({'args': list(map(str, argv)), 'seconds': time.perf_counter() - started,
                              'returncode': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr})
        require(proc.returncode == 0, 'CLI succeeded: ' + str(argv[0]))
        return json.loads(proc.stdout) if proc.stdout.strip().startswith(('{', '[')) else proc.stdout

    async def call(client, name, arguments=None, *, error=False):
        started = time.perf_counter()
        result = await client.call_tool(name, arguments or {})
        report['calls'].append({'tool': name, 'args': arguments or {},
                               'seconds': time.perf_counter() - started,
                               'result': result.model_dump(mode='json', by_alias=True)})
        require(bool(result.is_error) == error, f'{name}: expected error={error}')
        if not error:
            require(isinstance(result.structured_content, dict), name + ': structured object')
        return result.structured_content

    write(draft, data)
    if args.model_dir:
        cli('syntax-backend', database, 'stanza', args.model_dir.resolve(), 'stdio-model-test')
    require(not database.exists(), 'backend configuration does not create database')
    params = StdioServerParameters(command=sys.executable,
        args=['-B', '-I', str(root / 'scripts/mcp_server.py'), '--workspace', str(workspace)])
    context = {'section': 'results', 'role': 'support', 'candidate_file': 'candidate-inputs/bound.json'}
    choice = {**context, 'round_id': data['round_id']}

    async with Client(params, mode='legacy', cache=None, read_timeout_seconds=90) as client:
        report['protocol_legacy'] = str(client.protocol_version)
        listing = await client.list_tools()
        report['tools'] = listing.model_dump(mode='json', by_alias=True)
        names = {tool.name: tool for tool in listing.tools}
        require(set(names) == {'sonder_status', 'sonder_learning_policy', 'sonder_bind', 'sonder_prepare', 'sonder_nearest', 'sonder_rank',
                              'sonder_record_choice', 'sonder_prune'}, 'eight exact tool names')
        require(set(names['sonder_prepare'].input_schema['required']) ==
                {'section', 'role', 'draft_file', 'output_file'}, 'prepare schema requires context and both paths')
        require(names['sonder_prepare'].input_schema['properties']['summary']['type'] == 'boolean',
                'prepare summary has boolean SDK schema')
        require(names['sonder_prepare'].annotations.read_only_hint is False,
                'prepare acknowledges publication and store migration')
        require('round_id' in names['sonder_record_choice'].input_schema['required'], 'choice ID is required in SDK schema')
        require(names['sonder_status'].annotations.read_only_hint is True, 'status read-only annotation')
        require(names['sonder_rank'].annotations.read_only_hint is False, 'rank acknowledges store migration')
        require(names['sonder_prune'].annotations.destructive_hint is True, 'prune declares deletion')
        status = await call(client, 'sonder_status')
        instance = status['instance_id']
        require(status['workspace'] == str(workspace) and status['database'] == str(database), 'correct workspace and database')
        require(status['engine_path'] == str(root / 'library/learn.py'), 'correct installation engine')
        require(status['engine_info']['database_schema'] == 9, 'context key schema 9 reported')
        require(status['engine_info']['context_key_version'] == 2, 'context key format 2 reported')
        require('injective_context_keys' in status['engine_info']['capabilities'], 'context isolation capability reported')
        require('choice_id_guard' in status['engine_info']['capabilities'], 'guard capability reported')
        require('checked_candidate_preparation' in status['engine_info']['capabilities'], 'checked preparation capability reported')
        require('coextensive_grammar_frames' in status['engine_info']['capabilities'], 'coextensive frame capability reported')
        require(7 in status['engine_info']['input_versions']['grammar'], 'shared evidence input v7 supported')
        require('grounded_grammar_precedence' in status['engine_info']['capabilities'], 'grammar source precedence capability reported')
        require('text_first_ranking' in status['engine_info']['capabilities'], 'text-first ranking capability reported')
        require('choice_learning_control' in status['engine_info']['capabilities'], 'per-choice learning control reported')
        require('choice_edit_scope_guard' in status['engine_info']['capabilities'], 'shared edit scope guard reported')
        require('independent_analysis_scoring' in status['engine_info']['capabilities'], 'independent analysis scoring reported')
        require('grammar_link_order' in status['engine_info']['capabilities'], 'independent grammatical relation order reported')
        require('grammar_link_identity' in status['engine_info']['capabilities'], 'bound relation identity reported')
        require('grammar_observation_identity' in status['engine_info']['capabilities'], 'fieldwise grammar observation identity reported')
        require(status['engine_info']['scoring_version'] == 2, 'scoring version 2 reported')
        require('choice_context_guard' in status['engine_info']['capabilities'], 'saved context guard reported')
        require('analysis_uncertainty_guard' in status['engine_info']['capabilities'], 'rhetorical uncertainty guard reported')
        require('phrase_literal_guard' in status['engine_info']['capabilities'], 'phrase literal guard reported')
        require(3 in status['engine_info']['input_versions']['grammar'], 'temporal grammar input v3 supported')
        require(4 in status['engine_info']['input_versions']['grammar'], 'comparative grammar input v4 supported')
        require(5 in status['engine_info']['input_versions']['grammar'], 'recipient grammar input v5 supported')
        require(6 in status['engine_info']['input_versions']['grammar'], 'explicit slot certainty v6 supported')
        require(2 in status['engine_info']['input_versions']['analysis'], 'multi-part analysis input v2 supported')
        require(status['engine_info']['feature_versions']['analysis_topology'] == 'v1', 'unit topology version reported')
        require(not database.exists(), 'startup/status leave database absent')
        bind_args = {'draft_file': 'candidate-inputs/draft.json', 'output_file': 'candidate-inputs/bound.json'}
        await call(client, 'sonder_bind', bind_args)
        saved = json.loads(bound.read_text())
        require(saved['edit_scope'] == data['edit_scope'], 'binding retains literal edit scope')
        require(saved['learning_context'] == data['learning_context'], 'binding retains exact learning context')
        require(saved['candidates'][0]['text'] == sentence, 'Unicode CRLF quotes math and backslashes preserved')
        before = bound.read_bytes()
        require((await call(client, 'sonder_bind', bind_args))['reused'] is True, 'identical bind retry reuses file')
        await call(client, 'sonder_bind', {**bind_args, 'output_file': 'working/overwrite.json'}, error=True)
        await call(client, 'sonder_bind', {**bind_args, 'output_file': 'candidate-inputs'}, error=True)
        write(draft, {**data, 'round_id': 'changed-draft'})
        await call(client, 'sonder_bind', bind_args, error=True)
        require(bound.read_bytes() == before, 'conflicting bind preserves prior candidate file')
        await call(client, 'sonder_record_choice', choice, error=True)
        saved.update(chosen='own', author_owned=True)
        write(bound, saved)
        await call(client, 'sonder_record_choice', context, error=True)
        await call(client, 'sonder_record_choice', {**choice, 'round_id': 'wrong'}, error=True)
        write(bound, {key: value for key, value in saved.items() if key != 'round_id'})
        await call(client, 'sonder_record_choice', choice, error=True)
        require(not database.exists(), 'pending/missing/mismatched IDs never create database')
        write(bound, saved)
        await call(client, 'sonder_rank', {**context, 'role': 'limitation'}, error=True)
        await call(client, 'sonder_record_choice', {**choice, 'role': 'limitation'}, error=True)
        require(not database.exists(), 'drifted context fails before first store access')
        cold = await call(client, 'sonder_rank', context)
        require(all(row['scoring_version'] == 2 and 'analysis_score' in row for row in cold['candidates']), 'rank exposes analysis component and scoring version')
        require(all(row['grammar_scores_by_family']['link_pair_order'] == 0 for row in cold['candidates']), 'new grammatical order family starts without preference')
        require(all(abs(row['syntax_score'] - row['base_syntax_score'] - row['analysis_score'] - row['grammar_score'] - row['analysis_topology_score']) < 0.00001 for row in cold['candidates']), 'rank structure components sum independently')
        require(all(row['heuristic_template_active'] is False for row in cold['candidates']),
                'explicit grammar abstention suppresses raw character templates')
        rank_calls = [report['calls'][-1]['seconds']]
        for _ in range(3):
            warm = await call(client, 'sonder_rank', context)
            rank_calls.append(report['calls'][-1]['seconds'])
            require(warm == cold, 'repeated empty-memory ranking is identical')
        require(cli('rank-file', database, 'results', 'support', bound) == cold['candidates'], 'CLI and MCP ranking parity')
        report['rank_seconds'] = rank_calls
        if args.model_dir:
            require(all(row['syntax_backend'] == 'stanza' for row in cold['candidates']), 'real Stanza used for every candidate')
            fingerprints = {row['syntax_model_fingerprint'] for row in cold['candidates']}
            require(len(fingerprints) == 1 and len(next(iter(fingerprints))) == 64, 'stable full model fingerprint')
            report['model_fingerprint'] = next(iter(fingerprints))
        # One fresh decision submitted concurrently; exactly one call may apply it.
        receipts = await asyncio.gather(*(call(client, 'sonder_record_choice', choice) for _ in range(6)))
        first = next(item for item in receipts if not item['replayed'])
        require(first['feedback_applied'] is True, 'ordinary choice applies learning')
        require(first['edit_scope'] == data['edit_scope'], 'ordinary receipt persists edit scope')
        require(first['learning_context'] == data['learning_context'], 'receipt persists effective learning context')
        require(sum(not item['replayed'] for item in receipts) == 1, 'six concurrent submissions apply once')
        require(all(item == {**first, 'replayed': item['replayed']} for item in receipts), 'concurrent receipts agree')
        learned = snapshot()
        with sqlite3.connect(database) as conn:
            phrase_keys = {row[0] for row in conn.execute('SELECT feature FROM phrase_prefs')}
        require(not phrase_keys.intersection({'word:k_b', 'word:citep', 'word:sdk2024'}),
                'real author feedback does not learn math symbols or citation keys as phrase taste')
        require('cjk2:样品' in phrase_keys, 'literal guard preserves ordinary prose learning')
        # Untrained rhetorical labels must not dilute learned base syntax less
        # merely because the host later masks a field as unknown.
        rhetorical = json.loads(json.dumps(saved['candidates'][0]))
        rhetorical['analysis']['spans'] = [{'start': 0, 'end': len(sentence), 'text': sentence,
            'move': 'result', 'polarity': 'affirmative', 'certainty': 'asserted'}]
        rhetorical_file = inputs / 'rhetorical.json'
        rhetorical_data = {'round_id': 'unselected-rhetorical-probe',
                           'learning_context': data['learning_context'], 'candidates': [rhetorical]}
        write(rhetorical_file, rhetorical_data)
        rhetorical_args = {**context, 'candidate_file': 'candidate-inputs/rhetorical.json'}
        rhetorical_known = (await call(client, 'sonder_rank', rhetorical_args))['candidates'][0]
        for field in ('move', 'polarity', 'certainty'):
            masked = json.loads(json.dumps(rhetorical_data))
            masked['candidates'][0]['analysis']['spans'][0][field] = 'unknown'
            write(rhetorical_file, masked)
            row = (await call(client, 'sonder_rank', rhetorical_args))['candidates'][0]
            require(row['score'] <= rhetorical_known['score'] and row['analysis_feature_capacity'] == 2,
                    'masked rhetorical ' + field + ' cannot raise score')
        require(snapshot() == learned, 'uncertainty rank probes preserve all learned rows')
        await call(client, 'sonder_rank', {**context, 'section': 'discussion'}, error=True)
        await call(client, 'sonder_record_choice', {**choice, 'section': 'discussion'}, error=True)
        write(bound, {**saved, 'learning_context': {'section': 'results', 'role': 'limitation'}})
        await call(client, 'sonder_record_choice', {**choice, 'role': 'limitation'}, error=True)
        write(bound, saved)
        require(snapshot() == learned, 'context drift and rewritten context preserve original history')
        write(bound, {**saved, 'edit_scope': {**saved['edit_scope'], 'before_text': sentence + '\n'}})
        await call(client, 'sonder_rank', context, error=True)
        await call(client, 'sonder_record_choice', choice, error=True)
        write(bound, {**saved, 'edit_scope': {**saved['edit_scope'], 'sentence_ids': ['S002', 'S001']}})
        await call(client, 'sonder_record_choice', choice, error=True)
        write(bound, saved)
        require(snapshot() == learned, 'scope baseline and identity conflicts preserve all rows')
        require(await call(client, 'sonder_record_choice', choice) == {**first, 'replayed': True}, 'same-process receipt replay')
        require(cli('choice-file', database, 'results', 'support', bound) == {**first, 'replayed': True}, 'CLI replays MCP decision')
        write(bound, {**saved, 'chosen': 'alt'})
        await call(client, 'sonder_record_choice', choice, error=True)
        write(bound, {**saved, 'round_id': 'different-file-decision'})
        await call(client, 'sonder_record_choice', choice, error=True)
        write(bound, saved)
        require(snapshot() == learned, 'replays and conflicts preserve every row and timestamp')
        nearest = await call(client, 'sonder_nearest', {'sentence_file': 'sentence.txt'})
        require(nearest['exemplars'][0]['text'] == sentence, 'author exemplar retrieved exactly')
        await call(client, 'sonder_nearest', {'sentence_file': 'sentence.txt', 'limit': 0}, error=True)
        sentinel = evidence / (workspace.name + '-outside.txt')
        sentinel.write_text('outside sentinel')
        (workspace / 'escape.txt').symlink_to(sentinel)
        for path in ('../' + sentinel.name, str(sentinel), 'escape.txt'):
            await call(client, 'sonder_nearest', {'sentence_file': path}, error=True)
        settings = workspace / '.similarity_backend'
        settings.symlink_to(sentinel)
        await call(client, 'sonder_status', error=True)
        settings.unlink()
        require(sentinel.read_text() == 'outside sentinel', 'outside files untouched')
        require((await call(client, 'sonder_status'))['instance_id'] == instance, 'server survives invalid requests')
        require(snapshot() == learned, 'read/error operations preserve learned rows')
        with sqlite3.connect(database) as conn:
            require(conn.execute('SELECT COUNT(*) FROM choice_events').fetchone()[0] == 1, 'exactly one decision event')
            require(conn.execute('SELECT rewards FROM exemplars').fetchone()[0] == 1, 'exactly one author reward')

        prior_learning = learning_rows()
        optout_file = inputs / 'optout.json'
        optout_data = {**saved, 'round_id': 'stdio-editorial-only', 'learn_from_choice': False}
        write(optout_file, optout_data)
        optout_args = {**context, 'candidate_file': 'candidate-inputs/optout.json', 'round_id': optout_data['round_id']}
        await call(client, 'sonder_record_choice', {**optout_args, 'role': 'limitation'}, error=True)
        require(snapshot() == learned, 'editorial-only context mismatch preserves all rows')
        optouts = await asyncio.gather(*(call(client, 'sonder_record_choice', optout_args) for _ in range(4)))
        optout_first = next(row for row in optouts if not row['replayed'])
        require(optout_first['edit_scope'] == data['edit_scope'], 'editorial-only receipt persists scope')
        require(optout_first['learning_context'] == data['learning_context'], 'editorial receipt persists context')
        require(sum(not row['replayed'] for row in optouts) == 1, 'four opt-out deliveries record one editorial decision')
        require(all(row['feedback_applied'] is False and row['author_exemplar'] is False for row in optouts), 'opt-out never learns or stores author text')
        require(all('syntax_backend' not in row for row in optouts), 'opt-out does not claim a parser result')
        require(learning_rows() == prior_learning, 'opt-out preserves all learning rows and timestamps')
        write(optout_file, {**optout_data, 'learn_from_choice': True})
        await call(client, 'sonder_record_choice', optout_args, error=True)
        write(optout_file, optout_data)
        require(learning_rows() == prior_learning, 'conflicting learning consent does not update memory')
        with sqlite3.connect(database) as conn:
            require(conn.execute('SELECT COUNT(*) FROM choice_events').fetchone()[0] == 2, 'one learning and one editorial event')
        learned = snapshot()

    async with Client(params, mode='auto', cache=None, read_timeout_seconds=90) as client:
        report['protocol_auto'] = str(client.protocol_version)
        restarted = await call(client, 'sonder_status')
        require(restarted['instance_id'] != instance, 'real server restart changes instance')
        require(await call(client, 'sonder_record_choice', choice) == {**first, 'replayed': True}, 'restart/auto-protocol replays same receipt')
        require(await call(client, 'sonder_record_choice', optout_args) == {**optout_first, 'replayed': True}, 'restart preserves opt-out receipt')
        require(snapshot() == learned, 'restart replay preserves learned rows')
        await call(client, 'sonder_prune')

    # Exercise actual v2 bind/rank/choice transport in its own synthetic store,
    # so the v1 concurrency/restart assertions above retain their exact counts.
    units_workspace = Path(tempfile.mkdtemp(prefix='units-', dir=evidence)).resolve()
    units_db = units_workspace / 'preference.db'
    units_inputs = units_workspace / 'candidate-inputs'
    units_inputs.mkdir()
    if args.model_dir:
        cli('syntax-backend', units_db, 'stanza', args.model_dir.resolve(), 'units-test')
    units_params = StdioServerParameters(command=sys.executable,
        args=['-B', '-I', str(root / 'scripts/mcp_server.py'), '--workspace', str(units_workspace)])

    def units_draft(subject, method, predicate, round_id):
        candidates = []
        for position in ('before', 'embedded'):
            text = method + '，' + subject + predicate + '。' if position == 'before' else subject + method + predicate + '。'
            parts = [subject + predicate] if position == 'before' else [subject, predicate]
            candidates.append({'id': position, 'label': 'Neutral', 'text': text,
                'analysis': {'units': [
                    {'move': 'result', 'polarity': 'affirmative', 'certainty': 'asserted', 'parts': [{'text': part} for part in parts]},
                    {'move': 'method', 'polarity': 'affirmative', 'certainty': 'asserted', 'parts': [{'text': method}]}]},
                'grammar': {'frames': []}})
        return {'round_id': round_id, 'learning_context': data['learning_context'], 'candidates': candidates}

    async with Client(units_params, mode='auto', cache=None, read_timeout_seconds=90) as client:
        for name, value in [('train', units_draft('研究者', '通过逐点扫描', '测得平均响应', 'units-train')),
                            ('held', units_draft('团队', '利用独立标定', '获得噪声估计', 'units-held'))]:
            write(units_inputs / (name + '-draft.json'), value)
            await call(client, 'sonder_bind', {'draft_file': 'candidate-inputs/' + name + '-draft.json', 'output_file': 'candidate-inputs/' + name + '.json'})
        units_args = {'section': 'results', 'role': 'support', 'candidate_file': 'candidate-inputs/held.json'}
        cold_units = (await call(client, 'sonder_rank', units_args))['candidates']
        require(all(row['analysis_input_version'] == 2 and row['analysis_topology_score'] == 0 for row in cold_units),
                'new unit topology starts with zero preference')
        train_file = units_inputs / 'train.json'
        train = json.loads(train_file.read_text())
        train.update(chosen='embedded', author_owned=True)
        write(train_file, train)
        units_choice = {**units_args, 'candidate_file': 'candidate-inputs/train.json', 'round_id': 'units-train'}
        units_receipt = await call(client, 'sonder_record_choice', units_choice)
        require(units_receipt['analysis_topology_negative_feedback'] and units_receipt['analysis_input_version'] == 2,
                'v2 feedback includes comparable topology')
        hot_units = (await call(client, 'sonder_rank', units_args))['candidates']
        by_id = {row['id']: row for row in hot_units}
        require(by_id['embedded']['analysis_topology_score'] > by_id['before']['analysis_topology_score'],
                'unit topology preference transfers through real MCP to different wording')
        require(cli('rank-file', units_db, 'results', 'support', units_inputs / 'held.json') == hot_units,
                'v2 ranking parity in a fresh CLI process')
        require(await call(client, 'sonder_record_choice', units_choice) == {**units_receipt, 'replayed': True},
                'v2 feedback replays through real MCP')
        held = json.loads((units_inputs / 'held.json').read_text())
        partial = json.loads(json.dumps(held))
        for unit in partial['candidates'][1]['analysis']['units']:
            unit['certainty'] = 'unknown'
        write(units_inputs / 'partial.json', partial)
        partial_rows = (await call(client, 'sonder_rank', {**units_args, 'candidate_file': 'candidate-inputs/partial.json'}))['candidates']
        partial_row = next(row for row in partial_rows if row['id'] == 'embedded')
        require(partial_row['score'] <= by_id['embedded']['score'], 'masked v2 certainty cannot raise score')
        mixed = json.loads(json.dumps(held))
        old = mixed['candidates'][0]['analysis']
        old['spans'] = [{**unit['parts'][0], **{key: unit[key] for key in ('move', 'polarity', 'certainty')}}
                        for unit in old.pop('units')]
        old['version'] = 1
        mixed.update(chosen='before', round_id='units-mixed')
        write(units_inputs / 'mixed.json', mixed)
        def topology_rows():
            with sqlite3.connect(units_db) as conn:
                return conn.execute("SELECT * FROM syntax_prefs WHERE feature LIKE 'syntax:analysis-topology-%' ORDER BY feature").fetchall()
        before_topology = topology_rows()
        mixed_receipt = await call(client, 'sonder_record_choice', {**units_args, 'candidate_file': 'candidate-inputs/mixed.json', 'round_id': 'units-mixed'})
        require(not mixed_receipt['analysis_topology_negative_feedback'] and topology_rows() == before_topology,
                'legacy chosen analysis cannot reject unrepresented topology through MCP')
    async with Client(units_params, mode='auto', cache=None, read_timeout_seconds=90) as client:
        require(await call(client, 'sonder_record_choice', units_choice) == {**units_receipt, 'replayed': True},
                'v2 receipt survives real server restart')
    # Real v5 protocol, argument-order transfer, and both mixed-version choices.
    recipient_workspace = Path(tempfile.mkdtemp(prefix='recipients-', dir=evidence)).resolve()
    recipient_inputs = recipient_workspace / 'candidate-inputs'
    recipient_inputs.mkdir()
    recipient_db = recipient_workspace / 'preference.db'
    recipient_params = StdioServerParameters(command=sys.executable,
        args=['-B', '-I', str(root / 'scripts/mcp_server.py'), '--workspace', str(recipient_workspace)])

    def recipient_draft(subject, event, patient, recipient, round_id):
        candidates = []
        for order in ('recipient_first', 'patient_first'):
            sentence = (f'{subject} {event} {recipient} {patient}.' if order == 'recipient_first'
                        else f'{subject} {event} {patient} to {recipient}.')
            candidates.append({'id': order, 'label': 'Neutral', 'text': sentence,
                'analysis': {'units': []}, 'grammar': {'version': 5, 'frames': [
                    {'kind': 'expression', 'text': sentence, 'form': 'verbal', 'voice': 'active',
                     'slots': [{'role': role, 'text': text} for role, text in
                               [('agent', subject), ('event', event), ('patient', patient), ('recipient', recipient)] ]}]}})
        return {'round_id': round_id, 'learning_context': {'section': 'methods', 'role': 'detail'}, 'candidates': candidates}

    def argument_rows():
        with sqlite3.connect(recipient_db) as conn:
            return {row[1]: row for row in conn.execute('SELECT * FROM syntax_prefs ORDER BY feature')
                    if row[1].startswith(('syntax:grammar-v1:writing-model:expression_slots:',
                                          'syntax:grammar-v1:writing-model:expression:'))}

    async with Client(recipient_params, mode='auto', cache=None, read_timeout_seconds=90) as client:
        for name, value in [('train', recipient_draft('We', 'told', 'the result', 'the operator', 'recipient-train')),
                            ('held', recipient_draft('The team', 'gave', 'new settings', 'the technician', 'recipient-held'))]:
            write(recipient_inputs / (name + '-draft.json'), value)
            await call(client, 'sonder_bind', {'draft_file': 'candidate-inputs/' + name + '-draft.json',
                                              'output_file': 'candidate-inputs/' + name + '.json'})
        recipient_args = {'section': 'methods', 'role': 'detail', 'candidate_file': 'candidate-inputs/held.json'}
        cold = (await call(client, 'sonder_rank', recipient_args))['candidates']
        require(all(row['grammar_input_version'] == 5 and row['grammar_score'] == 0 for row in cold), 'v5 cold scores are zero')
        train = json.loads((recipient_inputs / 'train.json').read_text())
        train.update(chosen='recipient_first', author_owned=True)
        write(recipient_inputs / 'train.json', train)
        recipient_choice = {**recipient_args, 'candidate_file': 'candidate-inputs/train.json', 'round_id': 'recipient-train'}
        receipt = await call(client, 'sonder_record_choice', recipient_choice)
        require(receipt['argument_shape_negative_feedback'], 'complete v5 supports argument-shape feedback')
        hot = (await call(client, 'sonder_rank', recipient_args))['candidates']
        by_id = {row['id']: row for row in hot}
        require(by_id['recipient_first']['grammar_score'] > by_id['patient_first']['grammar_score'],
                'real MCP recipient order transfers to new words')
        require(cli('rank-file', recipient_db, 'methods', 'detail', recipient_inputs / 'held.json') == hot, 'v5 fresh CLI ranking parity')
        before = argument_rows()
        require(await call(client, 'sonder_record_choice', recipient_choice) == {**receipt, 'replayed': True}, 'v5 choice replays')
        require(argument_rows() == before, 'v5 replay preserves learned rows')
        mixed = json.loads((recipient_inputs / 'held.json').read_text())
        legacy = mixed['candidates'][0]['grammar']
        legacy['version'] = 4
        for frame in legacy['frames']:
            frame['slots'] = [slot for slot in frame['slots'] if slot['role'] != 'recipient']
        # Seed old and modern shapes before rejecting either, so an illicit
        # penalty cannot hide behind the nonnegative weight floor.
        for item in mixed['candidates']:
            rid = 'recipient-seed-' + item['id']
            write(recipient_inputs / 'seed.json', {**mixed, 'round_id': rid, 'chosen': item['id'],
                                                   'author_owned': True, 'candidates': [item]})
            await call(client, 'sonder_record_choice', {**recipient_args, 'candidate_file': 'candidate-inputs/seed.json', 'round_id': rid})
        for chosen in ('recipient_first', 'patient_first'):
            rid = 'recipient-mixed-' + chosen
            write(recipient_inputs / 'mixed.json', {**mixed, 'round_id': rid, 'chosen': chosen, 'author_owned': True})
            before = argument_rows()
            result = await call(client, 'sonder_record_choice', {**recipient_args, 'candidate_file': 'candidate-inputs/mixed.json', 'round_id': rid})
            after = argument_rows()
            rejected = {key: value for key, value in before.items()
                        if ('recipient' in key) == (chosen == 'recipient_first')}
            require(bool(rejected) and all(after[key] == value for key, value in rejected.items()),
                    'mixed recipient choice preserves incomparable rejected shapes: ' + chosen)
            require(result['grammar_negative_feedback'] and not result['argument_shape_negative_feedback'],
                    'mixed v4/v5 diagnostic isolates shape gate: ' + chosen)
        # Explicit partial roles must not pick up the legacy short template
        # seeded above, even though its weight is still stored and usable.
        v6 = json.loads((recipient_inputs / 'held-draft.json').read_text())
        v6['round_id'] = 'certainty-v6'
        v6['candidates'] = v6['candidates'][:1]
        for frame in v6['candidates'][0]['grammar']['frames']:
            frame['slots_complete'] = True
        v6['candidates'][0]['grammar']['version'] = 6
        write(recipient_inputs / 'certainty-draft.json', v6)
        await call(client, 'sonder_bind', {'draft_file': 'candidate-inputs/certainty-draft.json',
                                          'output_file': 'candidate-inputs/certainty.json'})
        certainty_args = {**recipient_args, 'candidate_file': 'candidate-inputs/certainty.json'}
        known = (await call(client, 'sonder_rank', certainty_args))['candidates'][0]
        partial = json.loads(json.dumps(v6))
        partial['round_id'] = 'certainty-partial'
        frame = partial['candidates'][0]['grammar']['frames'][0]
        frame['slots_complete'] = False
        frame['slots'] = [slot for slot in frame['slots'] if slot['role'] != 'recipient']
        write(recipient_inputs / 'partial-draft.json', partial)
        await call(client, 'sonder_bind', {'draft_file': 'candidate-inputs/partial-draft.json',
                                          'output_file': 'candidate-inputs/partial.json'})
        partial_args = {**recipient_args, 'candidate_file': 'candidate-inputs/partial.json'}
        masked = (await call(client, 'sonder_rank', partial_args))['candidates'][0]
        require(masked['grammar_input_version'] == 6 and masked['grammar_status'] == 'partial', 'v6 partial roles remain explicit')
        require(masked['grammar_score'] <= known['grammar_score'], 'partial roles cannot raise grammar score')
        require(all(':expression_slots:' not in row['feature'] and ':expression:' not in row['feature']
                    for row in masked['matched_grammar_features']), 'partial inventory cannot match a complete shape')
        canonical = json.loads((recipient_inputs / 'partial.json').read_text())
        canonical.update(chosen=canonical['candidates'][0]['id'], author_owned=True)
        write(recipient_inputs / 'partial.json', canonical)
        before = argument_rows()
        certainty_choice = {**partial_args, 'round_id': 'certainty-partial'}
        partial_receipt = await call(client, 'sonder_record_choice', certainty_choice)
        require(not partial_receipt['grammar_negative_feedback'] and argument_rows() == before,
                'partial v6 feedback neither reinforces nor rejects complete shapes')
        require(await call(client, 'sonder_record_choice', certainty_choice) == {**partial_receipt, 'replayed': True},
                'partial v6 receipt replays')
        require(cli('rank-file', recipient_db, 'methods', 'detail', recipient_inputs / 'partial.json') ==
                (await call(client, 'sonder_rank', partial_args))['candidates'], 'v6 fresh CLI and real MCP agree')
    async with Client(recipient_params, mode='auto', cache=None, read_timeout_seconds=90) as client:
        require(await call(client, 'sonder_record_choice', recipient_choice) == {**receipt, 'replayed': True},
                'v5 original receipt survives mixed feedback and server restart')
    pair_workspace = Path(tempfile.mkdtemp(prefix='link-order-', dir=evidence)).resolve()
    pair_inputs = pair_workspace / 'candidate-inputs'
    pair_inputs.mkdir()
    actual = json.loads((ROOT / 'tests/fixtures/link-order.json').read_text())['writers']
    pair_training = actual['a']['training']
    pair_training.update(round_id='sdk-link-order', chosen='forward', author_owned=True)
    write(pair_inputs / 'training.json', pair_training)
    write(pair_inputs / 'query.json', actual['b']['query'])
    pair_params = StdioServerParameters(command=sys.executable,
        args=['-B', '-I', str(root / 'scripts/mcp_server.py'), '--workspace', str(pair_workspace)])
    pair_args = {'section': 'methods', 'role': 'detail', 'candidate_file': 'candidate-inputs/query.json'}
    pair_choice = {**pair_args, 'candidate_file': 'candidate-inputs/training.json', 'round_id': 'sdk-link-order'}
    async with Client(pair_params, mode='auto', cache=None, read_timeout_seconds=90) as client:
        cold_pairs = (await call(client, 'sonder_rank', pair_args))['candidates']
        require(all(row['score'] == 0 for row in cold_pairs), 'actual same-move pairs start tied')
        pair_receipt = await call(client, 'sonder_record_choice', pair_choice)
        require(pair_receipt['link_pair_negative_feedback'] is False,
                'partial actual grammar does not reject alternative pair structures')
        pair_ranked = (await call(client, 'sonder_rank', pair_args))['candidates']
        by_id = {row['id']: row for row in pair_ranked}
        require(by_id['forward']['grammar_scores_by_family']['link_pair_order'] >
                by_id['reverse']['grammar_scores_by_family']['link_pair_order'],
                'actual grammatical relation order transfers across writers and vocabulary')
        require(by_id['forward']['score'] > by_id['reverse']['score'], 'new order evidence changes complete ranking')
        duplicate_pool = json.loads(json.dumps(actual['b']['query']))
        duplicate_pool['round_id'] = 'sdk-pair-duplicate-pending'
        for candidate in duplicate_pool['candidates']:
            frame = json.loads(json.dumps(next(f for f in candidate['grammar']['frames']
                                               if f['kind'] == 'link' and f['relation'] == 'temporal')))
            frame['end'] += 1
            frame['text'] = candidate['text'][frame['start']:frame['end']]
            candidate['grammar']['frames'].append(frame)
        write(pair_inputs / 'duplicate-query.json', duplicate_pool)
        duplicate_rank = (await call(client, 'sonder_rank', {**pair_args, 'candidate_file':'candidate-inputs/duplicate-query.json'}))['candidates']
        require([(row['id'],row['score']) for row in duplicate_rank] == [(row['id'],row['score']) for row in pair_ranked],
                'duplicate literal relation scope cannot change rank scores')
        partial_pool = json.loads(json.dumps(actual['a']['training']))
        for field in ['chosen','author_owned']:
            partial_pool.pop(field,None)
        partial_pool['round_id']='sdk-partial-pending'
        write(pair_inputs / 'partial-observation.json',partial_pool)
        partial_args={**pair_args,'candidate_file':'candidate-inputs/partial-observation.json'}
        partial_rank=(await call(client,'sonder_rank',partial_args))['candidates']
        repeated=json.loads(json.dumps(partial_pool));repeated['round_id']='sdk-repeated-observation'
        for candidate in repeated['candidates']:
            frame=json.loads(json.dumps(next(f for f in candidate['grammar']['frames']
                if f['kind']=='expression' and any(s['role']=='event' and s['text']=='测量' for s in f['slots']))))
            frame['end']+=1;frame['text']=candidate['text'][frame['start']:frame['end']]
            candidate['grammar']['frames'].append(frame)
        write(pair_inputs / 'repeated-observation.json',repeated)
        repeated_rank=(await call(client,'sonder_rank',{**partial_args,'candidate_file':'candidate-inputs/repeated-observation.json'}))['candidates']
        require([(r['id'],r['score'],r['grammar_scores_by_family']) for r in partial_rank] ==
                [(r['id'],r['score'],r['grammar_scores_by_family']) for r in repeated_rank],
                'repeated partial expression keeps every family score')
        pair_db = pair_workspace / 'preference.db'
        with sqlite3.connect(pair_db) as conn:
            before_pairs = list(conn.iterdump())
        require(await call(client, 'sonder_record_choice', pair_choice) == {**pair_receipt, 'replayed': True},
                'grammatical pair feedback replays exactly')
        with sqlite3.connect(pair_db) as conn:
            require(list(conn.iterdump()) == before_pairs, 'replayed pair choice does not relearn')
    prepare_workspace = Path(tempfile.mkdtemp(prefix='prepare-', dir=evidence)).resolve()
    prepare_inputs = prepare_workspace / 'candidate-inputs'
    prepare_inputs.mkdir()
    prepare_db = prepare_workspace / 'preference.db'
    prepare_draft = prepare_inputs / 'draft.json'
    prepare_bound = prepare_inputs / 'bound.json'
    prepare_data = {**data, 'round_id': 'stdio-prepare', 'text_format': 'markdown'}
    write(prepare_draft, prepare_data)
    if args.model_dir:
        cli('syntax-backend', prepare_db, 'stanza', args.model_dir.resolve(), 'stdio-prepare-model-test')
    prepare_args = {'section': 'results', 'role': 'support',
                    'draft_file': 'candidate-inputs/draft.json', 'output_file': 'candidate-inputs/bound.json'}
    prepare_params = StdioServerParameters(command=sys.executable,
        args=['-B', '-I', str(root / 'scripts/mcp_server.py'), '--workspace', str(prepare_workspace)])
    async with Client(prepare_params, mode='auto', cache=None, read_timeout_seconds=90) as client:
        await call(client, 'sonder_prepare', {**prepare_args, 'section': 'methods'}, error=True)
        await call(client, 'sonder_prepare', {**prepare_args, 'output_file': 'working.json'}, error=True)
        await call(client, 'sonder_prepare', {**prepare_args, 'draft_file': '../outside.json'}, error=True)
        require(not prepare_db.exists() and not prepare_bound.exists(), 'invalid preparation does not publish or open store')
        # A valid old output must not be ranked when the replacement fails.
        await call(client, 'sonder_bind', {k: prepare_args[k] for k in ['draft_file', 'output_file']})
        old = prepare_bound.read_bytes(), prepare_bound.stat().st_mtime_ns
        write(prepare_draft, {**prepare_data, 'round_id': 'corrected-round'})
        await call(client, 'sonder_prepare', prepare_args, error=True)
        require(not prepare_db.exists(), 'stale output error stops before ranking/store')
        require((prepare_bound.read_bytes(), prepare_bound.stat().st_mtime_ns) == old,
                'stale output error preserves old canonical')
        write(prepare_draft, prepare_data)
        prepared = await call(client, 'sonder_prepare', {**prepare_args, 'summary': True})
        require(prepared['preparation_version'] == 1 and prepared['binding'] == {
            'output': str(prepare_bound), 'round_id': 'stdio-prepare', 'candidates': 2, 'reused': True},
            'checked preparation returns binding and rank receipt')
        require(all(row['summary_version'] == 1 for row in prepared['candidates']), 'prepare summary is explicit')
        require(json.loads(prepare_bound.read_text())['edit_scope'] == data['edit_scope'], 'prepare preserves literal scope')
        require(json.loads(prepare_bound.read_text())['text_format'] == 'markdown', 'prepare preserves saved format')
        if args.model_dir:
            require(all(row['syntax_backend'] == 'stanza' and row.get('syntax_fallback_reason') is None
                        for row in prepared['candidates']), 'prepare uses actual offline Stanza without fallback')
        with sqlite3.connect(prepare_db) as conn:
            prepare_before = list(conn.iterdump())
            require(conn.execute('SELECT count(*) FROM choice_events').fetchone()[0] == 0,
                    'prepare records no author choices')
            require(conn.execute('SELECT count(*) FROM exemplars').fetchone()[0] == 0,
                    'prepare records no author exemplars')
        require((await call(client, 'sonder_prepare', {**prepare_args, 'summary': True})) == prepared,
                'identical pending prepare retry returns same ranks')
        require((prepare_bound.read_bytes(), prepare_bound.stat().st_mtime_ns) == old,
                'prepare retry preserves canonical bytes and mtime')
        ranked = await call(client, 'sonder_rank', {'section': 'results', 'role': 'support',
                            'candidate_file': 'candidate-inputs/bound.json', 'summary': True})
        require(ranked['candidates'] == prepared['candidates'], 'prepare and existing rank have identical output')
        require(cli('prepare-file', prepare_db, 'results', 'support', prepare_draft, prepare_bound,
                    '--summary') == prepared, 'CLI and real MCP preparation agree')
        with sqlite3.connect(prepare_db) as conn:
            require(list(conn.iterdump()) == prepare_before, 'prepare and rank retries do not write feedback or policy')
    shared_workspace = Path(tempfile.mkdtemp(prefix='shared-evidence-', dir=evidence)).resolve()
    shared_inputs = shared_workspace / 'candidate-inputs'; shared_inputs.mkdir()
    shared_db = shared_workspace / 'preference.db'
    if args.model_dir:
        cli('syntax-backend', shared_db, 'stanza', args.model_dir.resolve(), 'stdio-shared-evidence')
    shared_fixture = json.loads((ROOT / 'tests/fixtures/coextensive-grammar.json').read_text())
    for label in ['training', 'query', 'temporal']:
        write(shared_inputs / (label + '.draft.json'), shared_fixture[label])
    shared_params = StdioServerParameters(command=sys.executable,
        args=['-B', '-I', str(root / 'scripts/mcp_server.py'), '--workspace', str(shared_workspace)])
    async with Client(shared_params, mode='auto', cache=None, read_timeout_seconds=90) as client:
        await call(client, 'sonder_learning_policy', {'action': 'ensure-adaptive'})
        for label in ['training', 'query', 'temporal']:
            result = await call(client, 'sonder_prepare', {'section': 'methods', 'role': 'detail',
                'draft_file': 'candidate-inputs/' + label + '.draft.json',
                'output_file': 'candidate-inputs/' + label + '.json', 'summary': True})
            require(all(r['grammar_input_version'] == 7 for r in result['candidates']), label + ': actual SDK accepts v7')
            require(all(r['relation_coverage_status'] == 'complete' for r in result['candidates']),
                    label + ': distinct coextensive targets retain complete inventories')
            if args.model_dir:
                require(all(r['syntax_backend'] == 'stanza' and r['syntax_fallback_reason'] is None
                            for r in result['candidates']), label + ': v7 uses actual offline model')
            repeated = cli('prepare-file', shared_db, 'methods', 'detail', shared_inputs / (label + '.draft.json'),
                           shared_inputs / (label + '.json'), '--summary')
            require(repeated == {**result, 'binding': {**result['binding'], 'reused': True}},
                    label + ': actual CLI and SDK ranks and binding agree')
        original = shared_inputs / 'training.json'
        decision = json.loads(original.read_text()); decision.update(chosen='active', author_owned=True)
        write(original, decision)
        choice_args = {'section': 'methods', 'role': 'detail', 'candidate_file': 'candidate-inputs/training.json',
                       'round_id': decision['round_id']}
        receipt = await call(client, 'sonder_record_choice', choice_args)
        ranked = await call(client, 'sonder_rank', {'section': 'methods', 'role': 'detail',
                            'candidate_file': 'candidate-inputs/query.json', 'summary': True})
        require(ranked['candidates'][0]['id'] == 'active' and
                ranked['candidates'][0]['score'] > ranked['candidates'][1]['score'],
                'shared-argument preference transfers to independent vocabulary over MCP')
        with sqlite3.connect(shared_db) as conn: before_shared = list(conn.iterdump())
        require(await call(client, 'sonder_record_choice', choice_args) == {**receipt, 'replayed': True},
                'v7 author choice replays exactly')
        duplicate = json.loads(json.dumps(shared_fixture['training']))
        duplicate['candidates'][0]['grammar']['frames'][1] = duplicate['candidates'][0]['grammar']['frames'][0]
        write(shared_inputs / 'bad.draft.json', duplicate)
        await call(client, 'sonder_prepare', {'section': 'methods', 'role': 'detail',
            'draft_file': 'candidate-inputs/bad.draft.json', 'output_file': 'candidate-inputs/bad.json'}, error=True)
        require(not (shared_inputs / 'bad.json').exists(), 'v7 duplicate anchor fails before publication')
        with sqlite3.connect(shared_db) as conn:
            require(list(conn.iterdump()) == before_shared, 'v7 replay and rejected duplicate leave learning rows unchanged')
    malformed_workspace = Path(tempfile.mkdtemp(prefix='coverage-errors-', dir=evidence)).resolve()
    malformed_inputs = malformed_workspace / 'candidate-inputs'; malformed_inputs.mkdir()
    malformed_db = malformed_workspace / 'preference.db'
    if args.model_dir:
        cli('syntax-backend', malformed_db, 'stanza', args.model_dir.resolve(), 'stdio-coverage-errors')
    coverage_fixture = json.loads((ROOT / 'tests/fixtures/relation-learning.json').read_text())['pools']['T001']
    canonical = json.loads(json.dumps(coverage_fixture['data']))
    canonical.update(round_id='coverage-error-recovery', chosen='a', author_owned=False)
    for candidate in canonical['candidates']:
        candidate['relation_coverage'] = coverage_fixture['coverage'][candidate['id']]
    broken = json.loads(json.dumps(canonical))
    broken['candidates'][0]['relation_coverage']['links'][0]['head_kind'] = ['event']
    write(malformed_inputs / 'bound.json', broken)
    broken_draft = json.loads(json.dumps(broken))
    for candidate in broken_draft['candidates']:
        candidate['relation_coverage'].pop('text_sha256')
        candidate['relation_coverage'].pop('grammar_sha256')
    write(malformed_inputs / 'draft.json', broken_draft)
    malformed_params = StdioServerParameters(command=sys.executable,
        args=['-B', '-I', str(root / 'scripts/mcp_server.py'), '--workspace', str(malformed_workspace)])
    malformed_context = {'section': 'methods', 'role': 'detail', 'candidate_file': 'candidate-inputs/bound.json'}
    async with Client(malformed_params, mode='auto', cache=None, read_timeout_seconds=90) as client:
        initial_instance = (await call(client, 'sonder_status'))['instance_id']
        for tool, arguments in [
                ('sonder_rank', malformed_context),
                ('sonder_record_choice', {**malformed_context, 'round_id': canonical['round_id']}),
                ('sonder_prepare', {'section': 'methods', 'role': 'detail',
                    'draft_file': 'candidate-inputs/draft.json', 'output_file': 'candidate-inputs/prepared.json'})]:
            await call(client, tool, arguments, error=True)
            message = json.dumps(report['calls'][-1]['result'])
            require('candidates[0].relation_coverage' in message and 'links[0].head_kind' in message,
                    tool + ': malformed relation type returns the exact input field')
            require('TypeError' not in message, tool + ': no internal type error leaks')
            require(not malformed_db.exists() and not (malformed_inputs / 'prepared.json').exists(),
                    tool + ': malformed relation type does not publish or open store')
        write(malformed_inputs / 'bound.json', canonical)
        recovered = await call(client, 'sonder_rank', malformed_context)
        require(all(row['score'] == 0 for row in recovered['candidates']), 'corrected relation input ranks without feedback')
        require((await call(client, 'sonder_status'))['instance_id'] == initial_instance,
                'same MCP process remains usable after invalid relation inputs')
    context_workspace = Path(tempfile.mkdtemp(prefix='context-keys-', dir=evidence)).resolve()
    context_inputs = context_workspace / 'candidate-inputs'; context_inputs.mkdir()
    context_db = context_workspace / 'preference.db'
    if args.model_dir:
        cli('syntax-backend', context_db, 'stanza', args.model_dir.resolve(), 'stdio-context-keys')
    context_params = StdioServerParameters(command=sys.executable,
        args=['-B', '-I', str(root / 'scripts/mcp_server.py'), '--workspace', str(context_workspace)])
    async with Client(context_params, mode='auto', cache=None, read_timeout_seconds=90) as client:
        await call(client, 'sonder_learning_policy', {'action': 'ensure-adaptive'})
        for pair_id, pair in enumerate([(('methods::detail', 'support'), ('methods', 'detail::support')),
                                        (('a:', 'b'), ('a', ':b'))]):
            rank_args, choice_args = [], []
            for side, (section, role) in enumerate(pair):
                key = f'{pair_id}-{side}'
                context_value = {'section': section, 'role': role}
                for kind in ['training', 'query']:
                    item = json.loads(json.dumps(shared_fixture[kind]))
                    item.update(round_id='context-' + key + '-' + kind, learning_context=context_value)
                    draft_name = key + '-' + kind + '.draft.json'
                    bound_name = key + '-' + kind + '.json'
                    write(context_inputs / draft_name, item)
                    await call(client, 'sonder_prepare', {**context_value,
                        'draft_file': 'candidate-inputs/' + draft_name,
                        'output_file': 'candidate-inputs/' + bound_name})
                training_path = context_inputs / (key + '-training.json')
                decision = json.loads(training_path.read_text())
                decision.update(chosen='active' if side == 0 else 'passive', author_owned=True)
                write(training_path, decision)
                choice_args.append({**context_value, 'candidate_file': 'candidate-inputs/' + training_path.name,
                                    'round_id': decision['round_id']})
                rank_args.append({**context_value, 'candidate_file': 'candidate-inputs/' + key + '-query.json'})
            first = await call(client, 'sonder_record_choice', choice_args[0])
            cold = await call(client, 'sonder_rank', rank_args[1])
            require(all(row['score'] == 0 for row in cold['candidates']), f'context pair {pair_id}: other tuple remains cold')
            await call(client, 'sonder_record_choice', choice_args[1])
            for side, expected in enumerate(['active', 'passive']):
                ranked = await call(client, 'sonder_rank', rank_args[side])
                require(ranked['candidates'][0]['id'] == expected and
                        ranked['candidates'][0]['score'] > ranked['candidates'][1]['score'],
                        f'context pair {pair_id} side {side}: opposite preference stays local')
                if args.model_dir:
                    require(all(r['syntax_backend'] == 'stanza' and r['syntax_fallback_reason'] is None
                                for r in ranked['candidates']), 'context ranks use real offline model')
            with sqlite3.connect(context_db) as conn: before_context = list(conn.iterdump())
            require(await call(client, 'sonder_record_choice', choice_args[0]) == {**first, 'replayed': True},
                    'encoded context receipt replays original pair')
            with sqlite3.connect(context_db) as conn:
                require(list(conn.iterdump()) == before_context, 'encoded receipt replay preserves rows')
    # Optional compatibility gate uses an actual pre-change engine, never a mock.
    if args.legacy_engine_root:
        legacy_root = args.legacy_engine_root.resolve()
        def old_cli(*argv):
            proc = subprocess.run([sys.executable, '-B', '-I', str(legacy_root / 'library/learn.py'),
                                   *map(str, argv)], capture_output=True, text=True, timeout=90)
            report['cli'].append({'engine_root': str(legacy_root), 'args': list(map(str, argv)),
                                  'returncode': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr})
            require(proc.returncode == 0, 'actual old CLI succeeded: ' + str(argv[0]))
            return json.loads(proc.stdout)
        require(old_cli('engine-info')['database_schema'] == 8, 'legacy gate really uses schema 8 engine')
        legacy_workspace = Path(tempfile.mkdtemp(prefix='legacy-context-', dir=evidence)).resolve()
        legacy_inputs = legacy_workspace / 'candidate-inputs'; legacy_inputs.mkdir()
        legacy_db = legacy_workspace / 'preference.db'
        old_data = json.loads(json.dumps(shared_fixture['training']))
        old_data.update(round_id='legacy-context', learning_context={'section': 'methods::detail', 'role': 'support'})
        write(legacy_inputs / 'draft.json', old_data)
        old_cli('bind-file', legacy_inputs / 'draft.json', legacy_inputs / 'bound.json')
        decision = json.loads((legacy_inputs / 'bound.json').read_text())
        decision.update(chosen='active', author_owned=True); write(legacy_inputs / 'bound.json', decision)
        old_cli('learning-policy', legacy_db, 'ensure-adaptive')
        old_receipt = old_cli('choice-file', legacy_db, 'methods::detail', 'support', legacy_inputs / 'bound.json')
        with sqlite3.connect(legacy_db) as conn: old_rows = list(conn.iterdump())
        legacy_params = StdioServerParameters(command=sys.executable,
            args=['-B', '-I', str(root / 'scripts/mcp_server.py'), '--workspace', str(legacy_workspace)])
        old_choice = {'section': 'methods::detail', 'role': 'support',
                      'candidate_file': 'candidate-inputs/bound.json', 'round_id': 'legacy-context'}
        async with Client(legacy_params, mode='auto', cache=None, read_timeout_seconds=90) as client:
            replay = await call(client, 'sonder_record_choice', old_choice)
            require(replay == {**old_receipt, 'replayed': True}, 'actual schema 8 receipt replays over schema 9 MCP')
            await call(client, 'sonder_rank', {k: v for k, v in old_choice.items() if k != 'round_id'}, error=True)
            require('ambiguous legacy context' in json.dumps(report['calls'][-1]['result']), 'MCP exposes legacy ambiguity error')
            with sqlite3.connect(legacy_db) as conn:
                require(list(conn.iterdump()) == old_rows, 'upgrade/replay/legacy guard preserve all old rows')
                require(conn.execute('PRAGMA user_version').fetchone()[0] == 9, 'legacy store marks key format schema')
            decision.update(round_id='new-ambiguous'); write(legacy_inputs / 'bound.json', decision)
            await call(client, 'sonder_record_choice', {**old_choice, 'round_id': 'new-ambiguous'}, error=True)
            decision['learn_from_choice'] = False; write(legacy_inputs / 'bound.json', decision)
            optout = await call(client, 'sonder_record_choice', {**old_choice, 'round_id': 'new-ambiguous'})
            require(optout['feedback_applied'] is False and optout['author_exemplar'] is False,
                    'explicit editorial choice remains available with ambiguous history')
            with sqlite3.connect(legacy_db) as conn:
                conn.execute("DELETE FROM choice_events WHERE round_id='new-ambiguous'")
                require(list(conn.iterdump()) == old_rows, 'opt-out only adds its editorial receipt')
    report['status'] = 'passed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--server-root', type=Path, default=ROOT)
    parser.add_argument('--model-dir', type=Path)
    parser.add_argument('--legacy-engine-root', type=Path,
                        help='Optional frozen schema-8 installation for real upgrade/replay checks')
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    report = {'status': 'running', 'checks': [], 'calls': [], 'cli': []}
    try:
        asyncio.run(check(args, report))
    except BaseException as error:
        report.update(status='failed', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        (args.evidence / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'checks': len(report['checks']),
                      'rank_seconds': report['rank_seconds'], 'workspace': report['workspace']}))


if __name__ == '__main__':
    main()
