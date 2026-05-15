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
import time
import requests
import pandas as pd
from pathlib import Path
from datetime import date

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
                retries: int = 3, sleep_sec: int = 10) -> int:
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
            resp = requests.get(URL, params=params, timeout=30)
            df = pd.read_xml(io.BytesIO(resp.content)).fillna(0)
            return int(df["dtaCo"].dropna().values[1])
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(sleep_sec)
    raise RuntimeError(f"API 실패 sido={sido_code} fuel_ev={use_fuel_ev} prpos={prpos}: {last_err}")


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

    new_total_ev   = prev["total_ev"]   + total_ev
    new_pass_ev    = prev["passenger_ev"]  + passenger_ev
    new_comm_ev    = prev["commercial_ev"] + commercial_ev

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
