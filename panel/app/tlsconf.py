"""Domain and HTTPS certificate of the panel: self-signed (installer), your own
certificate (uploaded in the UI) or Let's Encrypt (certbot, validated over HTTP on
port 80 or over DNS at Cloudflare), with automatic renewal.

The service runs without root: everything lives in /var/lib/swpanel/tls and /var/lib/swpanel/acme.
The certificate only comes into use when the panel restarts (run.py picks the file).
"""
import datetime
import hashlib
import ipaddress
import json
import os
import re
import shutil
import ssl
import subprocess
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

import store

TLS_DIR = os.path.join(store.DATA_DIR, "tls")
ACME_DIR = os.path.join(store.DATA_DIR, "acme")
WEBROOT = os.path.join(ACME_DIR, "webroot")
ACTIVE_CRT, ACTIVE_KEY = os.path.join(TLS_DIR, "active.crt"), os.path.join(TLS_DIR, "active.key")
SETTINGS = os.path.join(TLS_DIR, "settings.json")
CF_INI = os.path.join(TLS_DIR, "cloudflare.ini")
DEFAULT_CRT, DEFAULT_KEY = os.path.join(store.CONF_DIR, "tls.crt"), os.path.join(store.CONF_DIR, "tls.key")
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,63}$")
CF_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{30,100}$")
RENEW_DAYS = 30


class TLSError(Exception):
    pass


def _mkdirs():
    for d in (TLS_DIR, ACME_DIR, WEBROOT):
        os.makedirs(d, mode=0o700, exist_ok=True)


def _write(path, data, mode=0o600):
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "w") as f:
        f.write(data)
    os.replace(tmp, path)


def settings():
    try:
        return json.load(open(SETTINGS))
    except (OSError, ValueError):
        return {"mode": "selfsigned"}


def save_settings(s):
    _mkdirs()
    _write(SETTINGS, json.dumps(s))


def active_files():
    """Certificate/key pair the panel should use; falls back to the self-signed one if the other does not load."""
    if os.path.exists(ACTIVE_CRT) and os.path.exists(ACTIVE_KEY):
        try:
            ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(ACTIVE_CRT, ACTIVE_KEY)
            return ACTIVE_CRT, ACTIVE_KEY
        except (ssl.SSLError, OSError):
            pass
    return DEFAULT_CRT, DEFAULT_KEY


def _names(cert):
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        return [str(n.value) for n in san]
    except x509.ExtensionNotFound:
        return []


def _cn(name):
    a = name.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    return a[0].value if a else name.rfc4514_string()


def _not_after(cert):
    return cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)


def describe(pem):
    cert = x509.load_pem_x509_certificates(pem.encode() if isinstance(pem, str) else pem)[0]
    na = _not_after(cert)
    fp = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest().upper()
    return {"subject": _cn(cert.subject), "issuer": _cn(cert.issuer), "names": _names(cert),
            "not_after": int(na.timestamp()), "days_left": int((na.timestamp() - time.time()) // 86400),
            "self_signed": cert.subject == cert.issuer,
            "fingerprint": ":".join(fp[i:i + 2] for i in range(0, 64, 2))}


def matches(domain, names):
    d = domain.lower()
    for n in names:
        n = n.lower()
        if n == d or (n.startswith("*.") and "." in d and d.split(".", 1)[1] == n[2:]):
            return True
    return False


def status():
    s = settings()
    crt, _ = active_files()
    out = {"mode": s.get("mode", "selfsigned") if crt == ACTIVE_CRT else "selfsigned", "domain": s.get("domain", ""),
           "method": s.get("method", ""), "staging": bool(s.get("staging")), "email_set": bool(s.get("email")),
           "last_renew": s.get("last_renew"), "last_error": s.get("last_error", ""),
           "certbot": bool(shutil.which("certbot")), "renew_days": RENEW_DAYS}
    try:
        out["cert"] = describe(open(crt).read())
    except Exception as e:
        out["cert"] = None
        out["last_error"] = out["last_error"] or str(e)
    return out


def validate_pair(cert_pem, key_pem, domain=""):
    """Checks chain and key before installing. Returns the certificate description."""
    try:
        certs = x509.load_pem_x509_certificates(cert_pem.encode())
    except ValueError:
        raise TLSError("the certificate is not in PEM format")
    if not certs:
        raise TLSError("no certificate found")
    try:
        key = serialization.load_pem_private_key(key_pem.encode(), password=None)
    except TypeError:
        raise TLSError("the private key is protected by a passphrase; send the key without a passphrase")
    except ValueError:
        raise TLSError("the private key is not in PEM format")
    pub = lambda k: k.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if pub(key.public_key()) != pub(certs[0].public_key()):
        raise TLSError("the private key does not match the certificate (the first block must be the server certificate)")
    d = describe(cert_pem)
    if d["days_left"] < 0:
        raise TLSError("the certificate has expired")
    if domain and not matches(domain, d["names"] or [d["subject"]]):
        raise TLSError(f"the certificate is not valid for {domain} (names: {', '.join(d['names']) or d['subject']})")
    return d


def install(cert_pem, key_pem):
    _mkdirs()
    _write(ACTIVE_KEY, key_pem.strip() + "\n")
    _write(ACTIVE_CRT, cert_pem.strip() + "\n", 0o644)
    try:
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(ACTIVE_CRT, ACTIVE_KEY)
    except (ssl.SSLError, OSError) as e:
        for f in (ACTIVE_CRT, ACTIVE_KEY):
            os.remove(f)
        raise TLSError(f"the server could not load the certificate: {e}")


def install_custom(domain, cert_pem, key_pem):
    d = validate_pair(cert_pem, key_pem, domain)
    install(cert_pem, key_pem)
    save_settings({"mode": "custom", "domain": domain})
    return d


def local_ips():
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5).stdout.split()
    except Exception:
        out = []
    ips = []
    for x in out:
        try:
            ips.append(ipaddress.ip_address(x))
        except ValueError:
            pass
    return ips


