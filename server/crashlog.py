"""Keeps the evidence of a game crash. When the bridge stops answering (connection refused / reset, not a mere
timeout) the game's Player.log and the mod's CS2MCP.log are copied, once per outage, into a timestamped folder -
before relaunching the game rotates them away. CS2MCP.log lists every command as '> method ...' and '< method N ms';
a '>' with no '<' as the last line is the call that was running when the game died.

Folder: $CS2MCP_CRASH_DIR, else ~/cs2mcp-crashes. Game log folder: $CS2_LOG_DIR, else searched under /mnt/c/Users
(WSL) or the Windows profile."""
import glob, os, shutil, time

_SUB = os.path.join("AppData", "LocalLow", "Colossal Order", "Cities Skylines II")
_FILES = [("Player.log", ""), ("Player-prev.log", ""), ("CS2MCP.log", "Logs"), ("CS2MCP-prev.log", "Logs")]
_saved = False                                                                              # one snapshot per outage


def _game_dir():
    d = os.environ.get("CS2_LOG_DIR")
    if d and os.path.isdir(d): return d
    for pat in (os.path.join(os.path.expanduser("~"), _SUB), "/mnt/c/Users/*/" + _SUB.replace("\\", "/")):
        for c in glob.glob(pat):
            if os.path.isdir(c): return c
    return None


def is_crash(exc):
    """True for a dead bridge (nothing listening / connection reset); False for timeouts, which only mean 'busy'."""
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, (TimeoutError,)) or "timed out" in str(reason).lower(): return False
    return isinstance(reason, (ConnectionError, OSError))


def snapshot(note=""):
    """Copy the logs once per outage; returns the folder or None. Never raises."""
    global _saved
    if _saved: return None
    try:
        src = _game_dir()
        if not src: return None
        dst = os.path.join(os.environ.get("CS2MCP_CRASH_DIR") or os.path.join(os.path.expanduser("~"), "cs2mcp-crashes"),
                           time.strftime("%Y%m%d-%H%M%S"))
        os.makedirs(dst, exist_ok=True)
        n = 0
        for name, sub in _FILES:
            f = os.path.join(src, sub, name)
            if os.path.isfile(f): shutil.copy2(f, os.path.join(dst, name)); n += 1
        if note:
            with open(os.path.join(dst, "nota.txt"), "w") as fh: fh.write(note + "\n")
        _saved = n > 0
        return dst if n else None
    except Exception:
        return None


def alive():
    """Call when the bridge answers again: the next outage gets its own snapshot."""
    global _saved
    _saved = False
