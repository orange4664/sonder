"""Bounded acceptance profile and nonnegative comparative preferences.

The caller owns validation, feature extraction, transaction and clock. This
module never opens a database or imports optional NLP dependencies. Old receipts
and exemplars stay in their original tables and are not training inputs here.
"""
from collections import defaultdict
import importlib.util
import math
from pathlib import Path

_spec = importlib.util.spec_from_file_location('sonder_relative_learning', Path(__file__).with_name('relative_learning.py'))
_relative = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_relative)

SCORING_VERSION = 3
SCORING_VERSIONS = {'adaptive-v1': SCORING_VERSION, 'adaptive-v2': 4, 'adaptive-v3': 5}
POLICIES = ('legacy', *SCORING_VERSIONS)
OPPOSITION_HORIZON = 32.0


def create_schema(conn):
    for statement in (
        'CREATE TABLE IF NOT EXISTS adaptive_settings (key TEXT PRIMARY KEY NOT NULL,value TEXT NOT NULL)',
        "CREATE TABLE IF NOT EXISTS adaptive_weights (context TEXT NOT NULL,channel TEXT NOT NULL CHECK(channel IN ('profile','comparison')),feature TEXT NOT NULL,weight REAL NOT NULL CHECK(weight>=0),last_at TEXT NOT NULL,PRIMARY KEY(context,channel,feature))",
        'CREATE TABLE IF NOT EXISTS adaptive_legacy (context TEXT NOT NULL,feature TEXT NOT NULL,weight INTEGER NOT NULL,last_at TEXT NOT NULL,PRIMARY KEY(context,feature))',
        'CREATE TABLE IF NOT EXISTS adaptive_legacy_mass (context TEXT PRIMARY KEY NOT NULL,raw_mass REAL NOT NULL CHECK(raw_mass>=1))',
        f'CREATE TABLE IF NOT EXISTS adaptive_opposition (context TEXT NOT NULL,feature TEXT NOT NULL,value REAL NOT NULL CHECK(value>=0 AND value<={OPPOSITION_HORIZON:g}),last_at TEXT NOT NULL,PRIMARY KEY(context,feature))',
        'CREATE TABLE IF NOT EXISTS adaptive_relative (context TEXT NOT NULL,feature TEXT NOT NULL,value REAL NOT NULL,last_at TEXT NOT NULL,PRIMARY KEY(context,feature))',
    ):
        conn.execute(statement)


def policy(conn):
    settings = dict(conn.execute('SELECT key,value FROM adaptive_settings'))
    value = settings.get('policy', 'legacy')
    if value not in POLICIES:
        raise ValueError('unsupported stored learning policy')
    return dict(policy=value, configured='policy' in settings,
                scoring_version=SCORING_VERSIONS.get(value, 2),
                adaptive_initialized=settings.get('initialized') == '1', changed=False)


def configure(conn, action):
    """Called under the writer lock, including the one-time raw snapshot."""
    state = policy(conn)
    if action == 'ensure-adaptive':
        if state['configured']:
            return state
        action = 'adaptive-v3'
    if action not in POLICIES:
        raise ValueError('learning policy must be legacy, adaptive-v1, adaptive-v2, adaptive-v3 or ensure-adaptive')
    if action in SCORING_VERSIONS and not state['adaptive_initialized']:
        for table, prefix in [('phrase_prefs', 'phrase|'), ('syntax_prefs', 'syntax|')]:
            conn.execute(f'INSERT INTO adaptive_legacy(context,feature,weight,last_at) '
                         f'SELECT context,? || feature,weight,last_at FROM {table}', (prefix,))
        conn.execute('INSERT INTO adaptive_legacy_mass(context,raw_mass) '
                     'SELECT context,MAX(1.0,SUM(weight)) FROM adaptive_legacy GROUP BY context')
        conn.execute("INSERT INTO adaptive_settings VALUES('initialized','1')")
    changed = not state['configured'] or state['policy'] != action
    if changed:
        # The comparison/profile model is shared, so returning to v1 never
        # discards newer learning. A suspended correction trace cannot claim
        # to include choices made while another strategy was active.
        if {'adaptive-v2', 'adaptive-v3'} & {state['policy'], action}:
            conn.execute('DELETE FROM adaptive_opposition')
        conn.execute("INSERT INTO adaptive_settings VALUES('policy',?) "
                     'ON CONFLICT(key) DO UPDATE SET value=excluded.value', (action,))
    return {**policy(conn), 'changed': changed}


