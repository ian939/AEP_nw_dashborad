# CLAUDE.md

매월 자동화(GitHub Actions) 및 데이터 갱신 작업 시 반드시 지켜야 할 규칙 모음.
새 세션이 시작될 때 이 파일을 우선 읽어 컨벤션을 일관되게 적용할 것.

---

## 1. 보고 시점 기준 — "전월 보고서" 컨벤션

매월 15일 KST 03:00에 워크플로우가 실행되며, 그 결과물은 **전월 말 기준 보고서**로 라벨링한다.

| 데이터 소스 | 받아오는 시점 | 라벨링 |
|---|---|---|
| 환경부 충전기 OpenAPI (`fetch_api.py`) | 실행 시점(예: 5/15) 실시간 snapshot | **전월**로 라벨 (예: `2026-04`) |
| data.go.kr EV 신규등록 (`fetch_ev_registration.py`) | API가 기본적으로 **전월** 데이터 반환 | 전월 그대로 (예: `2026-04`) |

이유:
- EV 신규등록 API는 완료된 월(전월)만 데이터 제공
- 두 소스의 `ym`을 자연 정렬시키기 위해 충전기 snapshot도 전월로 표기
- 결과적으로 대시보드 KPI/차트/테이블 모두 "Apr-26" 같은 통일된 라벨

구현:
- `scripts/transform.py` 디폴트 `year_month` = 전월 (`datetime.now().month - 1`)
- 1월에 실행되면 작년 12월 (`{year-1}-12`)

---

## 2. 누적 EV 산정 — 멱등성(idempotency) 필수

`fetch_ev_registration.py`는 `cumulative.json`을 `prev["total_ev"] + monthly_new` 방식으로 갱신하므로 **동일 ym으로 두 번 실행되면 중복합산** 위험.

### 멱등성 가드 (이미 구현되어 있음 — 깨뜨리지 말 것)
```python
if prev["year_month"] == ym:
    # 이미 이번 달 반영된 상태 → 이전 월 누적을 역산하여 재계산
    prev_total_ev = prev["total_ev"] - prev_monthly["monthly_new"]["total_ev"]
    ...
```

### 검증 공식
새 cumulative는 **반드시** 아래를 만족해야 함:
```
new_cumulative.total_ev = (직전 ym의 cumulative.total_ev) + monthly_new.total_ev
new_cumulative.passenger_ev = (직전 ym).passenger_ev + monthly_new.passenger_ev
new_cumulative.commercial_ev = (직전 ym).commercial_ev + monthly_new.commercial_ev
```

값이 어긋나면 중복합산 의심 → `git log -p data/ev_registration/cumulative.json`으로 확인 후 정정.

---

## 3. ev_market — 두 시계열의 분리

### `ev_months` (누적 추세, bar + penetration line)
- ym = 충전기 snapshot 라벨 (= 전월)
- 누적 EV는 `cumulative.json` 기반 (그 시점에 알려진 최신값)
- penetration % 는 **추정값**으로 채움 (아래 6번 항목)

### `ev_sales_months` (월간 신규등록, bar + share line)
- ym = `ev_registration/{ym}.json`의 `year_month`
- 별도 패스 `append_ev_sales_from_registrations()`에서 등록 파일을 직접 순회하며 누락분만 append
- 이 패스는 **build마다 안전하게 재구성** 가능 (이미 있으면 skip)

build 순서 (`build_dashboard_data.py`):
1. 월별 snapshot 루프 → `months`, `slow_trend`, `fast_trend`, `ev_months` 누적, 차충비
2. `append_ev_sales_from_registrations(result)` → `ev_sales_*` 보장
3. `build_overview_5month(result, ...)` → 최근 5개월 재계산

---

## 4. 차충비(EVs / Port) — fallback 매칭

