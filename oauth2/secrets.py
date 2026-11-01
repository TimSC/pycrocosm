"""The secrets OAuth2 gives out: access tokens and authorization codes.

Each is a random string of 256 bits, which only the client holds; the server
keeps its SHA-256, to find it by. A fast hash suits a secret this long, which
cannot be guessed, unlike a password, and lets each request find its row by an
index."""

import hashlib
import secrets

def new_secret():
	"""A new secret, and the hash to keep of it."""
	secret = secrets.token_urlsafe(32)
	return secret, hash_secret(secret)

def hash_secret(secret):
	return hashlib.sha256(secret.encode("utf-8")).hexdigest()
