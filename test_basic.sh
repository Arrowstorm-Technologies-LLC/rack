#!/usr/bin/env bash
# Basic smoke / verification tests for rack (bash + python)
# Run with: ./test_basic.sh   (from inside the rack repo dir)
set -euo pipefail

echo "== rack basic verification =="

echo "[1/10] Bash syntax check..."
bash -n ./rack
echo "  OK"

echo "[2/10] Python syntax/compile check..."
python3 -m py_compile ./rack.py
echo "  OK"

echo "[3/10] Bash help / usage..."
./rack 2>&1 | head -5 || true
echo "  (shown)"

echo "[4/10] Python help / usage..."
python3 ./rack.py 2>&1 | head -5 || true
echo "  (shown)"

echo "[5/10] Name validation rejects bad names (., .., leading -)..."
set +e
( ./rack '..' owner/repo 2>&1 || true ) | grep -qi 'invalid name' || { echo "FAIL: did not reject .."; exit 1; }
( python3 ./rack.py '..' owner/repo 2>&1 || true ) | grep -qi 'invalid name' || { echo "FAIL: py did not reject .."; exit 1; }
( ./rack 'bad/name' owner/repo 2>&1 || true ) | grep -qi 'invalid name' || { echo "FAIL: did not reject bad/name"; exit 1; }
set -e
echo "  OK"

echo "[6/10] Dry-run flag accepted (no real work)..."
( ./rack -n testdry https://example.com/foo.tar.gz 2>&1 || true ) | grep -qi 'dry run' || { echo "FAIL bash -n"; exit 1; }
( python3 ./rack.py -n testdry https://example.com/foo.tar.gz 2>&1 || true ) | grep -qi 'dry run' || { echo "FAIL py -n"; exit 1; }
echo "  OK"

echo "[7/10] System name conflict reported in dry-run..."
( ./rack -n ls https://example.com/foo.tar.gz 2>&1 || true ) | grep -qi 'conflicts with existing command' || { echo "FAIL bash conflict dry-run"; exit 1; }
( python3 ./rack.py -n ls https://example.com/foo.tar.gz 2>&1 || true ) | grep -qi 'conflicts with existing command' || { echo "FAIL py conflict dry-run"; exit 1; }
echo "  OK"

echo "[8/10] Non-interactive install aborts on system name conflict..."
set +e
( ./rack ls https://example.com/foo.tar.gz </dev/null 2>&1 || true ) | grep -qi 'conflicts with existing command' || { echo "FAIL bash non-interactive conflict"; exit 1; }
( python3 ./rack.py ls https://example.com/foo.tar.gz </dev/null 2>&1 || true ) | grep -qi 'conflicts with existing command' || { echo "FAIL py non-interactive conflict"; exit 1; }
set -e
echo "  OK"

echo "[9/10] Bash installer-first archive logic present..."
grep -q '_find_installer_script' ./rack
grep -q '_run_project_installer' ./rack
grep -q 'RACK_DIR=' ./rack
echo "  OK"

echo "[10/12] Python installer-first archive logic present..."
grep -q 'find_installer_script' ./rack.py
grep -q 'run_project_installer' ./rack.py
grep -q 'SOURCE_ARCHIVE' ./rack.py
echo "  OK"

echo "[11/12] Bash branch-archive fallback for release-less repos..."
grep -q '_gh_fallback_branch_archive' ./rack
grep -q '_gh_repo_exists' ./rack
echo "  OK"

echo "[12/12] Python branch-archive fallback for release-less repos..."
grep -q 'fallback_branch_archive' ./rack.py
grep -q 'gh_repo' ./rack.py
echo "  OK"

echo ""
echo "All basic verification checks passed."
echo "Note: full end-to-end requires network + real GitHub assets."
echo "Cross-distro tests: manual verification recommended for asset filtering and source fallback (see examples in docs)."
