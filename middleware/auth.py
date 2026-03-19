"""
middleware/auth.py — Supabase JWT validation middleware

Validates the Bearer token from the Authorization header using the
Supabase JWT secret (HS256) or JWKS public key (ES256), then returns
the user_id (sub claim).
"""

import os
import httpx
import jwt
from jwt.algorithms import ECAlgorithm
from fastapi import Request, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

security = HTTPBearer(auto_error=False)

SKIP_PATHS = {"/", "/docs", "/openapi.json", "/redoc"}

# Simple in-process JWKS cache: { kid: public_key }
_jwks_cache: dict = {}


def _get_public_key(kid: str):
    """Return the EC public key for the given kid, fetching JWKS if needed."""
    if kid not in _jwks_cache:
        supabase_url = os.environ["SUPABASE_URL"].rstrip("/")
        resp = httpx.get(f"{supabase_url}/auth/v1/.well-known/jwks.json", timeout=5)
        resp.raise_for_status()
        for k in resp.json().get("keys", []):
            _jwks_cache[k["kid"]] = ECAlgorithm.from_jwk(k)
    return _jwks_cache.get(kid)


def _decode_token(token: str) -> dict:
    header = jwt.get_unverified_header(token)
    alg = header.get("alg", "HS256")

    if alg == "HS256":
        secret = os.environ["SUPABASE_JWT_SECRET"]
        return jwt.decode(token, secret, algorithms=["HS256"], audience="authenticated")

    if alg == "ES256":
        kid = header.get("kid", "")
        key = _get_public_key(kid)
        if key is None:
            raise jwt.InvalidTokenError(f"No JWKS key found for kid={kid}")
        return jwt.decode(token, key, algorithms=["ES256"], audience="authenticated")

    raise jwt.InvalidTokenError(f"Unsupported algorithm: {alg}")


async def get_current_user_id(request: Request) -> str:
    """
    Dependency: extract and validate Supabase JWT, return user_id (sub claim).
    Accepts token from Authorization header or ?token= query param
    (the latter is needed for OAuth redirect flows).
    Raises HTTP 401 if missing or invalid.
    """
    credentials: HTTPAuthorizationCredentials | None = await security(request)
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization token",
        )
    token = credentials.credentials

    try:
        payload = _decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}")

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing sub claim")

    return user_id
