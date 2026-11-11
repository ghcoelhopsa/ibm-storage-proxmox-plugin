"""Local state of the panel: config, credentials, performance history."""
import json
import os
import shutil
import sqlite3
import threading
import time

CONF_DIR = os.environ.get("SWPANEL_CONF", "/etc/swpanel")
DATA_DIR = os.environ.get("SWPANEL_DATA", "/var/lib/swpanel")
KEY_DIR = os.path.join(DATA_DIR, "keys")
ARRAY_KNOWN_HOSTS = os.path.join(DATA_DIR, "array_known_hosts")
NODE_KEY = os.path.join(DATA_DIR, "keys", "nodes_id_ed25519")
NODE_KNOWN_HOSTS = os.path.join(DATA_DIR, "node_known_hosts")
DB = os.path.join(DATA_DIR, "perf.db")

os.makedirs(KEY_DIR, mode=0o700, exist_ok=True)
_lock = threading.Lock()


SETUP_CODE_FILE = os.path.join(DATA_DIR, "setup-code")


def config():
    """Proxmox connection: the one saved by the setup screen takes priority
    over the installer's. None = panel not configured yet."""
    for d in (DATA_DIR, CONF_DIR):
        p = os.path.join(d, "config.json")
        if os.path.exists(p):
            try:
                c = json.load(open(p))
                if c.get("pve_host") and c.get("pve_fingerprint"):
                    return c
            except (OSError, ValueError):
                pass
    return None


def save_config(cfg):
    p = os.path.join(DATA_DIR, "config.json")
    with open(p + ".tmp", "w") as f:
        json.dump(cfg, f)
    os.chmod(p + ".tmp", 0o600)
    os.replace(p + ".tmp", p)
    if os.path.exists(SETUP_CODE_FILE):
        os.unlink(SETUP_CODE_FILE)


def setup_code():
    try:
        return open(SETUP_CODE_FILE).read().strip() or None
    except OSError:
        return None


def array_key(storeid):
    return os.path.join(KEY_DIR, f"{storeid}_id_rsa")


def has_array_key(storeid):
    return os.path.exists(array_key(storeid))


def install_array_key(storeid, tmp_keyfile):
    dst = array_key(storeid)
    shutil.move(tmp_keyfile, dst)
    if os.path.exists(tmp_keyfile + ".pub"):
        shutil.move(tmp_keyfile + ".pub", dst + ".pub")
    os.chmod(dst, 0o600)


def remove_array_key(storeid):
    for f in (array_key(storeid), array_key(storeid) + ".pub"):
        if os.path.exists(f):
            os.unlink(f)


def add_known_hosts(address, lines):
    with _lock:
        keep = []
        if os.path.exists(ARRAY_KNOWN_HOSTS):
            keep = [l for l in open(ARRAY_KNOWN_HOSTS).read().splitlines()
                    if l and l.split()[0] != address]
        with open(ARRAY_KNOWN_HOSTS, "w") as f:
            f.write("\n".join(keep + lines) + "\n")
        os.chmod(ARRAY_KNOWN_HOSTS, 0o600)


def db():
    c = sqlite3.connect(DB, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS stats (ts INTEGER, storeid TEXT, name TEXT, value REAL)")
    c.execute("CREATE INDEX IF NOT EXISTS stats_i ON stats (storeid, name, ts)")
    return c


def save_stats(storeid, values, ts=None):
    ts = int(ts or time.time())
    with _lock:
        c = db()
        c.executemany("INSERT INTO stats VALUES (?,?,?,?)", [(ts, storeid, k, v) for k, v in values.items()])
        c.execute("DELETE FROM stats WHERE ts < ?", (ts - 7 * 86400,))
        c.commit()
        c.close()


def load_stats(storeid, names, since):
    c = db()
    q = f"SELECT ts, name, value FROM stats WHERE storeid=? AND ts>=? AND name IN ({','.join('?' * len(names))}) ORDER BY ts"
    rows = c.execute(q, [storeid, since, *names]).fetchall()
    c.close()
    out = {n: [] for n in names}
    for ts, n, v in rows:
        out[n].append([ts, v])
    return out


# --- counters (FC) with history, to work out how much they rose ---------

def save_counters(prefix, values, ts=None):
    ts = int(ts or time.time())
    with _lock:
        c = db()
        c.execute("CREATE TABLE IF NOT EXISTS counters (ts INTEGER, key TEXT, value INTEGER)")
        c.execute("CREATE INDEX IF NOT EXISTS counters_i ON counters (key, ts)")
        last = {}
        for k in values:
            r = c.execute("SELECT ts FROM counters WHERE key=? ORDER BY ts DESC LIMIT 1", (f"{prefix}|{k}",)).fetchone()
            last[k] = r[0] if r else 0
        # at most one sample every 5 min per counter
        c.executemany("INSERT INTO counters VALUES (?,?,?)",
                      [(ts, f"{prefix}|{k}", int(v)) for k, v in values.items() if ts - last[k] >= 300])
        c.execute("DELETE FROM counters WHERE ts < ?", (ts - 8 * 86400,))
        c.commit()
        c.close()


def counter_deltas(prefix, values, hours=24):
    """How much each counter rose since the oldest sample inside the window.
    If the counter was reset (reboot/reset), the current value counts."""
    since = int(time.time()) - hours * 3600
    c = db()
    c.execute("CREATE TABLE IF NOT EXISTS counters (ts INTEGER, key TEXT, value INTEGER)")
    out = {}
    for k, v in values.items():
        r = c.execute("SELECT ts, value FROM counters WHERE key=? AND ts>=? ORDER BY ts LIMIT 1",
                      (f"{prefix}|{k}", since)).fetchone()
        if r:
            out[k] = {"delta": int(v) - r[1] if int(v) >= r[1] else int(v), "since": r[0]}
    c.close()
    return out
