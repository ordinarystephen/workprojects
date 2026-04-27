import base64, json, urllib.request

with open("test.xlsx", "rb") as f:
    file_b64 = base64.b64encode(f.read()).decode("ascii")

req = urllib.request.Request(
    "http://127.0.0.1:5000/upload",
    data=json.dumps({
        "file_name": "test.xlsx",
        "file_b64": file_b64,
        "mode": "industry-portfolio-level",
        "parameters": {"portfolio": "Information Technology"},
        "length": "full",
        "prompt": "diagnostic",
    }).encode(),
    headers={
        "Content-Type": "application/json",
        "X-Kronos-Session": "diagnostic-perslice",
    },
    method="POST",
)
with urllib.request.urlopen(req, timeout=120) as r:
    resp = json.loads(r.read())

print("Top-level keys:")
for key in resp.keys():
    print(f"  {key}")
print()

verification = resp.get("verification", {})
print("Verification keys (exact spelling):")
for key in verification.keys():
    print(f"  '{key}'")
print()

print(f"total: {verification.get('total')}")
print(f"verified_count: {verification.get('verified_count')}")
print(f"all_clear: {verification.get('all_clear')}")
print()

# Print first 2 claim results to see what status/reason they have
results = verification.get("claim_results") or verification.get("claims_results") or []
print(f"Number of claim results: {len(results)}")
print()
print("First 2 claim results:")
for r in results[:2]:
    print(json.dumps(r, indent=2))
print()

# Print first 2 claims to see what source_field they have
claims = resp.get("claims", [])
print(f"Number of claims: {len(claims)}")
print()
print("First 2 claims:")
for c in claims[:2]:
    print(json.dumps(c, indent=2))
print()

# Print first 5 verifiable_values keys to see what slicer publishes
verifiable_values = resp.get("verifiable_values", {})
print(f"Number of verifiable_values keys: {len(verifiable_values)}")
print()
print("First 10 verifiable_values keys:")
for key in list(verifiable_values.keys())[:10]:
    print(f"  '{key}'")

results = verification.get("claim_results") or []
print("All FAILING claim results:")
for r in results:
    if r.get("status") != "verified":
        idx = r.get("claim_index", -1)
        if 0 <= idx < len(claims):
            source_field = claims[idx].get("source_field", "(missing)")
            cited = claims[idx].get("cited_value", "(missing)")
        else:
            source_field = "(unknown)"
            cited = "(unknown)"
        print(f"  claim_index={idx}")
        print(f"    source_field: {source_field}")
        print(f"    cited_value: {cited}")
        print(f"    status: {r.get('status')}")
        print(f"    reason: {r.get('reason')}")
        print(f"    expected: {r.get('expected')}")
        print(f"    actual: {r.get('actual')}")
        print()