스냅샷 ym 과 ev_registration ym 이 일치하지 않을 수 있으므로 (예: 충전기 4월, ev_reg는 2026-04뿐) `find_latest_ev_reg(target_ym)` 사용:
- target_ym **이하**에서 가장 최근 ev_reg 파일 반환
- 없으면 None

차충비 = `cumulative.total_ev / charger_count`로 산정.

---

## 5. EV Share (월간 판매 비율) — 카테고리별 분모

### 차트 약속 (반드시 지킬 것)
- Passenger Share = `Passenger EV / Total Passenger Vehicles`
- Commercial Share = `Commercial EV / Total Commercial Vehicles`

### 현재 구현 (B안 — 근사)
data.go.kr API가 카테고리별 전체차량을 별도로 주지 않으므로, **직전 historical share 에서 역산하여 차량 분모 비율을 carry-forward**.

```python
# build_dashboard_data.py: append_ev_sales_from_registrations 내부
prev_pass_total = prev_pass_ev / (prev_pass_share / 100)
prev_comm_total = prev_comm_ev / (prev_comm_share / 100)
pass_ratio = prev_pass_total / (prev_pass_total + prev_comm_total)  # ~ 0.77
comm_ratio = prev_comm_total / (prev_pass_total + prev_comm_total)  # ~ 0.23

new_pass_total = this_month_total_veh * pass_ratio
new_comm_total = this_month_total_veh * comm_ratio
new_pass_share = pass_ev / new_pass_total * 100
new_comm_share = comm_ev / new_comm_total * 100
```

### 향후 정확도 향상 (A안 — 미구현)
`fetch_ev_registration.py`에 `useFuelCode` 미지정 + `prpos=1/2/3` 호출 3개 추가 (시도 17 × 3 = 51 calls 증가) 시 카테고리별 전체차량을 직접 수집해 carry-forward 없이 정확 계산 가능.

---

## 6. Penetration % — 추정값 부여

`_append_ev_market` 에서 새 월 추가 시 penetration 을 None 으로 두지 않고 직전 값으로 cum_veh 역산 → 새 cum_veh 도출 → 새 penetration 계산.

```python
prev_total_veh = (prev_cum_K * 1000) / (prev_pen / 100)
new_total_veh = prev_total_veh + monthly_new_total_vehicles
new_pen = (new_cum_K * 1000) / new_total_veh * 100
```

승용/상용은 직전 ratio 로 스케일.

---

## 7. KPI vs Chart vs Table — 보급률 표시 규칙

3곳에서 보여주는 보급률은 **의도적으로 다른 지표** (혼동 주의):

| 위치 | 지표 | 데이터 키 |
|---|---|---|
| KPI 카드 `EV Penetration Rate` | **전체** EV / 전체차량 | `ev_total_penetration_pct[lastIdx]` |
| Chart `Passenger EVs / Penetration % Trend` | **승용** EV 보급률 | `ev_passenger_penetration_pct` |
| Chart `Commercial EVs / Penetration % Trend` | **상용** EV 보급률 | `ev_commercial_penetration_pct` |
| Table 마지막 — `EV Penetration Rate` 행 | **전체** (KPI 와 동일해야 함) | `ev_total_penetration_pct[penStart+i]` |
| Table 하위 행 — `Passenger EV ①`, `Commercial EV ②` | 각 카테고리 | `ev_*_penetration_pct[penStart+i]` |

### 테이블 인덱싱 (off-by-one 방지)
```javascript
// AEP Dashboard.html (renderEvMarket 함수)
const penStart = ev.ev_months.indexOf(precise.months[0]);  // ← 동적 결정
// 절대 'Mar-25' 같은 하드코딩하지 말 것 (precise.months 가 변함)
```

### 검증
새 월 추가 후 반드시 확인:
- KPI 카드의 보급률 값 == 테이블 `EV Penetration Rate` 행의 마지막 열 값
- 차트는 카테고리별이므로 카드 제목(Passenger/Commercial) 확인 후 값 비교

---

## 8. GitHub Pages 자동 배포