def weights_at(conn, context, now, decay):
    current = {'profile': {}, 'comparison': {}, 'legacy': {}}
    for channel, feature, weight, last_at in conn.execute(
            'SELECT channel,feature,weight,last_at FROM adaptive_weights WHERE context=?', (context,)):
        current[channel][feature] = weight * decay(last_at, now)
    for feature, weight, last_at in conn.execute(
            'SELECT feature,weight,last_at FROM adaptive_legacy WHERE context=?', (context,)):
        current['legacy'][feature] = int(weight * decay(last_at, now))
    row = conn.execute('SELECT raw_mass FROM adaptive_legacy_mass WHERE context=?', (context,)).fetchone()
    current['legacy_raw_mass'] = row[0] if row else None
    if policy(conn)['policy'] == 'adaptive-v3':
        current['relative'] = {feature: value * decay(stamp, now) for feature, value, stamp in conn.execute(
            'SELECT feature,value,last_at FROM adaptive_relative WHERE context=?', (context,))}
        if any(key not in _relative.features.KEY_GROUP or not math.isfinite(value)
               for key, value in current['relative'].items()):
            raise ValueError('unsupported or non-finite relative feature state')
    return current


def _dot(weights, vector):
    return sum(weights.get(key, 0) * value for key, value in vector.items())


def scores(current, vector):
    comparison = _dot(current['comparison'], vector)
    if 'relative' in current:
        comparison += _dot(_relative.readout(current['relative']), vector)
    profile = _dot(current['profile'], vector)
    legacy = round(_dot(current['legacy'], vector), 6)
    mass = current['legacy_raw_mass']
    prior = legacy / mass if mass is not None else 0.0
    return dict(comparison_score=comparison, profile_score=profile,
                legacy_score=legacy, legacy_prior_score=prior, legacy_raw_mass=mass,
                score=comparison + profile + prior)


def _strength(weights, vector, eta):
    return eta / (1 + math.exp(max(-50, min(50, _dot(weights, vector)))))


def _write_weights(conn, context, channel, weights, now, ceiling):
    for feature, weight in sorted(weights.items()):
        if not math.isfinite(weight) or weight < 0:
            raise ValueError('adaptive weights must be finite and nonnegative')
        conn.execute('INSERT INTO adaptive_weights VALUES(?,?,?,?,?) '
                     'ON CONFLICT(context,channel,feature) DO UPDATE SET '
                     'weight=excluded.weight,last_at=excluded.last_at',
                     (context, channel, feature, min(weight, ceiling), now.isoformat()))


