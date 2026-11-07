import hmac
import os
import threading
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

KIS_APP_KEY = os.getenv('KIS_APP_KEY', '').strip()
KIS_APP_SECRET = os.getenv('KIS_APP_SECRET', '').strip()
KIS_BASE_URL = os.getenv('KIS_BASE_URL', 'https://openapi.koreainvestment.com:9443').rstrip('/')
APP_ACCESS_SECRET = os.getenv('APP_ACCESS_SECRET', '').strip()
KIS_TIMEOUT = float(os.getenv('KIS_TIMEOUT', '10'))

app = FastAPI(title='Private KIS Market Data API', version='0.2.0', description='Read-only private investment dashboard')
bearer = HTTPBearer(auto_error=False)
_token: Optional[str] = None
_token_expiry = 0.0
_token_lock = threading.Lock()


def require_private_access(credentials: HTTPAuthorizationCredentials = Depends(bearer)):
    if not APP_ACCESS_SECRET:
        raise HTTPException(status_code=503, detail='Server access secret is not configured.')
    if credentials is None or credentials.scheme.lower() != 'bearer':
        raise HTTPException(status_code=401, detail='Bearer access token required.')
    if not hmac.compare_digest(credentials.credentials, APP_ACCESS_SECRET):
        raise HTTPException(status_code=403, detail='Access denied.')
    return True


def get_kis_token():
    global _token, _token_expiry
    if not KIS_APP_KEY or not KIS_APP_SECRET:
        raise HTTPException(status_code=503, detail='KIS credentials are not configured on the server.')
    now = datetime.now(timezone.utc).timestamp()
    if _token and now < _token_expiry:
        return _token
    with _token_lock:
        now = datetime.now(timezone.utc).timestamp()
        if _token and now < _token_expiry:
            return _token
        try:
            response = requests.post(
                f'{KIS_BASE_URL}/oauth2/tokenP',
                headers={'content-type': 'application/json; charset=UTF-8'},
                json={'grant_type': 'client_credentials', 'appkey': KIS_APP_KEY, 'appsecret': KIS_APP_SECRET},
                timeout=KIS_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f'KIS token request failed: {type(exc).__name__}') from exc
        except ValueError as exc:
            raise HTTPException(status_code=502, detail='KIS returned invalid token JSON.') from exc
        token = payload.get('access_token')
        if not token:
            raise HTTPException(status_code=502, detail='KIS token response did not contain access_token.')
        expires_in = int(payload.get('expires_in', 86400))
        _token = token
        _token_expiry = datetime.now(timezone.utc).timestamp() + max(300, expires_in - 900)
        return _token


def kis_get(path, tr_id, params):
    token = get_kis_token()
    headers = {
        'content-type': 'application/json; charset=UTF-8',
        'authorization': f'Bearer {token}',
        'appkey': KIS_APP_KEY,
        'appsecret': KIS_APP_SECRET,
        'tr_id': tr_id,
        'custtype': 'P',
    }
    try:
        response = requests.get(f'{KIS_BASE_URL}{path}', headers=headers, params=params, timeout=KIS_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f'KIS quote request failed: {type(exc).__name__}') from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail='KIS returned invalid quote JSON.') from exc
    if payload.get('rt_cd') not in (None, '0'):
        raise HTTPException(status_code=502, detail={
            'message': 'KIS rejected the quote request.',
            'provider_code': payload.get('msg_cd'),
            'provider_message': payload.get('msg1'),
        })
    return payload


@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'private-kis-market-api'}


@app.get('/api/v1/status')
def status(_=Depends(require_private_access)):
    return {
        'service': 'private-kis-market-api',
        'credentials_configured': bool(KIS_APP_KEY and KIS_APP_SECRET),
        'mode': 'read-only',
        'server_time_utc': datetime.now(timezone.utc).isoformat(),
    }


@app.get('/api/v1/quote/domestic/{symbol}')
def domestic_quote(symbol: str, market: str = Query(default='UN', pattern='^(J|NX|UN)$'), _=Depends(require_private_access)):
    if not symbol.isdigit() or len(symbol) != 6:
        raise HTTPException(status_code=422, detail='Domestic symbol must be a six-digit code.')
    payload = kis_get('/uapi/domestic-stock/v1/quotations/inquire-price', 'FHKST01010100', {
        'FID_COND_MRKT_DIV_CODE': market, 'FID_INPUT_ISCD': symbol,
    })
    output = payload.get('output') or payload.get('output1') or {}
    return {
        'symbol': symbol, 'market': market, 'price': output.get('stck_prpr'),
        'change': output.get('prdy_vrss'), 'change_rate': output.get('prdy_ctrt'),
        'volume': output.get('acml_vol'), 'provider': 'KIS',
        'provider_message': payload.get('msg1'),
        'received_at': datetime.now(timezone.utc).isoformat(), 'raw': output,
    }


