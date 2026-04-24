#!/usr/bin/env bash
# Manual smoke test for the length toggle backend.
# Fixed version: writes request body to temp file to avoid argv size limits.

set -u

BASE_URL="${KRONOS_URL:-http://localhost:5000}"
FIXTURE="${KRONOS_FIXTURE:-./pipeline/tests/fixtures/smoke_lending.xlsx}"
SESSION_ID="smoke-$(date +%s)"

if [[ ! -f "$FIXTURE" ]]; then
  echo "Fixture not found at: $FIXTURE"
  echo "Set KRONOS_FIXTURE env var or copy a workbook to this path."
  exit 1
fi

FILE_B64=$(base64 < "$FIXTURE" | tr -d '\n')
FILE_NAME=$(basename "$FIXTURE")

# Temp directory for request bodies; cleaned up on exit
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

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

  # Build request body to a file (avoids argv size limit)
  if [[ "$length" == "OMITTED" ]]; then
    python3 - "$FILE_NAME" "$FILE_B64" > "$body_file" <<'EOF'
import json, sys
file_name, file_b64 = sys.argv[1], sys.argv[2]
body = {
    "file_name": file_name,
    "file_b64": file_b64,
    "mode": "firm-level",
    "parameters": {},
    "prompt": "Firm-level smoke test",
}
print(json.dumps(body))
EOF
  else
    python3 - "$FILE_NAME" "$FILE_B64" "$length" > "$body_file" <<'EOF'
import json, sys
file_name, file_b64, length = sys.argv[1], sys.argv[2], sys.argv[3]
body = {
    "file_name": file_name,
    "file_b64": file_b64,
    "mode": "firm-level",
    "parameters": {},
    "prompt": "Firm-level smoke test",
    "length": length,
}
print(json.dumps(body))
EOF
  fi

  # -d @file reads body from file instead of argv
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
    NARRATIVE=$(echo "$BODY_OUT" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    print(data.get('narrative', ''))
except Exception as e:
    print(f'JSON parse error: {e}', file=sys.stderr)
    sys.exit(1)
")
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