- `pages build and deployment` 워크플로우는 GitHub Pages 시스템이 자동 추가한 것 (직접 건드리지 말 것)
- main 브랜치 push 시마다 자동 실행, 정적 사이트를 `https://ian939.github.io/AEP_nw_dashborad/` 로 배포
- public repo 이므로 Actions 분 사용량은 무과금

---

## 9. 실패 → 재시도 흐름

매월 15일 자동 실행 실패 시:
1. **Actions 탭 → 실패한 run → Re-run all jobs** (또는 manual `Run workflow`)
2. 멱등성 가드 덕분에 동일 ym 재실행 안전
3. 재실행 후 `git pull` 로 로컬 동기화

흔한 실패 패턴:
- `lxml not found` → `requirements.txt`에 명시되어 있음 (이미 반영)
- `pd.read_xml(bytes)` 에러 → `io.BytesIO(resp.content)` 로 감싸야 함 (이미 반영)
- API rate limit → 재시도 로직(3회, 지수 백오프)이 `fetch_*.py`에 있음

---

## 10. 새 대시보드 추가

레포는 허브(`index.html`) + 개별 대시보드 (`AEP Dashboard.html`, `Regional CPO Dashboard.html`) 구조.
새 대시보드 추가 시:
1. `repo/<Dashboard Name>.html` 생성 (AEP 톤앤매너 — `--navy`, `--sk-red` 토큰 재사용)
2. `repo/index.html` 의 `<div class="grid">` 안에 카드 블록 복제 (href·타이틀·설명·tag만 교체)
3. 자동/수동 갱신 주기 tag (`tag-auto`/`tag-manual`) 명시

---

## 11. 섹션⑥ 충전기 신규/철거 리포트 (Charger Deployment & Removal)

AEP Dashboard 탭 ⑥. 월 스냅샷 비교로 충전기 신규/철거를 완속·급속으로 나눠 분석한다.
별도 수집 레포에 의존하지 않고 **AEP 월간 워크플로 내부에서 자동 갱신**된다.

### 데이터 흐름
1. `fetch_api.py` 가 이미 받는 `data/raw/raw_YYYYMMDD.parquet`(환경부 전체 스냅샷)이 원천.
2. `scripts/charger_report/build_snapshots.py --ingest <raw>` → 슬림 스냅샷 `data/charger_snapshots/{MMM-YY}.parquet` 생성·append·prune(N_MONTHS=4). 라벨=**스냅샷 당월**(예: 5/15 실행 → `May-26`). AEP 메인 KPI의 "전월" 라벨과 다른 자기완결 서브리포트.
3. `scripts/charger_report/analyze.py` (collector SSOT 무수정) → `output/report_data.json` + xlsx.
4. `scripts/charger_report/verify.py` (독립 재계산 게이트) → 마지막 줄 **`✅ 전체 검증 통과`** 일 때만 발행.
5. `scripts/build_charger_deployment.py` 가 위를 오케스트레이션하고 통과분을 **`data/charger_deployment.json`**(대시보드 fetch 대상)으로 복사.
6. 워크플로: `monthly-update.yml` 의 `Build dashboard data` 뒤 `Build charger deployment report` 스텝. `git add data/` 가 스냅샷·발행본 자동 포함.

