"""
전월 EV 신규등록 대수 수집 (GitHub Actions 자동 실행용)

- data.go.kr 신규등록차량 API 호출 (17개 시도 × 조건별 합산 → 전국 합계)
- 수집 대상: 전체차량, 전기차량, 비영업EV(승용), 영업EV(상용)
- 결과: data/ev_registration/YYYY-MM.json + cumulative.json 갱신

환경변수:
  EV_REG_API_KEY: data.go.kr API 서비스키 (GitHub Secret)
  TARGET_YYYYMM:  강제 지정 시 사용 (생략 시 전월 자동 감지)
"""

import os
import io
import json
import sys
import time
import requests
import pandas as pd
from pathlib import Path
from datetime import date

# Windows 기본 콘솔 인코딩(cp949)에서 '—' '→' 같은 문자가 UnicodeEncodeError 를 내
# 멱등 재실행 경로가 죽던 문제 방지. CI(리눅스)는 이미 UTF-8 이라 무영향.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
EV_REG_DIR = REPO_ROOT / "data" / "ev_registration"
EV_REG_DIR.mkdir(parents=True, exist_ok=True)

URL = "https://apis.data.go.kr/B553881/newRegistlnfoService_02/getnewRegistlnfoService02"

SIDO_CODES = {
    '1':'서울특별시', '2':'부산광역시', '3':'대구광역시', '4':'인천광역시',
    '5':'광주광역시', '6':'대전광역시', '7':'울산광역시', '8':'세종특별자치시',
    '9':'경기도', '10':'강원도', '11':'충청북도', '12':'충청남도',
    '13':'전라북도', '14':'전라남도', '15':'경상북도', '16':'경상남도',
    '17':'제주특별자치도',
}


def get_target_ym() -> tuple[str, str]:
    """전월 연(str), 월(str) 반환. TARGET_YYYYMM 환경변수 우선."""
    override = os.environ.get("TARGET_YYYYMM", "").strip()
    if override and len(override) == 6:
        return override[:4], override[4:]
    today = date.today()
    if today.month == 1:
        return str(today.year - 1), "12"
    return str(today.year), f"{today.month - 1:02d}"


def fetch_count(yr: str, month: str, sido_code: str,
                use_fuel_ev: bool = False, prpos: str | None = None,
                retries: int = 5, timeout: int = 60) -> int:
    """KOTSA 단건 조회. 실패 시 지수 백오프(5→10→20→40초)로 재시도.

    타임아웃 60초/5회는 GitHub 러너에서 관측된 간헐적 연결 지연을 흡수하기 위한 값이다.
    로컬 정상 응답은 0.5초 수준이므로 정상 경로의 비용 증가는 없다.
    """
    api_key = os.environ.get("EV_REG_API_KEY", "")
    params = {
        "serviceKey":    api_key,
        "registYy":      yr,
        "registMt":      month,
        "registGrcCode": sido_code,
    }
    if use_fuel_ev:
        params["useFuelCode"] = "5"
    if prpos is not None:
        params["prposSeNm"] = prpos

    last_err = None
    for attempt in range(retries):
        try:
            resp = requests.get(URL, params=params, timeout=timeout)
            df = pd.read_xml(io.BytesIO(resp.content)).fillna(0)
            return int(df["dtaCo"].dropna().values[1])
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                backoff = 5 * (2 ** attempt)   # 5, 10, 20, 40
                print(f"    [retry {attempt+1}/{retries-1}] sido={sido_code} "
                      f"{type(e).__name__} → {backoff}s 후 재시도", flush=True)
                time.sleep(backoff)
    raise RuntimeError(f"API 실패 sido={sido_code} fuel_ev={use_fuel_ev} prpos={prpos}: {last_err}")


def preflight() -> None:
    """85회 본수집 전 단건으로 연결을 확인한다.

    과거 실패는 전부 첫 호출부터 ConnectTimeoutError 였다(러너→apis.data.go.kr 경로 문제).
    본수집에 들어가면 시도마다 재시도를 반복해 시간만 쓰고 전량 폐기되므로,
    여기서 먼저 끊고 원인을 로그에 남긴다.
    """
    api_key = os.environ.get("EV_REG_API_KEY", "")
    if not api_key:
        raise RuntimeError("EV_REG_API_KEY 가 비어 있습니다 (GitHub Secret 확인).")
    try:
        resp = requests.get(URL, params={
            "serviceKey": api_key, "registYy": "2026",
            "registMt": "06", "registGrcCode": "1",
        }, timeout=60)
    except Exception as e:
        raise RuntimeError(
            f"[preflight] KOTSA 연결 실패: {type(e).__name__}: {e}\n"
            "  → 러너에서 apis.data.go.kr 로 나가는 경로 문제일 가능성이 높습니다.\n"
            "    (같은 시각 로컬에서 정상 응답하면 공급자 장애가 아님)\n"
            "  → 회복 후 재실행하거나, 로컬에서 TARGET_YYYYMM 지정 실행 후 커밋하세요."
        ) from e

    body = resp.text
    if "SERVICETIMEOUT_ERROR" in body or "<errMsg>" in body:
        raise RuntimeError(
            f"[preflight] KOTSA 서비스 오류 응답(HTTP {resp.status_code}): {body[:200]}\n"
            "  → 공급자 백엔드 장애입니다. 회복 후 재실행하세요."
        )
    print(f"[preflight] KOTSA 연결 정상 (HTTP {resp.status_code})")


