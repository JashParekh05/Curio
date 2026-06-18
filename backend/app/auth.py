import os
import logging
import httpx
import jwt
from jwt import PyJWKClient
from fastapi import Depends, HTTPException, Header
from typing import Annotated

logger = logging.getLogger(__name__)

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        supabase_url = os.environ["SUPABASE_URL"].rstrip("/")
        _jwks_client = PyJWKClient(f"{supabase_url}/auth/v1/.well-known/jwks.json")
    return _jwks_client


def require_user(authorization: Annotated[str | None, Header()] = None) -> str:
    """FastAPI dependency — validates Supabase JWT and returns the caller's user_id."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        # Only asymmetric algorithms — we verify against Supabase's JWKS public
        # keys. Allowing HS256 here would enable an algorithm-confusion attack
        # (forge an HS256 token using the public key bytes as the HMAC secret).
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256", "RS256"],
            options={"verify_aud": True},
            audience="authenticated",
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError as e:
        logger.warning(f"[auth] Invalid JWT: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        logger.error(f"[auth] JWKS fetch/decode failed: {e}")
        raise HTTPException(status_code=401, detail="Could not verify token")

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing sub claim")
    return user_id


def _operator_ids() -> set[str]:
    """Parse the comma-separated OPERATOR_USER_IDS env into a set of user ids,
    discarding blank entries."""
    return {s.strip() for s in os.getenv("OPERATOR_USER_IDS", "").split(",") if s.strip()}


def is_operator(user_id: str) -> bool:
    """True when user_id is in the OPERATOR_USER_IDS env allowlist."""
    return user_id in _operator_ids()


def require_operator(caller_id: str = Depends(require_user)) -> str:
    """FastAPI dependency — requires the caller to hold the Operator role.

    Builds on require_user; raises HTTP 403 when the caller is not an operator,
    otherwise returns the caller's user_id."""
    if not is_operator(caller_id):
        raise HTTPException(status_code=403, detail="Operator role required")
    return caller_id
