import base64
import json
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAX_DATAGRAM = 1200


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def cipher(key, kind):
    material = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"quant-link-lab-v1/" + kind.encode(),
    ).derive(key.material)
    return ChaCha20Poly1305(material)


def seal(key, kind, body):
    header = {
        "v": 1,
        "kind": kind,
        "key_id": key.key_id,
        "session": key.session,
        "path": key.path,
    }
    nonce = secrets.token_bytes(12)
    encrypted = cipher(key, kind).encrypt(nonce, encode(body), encode(header))
    packet = encode(
        {
            "header": header,
            "nonce": base64.b64encode(nonce).decode(),
            "body": base64.b64encode(encrypted).decode(),
        }
    )
    if len(packet) > MAX_DATAGRAM:
        raise ValueError("datagram exceeds 1200-byte wire budget")
    return packet


def inspect(packet, kind):
    if len(packet) > MAX_DATAGRAM:
        raise ValueError("oversized datagram")
    envelope = json.loads(packet)
    header = envelope["header"]
    if set(header) != {"v", "kind", "key_id", "session", "path"}:
        raise ValueError("invalid header")
    if header["v"] != 1 or header["kind"] != kind or header["path"] not in {"A", "B"}:
        raise ValueError("invalid protocol or path")
    for name in ("session", "key_id"):
        if not isinstance(header[name], str) or not 1 <= len(header[name]) <= 128:
            raise ValueError("invalid identifier")
    return envelope


def unseal(key, envelope, kind):
    header = envelope["header"]
    if (header["key_id"], header["session"], header["path"], header["kind"]) != (
        key.key_id,
        key.session,
        key.path,
        kind,
    ):
        raise ValueError("key scope mismatch")
    plaintext = cipher(key, kind).decrypt(
        base64.b64decode(envelope["nonce"], validate=True),
        base64.b64decode(envelope["body"], validate=True),
        encode(header),
    )
    return json.loads(plaintext)
