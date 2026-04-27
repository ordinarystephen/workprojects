import base64
import json
import urllib.request

with open("test.xlsx", "rb") as f:
    file_b64 = base64.b64encode(f.read()).decode("ascii")

req = urllib.request.Request(
    "http://127.0.0.1:5000/upload",
    data=json.dumps({
        "file_name": "test.xlsx",
        "file_b64": file_b64,
        "mode": "firm-level",
        "parameters": {},
        "length": "full",
        "prompt": "diagnostic",
    }).encode(),
    headers={
        "Content-Type": "application/json",
        "X-Kronos-Session": "diagnostic",
    },
    method="POST",
)
with urllib.request.urlopen(req, timeout=120) as r:
    resp = json.loads(r.read())

print("Top-level keys in response:")
for key in resp.keys():
    print(f"  {key}")

print()
print("If 'verification' or 'verification_result' is present:")
for key in ["verification", "verification_result", "validation"]:
    if key in resp:
        print(f"  Found '{key}': type={type(resp[key]).__name__}")
        if isinstance(resp[key], dict):
            print(f"    Sub-keys: {list(resp[key].keys())}")