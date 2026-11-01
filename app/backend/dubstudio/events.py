"""Live events: stored in the events table and pushed to every open WebSocket (thread-safe).

With `REDIS_URL` set (the deployed platform, docs/09 §1) every event goes through a Redis channel instead, so the
workers and all API replicas see the same stream and a browser connected to any replica gets the progress of a step
running anywhere. Without Redis — one machine, which is how it runs today — the events stay in this process.
"""
import asyncio, json, threading, time

CHANNEL = "dubstudio:events"


class Bus:
    def __init__(self):
        self.subs = set()           # (loop, asyncio.Queue)
        self.lock = threading.Lock()
        self.db = None
        self.redis = None
        self.reader = None

    def subscribe(self, loop):
        q = asyncio.Queue(maxsize=1000)
        with self.lock:
            self.subs.add((loop, q))
        return q

    def unsubscribe(self, loop, q):
        with self.lock:
            self.subs.discard((loop, q))

    # ---- Redis (only when more than one process has to hear the events) ----
    def connect_redis(self, url, log=print):
        if not url:
            return False
        try:
            import redis
            self.redis = redis.Redis.from_url(url, decode_responses=True)
            self.redis.ping()
        except Exception as ex:
            self.redis = None
            log(f"events: Redis at {url.split('@')[-1]} is not answering ({str(ex)[:80]}); events stay in this process")
            return False
        self.reader = threading.Thread(target=self._listen, name="events-redis", daemon=True)
        self.reader.start()
        return True

    def _listen(self):
        pubsub = self.redis.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(CHANNEL)
        for msg in pubsub.listen():
            try:
                self._fanout(json.loads(msg["data"]))
            except Exception:
                pass

    def publish(self, kind, episode_id=None, job_id=None, store=True, **data):
        ev = {"ts": time.time(), "kind": kind, "episode_id": episode_id, "job_id": job_id, "data": data}
        if store and self.db is not None:
            ev["id"] = self.db.insert("events", ts=ev["ts"], episode_id=episode_id, job_id=job_id, kind=kind, data=data)
        if self.redis is not None:
            try:
                self.redis.publish(CHANNEL, json.dumps(ev, default=str))   # comes back through _listen, once
                return ev
            except Exception:
                pass                                                        # Redis went away: keep working locally
        self._fanout(ev)
        return ev

    def _fanout(self, ev):
        with self.lock:
            subs = list(self.subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(self._put, q, ev)
            except RuntimeError:
                pass

    @staticmethod
    def _put(q, ev):
        if q.full():
            try:
                q.get_nowait()
            except Exception:
                pass
        q.put_nowait(ev)


bus = Bus()