def issue_selfsigned(domain, years=10):
    """Generates a self-signed certificate for the domain (and the panel's current IPs) and installs it."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, domain)])
    now = datetime.datetime.now(datetime.timezone.utc)
    san = [x509.DNSName(domain)] + [x509.IPAddress(i) for i in local_ips()]
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=365 * int(years)))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    install(cert_pem, key_pem)
    save_settings({"mode": "generated", "domain": domain})
    return describe(cert_pem)


def reset():
    for f in (ACTIVE_CRT, ACTIVE_KEY, CF_INI):
        try:
            os.remove(f)
        except OSError:
            pass
    save_settings({"mode": "selfsigned"})


# --- Let's Encrypt (certbot without root, everything inside ACME_DIR) --------
def _certbot(args, timeout=240):
    if not shutil.which("certbot"):
        raise TLSError("certbot is not installed in this container (run the panel deploy again)")
    base = ["certbot", "--non-interactive", "--config-dir", os.path.join(ACME_DIR, "config"),
            "--work-dir", os.path.join(ACME_DIR, "work"), "--logs-dir", os.path.join(ACME_DIR, "logs")]
    try:
        p = subprocess.run(base + args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise TLSError("certbot did not respond in time")
    if p.returncode != 0:
        out = (p.stderr or p.stdout).strip()
        lines = [l.strip() for l in out.splitlines() if l.strip() and not l.startswith(("Saving debug log", "Ask for help", "See the logfile"))]
        raise TLSError("Let's Encrypt refused: " + " ".join(lines[-6:])[:900])
    return p.stdout


def _live(domain):
    d = os.path.join(ACME_DIR, "config", "live", domain)
    return os.path.join(d, "fullchain.pem"), os.path.join(d, "privkey.pem")


def issue_letsencrypt(domain, email="", method="http", cf_token="", staging=False):
    _mkdirs()
    args = ["certonly", "--agree-tos", "--cert-name", domain, "-d", domain, "--keep-until-expiring"]
    args += ["-m", email] if email else ["--register-unsafely-without-email"]
    if staging:
        args.append("--staging")
    if method == "cloudflare":
        if cf_token:
            _write(CF_INI, f"dns_cloudflare_api_token = {cf_token}\n")
        if not os.path.exists(CF_INI):
            raise TLSError("enter the Cloudflare API token")
        args += ["--dns-cloudflare", "--dns-cloudflare-credentials", CF_INI, "--dns-cloudflare-propagation-seconds", "30"]
    else:
        args += ["--webroot", "-w", WEBROOT]
    old = settings()
    if old.get("mode") == "letsencrypt" and (old.get("domain") != domain or bool(old.get("staging")) != bool(staging)):
        args.append("--force-renewal")            # environment or name changed: do not reuse the previous one
    _certbot(args)
    crt, key = _live(domain)
    install(open(crt).read(), open(key).read())
    save_settings({"mode": "letsencrypt", "domain": domain, "email": email, "method": method, "staging": bool(staging),
                   "last_renew": int(time.time()), "last_error": ""})
    return describe(open(ACTIVE_CRT).read())


def renew_if_due():
    """Called once a day. True = new certificate installed (the panel must restart)."""
    s = settings()
    if s.get("mode") != "letsencrypt" or not s.get("domain"):
        return False
    try:
        if describe(open(ACTIVE_CRT).read())["days_left"] > RENEW_DAYS:
            return False
        before = open(ACTIVE_CRT).read()
        _certbot(["renew", "--cert-name", s["domain"]])
        crt, key = _live(s["domain"])
        new = open(crt).read()
        if new.strip() == before.strip():
            return False
        install(new, open(key).read())
        s.update(last_renew=int(time.time()), last_error="")
        save_settings(s)
        return True
    except Exception as e:
        s["last_error"] = f"{time.strftime('%Y-%m-%d %H:%M')} renewal: {e}"[:900]
        save_settings(s)
        return False
