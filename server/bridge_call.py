"""Call the CS2MCP bridge directly with a long timeout: python bridge.py <method> '<json params>' [timeout_s]
<json params> may be @path\\to\\params.json for payloads too long for a command line (e.g. a full sub-object list)."""
import json, sys, urllib.request, urllib.error
method = sys.argv[1]
arg = sys.argv[2] if len(sys.argv) > 2 else "{}"
params = json.load(open(arg[1:], encoding="utf-8")) if arg.startswith("@") else json.loads(arg)
timeout = float(sys.argv[3]) if len(sys.argv) > 3 else 120
params.setdefault("timeout_s", timeout)
req = urllib.request.Request("http://127.0.0.1:17654/rpc", data=json.dumps({"method": method, "params": params}).encode(),
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=timeout + 30) as r: out = json.loads(r.read())
except urllib.error.HTTPError as e:
    out = json.loads(e.read())
print(json.dumps(out, indent=1))
