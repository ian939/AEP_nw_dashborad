"""
월별 스냅샷 JSON들을 병합하여 대시보드 시계열 JSON 생성

입력: data/monthly/*.json (월별 스냅샷)
     data/historical.json (PPT에서 추출한 과거 Jan '23 ~ Feb '26 데이터)
출력: data/dashboard-data.json (대시보드 AEP Dashboard.html이 fetch하는 파일)

정책:
- 과거 데이터(historical.json)는 PPT 원본 유지 (재계산하지 않음)
- 새로 쌓이는 월별 스냅샷만 historical 뒤에 이어붙임
- 중복 월이 있으면 최신(monthly) 우선
- overview_5month는 최신 5개월로 자동 재계산

Usage:
    python build_dashboard_data.py
"""

import json
import re
import sys
from pathlib import Path
from datetime import datetime

REPO_ROOT = Path(__file__).resolve().parent.parent
MONTHLY_DIR = REPO_ROOT / "data" / "monthly"
HISTORICAL_FILE = REPO_ROOT / "data" / "historical.json"
MAPPING_FILE = REPO_ROOT / "data" / "operator_mapping.json"
OUTPUT_FILE = REPO_ROOT / "data" / "dashboard-data.json"
EV_REG_DIR = REPO_ROOT / "data" / "ev_registration"

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


def find_latest_ev_reg(target_ym: str) -> Path | None:
    """target_ym 이하 중 가장 최근의 ev_registration 파일 경로를 반환.
    스냅샷 월(예: 2026-05)에 대응하는 ev_reg 파일이 없을 때,
    가장 최근에 보고된 EV 누적치(예: 2026-04.json)를 사용하기 위함."""
    candidates = sorted(
        f for f in EV_REG_DIR.glob("*.json")
        if f.stem != "cumulative"
        and re.match(r"^\d{4}-\d{2}$", f.stem)
        and f.stem <= target_ym
    )
    return candidates[-1] if candidates else None


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

    # Top 10 추적 사업자 (landscape 탭 추세 그래프용)
    mapping = load_mapping()
    slow_top10 = mapping.get("dashboard_top10", {}).get("slow", [])
    fast_top10 = mapping.get("dashboard_top10", {}).get("fast", [])

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
        already_in_historical = label in result.get("months", [])

        if already_in_historical:
            print(f"  [skip] {label} - historical에 이미 존재 (차충비만 재계산)")
        else:
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

            # Top 10 trend — all_operators에서 조회 (없으면 None)
            slow_all = snap["slow"].get("all_operators", {})
            fast_all = snap["fast"].get("all_operators", {})
            result.setdefault("slow_trend_top10", {})
            result.setdefault("fast_trend_top10", {})
            for op in slow_top10:
                result["slow_trend_top10"].setdefault(op, []).append(slow_all.get(op))
            for op in fast_top10:
                result["fast_trend_top10"].setdefault(op, []).append(fast_all.get(op))

            month_totals[label] = {
                "slow_K": round(snap["slow"]["total"] / 1000, 1),
                "fast_K": round(snap["fast"]["total"] / 1000, 1),
                "slow_ev": None,
                "fast_ev": None,
            }
            print(f"  [append] {label}")

        # EV 신규등록 데이터로 차충비(EVs/port) 계산 — historical 월 포함
        # 스냅샷 ym과 동일한 ev_reg가 없으면 가장 최근(<=ym) 파일을 fallback으로 사용
        # (예: 5월 스냅샷 + 4월 신규등록 → 4월말 누적으로 5월 차충비 산정)
        ev_reg_file = find_latest_ev_reg(ym)
        if ev_reg_file is not None:
            ev_reg = json.loads(ev_reg_file.read_text(encoding="utf-8"))
            cum_ev = ev_reg["cumulative"]["total_ev"]
            slow_total = snap["slow"]["total"]
            fast_total = snap["fast"]["total"]
            slow_ev = round(cum_ev / slow_total, 2) if slow_total > 0 else None
            fast_ev = round(cum_ev / fast_total, 2) if fast_total > 0 else None
            print(f"  [ev_reg] {ym} 누적EV={cum_ev:,} slow_ev={slow_ev} fast_ev={fast_ev}")

            # month_totals 덮어쓰기 (historical도 포함)
            if label not in month_totals:
                month_totals[label] = {
                    "slow_K": round(snap["slow"]["total"] / 1000, 1),
                    "fast_K": round(snap["fast"]["total"] / 1000, 1),
                    "slow_ev": None,
                    "fast_ev": None,
                }
            month_totals[label]["slow_ev"] = slow_ev
            month_totals[label]["fast_ev"] = fast_ev

            # ev_market 배열 업데이트 (신규 월만)
            if not already_in_historical:
                result = _append_ev_market(result, label, ym, ev_reg, snap)

    # EV 신규등록 파일을 직접 순회해 ev_sales_* 보장
    # (스냅샷 월과 등록 월이 다를 때, 또는 historical에 없는 월을 누적)
    result = append_ev_sales_from_registrations(result)

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


