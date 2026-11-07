import hmac
import os
import threading
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

KIS_APP_KEY = os.getenv("KIS_APP_KEY", "").strip()
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "").strip()
KIS_BASE_URL = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443").rstrip("/")
APP_ACCESS_SECRET = os.getenv("APP_ACCESS_SECRET", "").strip()
KIS_TIMEOUT = float(os.getenv("KIS_TIMEOUT", "10"))

app = FastAPI(
    title="Private KIS Market Data API",
    version="0.1.0",
    description="Private, read-only market data gateway for personal investment research."
)
bearer = HTTPBearer(auto_error=False)

_token: Optional[str] = None
_token_expiry: float = 0.0
_token_lock = threading.Lock()


def require_private_access(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    if not APP_ACCESS_SECRET:
        raise HTTPException(status_code=503, detail="Server access secret is not configured.")
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer access token required.")
    if not hmac.compare_digest(credentials.credentials, APP_ACCESS_SECRET):
        raise HTTPException(status_code=403, detail="Access denied.")
    return True


def get_kis_token() -> str:
    global _token, _token_expiry
    if not KIS_APP_KEY or not KIS_APP_SECRET:
        raise HTTPException(status_code=503, detail="KIS credentials are not configured on the server.")

    now = datetime.now(timezone.utc).timestamp()
    if _token and now < _token_expiry:
        return _token

    with _token_lock:
        now = datetime.now(timezone.utc).timestamp()
        if _token and now < _token_expiry:
            return _token

        try:
            response = requests.post(
                f"{KIS_BASE_URL}/oauth2/tokenP",
                headers={"content-type": "application/json; charset=UTF-8"},
                json={
                    "grant_type": "client_credentials",
                    "appkey": KIS_APP_KEY,
                    "appsecret": KIS_APP_SECRET,
                },
                timeout=KIS_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"KIS token request failed: {type(exc).__name__}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="KIS returned invalid token JSON.") from exc

        token = payload.get("access_token")
        if not token:
            raise HTTPException(status_code=502, detail="KIS token response did not contain access_token.")

        expires_in = int(payload.get("expires_in", 86400))
        _token = token
        # Refresh slightly before expiry. KIS may return the prior token inside its reissue interval.
        _token_expiry = datetime.now(timezone.utc).timestamp() + max(300, expires_in - 900)
        return _token


def kis_get(path: str, tr_id: str, params: dict) -> dict:
    token = get_kis_token()
    headers = {
        "content-type": "application/json; charset=UTF-8",
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": tr_id,
        "custtype": "P",
    }
    try:
        response = requests.get(
            f"{KIS_BASE_URL}{path}",
            headers=headers,
            params=params,
            timeout=KIS_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"KIS quote request failed: {type(exc).__name__}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="KIS returned invalid quote JSON.") from exc

    if payload.get("rt_cd") not in (None, "0"):
        # Do not return provider internals or credentials to the client.
        raise HTTPException(
            status_code=502,
            detail={
                "message": "KIS rejected the quote request.",
                "provider_code": payload.get("msg_cd"),
                "provider_message": payload.get("msg1"),
            },
        )
    return payload


@app.get("/health")
def health():
    return {"status": "ok", "service": "private-kis-market-api"}


@app.get("/api/v1/status")
def status(_=Depends(require_private_access)):
    return {
        "service": "private-kis-market-api",
        "credentials_configured": bool(KIS_APP_KEY and KIS_APP_SECRET),
        "mode": "read-only",
        "server_time_utc": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/quote/domestic/{symbol}")
def domestic_quote(
    symbol: str,
    market: str = Query(default="UN", pattern="^(J|NX|UN)$"),
    _=Depends(require_private_access),
):
    if not symbol.isdigit() or len(symbol) != 6:
        raise HTTPException(status_code=422, detail="Domestic symbol must be a six-digit code.")
    payload = kis_get(
        "/uapi/domestic-stock/v1/quotations/inquire-price",
        "FHKST01010100",
        {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": symbol},
    )
    output = payload.get("output") or payload.get("output1") or {}
    return {
        "symbol": symbol,
        "market": market,
        "price": output.get("stck_prpr"),
        "change": output.get("prdy_vrss"),
        "change_rate": output.get("prdy_ctrt"),
        "volume": output.get("acml_vol"),
        "provider": "KIS",
        "provider_message": payload.get("msg1"),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "raw": output,
    }


@app.get("/api/v1/quote/overseas/{exchange}/{symbol}")
def overseas_quote(
    exchange: str,
    symbol: str,
    _=Depends(require_private_access),
):
    exchange = exchange.upper()
    allowed = {"NAS", "NYS", "AMS", "HKS", "TSE", "SHS", "SZS", "HSX", "HNX"}
    if exchange not in allowed:
        raise HTTPException(status_code=422, detail=f"Unsupported exchange. Allowed: {', '.join(sorted(allowed))}")
    if not symbol or len(symbol) > 20:
        raise HTTPException(status_code=422, detail="Invalid overseas symbol.")
    payload = kis_get(
        "/uapi/overseas-price/v1/quotations/price",
        "HHDFS00000300",
        {"AUTH": "", "EXCD": exchange, "SYMB": symbol.upper()},
    )
    output = payload.get("output") or payload.get("output1") or {}
    return {
        "symbol": symbol.upper(),
        "exchange": exchange,
        "price": output.get("last"),
        "change": output.get("diff"),
        "change_rate": output.get("rate"),
        "volume": output.get("tvol"),
        "provider": "KIS",
        "provider_message": payload.get("msg1"),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "raw": output,
    }
