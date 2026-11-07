# Private KIS Market Data API (starter)

개인 투자 분석 앱을 위한 **시세 조회 전용** FastAPI 서버 시작 패키지입니다.
실전 주문 API는 포함하지 않습니다.

## 현재 포함 기능
- KIS OAuth2 Client Credentials 토큰 발급 및 서버 메모리 캐시
- 국내 주식 현재가 REST 조회
- 해외 주식 현재체결가 REST 조회
- 모든 `/api/v1/*` 경로에 Bearer 앱 접근키 적용
- 헬스체크 및 상태 확인
- Render Blueprint 초안

## 아직 구현/검증되지 않은 기능
- KIS 실계정 인증 성공 여부 (발급된 개인 키로 배포 후 테스트 필요)
- WebSocket 스트리밍
- 거래일 달력과 직전 거래일 종가 보정
- 지속형 시세 저장소
- iPhone PWA 프런트엔드와 사용자 로그인 화면
- 모든 국가/거래소별 시세 권한 및 실시간 수신 권한

## 로컬 실행
Python 3.11 이상 권장.

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# .env 파일에 본인의 값을 입력
set -a && source .env && set +a  # Windows PowerShell은 별도 환경변수 설정
uvicorn app.main:app --reload
```

## API 사용
모든 API 키는 서버에만 둡니다. 앱에서는 KIS APP KEY/SECRET을 절대 사용하지 않습니다.

```bash
curl http://127.0.0.1:8000/health
curl -H "Authorization: Bearer YOUR_APP_ACCESS_SECRET" \
  http://127.0.0.1:8000/api/v1/status

# 삼성전자 예시
curl -H "Authorization: Bearer YOUR_APP_ACCESS_SECRET" \
  "http://127.0.0.1:8000/api/v1/quote/domestic/005930?market=UN"

# Apple 예시 (NASDAQ)
curl -H "Authorization: Bearer YOUR_APP_ACCESS_SECRET" \
  "http://127.0.0.1:8000/api/v1/quote/overseas/NAS/AAPL"
```

`market=UN`은 국내 통합시장 조회 코드입니다. 실제 계정 권한과 API 공지/호출 제한에 따라 조회 결과가 달라질 수 있습니다.

## Render 배포
1. 이 폴더의 파일을 GitHub 저장소에 업로드합니다.
2. Render에서 New > Blueprint로 저장소를 연결합니다.
3. `KIS_APP_KEY`, `KIS_APP_SECRET`, `APP_ACCESS_SECRET`을 Render 환경변수 화면에서 직접 입력합니다.
4. 배포가 끝나면 `/health`와 인증된 `/api/v1/status`를 확인합니다.
5. API 호출이 성공하기 전에는 앱에 실시간 데이터로 표시하지 않습니다.

무료 Render Web Service는 유휴 시 중단될 수 있으므로, 상시 WebSocket 스트리밍 용도로는 적합하지 않을 수 있습니다. 실시간 상시 연결은 배포 플랜과 실행시간 제약을 확인한 뒤 구성합니다.

## 보안 및 사용 조건
- 본 패키지는 본인 자산의 투자 분석을 위한 개인용 시작 코드입니다.
- KIS 시세정보는 개인 고객의 자기 자산 투자 목적에 한해 이용하고 제3자에게 제공하지 않아야 합니다.
- `APP_ACCESS_SECRET`은 개인 앱의 간단한 서버 접근 잠금 장치입니다. 실제 PWA 공개 전에는 강한 비밀값 관리와 필요 시 더 견고한 로그인 방식을 추가하세요.
- KIS APP KEY/SECRET을 GitHub, 브라우저 코드, 이슈, 채팅, 스크린샷에 올리지 마세요.
- KIS API 호출 유량 제한 및 실시간 시세 이용 권한을 확인하세요.
- 이 코드는 초도 연동용이며, 실제 운영 전 실계정에서 검증해야 합니다.

공식 문서:
- https://apiportal.koreainvestment.com/about-open-api
- https://apiportal.koreainvestment.com/apiservice-summary
- https://apiportal.koreainvestment.com/about-howto
