# SK electlink · Domestic CPO Market Dashboard

국내 전기차 충전 사업자(CPO) 시장 현황 대시보드 — 환경부 공공 OpenAPI 데이터를 매월 자동 수집해 업데이트합니다.

**라이브 대시보드**: `https://<your-github-username>.github.io/<repo-name>/`

---

## 📦 프로젝트 구조

```
.
├── index.html                         ← 허브(홈) 페이지 — 대시보드 목록
├── AEP Dashboard.html                 ← Domestic CPO Market 대시보드
├── data/
│   ├── historical.json                ← PPT 원본 (Jan '23 ~ Feb '26) — 재계산 안함
│   ├── operator_mapping.json          ← busiNm → 법인 매핑 (편집 가능)
│   ├── dashboard-data.json            ← 대시보드가 fetch하는 최종 파일
│   ├── raw/                           ← 매월 수집된 원본 데이터
│   └── monthly/                       ← 월별 집계 스냅샷 (YYYY-MM.json)
├── scripts/
│   ├── fetch_api.py                   ← 환경부 API 수집
│   ├── transform.py                   ← 원본 → 월별 스냅샷 변환
│   └── build_dashboard_data.py        ← 월별 스냅샷 → 대시보드 JSON 병합
├── .github/workflows/
│   └── monthly-update.yml             ← 매월 1일 03:00 KST 자동 실행
└── requirements.txt
```

---

## 🚀 최초 1회 배포 (Setup)

### 1. GitHub 레포 생성
```bash
# 이 폴더 전체를 신규 GitHub 레포에 push
git init
git add .
git commit -m "Initial dashboard setup"
git remote add origin https://github.com/<user>/<repo>.git
git push -u origin main
```

### 2. 환경부 API Key 발급
1. [공공데이터포털](https://www.data.go.kr/data/15076352/openapi.do) 접속
2. "한국환경공단_전기자동차 충전소 정보" API 활용신청
3. 승인 후 발급받은 서비스키 복사

### 3. GitHub Secret 등록
- 레포 페이지 → **Settings → Secrets and variables → Actions** → **New repository secret**
- **Name**: `MOE_API_KEY`
- **Secret**: 발급받은 API 서비스키

### 4. GitHub Pages 활성화
- **Settings → Pages**
- **Source**: `Deploy from a branch`
- **Branch**: `main` / `/ (root)`
- 저장 → 약 1~2분 후 `https://<user>.github.io/<repo>/` 접속 가능

### 5. 첫 데이터 수집 (수동 트리거)
- **Actions** 탭 → **Monthly Dashboard Update** → **Run workflow**
- 약 20~30분 소요 (49만건 수집 + 변환)
- 완료되면 `data/dashboard-data.json`이 자동 커밋됨

---

## 📅 자동 업데이트 스케줄

**매월 1일 KST 03:00** (UTC 기준 매월 1일 18:00) 에 자동 실행:

```yaml
- cron: '0 18 1 * *'
```

수동으로 언제든지 **Actions → Run workflow** 로 실행 가능.

---

## 🔧 매핑 테이블 편집

`data/operator_mapping.json` 편집해서 법인 통합 규칙 수정 가능.

예: 새로운 busiNm "차지비 신규" 가 생겼다면:
```json
"GS차지비": [
  "GS차지비",
  "차지비",
  "차지비 신규",    ← 추가
  ...
]
```

transform.py가 매핑 안 된 사업자 중 10건 이상인 것을 워크플로우 로그에 출력해 주므로, 로그를 보고 매핑 테이블을 업데이트하면 됩니다.

### 추적 대상 Top 5 변경

`operator_mapping.json`의 `dashboard_tracked` 섹션 편집:

```json
"dashboard_tracked": {
  "slow": ["GS차지비", "파워큐브", "에버온", "LG유플러스 볼트업", "플러그링크"],
  "fast": ["채비", "SK일렉링크", "이브이시스", "휴맥스이브이", "GS차지비"]
}
```

현재 정책: **고정 추적** — 추락해도 같은 5개 사업자를 계속 추적.

---

## 💻 로컬 개발 / 테스트

### 의존성 설치
```bash
pip install -r requirements.txt
```

### 수동 실행 순서
```bash
# 1. 환경부 데이터 수집
export MOE_API_KEY=your_key
python scripts/fetch_api.py

# 2. 원본 → 월별 스냅샷 변환
python scripts/transform.py

# 3. 대시보드 JSON 빌드
python scripts/build_dashboard_data.py

# 4. 로컬 서버로 테스트
python -m http.server 8000
# → http://localhost:8000 에서 확인
```

### 다른 월 데이터 처리
```bash
python scripts/transform.py --input data/raw/과거파일.xlsx --year-month 2026-01
```

---

## 📊 데이터 파이프라인 상세

### Stage 1: `fetch_api.py`
- 환경부 OpenAPI 호출 (페이징, 페이지당 9,999건)
- 재시도 로직 (3회, 지수 백오프)
- 출력: `data/raw/raw_YYYYMMDD.parquet`

### Stage 2: `transform.py`
1. busiNm 정규화 (공백 제거)
2. `operator_mapping.json` 적용 → 법인 통합
3. `newtype`으로 급속/완속 분리
4. 지역명으로 GSMA/광역시/기타 분류
5. Top 5 사업자별 × 지역별 교차집계
6. 출력: `data/monthly/YYYY-MM.json`

### Stage 3: `build_dashboard_data.py`
- `historical.json` (PPT 원본) + `monthly/*.json` 병합
- 중복 월은 historical 우선 (PPT 값 유지)
- 새 월만 시계열 뒤에 이어붙임
- 출력: `data/dashboard-data.json`

### Stage 4: 브라우저 렌더링
- `AEP Dashboard.html`이 `./data/dashboard-data.json` fetch (루트 `index.html`은 허브 페이지로 링크만 제공)
- Chart.js로 렌더링
- fetch 실패 시 `historical.json`으로 fallback

---

## 🎯 고정 정책 (변경하려면 README 업데이트 필요)

| 항목 | 현재 정책 |
|---|---|
| Top 5 선정 | 고정 추적 (추락해도 같은 사업자) |
| Slow에 SKEL 표시 | ❌ PPT 원본 유지 (미표시) |
| 과거 데이터 (Jan '23 ~ Feb '26) | 재계산 안함, historical.json 그대로 |
| 집계 기준 | busiNm (법인 단위, 매핑 테이블 적용) |
| 급속/완속 구분 | `newtype` 컬럼 기준 |
| 업데이트 주기 | 매월 1일 03:00 KST |

---

## 🐛 트러블슈팅

**Q: GitHub Actions가 실패했어요**
- Actions 탭 → 실패한 run 클릭 → 로그 확인
- 흔한 원인: `MOE_API_KEY` 미등록, API 서비스키 만료

**Q: 대시보드에 최신 데이터가 안 보여요**
- 브라우저 강제 새로고침 (Ctrl+Shift+R)
- `data/dashboard-data.json` 파일이 레포에 실제로 커밋됐는지 확인

**Q: 매핑 안 된 사업자 경고가 나와요**
- Actions 로그에 "매핑 안 된 busiNm" 메시지 확인
- 주요 사업자면 `data/operator_mapping.json`에 추가 후 재실행

**Q: 대시보드를 로컬에서 파일로 열면 안 떠요**
- `file://` 프로토콜은 fetch 제약이 있음
- `python -m http.server` 로 로컬 서버 띄워서 보기

---

## 📝 라이선스

내부 사용 — SK electlink 성장지원팀