def _append_ev_market(result: dict, label: str, ym: str,
                       ev_reg: dict, snap: dict) -> dict:
    """ev_market 배열에 신규 월 데이터 추가."""
    ev = result.get("ev_market")
    if ev is None:
        return result

    # 중복 방지
    if label in ev.get("ev_months", []):
        return result

    cum = ev_reg["cumulative"]
    monthly = ev_reg["monthly_new"]

    pass_K  = round(cum["passenger_ev"]  / 1000, 3)
    comm_K  = round(cum["commercial_ev"] / 1000, 3)
    total_K = round(cum["total_ev"]      / 1000, 3)

    # 누적 EV 시계열 — 새 월 append 전에 직전 penetration으로 추정값 계산
    # (cum_veh_prev = prev_cum_ev / prev_pen → cum_veh_new = + monthly_new_vehicles)
    monthly_new_veh = monthly.get("total_vehicles", 0)

    def _last_nonnull(arr):
        for v in reversed(arr or []):
            if v is not None:
                return v
        return None

    def _estimate_total_pen(pen_arr, K_arr, new_cum_K, monthly_new_veh):
        if monthly_new_veh <= 0:
            return None
        for i in range(len(pen_arr) - 1, -1, -1):
            if pen_arr[i] is not None and i < len(K_arr) and K_arr[i]:
                prev_total_veh = (K_arr[i] * 1000) / (pen_arr[i] / 100)
                new_total_veh = prev_total_veh + monthly_new_veh
                return round((new_cum_K * 1000) / new_total_veh * 100, 2)
        return None

    new_pen_total = _estimate_total_pen(
        ev.get("ev_total_penetration_pct", []),
        ev.get("ev_total_K", []),
        total_K,
        monthly_new_veh,
    )
    # 승용/상용 penetration: monthly_new 차량의 승용·상용 분할을 알 수 없으므로
    # 직전 ratio(승용 pen ÷ 전체 pen)로 스케일하여 추세 유지
    last_total_pen = _last_nonnull(ev.get("ev_total_penetration_pct", []))
    last_pass_pen  = _last_nonnull(ev.get("ev_passenger_penetration_pct", []))
    last_comm_pen  = _last_nonnull(ev.get("ev_commercial_penetration_pct", []))
    if new_pen_total is not None and last_total_pen:
        ratio_p = (last_pass_pen / last_total_pen) if last_pass_pen else None
        ratio_c = (last_comm_pen / last_total_pen) if last_comm_pen else None
        new_pen_pass = round(new_pen_total * ratio_p, 2) if ratio_p else None
        new_pen_comm = round(new_pen_total * ratio_c, 2) if ratio_c else None
    else:
        new_pen_pass = new_pen_comm = None

    ev["ev_months"].append(label)
    ev["ev_passenger_K"].append(pass_K)
    ev["ev_commercial_K"].append(comm_K)
    ev["ev_total_K"].append(total_K)
    ev["ev_total_penetration_pct"].append(new_pen_total)
    ev["ev_passenger_penetration_pct"].append(new_pen_pass)
    ev["ev_commercial_penetration_pct"].append(new_pen_comm)

    # 월간 판매(ev_sales_*)는 _append_ev_sales 별도 패스에서 처리.
    # (스냅샷 ym ≠ 등록 ym 인 경우 대비 — 5월 스냅샷 + 4월 신규등록 등)

    # ev_table_precise: 마지막 13개월 재계산
    months_13 = ev["ev_months"][-13:]
    precise_months = ev.get("ev_table_precise", {}).get("months", [])
    for m in months_13:
        if m not in precise_months:
            idx = ev["ev_months"].index(m)
            ev.setdefault("ev_table_precise", {"months":[], "total":[], "passenger":[], "commercial":[]})
            ev["ev_table_precise"]["months"].append(m)
            ev["ev_table_precise"]["total"].append(round(ev["ev_total_K"][idx] * 1000))
            ev["ev_table_precise"]["passenger"].append(round(ev["ev_passenger_K"][idx] * 1000))
            ev["ev_table_precise"]["commercial"].append(round(ev["ev_commercial_K"][idx] * 1000))
    # 최근 13개월만 유지
    for key in ["months", "total", "passenger", "commercial"]:
        ev["ev_table_precise"][key] = ev["ev_table_precise"][key][-13:]

    result["ev_market"] = ev
    print(f"  [ev_market] {label} 추가 완료 (누적EV {total_K}K)")
    return result


