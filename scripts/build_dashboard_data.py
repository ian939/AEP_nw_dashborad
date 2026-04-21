"""
월별 스냅샷 JSON들을 병합하여 대시보드 시계열 JSON 생성

입력: data/monthly/*.json (월별 스냅샷)
     data/historical.json (PPT에서 추출한 과거 Jan '23 ~ Feb '26 데이터)
출력: data/dashboard-data.json (대시보드 index.html이 fetch하는 파일)

정책:
- 과거 데이터(historical.json)는 PPT 원본 유지 (재계산하지 않음)
- 새로 쌓이는 월별 스냅샷만 historical 뒤에 이어붙임
- 중복 월이 있으면 최신(monthly) 우선
- overview_5month는 최신 5개월로 자동 재계산

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
MAPPING_FILE = REPO_ROOT / "data" / "operator_mapping.json"
OUTPUT_FILE = REPO_ROOT / "data" / "dashboard-data.json"

# 대시보드 표시명 (operator_mapping 한국어 키 → 대시보드 영문 표시명)
DISPLAY_NAMES = {
    "GS차지비":         "GS CHARGEV",
    "파워큐브":          "PowerCube",
    "에버온":           "EverOn",
    "LG유플러스 볼트업":  "Volt-up",
    "플러그링크":         "Pluglink",
    "채비":             "Chaevi",
    "SK일렉링크":        "SKEL",
    "이브이시스":         "EVSIS",
    "휴맥스이브이":       "Humax+JES",
}


def month_to_label(ym: str) -> str:
    """2026-02 → Feb-26"""
    y, m = ym.split("-")
    names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    return f"{names[int(m)-1]}-{y[2:]}"


def load_historical() -> dict:
    if not HISTORICAL_FILE.exists():
        print(f"WARNING: {HISTORICAL_FILE} 이 없습니다.")
        return {"months": [], "slow_trend": {}, "fast_trend": {}, "concentration": {}, "market_share": {}}
    with open(HISTORICAL_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_mapping() -> dict:
    with open(MAPPING_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_monthly_snapshots() -> list[tuple[str, dict]]:
    snapshots = []
    for p in sorted(MONTHLY_DIR.glob("*.json")):
        ym = p.stem
        with open(p, encoding="utf-8") as f:
            snap = json.load(f)
        snapshots.append((ym, snap))
    return snapshots


def merge_into_dashboard(historical: dict, snapshots: list) -> dict:
    result = json.loads(json.dumps(historical))

    # 월별 총 포트 수 추적 (overview 재계산용)
    # historical overview_5month에서 기존 총계 추출
    month_totals = {}
    old_ov = historical.get("overview_5month", {})
    for i, m in enumerate(old_ov.get("months", [])):
        month_totals[m] = {
            "slow_K": old_ov["slow_total_K"][i],
            "fast_K": old_ov["fast_total_K"][i],
            "slow_ev": old_ov["slow_ev_per_port"][i],
            "fast_ev": old_ov["fast_ev_per_port"][i],
        }

    for ym, snap in snapshots:
        label = month_to_label(ym)

        if label in result.get("months", []):
            print(f"  [skip] {label} - historical에 이미 존재")
            continue

        result["months"].append(label)
        if "months_fast_extended" in result:
            result["months_fast_extended"].append(label)

        for op_data in snap["slow"]["top5"]:
            op = op_data["operator"]
            result["slow_trend"].setdefault(op, []).append(op_data["count"])

        for op_data in snap["fast"]["top5"]:
            op = op_data["operator"]
            result["fast_trend"].setdefault(op, []).append(op_data["count"])

        for op, conc in snap.get("fast_concentration", {}).items():
            result["concentration"].setdefault(op, {"GSMA": [], "GSMA_plus_metro": []})
            result["concentration"][op]["GSMA"].append(conc["GSMA_pct"])
            result["concentration"][op]["GSMA_plus_metro"].append(conc["GSMA_plus_metro_pct"])

        for region in ["GSMA", "GSMA+광역시"]:
            region_key = "GSMA" if region == "GSMA" else "GSMA_plus_metro"
            for op, data in snap["fast_regional"][region]["operators"].items():
                result["market_share"].setdefault(op, {"GSMA": [], "GSMA_plus_metro": []})
                result["market_share"][op][region_key].append(data["ms_pct"])

        # 총계 등록 (EV per port는 API에서 수집 불가 → None으로 처리 후 보간)
        month_totals[label] = {
            "slow_K": round(snap["slow"]["total"] / 1000, 1),
            "fast_K": round(snap["fast"]["total"] / 1000, 1),
            "slow_ev": None,
            "fast_ev": None,
        }

        print(f"  [append] {label}")

    # EV per port: None인 월은 직전 알려진 값으로 보간
    last_slow_ev = None
    last_fast_ev = None
    for m in result["months"]:
        t = month_totals.get(m)
        if t is None:
            continue
        if t["slow_ev"] is not None:
            last_slow_ev = t["slow_ev"]
        else:
            t["slow_ev"] = last_slow_ev
        if t["fast_ev"] is not None:
            last_fast_ev = t["fast_ev"]
        else:
            t["fast_ev"] = last_fast_ev

    # overview_5month 재계산 (최신 5개월)
    mapping = load_mapping()
    slow_tracked = mapping["dashboard_tracked"]["slow"]
    fast_tracked = mapping["dashboard_tracked"]["fast"]
    result["overview_5month"] = build_overview_5month(
        result, month_totals, slow_tracked, fast_tracked
    )

    result["_meta"] = {
        "generated_at": datetime.now().isoformat(),
        "total_months": len(result["months"]),
        "latest_month": result["months"][-1] if result["months"] else None,
        "historical_months": len(historical.get("months", [])),
        "new_months_added": len(snapshots),
    }

    return result


def build_overview_5month(result: dict, month_totals: dict,
                          slow_tracked: list, fast_tracked: list) -> dict:
    """최신 5개월 데이터로 overview_5month 재계산."""
    all_months = result["months"]
    # fast_trend는 months_fast_extended 기준 인덱싱 (slow months보다 12개월 앞서 시작)
    fast_months = result.get("months_fast_extended", all_months)
    last_5 = all_months[-5:]

    slow_table  = {DISPLAY_NAMES.get(op, op): [] for op in slow_tracked}
    fast_table  = {DISPLAY_NAMES.get(op, op): [] for op in fast_tracked}
    slow_ms_line = {DISPLAY_NAMES.get(op, op): [] for op in slow_tracked}
    fast_ms_line = {DISPLAY_NAMES.get(op, op): [] for op in fast_tracked}
    slow_total_K = []
    fast_total_K = []
    slow_ev_list = []
    fast_ev_list = []
    slow_ms_pct  = []
    fast_ms_pct  = []

    for m in last_5:
        slow_idx = all_months.index(m)
        fast_idx = fast_months.index(m) if m in fast_months else -1
        mt = month_totals.get(m, {})
        s_K = mt.get("slow_K") or 0
        f_K = mt.get("fast_K") or 0
        slow_total_K.append(round(s_K, 1))
        fast_total_K.append(round(f_K, 1))
        slow_ev_list.append(mt.get("slow_ev"))
        fast_ev_list.append(mt.get("fast_ev"))

        s_top5_total = 0
        for op in slow_tracked:
            disp = DISPLAY_NAMES.get(op, op)
            trend = result["slow_trend"].get(op, [])
            count = int(trend[slow_idx]) if slow_idx < len(trend) and trend[slow_idx] is not None else 0
            slow_table[disp].append(count)
            ms = round(count / (s_K * 1000) * 100, 1) if s_K > 0 else 0
            slow_ms_line[disp].append(ms)
            s_top5_total += count
        slow_ms_pct.append(round(s_top5_total / (s_K * 1000) * 100, 1) if s_K > 0 else 0)

        f_top5_total = 0
        for op in fast_tracked:
            disp = DISPLAY_NAMES.get(op, op)
            trend = result["fast_trend"].get(op, [])
            count = int(trend[fast_idx]) if fast_idx >= 0 and fast_idx < len(trend) and trend[fast_idx] is not None else 0
            fast_table[disp].append(count)
            ms = round(count / (f_K * 1000) * 100, 1) if f_K > 0 else 0
            fast_ms_line[disp].append(ms)
            f_top5_total += count
        fast_ms_pct.append(round(f_top5_total / (f_K * 1000) * 100, 1) if f_K > 0 else 0)

    return {
        "months": last_5,
        "slow_total_K": slow_total_K,
        "fast_total_K": fast_total_K,
        "slow_ev_per_port": slow_ev_list,
        "fast_ev_per_port": fast_ev_list,
        "slow_top5_table": slow_table,
        "slow_top5_ms_pct": slow_ms_pct,
        "fast_top5_table": fast_table,
        "fast_top5_ms_pct": fast_ms_pct,
        "slow_top5_ms_line": slow_ms_line,
        "fast_top5_ms_line": fast_ms_line,
    }


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
    ov = merged.get("overview_5month", {})
    print(f"overview 기간: {ov.get('months', [])}")


if __name__ == "__main__":
    main()
