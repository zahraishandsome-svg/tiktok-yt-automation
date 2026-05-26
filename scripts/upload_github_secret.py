#!/usr/bin/env python3
"""
Upload a secret to a GitHub repo using NaCl SealedBox encryption.
No external dependencies beyond pycryptodome + cryptography (already in requirements).

Usage:
    python scripts/upload_github_secret.py \
        --token ghp_xxx \
        --repo owner/repo-name \
        --name SECRET_NAME \
        --value "secret value"

    # OR pass value from a file:
    python scripts/upload_github_secret.py \
        --token ghp_xxx \
        --repo owner/repo-name \
        --name TIKTOK_COOKIES \
        --file /path/to/cookies.txt \
        --base64
"""
import argparse
import base64
import hashlib
import json
import struct
import urllib.request
import urllib.error
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from Crypto.Cipher import Salsa20


# ── Poly1305 (pure Python) ────────────────────────────────────────────────────

def _poly1305(key: bytes, message: bytes) -> bytes:
    """Raw Poly1305 MAC. 32-byte key → 16-byte tag."""
    r = int.from_bytes(key[:16], "little") & 0x0ffffffc0ffffffc0ffffffc0fffffff
    s = int.from_bytes(key[16:], "little")
    p = (1 << 130) - 5
    h = 0
    for i in range(0, len(message), 16):
        block = message[i : i + 16]
        n = int.from_bytes(block, "little") | (1 << (8 * len(block)))
        h = (h + n) * r % p
    return ((h + s) & ((1 << 128) - 1)).to_bytes(16, "little")


# ── HSalsa20 (pure Python) ────────────────────────────────────────────────────

def _hsalsa20(key: bytes, nonce: bytes) -> bytes:
    """HSalsa20 key derivation: 32-byte key + 16-byte nonce → 32-byte subkey."""
    def rotl(v, n):
        return ((v << n) | (v >> (32 - n))) & 0xFFFFFFFF

    def qr(x, a, b, c, d):
        x[b] ^= rotl((x[a] + x[d]) & 0xFFFFFFFF, 7)
        x[c] ^= rotl((x[b] + x[a]) & 0xFFFFFFFF, 9)
        x[d] ^= rotl((x[c] + x[b]) & 0xFFFFFFFF, 13)
        x[a] ^= rotl((x[d] + x[c]) & 0xFFFFFFFF, 18)

    sigma = b"expand 32-byte k"
    s = struct.unpack("<4I", sigma)
    k = struct.unpack("<8I", key)
    n = struct.unpack("<4I", nonce)
    x = [s[0], k[0], k[1], k[2], k[3], s[1], n[0], n[1],
         n[2], n[3], s[2], k[4], k[5], k[6], k[7], s[3]]
    for _ in range(10):
        qr(x, 0, 4, 8, 12); qr(x, 5, 9, 13, 1)
        qr(x, 10, 14, 2, 6); qr(x, 15, 3, 7, 11)
        qr(x, 0, 1, 2, 3);   qr(x, 5, 6, 7, 4)
        qr(x, 10, 11, 8, 9); qr(x, 15, 12, 13, 14)
    return struct.pack("<8I", x[0], x[5], x[10], x[15], x[6], x[7], x[8], x[9])


# ── XSalsa20-Poly1305 (NaCl secretbox) ───────────────────────────────────────

def _secretbox(key: bytes, nonce24: bytes, message: bytes) -> bytes:
    """NaCl crypto_secretbox_xsalsa20poly1305. Returns tag || ciphertext."""
    sub_key = _hsalsa20(key, nonce24[:16])
    cipher  = Salsa20.new(key=sub_key, nonce=nonce24[16:24])
    otk     = cipher.encrypt(b"\x00" * 32)      # Poly1305 one-time key
    ct      = cipher.encrypt(message)
    tag     = _poly1305(otk, ct)
    return tag + ct


# ── NaCl SealedBox ────────────────────────────────────────────────────────────

def nacl_seal(recipient_pk_bytes: bytes, message: bytes) -> bytes:
    """crypto_box_seal — compatible with libsodium."""
    eph_priv   = X25519PrivateKey.generate()
    eph_pub_b  = eph_priv.public_key().public_bytes_raw()
    shared     = eph_priv.exchange(X25519PublicKey.from_public_bytes(recipient_pk_bytes))
    box_key    = _hsalsa20(shared, b"\x00" * 16)
    nonce24    = hashlib.blake2b(eph_pub_b + recipient_pk_bytes, digest_size=24).digest()
    return eph_pub_b + _secretbox(box_key, nonce24, message)


# ── GitHub API ────────────────────────────────────────────────────────────────

def upload_secret(token: str, repo: str, name: str, value: str) -> None:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
        "User-Agent": "tiktok-yt-automation/1.0",
    }

    # Get repo public key
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
        headers=headers,
    )
    with urllib.request.urlopen(req) as resp:
        pk_data = json.loads(resp.read())

    encrypted_b64 = base64.b64encode(
        nacl_seal(base64.b64decode(pk_data["key"]), value.encode())
    ).decode()

    body = json.dumps({"encrypted_value": encrypted_b64, "key_id": pk_data["key_id"]}).encode()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/secrets/{name}",
        data=body, headers=headers, method="PUT",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"  OK {repo} -> {name} (HTTP {resp.status})")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"  FAIL {repo} -> {name}: HTTP {e.code}: {body}")
        sys.exit(1)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Upload a secret to a GitHub repo.")
    p.add_argument("--token",  required=True, help="GitHub personal access token")
    p.add_argument("--repo",   required=True, help="owner/repo (can repeat)", action="append", dest="repos")
    p.add_argument("--name",   required=True, help="Secret name")
    p.add_argument("--value",  help="Secret value as string")
    p.add_argument("--file",   help="Read secret value from file")
    p.add_argument("--base64", action="store_true", help="Base64-encode file contents before uploading")
    args = p.parse_args()

    if args.file:
        raw = Path(args.file).read_bytes()
        value = base64.b64encode(raw).decode() if args.base64 else raw.decode()
    elif args.value:
        value = args.value
    else:
        p.error("Provide either --value or --file")

    for repo in args.repos:
        upload_secret(args.token, repo, args.name, value)


if __name__ == "__main__":
    main()
