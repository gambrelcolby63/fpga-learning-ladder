#!/usr/bin/env bash
# Run lint + tests for one project and report. Used by CI; handy locally too:
#   scripts/ci_project.sh 01-uart
#
# A project whose rtl/*.sv still contains the "LADDER-SKELETON" marker line counts as
# NOT STARTED: its results are reported but never fail the build. Delete the marker line from
# every file in rtl/ when you start a project, and from then on CI holds you to lint + tests.
set -uo pipefail
proj=${1:?usage: ci_project.sh <project-dir>}
root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root/$proj"
mkdir -p build

if grep -l "LADDER-SKELETON" rtl/*.sv >/dev/null 2>&1; then started=0; else started=1; fi

make lint > build/lint.log 2>&1; lint_rc=$?
make test > build/test.log 2>&1; test_rc=$?

lint_res=$([ $lint_rc -eq 0 ] && echo PASS || echo FAIL)
test_res=$([ $test_rc -eq 0 ] && echo PASS || echo FAIL)
passed=0; total=0
if [ -f build/summary.txt ]; then
  while read -r _ frac _; do
    passed=$((passed + ${frac%/*})); total=$((total + ${frac#*/}))
  done < build/summary.txt
fi
status=$([ $started -eq 1 ] && echo "started" || echo "not started (skeleton)")

echo "== $proj: $status | lint $lint_res | tests $test_res ($passed/$total)"
cat build/summary.txt 2>/dev/null
if [ $lint_rc -ne 0 ]; then echo "--- lint output (tail)"; tail -n 30 build/lint.log; fi
if [ $test_rc -ne 0 ]; then echo "--- most common failures"; grep -E "AssertionError|Error:" build/test.log | sort | uniq -c | sort -rn | head -n 10; fi

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  {
    echo "### $proj: $status"
    echo ""
    echo "| lint | tests passed |"
    echo "|---|---|"
    echo "| $lint_res | $passed / $total |"
    echo ""
    echo '```'
    cat build/summary.txt 2>/dev/null
    echo '```'
  } >> "$GITHUB_STEP_SUMMARY"
fi

if [ $started -eq 0 ]; then
  echo "::notice title=$proj not started::skeleton marker present, so results are informational ($passed/$total tests pass)"
  exit 0
fi
[ $lint_rc -eq 0 ] && [ $test_rc -eq 0 ]