def collect_national(yr: str, month: str,
                     use_fuel_ev: bool = False, prpos: str | None = None,
                     label: str = "") -> int:
    """17개 시도 합산 → 전국 총계"""
    total = 0
    for i, (code, name) in enumerate(SIDO_CODES.items(), 1):
        cnt = fetch_count(yr, month, code, use_fuel_ev=use_fuel_ev, prpos=prpos)
        total += cnt
        print(f"  [{i:02d}/17] {name}: {cnt:,}  (누계 {total:,})")
    return total


def main():
    yr, month = get_target_ym()
    ym = f"{yr}-{month}"
    print(f"\n=== EV 신규등록 수집: {ym} ===")
    preflight()

    print("\n[1/4] 전체 차량 신규등록")
    total_vehicles = collect_national(yr, month, label="전체차량")

    print("\n[2/4] 전기차 전체")
    total_ev = collect_national(yr, month, use_fuel_ev=True, label="전기차량")

    print("\n[3/4] 전기차 비영업 (승용: 자가+관용)")
    ev_own = collect_national(yr, month, use_fuel_ev=True, prpos="1", label="EV_자가")
    ev_gov = collect_national(yr, month, use_fuel_ev=True, prpos="3", label="EV_관용")
    passenger_ev = ev_own + ev_gov  # 비영업 = 자가 + 관용

    print("\n[4/4] 전기차 영업 (상용)")
    commercial_ev = collect_national(yr, month, use_fuel_ev=True, prpos="2", label="EV_영업")

    print(f"\n[결과] 총차량={total_vehicles:,} | 총EV={total_ev:,} | 승용EV={passenger_ev:,} | 상용EV={commercial_ev:,}")

    # 누적 파일 읽기
    cum_file = EV_REG_DIR / "cumulative.json"
    if not cum_file.exists():
        raise FileNotFoundError(
            f"{cum_file} 이 없습니다. "
            "data/ev_registration/cumulative.json seed 파일을 먼저 생성하세요."
        )
    prev = json.loads(cum_file.read_text(encoding="utf-8"))
    print(f"\n[누적 이전] {prev['year_month']} → total_ev={prev['total_ev']:,}")

    # 멱등성 가드: 같은 ym으로 재실행 시 이전 월의 누적을 재구성 (중복합산 방지)
    if prev["year_month"] == ym:
        monthly_path_existing = EV_REG_DIR / f"{ym}.json"
        if monthly_path_existing.exists():
            prev_monthly = json.loads(monthly_path_existing.read_text(encoding="utf-8"))
            print(f"[idempotent] {ym} 이미 누적에 반영됨 — 이전 월 기준으로 되돌려 재계산")
            prev_total_ev   = prev["total_ev"]      - prev_monthly["monthly_new"]["total_ev"]
            prev_pass_ev    = prev["passenger_ev"]  - prev_monthly["monthly_new"]["passenger_ev"]
            prev_comm_ev    = prev["commercial_ev"] - prev_monthly["monthly_new"]["commercial_ev"]
        else:
            prev_total_ev = prev["total_ev"]
            prev_pass_ev  = prev["passenger_ev"]
            prev_comm_ev  = prev["commercial_ev"]
    else:
        prev_total_ev = prev["total_ev"]
        prev_pass_ev  = prev["passenger_ev"]
        prev_comm_ev  = prev["commercial_ev"]

    new_total_ev   = prev_total_ev + total_ev
    new_pass_ev    = prev_pass_ev  + passenger_ev
    new_comm_ev    = prev_comm_ev  + commercial_ev

    print(f"[누적 갱신] {ym} → total_ev={new_total_ev:,}")

    # 월별 스냅샷 저장
    monthly = {
        "year_month": ym,
        "monthly_new": {
            "total_vehicles": total_vehicles,
            "total_ev":       total_ev,
            "passenger_ev":   passenger_ev,
            "commercial_ev":  commercial_ev,
        },
        "cumulative": {
            "total_ev":       new_total_ev,
            "passenger_ev":   new_pass_ev,
            "commercial_ev":  new_comm_ev,
        },
    }
    monthly_path = EV_REG_DIR / f"{ym}.json"
    monthly_path.write_text(json.dumps(monthly, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {monthly_path}")

    # 누적 갱신
    new_cum = {
        "year_month":    ym,
        "total_ev":      new_total_ev,
        "passenger_ev":  new_pass_ev,
        "commercial_ev": new_comm_ev,
    }
    cum_file.write_text(json.dumps(new_cum, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"누적 갱신: {cum_file}")


if __name__ == "__main__":
    main()
