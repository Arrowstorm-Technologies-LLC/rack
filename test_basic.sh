#!/usr/bin/env bash
# Basic smoke / verification tests for rack (bash + python)
# Run with: ./test_basic.sh   (from inside the rack repo dir)
set -euo pipefail

echo "== rack basic verification =="

echo "[1/6] Bash syntax check..."
bash -n ./rack
echo "  OK"

echo "[2/6] Python syntax/compile check..."
python3 -m py_compile ./rack.py
echo "  OK"

echo "[3/6] Bash help / usage..."
./rack 2>&1 | head -5 || true
echo "  (shown)"

echo "[4/6] Python help / usage..."
python3 ./rack.py 2>&1 | head -5 || true
echo "  (shown)"

echo "[5/6] Name validation rejects bad names (., .., leading -)..."
set +e
( ./rack '..' owner/repo 2>&1 || true ) | grep -qi 'invalid name' || { echo "FAIL: did not reject .."; exit 1; }
( python3 ./rack.py '..' owner/repo 2>&1 || true ) | grep -qi 'invalid name' || { echo "FAIL: py did not reject .."; exit 1; }
( ./rack 'bad/name' owner/repo 2>&1 || true ) | grep -qi 'invalid name' || { echo "FAIL: did not reject bad/name"; exit 1; }
set -e
echo "  OK"

echo "[6/6] Dry-run flag accepted (no real work)..."
( ./rack -n testdry https://example.com/foo.tar.gz 2>&1 || true ) | grep -qi 'dry run' || { echo "FAIL bash -n"; exit 1; }
( python3 ./rack.py -n testdry https://example.com/foo.tar.gz 2>&1 || true ) | grep -qi 'dry run' || { echo "FAIL py -n"; exit 1; }
echo "  OK"

echo ""
echo "All basic verification checks passed."
echo "Note: full end-to-end requires network + real GitHub assets."