@app.get('/api/v1/quote/overseas/{exchange}/{symbol}')
def overseas_quote(exchange: str, symbol: str, _=Depends(require_private_access)):
    exchange = exchange.upper()
    allowed = {'NAS', 'NYS', 'AMS', 'HKS', 'TSE', 'SHS', 'SZS', 'HSX', 'HNX'}
    if exchange not in allowed:
        raise HTTPException(status_code=422, detail=f'Unsupported exchange. Allowed: {", ".join(sorted(allowed))}')
    if not symbol or len(symbol) > 20:
        raise HTTPException(status_code=422, detail='Invalid overseas symbol.')
    payload = kis_get('/uapi/overseas-price/v1/quotations/price', 'HHDFS00000300', {
        'AUTH': '', 'EXCD': exchange, 'SYMB': symbol.upper(),
    })
    output = payload.get('output') or payload.get('output1') or {}
    return {
        'symbol': symbol.upper(), 'exchange': exchange, 'price': output.get('last'),
        'change': output.get('diff'), 'change_rate': output.get('rate'),
        'volume': output.get('tvol'), 'provider': 'KIS',
        'provider_message': payload.get('msg1'),
        'received_at': datetime.now(timezone.utc).isoformat(), 'raw': output,
    }


@app.get('/', response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML


DASHBOARD_HTML = r'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#101827"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-status-bar-style" content="black-translucent"><title>마켓 인사이트</title>
<style>
:root{color-scheme:dark;--bg:#0b1220;--panel:#121d2e;--line:#26364c;--muted:#8fa2bb;--text:#edf4ff;--green:#42d6a0;--red:#ff7b86}*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 85% -10%,#1b3558 0,transparent 40%),var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}header{padding:22px 18px 14px;display:flex;justify-content:space-between;align-items:center;max-width:1080px;margin:auto}h1{font-size:21px;margin:0}.sub{color:var(--muted);font-size:12px;margin-top:5px}.pill{font-size:11px;border:1px solid var(--line);border-radius:30px;padding:7px 10px;color:var(--muted)}main{max-width:1080px;margin:auto;padding:8px 16px 48px}.notice{background:#18283e;border:1px solid #2a4668;padding:12px 14px;border-radius:13px;color:#bfd8f8;font-size:12px;line-height:1.55;margin-bottom:16px}.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:13px}.card{background:linear-gradient(145deg,var(--panel),#101a29);border:1px solid var(--line);border-radius:17px;padding:16px;min-width:0}.wide{grid-column:span 8}.side{grid-column:span 4}.half{grid-column:span 6}.full{grid-column:1/-1}h2{font-size:14px;margin:0 0 13px;color:#dce9fb}.muted{color:var(--muted);font-size:13px;line-height:1.7}.small{font-size:11px;color:var(--muted)}.field{display:flex;gap:8px;flex-wrap:wrap}input,select{background:#0b1524;border:1px solid #30435d;color:var(--text);border-radius:10px;padding:12px;font-size:14px;min-width:0;flex:1}button{border:0;border-radius:10px;background:#377fe0;color:white;font-weight:650;padding:12px 15px;font-size:13px}button:disabled,input:disabled{opacity:.45}.stat{font-size:24px;font-weight:700;margin:6px 0}.statusline{font-size:12px;color:var(--muted);margin-top:10px;min-height:18px}.result{margin-top:13px;padding:13px;background:#0c1726;border:1px solid var(--line);border-radius:12px;display:none}.result strong{font-size:22px}.positive{color:var(--green)}.negative{color:var(--red)}.chat{height:125px;background:#0b1524;border:1px solid var(--line);border-radius:12px;padding:12px;font-size:13px;line-height:1.6;color:var(--muted);margin-bottom:10px}.footer{padding:20px 2px;color:#71849d;font-size:11px;line-height:1.6}@media(max-width:700px){.wide,.side,.half{grid-column:1/-1}header{padding-top:18px}.card{padding:14px}}
</style></head><body>
<header><div><h1>마켓 인사이트</h1><div class="sub">개인 투자 리서치 대시보드 · 읽기 전용</div></div><span class="pill" id="connection">연결 확인 전</span></header>
<main><div class="notice">개인용 초기 버전입니다. 국내·해외 주식 시세는 KIS API를 통해 조회합니다. AI 분석·ETF 데이터·휴장일 종가 보정은 아직 연결 전이며, 확인되지 않은 값은 표시하지 않습니다.</div><div class="grid">
<section class="card full"><h2>① 내 서버 접속</h2><div class="field"><input id="secret" type="password" autocomplete="off" placeholder="Render에 설정한 APP_ACCESS_SECRET"><button id="connect">서버 연결</button></div><div class="statusline" id="connectionMsg">접속 비밀값은 이 브라우저 탭의 세션에만 보관됩니다.</div></section>
<section class="card wide"><h2>② 국내 주식 시세</h2><div class="field"><input id="domestic" inputmode="numeric" maxlength="6" placeholder="종목코드 (예: 005930)"><select id="market"><option value="J">KRX</option><option value="NX">NXT</option><option value="UN" selected>통합</option></select><button id="domesticBtn">조회</button></div><div class="result" id="domesticResult"></div><div class="statusline" id="domesticMsg">6자리 종목코드를 입력해 조회하세요.</div></section>
<section class="card side"><h2>서버 상태</h2><div class="stat" id="serverState">—</div><div class="small" id="serverDetail">접속 후 상태를 확인합니다.</div></section>
<section class="card half"><h2>③ 해외 주식 시세</h2><div class="field"><select id="exchange"><option value="NAS">NASDAQ</option><option value="NYS">NYSE</option><option value="AMS">AMEX</option><option value="HKS">홍콩</option><option value="TSE">도쿄</option></select><input id="overseas" autocapitalize="characters" placeholder="티커 (예: AAPL)"></div><div class="field" style="margin-top:8px"><button id="overseasBtn">해외 시세 조회</button></div><div class="result" id="overseasResult"></div><div class="statusline" id="overseasMsg">티커를 입력해 조회하세요.</div></section>
<section class="card half"><h2>④ 시장·ETF 분석</h2><div class="muted">시장 지표, 환율, 채권, 원자재, 리츠 및 ETF 분석 영역입니다. 검증된 데이터 공급원을 연결한 뒤 표시합니다.</div><div class="statusline">데이터 연결 준비 중</div></section>
<section class="card full"><h2>⑤ AI 투자 분석</h2><div class="chat">AI 분석 기능은 다음 개발 단계에서 연결합니다. 우선 서버와 시세 조회 기능부터 검증하고 있습니다.</div><div class="field"><input disabled placeholder="AI 분석 API 연결 준비 중"><button disabled>질문하기</button></div><div class="statusline">AI 분석 API 미연결</div></section>
</div><div class="footer">투자 판단을 돕기 위한 정보 제공용이며 매수·매도 주문 기능은 없습니다. 시세는 거래소·상품·계정 권한에 따라 지연되거나 조회가 제한될 수 있습니다.</div></main>
<script>
const $=id=>document.getElementById(id);let access=sessionStorage.getItem('market_access')||'';if(access)$('secret').value=access;
async function api(path){if(!access)throw Error('먼저 서버 접속 비밀값을 입력하세요.');const r=await fetch(path,{headers:{Authorization:'Bearer '+access}});let d={};try{d=await r.json()}catch{}if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:(d.detail?.message||'HTTP '+r.status));return d}
$('connect').onclick=async()=>{access=$('secret').value.trim();if(!access){$('connectionMsg').textContent='접속 비밀값을 입력하세요.';return}try{const d=await api('/api/v1/status');sessionStorage.setItem('market_access',access);$('connection').textContent='서버 연결됨';$('connection').style.color='#42d6a0';$('connectionMsg').textContent='서버 인증 성공 · 읽기 전용';$('serverState').textContent='정상';$('serverDetail').textContent='KIS 인증정보 '+(d.credentials_configured?'설정됨':'미설정')}catch(e){access='';sessionStorage.removeItem('market_access');$('connection').textContent='연결 실패';$('connectionMsg').textContent=e.message;$('serverState').textContent='오류'}};
function render(id,msg,d){const box=$(id),p=d.price===null||d.price===undefined||d.price===''?NaN:Number(d.price),c=d.change===null||d.change===undefined||d.change===''?NaN:Number(d.change),r=d.change_rate===null||d.change_rate===undefined||d.change_rate===''?NaN:Number(d.change_rate),cl=c>0?'positive':c<0?'negative':'';box.style.display='block';box.innerHTML='<div class="small">'+d.symbol+(d.exchange?' · '+d.exchange:'')+' · KIS</div><strong>'+(Number.isFinite(p)?p.toLocaleString('en-US'):'데이터 없음')+'</strong> <span class="'+cl+'">'+(Number.isFinite(c)?(c>0?'+':'')+c.toLocaleString('en-US'):'')+' '+(Number.isFinite(r)?'('+r+'%)':'')+'</span><div class="small" style="margin-top:7px">수신: '+new Date(d.received_at).toLocaleString('ko-KR')+'</div>';$(msg).textContent=d.provider_message||'조회 완료'}
$('domesticBtn').onclick=async()=>{const s=$('domestic').value.trim();$('domesticMsg').textContent='조회 중…';try{if(!/^\d{6}$/.test(s))throw Error('종목코드 6자리를 입력하세요.');render('domesticResult','domesticMsg',await api('/api/v1/quote/domestic/'+s+'?market='+$('market').value))}catch(e){$('domesticMsg').textContent=e.message}};
$('overseasBtn').onclick=async()=>{const s=$('overseas').value.trim().toUpperCase();$('overseasMsg').textContent='조회 중…';try{if(!s)throw Error('티커를 입력하세요.');render('overseasResult','overseasMsg',await api('/api/v1/quote/overseas/'+$('exchange').value+'/'+encodeURIComponent(s)))}catch(e){$('overseasMsg').textContent=e.message}};
</script></body></html>'''
