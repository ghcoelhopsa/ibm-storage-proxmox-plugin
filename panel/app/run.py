"""Starts the panel: HTTPS on 443 with the active certificate (tlsconf) and a
minimal server on port 80 that only answers the Let's Encrypt challenge and
redirects everything else to HTTPS."""
import http.server
import os
import threading

import uvicorn

import tlsconf


class Port80(http.server.BaseHTTPRequestHandler):
    server_version = "swpanel"
    sys_version = ""

    def do_GET(self):
        pre = "/.well-known/acme-challenge/"
        if self.path.startswith(pre):
            tok = self.path[len(pre):]
            f = os.path.join(tlsconf.WEBROOT, ".well-known", "acme-challenge", tok)
            if tok and all(c.isalnum() or c in "-_" for c in tok) and os.path.isfile(f):
                data = open(f, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_error(404)
            return
        host = (self.headers.get("Host") or "").split(":")[0]
        if not host or not all(c.isalnum() or c in ".-" for c in host):
            self.send_error(400)
            return
        self.send_response(301)
        self.send_header("Location", f"https://{host}{self.path}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_HEAD = do_GET

    def log_message(self, *a):
        pass


def port80():
    try:
        srv = http.server.ThreadingHTTPServer(("0.0.0.0", 80), Port80)
    except OSError as e:
        print(f"port 80 unavailable ({e}); no redirect and no Let's Encrypt HTTP challenge", flush=True)
        return
    srv.daemon_threads = True
    srv.serve_forever()


if __name__ == "__main__":
    threading.Thread(target=port80, daemon=True).start()
    crt, key = tlsconf.active_files()
    print(f"certificate in use: {crt}", flush=True)
    # keep-alive longer than the browser's: the browser is always the one that closes an idle
    # connection, so it never tries to reuse a connection the server has just closed
    uvicorn.run("main:app", host="0.0.0.0", port=443, ssl_keyfile=key, ssl_certfile=crt,
                proxy_headers=True, server_header=False, timeout_keep_alive=620)
