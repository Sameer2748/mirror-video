"""Live events: stored in the events table and pushed to every open WebSocket (thread-safe)."""
import asyncio, json, threading, time


class Bus:
    def __init__(self):
        self.subs = set()           # (loop, asyncio.Queue)
        self.lock = threading.Lock()
        self.db = None

    def subscribe(self, loop):
        q = asyncio.Queue(maxsize=1000)
        with self.lock:
            self.subs.add((loop, q))
        return q

    def unsubscribe(self, loop, q):
        with self.lock:
            self.subs.discard((loop, q))

    def publish(self, kind, episode_id=None, job_id=None, store=True, **data):
        ev = {"ts": time.time(), "kind": kind, "episode_id": episode_id, "job_id": job_id, "data": data}
        if store and self.db is not None:
            ev["id"] = self.db.insert("events", ts=ev["ts"], episode_id=episode_id, job_id=job_id, kind=kind, data=data)
        with self.lock:
            subs = list(self.subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(self._put, q, ev)
            except RuntimeError:
                pass
        return ev

    @staticmethod
    def _put(q, ev):
        if q.full():
            try:
                q.get_nowait()
            except Exception:
                pass
        q.put_nowait(ev)


bus = Bus()