### 절대 규칙
- **분석 로직(analyze.py/verify.py)은 collector SSOT 그대로 — 재구현·수정 금지** (`verify.py`는 라벨 일반화 1줄만 수정됨).
- 매월 바뀌는 건 입력 raw 뿐. CPO 명단·차원·검증 불변식은 `scripts/charger_report/config.py` + collector `METHODOLOGY.md` 기준.
- 신규 코드(`build_snapshots.py`)는 **입력 컬럼 파생만** 담당: 완속/급속(chgerType 02·07·08 + output 30kW), `NewbusiNm`(transform.apply_operator_mapping + `NAME_MAP`), `지역명/권역`(zcode 표준표), `Kind/KindDetail`(kind 코드표). 코드표는 collector parquet 대조로 캘리브레이션됨(kind purity 1.000).
- **코드표는 원천 변경에 맞춰 갱신한다**(2026-07):
  - `ZCODE_TO_SIDO['12']='전남광주통합특별시'` 추가(구 29 광주+46 전남 통합). `ZCODE_TO_REGION['12']='5대광역시'`(구 광주 지위 승계). 폐지된 29·46 은 미이관 잔재(약 85건) 위해 맵에 유지. 안 넣으면 지역명 `(미상)`.
  - **kind 코드 O→0 정규화**: 원천이 2번째 자리를 숫자 0 아닌 영문 O 로 넣은 오타(`'BO'`→`'B0'` 주차시설)로 급속 카테고리 약 1,500기가 `(미상)`으로 빠졌던 것 수정.

### CPO 매칭 (exact-string) — 깨지면 holdings=0
`analyze.py` 는 `NewbusiNm == cpo`(config.CPO_LISTS) 정확 매칭. AEP 매핑은 `'LG유플러스 볼트업'` 을 쓰므로 `NAME_MAP` 으로 `'LG유플러스'` 로 정규화 필수. 펌프킨·한국전기차충전서비스·엘에스이링크는 busiId 미매핑이라 `bnm` fallback 으로 매칭(현재 정상). 새 월 갱신 후 **모든 CPO holdings 가 0이 아닌지** 확인.

### 시드 (최초 1회, 로컬)
`build_snapshots.py --seed` → collector `분석/data/{MM월말}.parquet` 4개를 `Feb-26~May-26` 으로 슬림 시드(파생 컬럼 그대로 사용 → 검증된 기존 리포트와 동일 수치 재현). CI엔 xlsx/collector 없으므로 시드는 로컬 전용, 결과 parquet만 커밋.

### 알려진 seam / 주의
- 시드(월말 기준) ↔ 향후 AEP(월 15일경) 스냅샷 케이던스 혼재 → 최초 증분 1개 구간만 기간이 짧음(일회성). 순증감은 키 차집합이라 날짜 무관하게 견고.
- 슬림 스냅샷 ~8.5MB×4. prune 으로 N_MONTHS 유지. 작업물(`output/`)은 `.gitignore`.
- 검증: `python scripts/build_charger_deployment.py --no-ingest` 로 현재 스토어만 재발행(시드 검증용). CI는 `--no-ingest` 없이 최신 raw ingest.

---

## 12. EV Sales Dashboard (모델별 판매 · Node 파이프라인)

`EV Sales Dashboard.html` (허브 카드 = 수동 톤이지만 실제로는 **월간 워크플로에서 자동 갱신**). 다나와 판매실적
기반 국내 EV 모델별 판매. Python 파이프라인과 별개인 **Node(ESM, 외부 의존성 0)** 파이프라인이 `repo/ev_sales/` 에 있다.

### 워크플로 구조 (monthly-update.yml — 2개 독립 job)
`update-aep`(Python)와 `update-ev-sales`(Node)로 **분리**돼 있다. EV job 은 `needs: update-aep` +
`if: always()` → **환경부 수집(AEP)이 실패해도 EV 대시보드는 항상 갱신**된다. 두 job 모두 main 에 push 하므로
**순차 실행 + 워크플로 레벨 `concurrency: monthly-update`(동시 실행 큐잉)** + EV push 전 `git pull --rebase` 로
push 경쟁을 막는다. 각 job 은 자기 산출물만 커밋한다(AEP=`git add data/`, EV=`ev_sales/data/ev_master.csv` +
`"EV Sales Dashboard.html"`).

