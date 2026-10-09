"""Game session control: list/load saves, main menu, quit, and a full restart loop (quit -> wait -> kill if hung ->
launch -> wait for the main menu -> optionally load a save and wait until the city is in). Mod 0.17 commands
list_saves / load_save / main_menu / quit_game; process control happens here (Windows)."""
import os, subprocess, time

EXE = "Cities2.exe"
STEAM_URI = "steam://rungameid/949230"


def _running():
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXE}", "/NH"], capture_output=True, text=True).stdout
    return EXE.lower() in out.lower()


def _status(rpc):
    try:
        return rpc("ping", {}, timeout=5)
    except Exception:
        return None


def wait_for(rpc, pred, timeout_s, step=3.0):
    """Poll the bridge until pred(status dict) is true. Returns (ok, last status, seconds waited)."""
    t0 = time.time(); last = None
    while time.time() - t0 < timeout_s:
        st = _status(rpc)
        if st is not None:
            last = st
            if pred(st): return True, last, round(time.time() - t0)
        time.sleep(step)
    return False, last, round(time.time() - t0)


def _mode(st):
    g = st.get("game", st) if isinstance(st, dict) else {}
    return g.get("game_mode"), g.get("city_loaded"), g.get("loading")


def settle(rpc, secs=20, timeout_s=300):
    """Wait until the city (or menu) is idle - not loading, no import running - and stays so for `secs` seconds. Calling
    main_menu / imports while a city is still settling after a load hung the game once (2026-09-24)."""
    t0 = time.time(); calm_since = None
    while time.time() - t0 < timeout_s:
        st = _status(rpc)
        ok = st is not None and not st.get("loading") and not st.get("import_running")
        calm_since = (calm_since or time.time()) if ok else None
        if calm_since and time.time() - calm_since >= secs: return True
        time.sleep(2)
    return False


def main_menu(rpc, timeout_s=240):
    """Settle, then go to the main menu and wait for it."""
    settle(rpc)
    rpc("main_menu", {}, timeout=30); time.sleep(5)
    ok, st, secs = wait_for(rpc, lambda s: s.get("game_mode") == "MainMenu" and not s.get("loading"), timeout_s)
    settle(rpc, 5)
    return {"main_menu": ok, "seconds": secs}


def load(rpc, name, wait=True, timeout_s=600):
    # the game hung twice (2026-09-24) when a load/menu request arrived right after a state change (fresh boot at the
    # menu reports game_mode "Other" while it is still initialising): wait for MainMenu, or 30 s of calm, first
    wait_for(rpc, lambda s: s.get("game_mode") == "MainMenu", 45)
    settle(rpc, 30 if (_status(rpc) or {}).get("game_mode") != "MainMenu" else 10)
    r = rpc("load_save", {"name": name}, timeout=30)
    if not wait: return r
    time.sleep(5)
    ok, st, secs = wait_for(rpc, lambda s: _mode(s)[1] and not _mode(s)[2], timeout_s)
    return {"request": r, "city_loaded": ok, "seconds": secs}


def restart(rpc, save=None, launch="steam", quit_wait_s=45, boot_timeout_s=420, load_timeout_s=600):
    """quit_game -> wait quit_wait_s for the process to end -> taskkill /F if it hangs (known exit hang in the game's
    platform shutdown) -> launch -> wait for the bridge at the main menu -> optionally load `save`."""
    log = []
    if _running():
        try: rpc("quit_game", {}, timeout=10); log.append("quit requested")
        except Exception as e: log.append(f"quit rpc failed: {e}")
        t0 = time.time()
        while _running() and time.time() - t0 < quit_wait_s: time.sleep(2)
        if _running():
            subprocess.run(["taskkill", "/IM", EXE, "/F"], capture_output=True); log.append("hung on exit -> killed")
            time.sleep(4)
        else:
            log.append(f"exited cleanly in {round(time.time() - t0)} s")
    # NOTE: launching Cities2.exe directly does NOT work (tested 2026-09-24): without the launcher's session token the
    # game spawns the Paradox Launcher itself and then hangs in a GameManager.OnGUI NullReferenceException loop.
    if launch == "steam":
        os.startfile(STEAM_URI); log.append("launched via Steam (the Paradox Launcher may need a Play click)")
    else:
        raise ValueError("launch must be 'steam'")
    ok, st, secs = wait_for(rpc, lambda s: _mode(s)[0] not in (None, "") and not _mode(s)[2] and not _mode(s)[1], boot_timeout_s, 5)
    log.append(f"main menu {'reached' if ok else 'NOT reached'} after {secs} s")
    res = {"log": log, "main_menu": ok, "status": st}
    if ok and save:
        res["load"] = load(rpc, save, True, load_timeout_s)
    return res
