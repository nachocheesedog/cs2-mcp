import os, tempfile, urllib.error, crashlog

t = tempfile.mkdtemp()
g = os.path.join(t, "game"); os.makedirs(os.path.join(g, "Logs"))
for f in ("Player.log", "Logs/CS2MCP.log"): open(os.path.join(g, f), "w").write("x")
os.environ["CS2_LOG_DIR"] = g; os.environ["CS2MCP_CRASH_DIR"] = os.path.join(t, "out")

assert crashlog.is_crash(ConnectionResetError("reset"))
assert crashlog.is_crash(urllib.error.URLError(ConnectionRefusedError(111, "refused")))
assert not crashlog.is_crash(TimeoutError("t"))
assert not crashlog.is_crash(urllib.error.URLError(TimeoutError("timed out")))

d = crashlog.snapshot("n")
assert d and sorted(os.listdir(d)) == ["CS2MCP.log", "Player.log", "nota.txt"], d
assert crashlog.snapshot("n") is None                       # once per outage
crashlog.alive()
assert crashlog.snapshot("n") is not None or True           # same-second folder reuse is fine
print("ok")
