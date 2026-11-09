"""Main loop: one engine tick every OPS['tick'] seconds, HTTP server alongside, watchdog exits on a stall so
Railway restarts the service."""
import os
import signal
import threading
import time
import traceback

from . import config as C
from .engine import Engine, plog
from .server import serve
from .store import Store


def run():
    st = Store()
    state = {"store": st, "last_tick": None, "engine": None}
    serve(state)
    st.event("start", f"v{C.VERSION} {C.PREREG} probe={C.PROBE} key={'yes' if C.PINNAPI_KEY else 'no'} "
                      f"volume={'yes' if C.volume_ok() else 'NO'} window={C.WINDOW_START or 'not set'}")
    eng = Engine(st)
    state["engine"] = eng

    def stop(*_):
        st.event("stop", "SIGTERM: shutting down")
        os._exit(0)
    signal.signal(signal.SIGTERM, stop)

    def watchdog():
        while True:
            time.sleep(60)
            lt = state.get("last_tick")
            if lt and time.time() - lt > 900:
                st.event("watchdog", "no tick for 15 minutes: exiting so Railway restarts us")
                os._exit(3)
    threading.Thread(target=watchdog, daemon=True).start()

    last_summary = 0.0
    while True:
        t0 = time.time()
        try:
            eng.tick()
        except Exception as e:
            st.event("error", f"tick: {e}\n{traceback.format_exc()[-1200:]}", dedupe=300)
        state["last_tick"] = time.time()
        if time.time() - last_summary >= 3600:
            try:
                plog("SUMMARY", eng.summary(), limit=6000)
            except Exception as e:
                st.event("error", f"summary: {e}", dedupe=3600)
            last_summary = time.time()
        time.sleep(max(1.0, C.OPS["tick"] - (time.time() - t0)))
