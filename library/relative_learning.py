"""Finite-group relative evidence mathematics; the adaptive owner handles SQL."""
import importlib.util
import math
from pathlib import Path

_spec = importlib.util.spec_from_file_location('sonder_relation_features', Path(__file__).with_name('relation_features.py'))
features = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(features)
MULTIPLIER = float((2 * len(features.RELATIONS)) ** 2)


def readout(local):
    result = {}
    for keys in features.GROUPS.values():
        if not any(key in local for key in keys):
            continue
        values = [local.get(key, 0.0) for key in keys]
        minimum = min(values)
        result.update({key: value - minimum for key, value in zip(keys, values)})
    return result


def gradient(local, difference):
    result = {}
    groups = {features.KEY_GROUP[key] for key in difference if key.startswith(features.PREFIX)}
    for group in groups:
        keys = features.GROUPS[group]
        minimum = min(local.get(key, 0.0) for key in keys)
        minima = [key for key in keys if local.get(key, 0.0) == minimum]
        mass = math.fsum(difference.get(key, 0.0) for key in keys)
        for key in keys:
            value = difference.get(key, 0.0) - (mass / len(minima) if key in minima else 0.0)
            if value:
                result[key] = value
    return result


def center_and_bound(local, ceiling):
    result = dict(local)
    for keys in features.GROUPS.values():
        if not any(key in local for key in keys):
            continue
        values = [local.get(key, 0.0) for key in keys]
        mean = math.fsum(values) / len(values)
        span = max(values) - min(values)
        factor = min(1.0, ceiling / span) if span else 1.0
        result.update({key: (value - mean) * factor for key, value in zip(keys, values)})
    if any(not math.isfinite(value) for value in result.values()):
        raise ValueError('relative weights must be finite')
    return result