### EV job 데이터 흐름
1. `Setup Node.js` → node 20.
2. `node ev_sales/scripts/collect_models.mjs` — 다나와(auto.danawa.com)에서 **전월** 모델 판매 스크래핑
   → `ev_sales/data/ev_master.csv`(dim=model) 멱등 upsert. **API키 불필요**(HTML 파싱). `continue-on-error: true`
   — 다나와가 CI IP를 막아도 job 이 죽지 않고 기존 CSV로 재빌드.
3. `node ev_sales/scripts/build_sk_dashboard.mjs` — CSV → self-contained `EV Sales Dashboard.html`(CDN Chart.js).
   출력 위치는 `findRepoRoot()`(index.html 상위 탐색)로 repo 루트에 씀.

### SSOT / drift 규칙 (중요)
- **`repo/ev_sales/` 가 CI 프로덕션 복사본.** 프로젝트 밖 `1. EV판매 트랙커/` 는 로컬/개발 원본(다크 대시보드,
  KOTSA secret, scan_ev 등)이며 두 곳의 스크립트(`lib.mjs`/`dashboard_data.mjs`/`build_sk_dashboard.mjs`/
  `collect_models.mjs`)는 **byte-identical**. 데이터 계산은 `dashboard_data.mjs` 단일 함수.
- **화이트리스트(`config/ev_models.json`) 편집은 `repo/ev_sales/config/` 가 CI 기준.** 신차 추가 시 이 파일을 갱신
  (로컬 트래커에서 `scan_ev.mjs` 로 후보만 찾고, 결과는 repo 복사본에 반영).
- 다나와 원본 스냅샷 `ev_sales/data/raw/*.json` 은 `.gitignore`(대시보드는 CSV로 재현 가능).

### 연료·지역·비중 (현재 placeholder)
KOTSA 신규등록 API 장애로 연료·지역·공식총계 축은 비어 "API 수집 후 표시" placeholder. API 회복 시
`dashboard_data.mjs` 의 `hasFuel`/`hasRegion`/`hasApiTotal` 플래그가 자동으로 차트를 켠다(로컬 트래커의
`collect.mjs` 로 채워야 함 — CI엔 미포함).

---

## 13. AEP job — 충전기(환경부) · 차량(KOTSA) 독립 수집

`update-aep` 안의 두 수집 소스는 **서로 다른 제공처**이고 이제 **독립적으로 실패**한다.
- 충전기: 환경부/한국환경공단 `B552584` (`fetch_api.py`)
- 차량(EV 신규등록): 한국교통안전공단(KOTSA) `B553881` (`fetch_ev_registration.py`)

과거엔 한 job 에 순차로 묶여 앞 스텝(충전기)이 죽으면 뒤(차량·빌드)가 전부 skip 됐다
(예: 2026-05 차량 데이터는 수집됐으나 충전기 실패로 대시보드에 반영 안 됨). 이를 분리:
- 두 fetch 스텝 모두 `continue-on-error: true` + `id`(charger/vehicle).
- `transform` / `build_charger_deployment` → **충전기 성공 시에만** (실패 시 돌리면 지난달 raw 를
  이번 달로 잘못 라벨링해 오염).
- `build_dashboard_data` / commit → **하나라도 성공하면** 실행. `build_dashboard_data` 는 raw 불필요
  (`monthly/` + `ev_registration/` 만 사용), `append_ev_sales_from_registrations()` 가 차량분만 갱신.
- 마지막 "수집 실패 표시" 스텝이 한 소스라도 실패 시 `exit 1` → **job 은 빨간불이지만 성공한 소스 데이터는 이미 커밋됨**.

### 운영 시 주의
- job 이 실패(red)여도 **차량분은 커밋됐을 수 있다.** 로그의 `charger=.. / vehicle=..` 로 어느 쪽이
  실패했는지 확인. 실패 소스는 해당 백엔드 회복 후 재실행하면 반영된다.
- 충전기 데이터는 실패한 달엔 지난달 값이 유지된다(차트에 새 충전기 월이 안 붙음). 정상 동작.

---