def append_ev_sales_from_registrations(result: dict) -> dict:
    """data/ev_registration/*.json 전체를 순회하여 ev_sales_* 배열에 누락 월을 추가.
    스냅샷 월(ym in monthly/)과 등록 월(ym in ev_registration/)이 다를 수 있으므로
    등록 파일을 단일 소스로 두고 한 번에 처리한다."""
    ev = result.get("ev_market")
    if ev is None:
        return result

    ev_reg_files = sorted(
        f for f in EV_REG_DIR.glob("*.json")
        if f.stem != "cumulative"
        and re.match(r"^\d{4}-\d{2}$", f.stem)
    )

    for ev_file in ev_reg_files:
        ev_reg = json.loads(ev_file.read_text(encoding="utf-8"))
        reg_label = month_to_label(ev_reg["year_month"])
        if reg_label in ev.get("ev_sales_months", []):
            continue
        monthly = ev_reg.get("monthly_new", {})
        total_veh   = monthly.get("total_vehicles", 0)
        total_ev_mo = monthly.get("total_ev", 0)
        pass_ev_mo  = monthly.get("passenger_ev", 0)
        comm_ev_mo  = monthly.get("commercial_ev", 0)
        other_mo    = (total_veh - total_ev_mo) if total_veh else None
        share_pct   = round(total_ev_mo / total_veh * 100, 2) if total_veh else None

        ev.setdefault("ev_sales_months", []).append(reg_label)
        ev.setdefault("ev_sales_total", []).append(total_ev_mo)
        ev.setdefault("ev_sales_passenger", []).append(pass_ev_mo)
        ev.setdefault("ev_sales_commercial", []).append(comm_ev_mo)
        ev.setdefault("ev_sales_other", []).append(other_mo)
        ev.setdefault("ev_sales_total_vehicles", []).append(total_veh)
        ev.setdefault("ev_sales_share_pct", []).append(share_pct)
        ev.setdefault("ev_sales_passenger_share_pct", []).append(
            round(pass_ev_mo / total_veh * 100, 2) if total_veh else None
        )
        ev.setdefault("ev_sales_commercial_share_pct", []).append(
            round(comm_ev_mo / total_veh * 100, 2) if total_veh else None
        )
        print(f"  [ev_sales] {reg_label} 추가 (monthly_new EV {total_ev_mo:,} / 전체차량 {total_veh:,})")

    result["ev_market"] = ev
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
