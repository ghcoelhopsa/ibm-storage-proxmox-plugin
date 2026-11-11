"""Proxmox VE API client.

Every call is made with the ticket of the logged-in user, so the Proxmox
permissions of that user are what decides what the panel may do.
The TLS certificate of the node is pinned by its SHA-256 fingerprint.
"""
import hashlib
import ssl
import time

import requests
import urllib3
from requests.adapters import HTTPAdapter

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def fetch_fingerprint(host, port=8006):
    pem = ssl.get_server_certificate((host, port), timeout=10)
    der = ssl.PEM_cert_to_DER_cert(pem)
    h = hashlib.sha256(der).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


class _PinnedAdapter(HTTPAdapter):
    def __init__(self, fingerprint, **kw):
        self._fp = fingerprint.replace(":", "").lower()
        super().__init__(**kw)

    def init_poolmanager(self, *a, **kw):
        kw["assert_fingerprint"] = self._fp
        return super().init_poolmanager(*a, **kw)


class PVEError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class PVE:
    def __init__(self, host, port, fingerprint):
        self.base = f"https://{host}:{port}/api2/json"
        self.s = requests.Session()
        self.s.verify = False            # trust comes from the pinned fingerprint
        self.s.mount("https://", _PinnedAdapter(fingerprint))

    # --- auth ---------------------------------------------------------
    def login(self, username, password, realm):
        user = username if "@" in username else f"{username}@{realm}"
        r = self.s.post(f"{self.base}/access/ticket",
                        data={"username": user, "password": password}, timeout=20)
        if r.status_code != 200:
            raise PVEError(401, "invalid user or password")
        d = r.json()["data"]
        return {"user": d["username"], "ticket": d["ticket"],
                "csrf": d["CSRFPreventionToken"], "issued": time.time()}

    def renew(self, sess):
        r = self.s.post(f"{self.base}/access/ticket",
                        data={"username": sess["user"], "password": sess["ticket"]}, timeout=20)
        if r.status_code != 200:
            raise PVEError(401, "session expired")
        d = r.json()["data"]
        sess.update(ticket=d["ticket"], csrf=d["CSRFPreventionToken"], issued=time.time())

    def realms(self):
        r = self.s.get(f"{self.base}/access/domains", timeout=10)
        r.raise_for_status()
        return r.json()["data"]

    # --- generic ------------------------------------------------------
    def call(self, sess, method, endpoint, **params):
        if time.time() - sess["issued"] > 3600:     # tickets live 2h
            self.renew(sess)
        headers = {"Cookie": f"PVEAuthCookie={sess['ticket']}"}
        if method != "GET":
            headers["CSRFPreventionToken"] = sess["csrf"]
        url = f"{self.base}{endpoint}"
        if method in ("GET", "DELETE"):
            r = self.s.request(method, url, params=params, headers=headers, timeout=60)
        else:
            r = self.s.request(method, url, data=params, headers=headers, timeout=60)
        if r.status_code != 200:
            msg = r.reason
            try:
                j = r.json()
                if j.get("errors"):
                    msg = "; ".join(f"{k}: {v}" for k, v in j["errors"].items())
                elif j.get("message"):
                    msg = j["message"]
            except ValueError:
                pass
            raise PVEError(r.status_code, msg.strip())
        return r.json().get("data")

    def get(self, sess, endpoint, **p):
        return self.call(sess, "GET", endpoint, **p)

    def privileges(self, sess, path="/"):
        perms = self.get(sess, "/access/permissions", path=path) or {}
        return set((perms.get(path) or {}).keys())

    def wait_task(self, sess, node, upid, timeout=900):
        end = time.time() + timeout
        while time.time() < end:
            st = self.get(sess, f"/nodes/{node}/tasks/{upid}/status")
            if st.get("status") == "stopped":
                return st.get("exitstatus"), self.task_log(sess, node, upid)
            time.sleep(1)
        return "timeout", []

    def task_log(self, sess, node, upid):
        return [l.get("t", "") for l in (self.get(sess, f"/nodes/{node}/tasks/{upid}/log", limit=500) or [])]
