#!/usr/bin/env bash
# Learning-engine / installation integration gate, not an author editing session.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -B -I -m unittest discover -s tests -p 'test_*.py' -v
echo "Learning integration PASS"
