"""Cryptography for signed device licensing.

* Entitlements are signed with the Nexus Ed25519 PRIVATE key (settings.LICENSE_SIGNING_PRIVATE_KEY, environment only).
  `canonical`, `normalize_fp`, `fingerprints_match` and `entitlement_device` are byte-for-byte the PRN agent's
  (agent/license_core.py), so its unchanged verifier accepts what is signed here.
* Hardware hashes are never stored as received: they are wrapped with a server-side HMAC key first.
* A device proves each request with its own Ed25519 key (the private half never leaves the PC).
"""
import base64
import hashlib
import hmac
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from django.conf import settings


class SigningUnavailable(Exception):
    """The server has no (valid) signing key configured."""


# ---------------------------------------------------------------- canonical form / fingerprints (mirror of the agent)
def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _hex64(value):
    value = str(value or "").strip().lower()
    return value if len(value) == 64 and all(c in "0123456789abcdef" for c in value) else ""


def normalize_fp(fp):
    fp = fp if isinstance(fp, dict) else {}
    return {k: _hex64(fp.get(k)) for k in ("smbios", "machine", "v3")}


def fingerprints_match(bound, presented):
    b, p = normalize_fp(bound), normalize_fp(presented)
    if b["v3"]:  # bound under V3: only the V3 digest counts
        return bool(p["v3"]) and hmac.compare_digest(b["v3"], p["v3"])
    if b["smbios"] and p["smbios"]:
        return hmac.compare_digest(b["smbios"], p["smbios"])
    if b["machine"] and p["machine"]:
        return hmac.compare_digest(b["machine"], p["machine"])
    return False


def has_identity(fp):
    return any(normalize_fp(fp).values())


def _protect_key():
    return hashlib.sha256(("prn-device-evidence|" + (settings.DEVICE_EVIDENCE_KEY or settings.SECRET_KEY)).encode()).digest()


def protect(hex_hash):
    return hmac.new(_protect_key(), hex_hash.encode(), hashlib.sha256).hexdigest() if hex_hash else ""


def protect_fp(fp):
    """What is stored: the three identifying hashes, each wrapped with the server key. Nothing else is kept."""
    return {k: protect(v) for k, v in normalize_fp(fp).items()}


def matches_stored(stored_json, presented):
    """Whether the computer presenting `presented` (agent hashes) is the one whose protected hashes are stored."""
    try:
        stored = json.loads(stored_json)
    except ValueError:
        return False
    return fingerprints_match(stored, protect_fp(presented))


# ---------------------------------------------------------------- entitlement signing
def _private_key():
    raw = settings.LICENSE_SIGNING_PRIVATE_KEY
    if not raw:
        raise SigningUnavailable("Licence signing is not configured on this server.")
    try:
        return Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw, validate=True))
    except Exception:  # never echo the value
        raise SigningUnavailable("The configured licence signing key is invalid.")


def public_key_b64():
    return base64.b64encode(_private_key().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


def sign_entitlement(payload):
    sig = _private_key().sign(canonical(payload))
    return {"payload": payload, "sig": base64.urlsafe_b64encode(sig).decode(), "key_id": settings.LICENSE_KEY_ID}


def generate_keypair():
    """-> (private_b64, public_b64): raw 32-byte keys, standard base64."""
    priv = Ed25519PrivateKey.generate()
    return (
        base64.b64encode(priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())).decode(),
        base64.b64encode(priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode(),
    )


# ---------------------------------------------------------------- device request signatures
def valid_public_key(b64):
    """True for the base64 of a raw 32-byte Ed25519 public key."""
    try:
        raw = base64.b64decode(b64 or "", validate=True)
        Ed25519PublicKey.from_public_bytes(raw)
        return len(raw) == 32
    except Exception:
        return False


def request_message(method, path, timestamp, nonce, body):
    """METHOD \\n PATH \\n TIMESTAMP \\n NONCE \\n lowercase-hex SHA-256(body); PATH has no query string."""
    return "\n".join([method.upper(), path, timestamp, nonce, hashlib.sha256(body or b"").hexdigest()]).encode()


def verify_request_signature(public_b64, signature, message):
    """Accepts standard or URL-safe base64 for the signature."""
    try:
        sig = base64.b64decode(signature + "=" * (-len(signature) % 4), altchars=b"-_") if ("-" in signature or "_" in signature) else base64.b64decode(signature, validate=True)
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64)).verify(sig, message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
