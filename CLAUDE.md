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
