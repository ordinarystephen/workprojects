#!/usr/bin/env bash
# Manual smoke test for the length toggle backend.
# Requires: KRONOS server running locally (python server.py or gunicorn),
#           a test workbook at ./smoke_test.xlsx (use your smoke fixture).

set -u  # error on undefined vars

BASE_URL="${KRONOS_URL:-http://localhost:5000}"
FIXTURE="${KRONOS_FIXTURE:-./pipeline/tests/fixtures/smoke_lending.xlsx}"
SESSION_ID="smoke-$(date +%s)"

if [[ ! -f "$FIXTURE" ]]; then
  echo "Fixture not found at: $FIXTURE"
  echo "Set KRONOS_FIXTURE env var or copy a workbook to this path."
  exit 1
fi

# Encode the workbook as base64 (KRONOS expects this for JSON transport)
FILE_B64=$(base64 < "$FIXTURE" | tr -d '\n')
FILE_NAME=$(basename "$FIXTURE")

run_test() {
  local label="$1"
  local length="$2"
  local expect_status="$3"

  echo ""
  echo "=========================================="
  echo "TEST: $label"
  echo "  length=$length, expected_status=$expect_status"
  echo "=========================================="

  # Build request body
  if [[ "$length" == "OMITTED" ]]; then
    BODY=$(cat <<EOF
{
  "file_name": "$FILE_NAME",
  "file_b64": "$FILE_B64",
  "mode": "firm-level",
  "parameters": {},
  "prompt": "Firm-level smoke test"
}
EOF
)
  else
    BODY=$(cat <<EOF
{
  "file_name": "$FILE_NAME",
  "file_b64": "$FILE_B64",
  "mode": "firm-level",
  "parameters": {},
  "prompt": "Firm-level smoke test",
  "length": "$length"
}
EOF
)
  fi

  # Send request, capture status and body separately
  RESPONSE=$(curl -s -w "\n---STATUS---\n%{http_code}" \
    -X POST "$BASE_URL/upload" \
    -H "Content-Type: application/json" \
    -H "X-Kronos-Session: $SESSION_ID" \
    -d "$BODY")

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
    # Extract narrative and count chars
    NARRATIVE=$(echo "$BODY_OUT" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    narrative = data.get('narrative', '')
    print(narrative)
except Exception as e:
    print(f'JSON parse error: {e}', file=sys.stderr)
    sys.exit(1)
")
    CHAR_COUNT=${#NARRATIVE}
    SENTENCE_COUNT=$(echo "$NARRATIVE" | grep -oE '[.!?]+' | wc -l | tr -d ' ')

    echo "Narrative length: $CHAR_COUNT chars, ~$SENTENCE_COUNT sentences"
    echo ""
    echo "First 300 chars:"
    echo "${NARRATIVE:0:300}..."
  else
    echo "Error response:"
    echo "$BODY_OUT" | head -10
  fi
}

echo "KRONOS Length Smoke Test"
echo "Base URL: $BASE_URL"
echo "Fixture: $FIXTURE"
echo "Session: $SESSION_ID"

# The five tests
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