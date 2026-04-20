"""
월별 스냅샷 JSON들을 병합하여 대시보드 시계열 JSON 생성

입력: data/monthly/*.json (월별 스냅샷)
     data/historical.json (PPT에서 추출한 과거 Jan '23 ~ Feb '26 데이터)
출력: data/dashboard-data.json (대시보드 index.html이 fetch하는 파일)

정책:
- 과거 데이터(historical.json)는 PPT 원본 유지 (재계산하지 않음)
- 새로 쌓이는 월별 스냅샷만 historical 뒤에 이어붙임
- 중복 월이 있으면 최신(monthly) 우선

Usage:
    python build_dashboard_data.py
"""

import json
import sys
from pathlib import Path
from datetime import datetime

REPO_ROOT = Path(__file__).resolve().parent.parent
MONTHLY_DIR = REPO_ROOT / "data" / "monthly"
HISTORICAL_FILE = REPO_ROOT / "data" / "historical.json"
OUTPUT_FILE = REPO_ROOT / "data" / "dashboard-data.json"


def month_to_label(ym: str) -> str:
    """2026-02 → Feb-26"""
    y, m = ym.split("-")
    names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    return f"{names[int(m)-1]}-{y[2:]}"


def load_historical() -> dict:
    """PPT 기반 과거 데이터 로드."""
    if not HISTORICAL_FILE.exists():
        print(f"WARNING: {HISTORICAL_FILE} 이 없습니다. 빈 historical로 시작합니다.")
        return {"months": [], "slow_trend": {}, "fast_trend": {}, "concentration": {}, "market_share": {}}
    with open(HISTORICAL_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_monthly_snapshots() -> list[tuple[str, dict]]:
    """data/monthly/*.json 을 모두 로드, (year_month, snapshot) 리스트 반환."""
    snapshots = []
    for p in sorted(MONTHLY_DIR.glob("*.json")):
        ym = p.stem  # e.g., "2026-03"
        with open(p, encoding="utf-8") as f:
            snap = json.load(f)
        snapshots.append((ym, snap))
    return snapshots


def merge_into_dashboard(historical: dict, snapshots: list) -> dict:
    """과거 데이터에 월별 스냅샷을 이어붙이기."""
    result = json.loads(json.dumps(historical))  # deep copy

    for ym, snap in snapshots:
        label = month_to_label(ym)

        # 이미 historical에 있는 월이면 skip (historical 원본 유지)
        if label in result.get("months", []):
            print(f"  [skip] {label} - historical에 이미 존재")
            continue

        result["months"].append(label)

        # Slow trend 업데이트 (Top 5 고정)
        for op_data in snap["slow"]["top5"]:
            op = op_data["operator"]
            result["slow_trend"].setdefault(op, []).append(op_data["count"])

        # Fast trend 업데이트
        for op_data in snap["fast"]["top5"]:
            op = op_data["operator"]
            result["fast_trend"].setdefault(op, []).append(op_data["count"])

        # Concentration (Fast Top 3)
        for op, conc in snap.get("fast_concentration", {}).items():
            result["concentration"].setdefault(op, {"GSMA": [], "GSMA_plus_metro": []})
            result["concentration"][op]["GSMA"].append(conc["GSMA_pct"])
            result["concentration"][op]["GSMA_plus_metro"].append(conc["GSMA_plus_metro_pct"])

        # Market Share
        for region in ["GSMA", "GSMA+광역시"]:
            region_key = "GSMA" if region == "GSMA" else "GSMA_plus_metro"
            for op, data in snap["fast_regional"][region]["operators"].items():
                result["market_share"].setdefault(op, {"GSMA": [], "GSMA_plus_metro": []})
                result["market_share"][op][region_key].append(data["ms_pct"])

        print(f"  [append] {label}")

    result["_meta"] = {
        "generated_at": datetime.now().isoformat(),
        "total_months": len(result["months"]),
        "latest_month": result["months"][-1] if result["months"] else None,
        "historical_months": len(historical.get("months", [])),
        "new_months_added": len(snapshots),
    }

    return result


def main():
    print("대시보드 데이터 빌드 시작")
    historical = load_historical()
    print(f"Historical 월 수: {len(historical.get('months', []))}")

    snapshots = load_monthly_snapshots()
    print(f"월별 스냅샷 수: {len(snapshots)}")

    merged = merge_into_dashboard(historical, snapshots)

    OUTPUT_FILE.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n저장: {OUTPUT_FILE}")
    print(f"총 월 수: {len(merged['months'])}")
    if merged["months"]:
        print(f"기간: {merged['months'][0]} ~ {merged['months'][-1]}")


if __name__ == "__main__":
    main()