## 14. 충전기 raw 소스 = collector CSV (직접 환경부 API 대체)

직접 환경부 API(`fetch_api.py`, data.go.kr `B552584`)가 게이트웨이 장기 장애로 반복 실패해,
충전기 raw 획득을 **collector 레포 `ian939/ev-charger-collector` 의 `latest_data.csv.gz`** 로 전환했다.

- `scripts/fetch_charger_from_collector.py` 가 gz(≈15MB, 전국 ~489k행, 매일 01:00 UTC 갱신)를 받아
  `data/raw/raw_YYYYMMDD.parquet` 로 변환. 이후 `transform.py`/`build_snapshots.py`/`build_dashboard_data.py`
  는 **무수정** 동작(기존 raw parquet 규칙 그대로).
- **`dtype=str` 필수**: chgerType `"06"`, zcode `"11"`, kind 코드의 앞자리 0/포맷 보존(숫자 추론 시 오분류).
- collector 파생 컬럼(`권역/지역명/newtype/Kind(new)/...`)은 **드롭** → 하위의 검증된 zcode·chgerType 유도
  경로를 그대로 태운다(파생 `지역명` 포맷 불일치 방지).
- 워크플로: `update-aep` 의 charger 스텝이 `fetch_charger_from_collector.py` 를 호출(`id: charger`,
  `continue-on-error` 유지). `fetch_api.py` 는 삭제하지 않고 참고용으로 잔존(현재 미사용).
- `latest_data.csv.gz` 는 collector 가 **매일 덮어쓰는 최신 날짜 스냅샷**(안정 파일명) → 15일 실행 시 항상 그 시점 최신본을 가져온다.
- **완전성 이중 게이트**: (1) 절대 하한 `MIN_ROWS=400_000`, (2) **직전 raw 대비 상대 하한 `MIN_RATIO_VS_PREV=0.95`**.
  절대 하한만으론 "약 3만대(6%) 누락" 같은 부분수집을 못 걸렀던 사례가 있어(2026-07 collector 재수집 전 489k), 직전 스냅샷 대비 5%↓ 면 실패시켜 **부분수집이 완전 데이터를 덮어쓰는 것을 방지**한다. 실패 시 collector 재수집 후 재실행.
- 일일 수집 실패 시 gz 는 하루 stale 가능(허용 — 상대 게이트는 통과, 데이터만 하루 이전).

### 소스 전환 단차/seam (일회성)
- collector 스냅샷(~489k)은 직접 API 선언치(~521k)보다 ~6% 적음(중복제거/삭제분 필터 추정) →
  충전기 총량에 **1회성 레벨 단차**(수용 결정). 방법론은 동일(둘 다 환경부 데이터).
- **섹션⑥(충전기 신규/철거)** 은 diff 기반이라, 마지막 API 스냅샷 → 첫 collector 스냅샷 전환 월에
  키(statId|chgerId) 차이로 **큰 가짜 순증감(예: 완속 -24k)** 이 한 번 나타난다(verify.py 는 내부
  정합성만 보므로 통과). 이후 collector→collector diff 는 정상. §11 의 시드↔케이던스 seam 과 동일 성격의
  **일회성 전환 구간**으로 간주. (전환 월 ⑥ 수치는 참고만.)

---

## 15. 차량(KOTSA) 수집 실패 시 수동 입력 절차

KOTSA `B553881` 장애로 자동수집이 실패했을 때, 누적 EV 실측값만 확보되면 수동 반영 가능하다.

### 진단 먼저 (재실행 낭비 방지)
로컬에서 **실제 키로** 호출해 `errMsg` 를 본다. curl 로 가짜 키를 던져 `403` 이 오는 건 게이트웨이만
살아있다는 뜻이므로 **회복 판정 근거가 아니다**.
- `SERVICETIMEOUT_ERROR`(code 05) → 서비스 다운. 재실행 무의미, 대기.
- 과거 성공월(예: 2026-06)도 같이 실패하면 전면 장애 확정.
- 대조군: 환경부 `B552584/EvCharger/getChargerInfo` 가 200 이면 내 네트워크·키는 결백.