def _opposition_feedback(conn, context, comparison, vector, differences, now, decay):
    """Accumulate only local comparative evidence, in the caller's transaction."""
    positive, negative = defaultdict(float), defaultdict(float)
    for difference in differences:
        for key, value in difference.items():
            denominator = max(vector.get(key, 0), vector.get(key, 0) - value)
            if denominator > 0:
                amount = min(1.0, abs(value) / denominator) / len(differences)
                (positive if value > 0 else negative)[key] += amount
    # Each mean is mathematically <= 1. Repeated additions such as nine 1/9
    # terms can round above it and turn a cleared trace slightly negative.
    positive = {key: min(1.0, value) for key, value in positive.items()}
    negative = {key: min(1.0, value) for key, value in negative.items()}
    previous = {key: (value, stamp) for key, value, stamp in conn.execute(
        'SELECT feature,value,last_at FROM adaptive_opposition WHERE context=?', (context,))}
    for key in sorted(positive.keys() | negative.keys()):
        value, stamp = previous.get(key, (0.0, now.isoformat()))
        trace = min(OPPOSITION_HORIZON,
                    value * decay(stamp, now) * (1 - positive.get(key, 0)) + negative.get(key, 0))
        if not math.isfinite(trace) or trace < 0:
            raise ValueError('opposition traces must be finite and nonnegative')
        if trace or key in previous:
            conn.execute('INSERT INTO adaptive_opposition VALUES(?,?,?,?) '
                         'ON CONFLICT(context,feature) DO UPDATE SET value=excluded.value,last_at=excluded.last_at',
                         (context, key, trace, now.isoformat()))
        if negative.get(key, 0) and key in comparison:
            comparison[key] *= 2 ** (-negative[key] * trace / OPPOSITION_HORIZON)
    return dict(opposition_features=len(negative), positive_trace_evidence_features=len(positive))


def update(conn, context, vector, differences, author_owned, now, decay, feature_family, ceiling):
    """Average distinct differences against the same decayed pre-update state."""
    current = weights_at(conn, context, now, decay)
    strategy = policy(conn)
    correction = {}
    relative = current.get('relative')
    comparison = current['comparison']
    relative_before = _relative.readout(relative) if relative is not None else {}
    if relative is not None:
        comparison = {**comparison, **relative_before}
    if strategy['policy'] in ('adaptive-v2', 'adaptive-v3'):
        correction = _opposition_feedback(conn, context, comparison, vector,
                                          differences, now, decay)
    if relative is not None:
        # Applying the existing correction to nonnegative excess retains the
        # group's minimum. Never shrink a negative latent value toward zero.
        relative = {key: value - (relative_before.get(key, 0) - comparison.get(key, 0))
                    for key, value in relative.items()}
    eta = 1.5 if author_owned else 1.0
    profile = current['profile']
    strength = _strength(profile, vector, eta)
    for key, value in vector.items():
        profile[key] = profile.get(key, 0) + strength * value
    mass = sum(profile.values())
    if mass > 1:
        profile = {key: value / mass for key, value in profile.items()}
    _write_weights(conn, context, 'profile', profile, now, ceiling)
    delta = defaultdict(float)
    relative_delta = defaultdict(float)
    for difference in differences:
        strength = _strength(comparison, difference, eta)
        for key, value in difference.items():
            if relative is None or not key.startswith(_relative.features.PREFIX):
                delta[key] += strength * value / len(differences)
        if relative is not None:
            for key, value in _relative.gradient(relative, difference).items():
                relative_delta[key] += _relative.MULTIPLIER * strength * value / len(differences)
    if differences:
        for key, value in delta.items():
            comparison[key] = max(0.0, comparison.get(key, 0) + value)
        _write_weights(conn, context, 'comparison',
                       {key: value for key, value in comparison.items()
                        if relative is None or not key.startswith(_relative.features.PREFIX)}, now, ceiling)
        if relative is not None:
            for key, value in relative_delta.items():
                relative[key] = relative.get(key, 0) + value
            for key, value in sorted(_relative.center_and_bound(relative, ceiling).items()):
                conn.execute('INSERT INTO adaptive_relative VALUES(?,?,?,?) '
                             'ON CONFLICT(context,feature) DO UPDATE SET value=excluded.value,last_at=excluded.last_at',
                             (context, key, value, now.isoformat()))
    counts = dict.fromkeys(('phrase', 'base', 'analysis', 'grammar'), 0)
    for key in {key for difference in differences for key in difference}:
        counts[feature_family(key)] += 1
    kinds = ['accepted_text_observation']
    if differences:
        kinds.append('comparative_preference')
    return dict(policy=strategy['policy'], scoring_version=strategy['scoring_version'],
                accepted_features=len(vector), profile_mass=sum(profile.values()),
                comparative_differences=len(differences), contrast_feature_counts=counts,
                evidence_kinds=kinds, **correction)
