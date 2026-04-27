#!/usr/bin/env bash
# Manual smoke test for the length toggle backend.
# v3: base64 lives in a temp file, never passed via argv.

set -u

BASE_URL="${KRONOS_URL:-http://localhost:5000}"
FIXTURE="${KRONOS_FIXTURE:-./pipeline/tests/fixtures/smoke_lending.xlsx}"
SESSION_ID="smoke-$(date +%s)"

if [[ ! -f "$FIXTURE" ]]; then
  echo "Fixture not found at: $FIXTURE"
  echo "Set KRONOS_FIXTURE env var or copy a workbook to this path."
  exit 1
fi

FILE_NAME=$(basename "$FIXTURE")
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# Encode file once, write to disk. Python reads from disk.
B64_FILE="$TMPDIR/file.b64"
base64 < "$FIXTURE" | tr -d '\n' > "$B64_FILE"

run_test() {
  local label="$1"
  local length="$2"
  local expect_status="$3"
  local body_file="$TMPDIR/body.json"

  echo ""
  echo "=========================================="
  echo "TEST: $label"
  echo "  length=$length, expected_status=$expect_status"
  echo "=========================================="

  # Write body via Python, reading base64 from file (no argv limit).
  # File name and length are small enough to pass as argv.
  FILE_NAME="$FILE_NAME" LENGTH="$length" B64_PATH="$B64_FILE" python3 > "$body_file" <<'PYEOF'
import json
import os

file_name = os.environ["FILE_NAME"]
length = os.environ["LENGTH"]
b64_path = os.environ["B64_PATH"]

with open(b64_path, "r") as f:
    file_b64 = f.read()

body = {
    "file_name": file_name,
    "file_b64": file_b64,
    "mode": "firm-level",
    "parameters": {},
    "prompt": "Firm-level smoke test",
}

if length != "OMITTED":
    body["length"] = length

print(json.dumps(body))
PYEOF

  RESPONSE=$(curl -s -w "\n---STATUS---\n%{http_code}" \
    -X POST "$BASE_URL/upload" \
    -H "Content-Type: application/json" \
    -H "X-Kronos-Session: $SESSION_ID" \
    -d "@$body_file")

  STATUS=$(echo "$RESPONSE" | awk '/---STATUS---/{flag=1; next} flag')
  BODY_OUT=$(echo "$RESPONSE" | sed '/---STATUS---/,$d')

  echo "Status: $STATUS (expected $expect_status)"

  if [[ "$STATUS" != "$expect_status" ]]; then
    echo "!!! STATUS MISMATCH !!!"
    echo "Response body:"
    echo "$BODY_OUT" | head -20
    return 1
  fi

  if [[ "$STATUS" == "200" ]]; then
    NARRATIVE=$(BODY_OUT="$BODY_OUT" python3 <<'PYEOF'
import json, os, sys
try:
    data = json.loads(os.environ["BODY_OUT"])
    print(data.get("narrative", ""))
except Exception as e:
    print(f"JSON parse error: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
)
    CHAR_COUNT=${#NARRATIVE}
    SENTENCE_COUNT=$(echo "$NARRATIVE" | grep -oE '[.!?]+' | wc -l | tr -d ' ')

    echo "Narrative length: $CHAR_COUNT chars, ~$SENTENCE_COUNT sentences"
    echo ""
    echo "First 500 chars:"
    echo "${NARRATIVE:0:500}..."
  else
    echo "Error response:"
    echo "$BODY_OUT" | head -10
  fi
}

echo "KRONOS Length Smoke Test"
echo "Base URL: $BASE_URL"
echo "Fixture: $FIXTURE"
echo "Session: $SESSION_ID"

run_test "1. Full length"          "full"         "200"
run_test "2. Executive length"     "executive"    "200"
run_test "3. Distillation length"  "distillation" "200"
run_test "4. Invalid length"       "brief"        "400"
run_test "5. Omitted length"       "OMITTED"      "200"

echo ""
echo "=========================================="
echo "Smoke test complete."
echo "=========================================="
echo ""
echo "What to verify by eye:"
echo "  - Test 1 narrative is longest (comprehensive)"
echo "  - Test 2 is meaningfully shorter with different structure (not just compressed)"
echo "  - Test 3 is 2-3 sentences total"
echo "  - Test 4 error message lists valid lengths: [distillation, executive, full]"
echo "  - Test 5 narrative matches Test 1 (omitted defaults to full)"