### 수동 입력
`data/ev_registration/{ym}.json` 을 직접 만든다. **`monthly_new` = 이번달 누적 − 직전달 누적**.
```json
{ "year_month": "2026-07", "_source": "manual",
  "monthly_new":  { "total_vehicles": 0, "total_ev": 34628, "passenger_ev": 29953, "commercial_ev": 4675 },
  "cumulative":   { "total_ev": 1131483, "passenger_ev": 925095, "commercial_ev": 206388 } }
```
그리고 `cumulative.json` 을 같은 값으로 갱신 후 `python scripts/build_dashboard_data.py`.

- **`total_vehicles` 는 `0` 으로 둔다(추정해 채우지 말 것).** 이 값은 *월간* 신규등록(flow)이며
  `ev_sales_share_pct`(월간 판매 비중)의 분모다. flow 는 월별 변동이 커서 추정 근거가 없으므로
  `0` → `None` → 대시보드 공백(—)이 정답이다.
- **단, 누적 보급률(penetration)은 별개다 — 추정해서 채운다.** 누적 테이블의 분모는 *누적*
  전체차량(~2,700만대)이라는 완만한 stock 이라 `_recent_veh_growth()` 가 직전 pen 역산값의
  최근 3개월 평균 증가분으로 추정한다. 분자(누적 EV)가 실측이므로 **분모를 ±25만대 달리 잡아도
  보급률 변동은 ±0.04%p**(4.15~4.19%) 수준이다. 즉 flow 는 추정 불가, stock 은 추정 가능.
- 추정한 월은 테이블에 `*` + 각주(`evMetricsNote`)로 **추정치임을 반드시 표기**한다. 숨기지 말 것.
- 반드시 `passenger + commercial == total` 검증(누적·월간 양쪽).
- 대시보드는 `evDataNotice` 배너로 미수집 월을 자동 표기(최근 6개월만 검사). 월 하드코딩 금지.
- API 회복 후 `TARGET_YYYYMM=202607` 로 재실행하면 §2 멱등성 가드가 수동값을 정식값으로 대체한다.

### 주의: 누적 EV 를 갱신하지 않으면 보급률이 하락처럼 보인다
분자(누적 EV)가 직전 월에 고정된 채 분모만 증가하면 보급률이 **하락한 것처럼** 보인다
(실제 사례: Jul-26 이 4.06%→4.04%). 계산 artifact이므로, 누적 EV 실측값을 먼저 반영해야 한다.
반영 후 Jul-26 은 4.16% 로 정상 상승했다.

### flow vs stock — 무엇을 추정할 수 있나
| 지표 | 분모 | 성격 | 미수집 시 |
|---|---|---|---|
| 누적 보급률 (Penetration) | 누적 전체차량 ~2,700만 | stock (완만) | **추정 O** (`*` 표기) |
| 월간 EV 판매 비중 (Share) | 월간 전체 신규등록 ~16만 | flow (변동 큼) | **추정 X** (공백 —) |

판단 기준은 "분모가 stock 인가 flow 인가". stock 은 월 변동이 전체의 0.6% 수준이라 추정 오차가
결과에 거의 영향을 주지 않는다. flow 는 전월비 ±30% 도 흔해 추정하면 숫자가 조용히 틀린다.

---

## 16. dashboard_top10 에 사업자 추가하는 법

완속/급속 추세 차트는 `slow_trend_top10`/`fast_trend_top10` 을 그린다(`slow_trend` 6사가 아님 —
차트 제목의 '+Epic' 과 혼동 주의). 새 사업자를 차트에 넣으려면 **두 곳** 을 고쳐야 한다.
1. `data/operator_mapping.json` → `dashboard_top10.slow`(또는 `.fast`) 에 이름 추가
2. `data/historical.json` → 해당 `*_trend_top10` 에 과거 구간 시드
   (`*_trend` 에 이미 있으면 그 배열을 그대로 복사)

2번을 빼먹으면 최근 몇 개월만 값이 있는 짧은 배열이 되고, **Chart.js 는 배열을 왼쪽부터 정렬하므로
과거 구간(Jan-24~)에 잘못 그려진다.** `build_dashboard_data.py` 의 `_append_aligned()` 가 선행 `None`
패딩으로 방어하지만, 과거 실제값을 원하면 시드가 필요하다.

검증: **재빌드를 2회 연속** 돌려 모든 계열 길이가 `len(months)`(급속은 `months_fast_extended`) 로
유지되는지 확인(멱등성). 길이가 늘어나면 중복 append 버그다.

---

## 17. SK일렉링크 = SKEL + KEVIT 합산 (Mar-26 이후)

`SK일렉링크` 는 **SKEL(busiId `ST`) + KEVIT/한국전기차인프라기술(busiId `KE`)** 합산이다.
`operator_mapping.json` 의 `mapping.SK일렉링크= ["ST","KE"]` 한 줄이 전부이며,
`apply_operator_mapping()` 이 busiId→operator 로 묶으므로 **코드 수정은 불필요**하다.

### 적용 구간과 단차 (중요)
| 구간 | 개월 | 집계 |
|---|---|---|
| Jan-24 ~ Feb-26 | 26 | **SKEL 단독** (historical.json 은 집계본이라 사업자별 원자료 없음 → 소급 불가) |
| **Mar-26 ~** | 5+ | **SKEL + KEVIT** (monthly 스냅샷 보유 → 재생성 가능) |

→ **Feb-26(4,830) → Mar-26(8,990) 완속 +4,160 은 실제 증설이 아니라 집계 기준 변경분**이다.
급속도 동일 성격(+358). §11·§14 seam 과 같은 일회성 구간으로 간주하고, 해당 4개 차트의
`.source` 각주에 명시했다(완속추세·급속추세·집중도④·M/S⑤).

### 재생성 절차 (매핑 바꾼 뒤 반드시)
monthly 스냅샷은 **사업자별 집계본**이라 매핑만 고치면 반영 안 된다. raw 부터 재생성해야 한다.
```bash
# raw ↔ ym 대응은 각 monthly json 의 _meta.source_file 로 확인
python scripts/transform.py --input data/raw/raw_20260420.parquet --year-month 2026-03
# ... 나머지 월 동일 ...
python scripts/build_dashboard_data.py
```

### Jul-26 반영 결과 (검증 완료)
| 지표 | 이전(SKEL) | 이후(+KEVIT) |
|---|---|---|
| 완속 | 5,291 (15위, 1.13%) | **10,435 (10위, 2.23%)** |
| 급속 | 5,182 (4위, 9.23%) | **5,542 (3위, 9.87%)** — 한국전력 추월 |
| 집중도 GSMA / +광역시 | 49.65% / 62.58% | **47.94% / 61.46%** (희석 → **하락**) |
| M/S GSMA / +광역시 | 11.54% / 11.17% | **11.92% / 11.74%** |

- **집중도는 내려간다.** KEVIT 급속 360기 중 GSMA 가 84기뿐이라 수도권 비중이 희석된다.
  보고 시 "감소"로 오해되지 않게 설명 필요.
- 시장 총량(완속 468,574 / 급속 56,140)은 **불변**. 사업자 재분류일 뿐이다.
- 검증법: 매핑 전 raw 에서 `busiId=='ST'` 단독 집계가 기존 대시보드 값과 일치하는지 먼저 확인한
  뒤(=방법론 동일성 입증), KE 합산치를 비교한다. 실제로 SKEL 완속 5,291·집중도 49.65%·M/S 11.54%
  가 소수점까지 재현됨을 확인하고 진행했다.
