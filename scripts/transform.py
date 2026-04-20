"""
원본 충전소 데이터 → 월별 스냅샷 JSON 변환

입력: data/raw/raw_YYYYMMDD.parquet (또는 .xlsx - 수동 업로드 케이스)
출력: data/monthly/YYYY-MM.json

처리 로직:
1. 원본 데이터를 Slow/Fast로 분리 (newtype 또는 chgerType 기준)
2. busiNm → 법인명 매핑 적용 (operator_mapping.json)
3. 지역별 그룹화 (GSMA / 광역시 / 기타)
4. 사업자별 × 지역별 교차집계
5. Top 5 M/S 계산

Usage:
    python transform.py                          # 최신 raw 파일 자동 탐색
    python transform.py --input path/to/file.xlsx  # 특정 파일 지정
    python transform.py --year-month 2026-03     # 저장 월 수동 지정
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"
MONTHLY_DIR = REPO_ROOT / "data" / "monthly"
MAPPING_FILE = REPO_ROOT / "data" / "operator_mapping.json"

MONTHLY_DIR.mkdir(parents=True, exist_ok=True)


def load_mapping() -> dict:
    """매핑 테이블 로드."""
    with open(MAPPING_FILE, encoding="utf-8") as f:
        return json.load(f)


def find_latest_raw() -> Path:
    """data/raw/ 에서 가장 최신 파일 찾기 (parquet 우선, xlsx fallback)."""
    candidates = list(RAW_DIR.glob("raw_*.parquet")) + list(RAW_DIR.glob("*.xlsx"))
    if not candidates:
        raise FileNotFoundError(f"{RAW_DIR} 에 raw 파일이 없습니다.")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def load_raw(path: Path) -> pd.DataFrame:
    """원본 데이터 로드 (parquet/xlsx 둘 다 지원).
    xlsx는 pandas.read_excel이 메모리를 많이 사용하므로 가능하면 parquet 사용 권장.
    """
    if path.suffix == ".parquet":
        df = pd.read_parquet(path)
    elif path.suffix == ".xlsx":
        # 대용량 xlsx는 openpyxl read_only + 청크 변환으로 처리
        print("  xlsx 읽는 중 (대용량일 경우 시간 소요)...")
        try:
            df = pd.read_excel(path, engine="openpyxl")
        except MemoryError:
            print("  [ERROR] 메모리 부족. xlsx를 먼저 parquet으로 변환 후 재시도하세요:")
            print("    python -c \"import pandas; pandas.read_excel('file.xlsx').to_parquet('file.parquet')\"")
            raise
    else:
        raise ValueError(f"지원하지 않는 파일 형식: {path.suffix}")
    return df


def normalize_busiNm(df: pd.DataFrame) -> pd.DataFrame:
    """busiNm 앞뒤 공백 제거 + 정규화."""
    df = df.copy()
    if "busiNm" not in df.columns:
        raise ValueError("busiNm 컬럼이 없습니다.")
    df["busiNm"] = df["busiNm"].astype(str).str.strip()
    return df


def apply_operator_mapping(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    """busiNm → 법인명(operator) 매핑 적용."""
    # 역매핑 테이블 생성: {원본busiNm: 통합법인명}
    reverse_map = {}
    for operator, variants in mapping["mapping"].items():
        for v in variants:
            reverse_map[v.strip()] = operator

    df = df.copy()
    df["operator"] = df["busiNm"].map(reverse_map).fillna(df["busiNm"])

    # 매핑 안 된 주요 업체 로그 (10개 이상인 것만)
    unmapped = df[~df["busiNm"].isin(reverse_map.keys())]
    if len(unmapped) > 0:
        top_unmapped = unmapped["busiNm"].value_counts().head(20)
        large_unmapped = top_unmapped[top_unmapped >= 10]
        if len(large_unmapped) > 0:
            print(f"\n[WARN] 매핑 안 된 busiNm 중 10건 이상인 것 {len(large_unmapped)}개:")
            for name, cnt in large_unmapped.items():
                print(f"  - {name}: {cnt:,}")
            print("→ operator_mapping.json 에 추가를 고려하세요.\n")

    return df


def determine_charger_type(df: pd.DataFrame) -> pd.DataFrame:
    """급속/완속 구분. newtype 컬럼 우선, 없으면 output(kW) 기준."""
    df = df.copy()
    if "newtype" in df.columns:
        df["charger_type"] = df["newtype"].map({"급속": "fast", "완속": "slow"})
    elif "output" in df.columns:
        # 50kW 이상 = 급속
        df["output"] = pd.to_numeric(df["output"], errors="coerce")
        df["charger_type"] = df["output"].apply(lambda x: "fast" if x >= 50 else "slow")
    else:
        raise ValueError("newtype 또는 output 컬럼이 필요합니다.")

    # 분류 실패는 제외
    df = df.dropna(subset=["charger_type"])
    return df


def determine_region(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    """지역명 → GSMA / 광역시 / 기타 분류."""
    df = df.copy()
    gsma = set(mapping["region_groups"]["GSMA"])
    metro = set(mapping["region_groups"]["광역시"])

    def classify(region):
        if region in gsma:
            return "GSMA"
        elif region in metro:
            return "광역시"
        return "기타"

    col = "지역명" if "지역명" in df.columns else "zcode"
    df["region_group"] = df[col].apply(classify) if col == "지역명" else "기타"
    return df


def aggregate_snapshot(df: pd.DataFrame, mapping: dict) -> dict:
    """월별 스냅샷 집계."""
    result = {}

    # ========== SLOW / FAST 전체 집계 ==========
    for ctype in ["slow", "fast"]:
        sub = df[df["charger_type"] == ctype]
        total = len(sub)

        # 사업자별 집계 (전체)
        by_operator = sub["operator"].value_counts().to_dict()

        # Top 5 추적 (고정)
        tracked_ops = mapping["dashboard_tracked"][ctype]
        top5 = [
            {"operator": op, "count": int(by_operator.get(op, 0))}
            for op in tracked_ops
        ]
        top5_total = sum(item["count"] for item in top5)
        top5_ms = (top5_total / total * 100) if total > 0 else 0

        result[ctype] = {
            "total": int(total),
            "top5": top5,
            "top5_total": int(top5_total),
            "top5_ms_pct": round(top5_ms, 2),
            "all_operators": {k: int(v) for k, v in by_operator.items() if v >= 100},
        }

    # ========== FAST 지역별 집계 (Top 3: 채비/SK일렉링크/이브이시스) ==========
    fast = df[df["charger_type"] == "fast"]
    fast_top3_ops = ["채비", "SK일렉링크", "이브이시스"]

    region_agg = {}
    for region in ["GSMA", "광역시", "GSMA+광역시", "전국"]:
        if region == "전국":
            region_df = fast
        elif region == "GSMA+광역시":
            region_df = fast[fast["region_group"].isin(["GSMA", "광역시"])]
        else:
            region_df = fast[fast["region_group"] == region]

        total = len(region_df)
        ops = {}
        for op in fast_top3_ops:
            cnt = int((region_df["operator"] == op).sum())
            ops[op] = {
                "count": cnt,
                "ms_pct": round(cnt / total * 100, 2) if total > 0 else 0,
            }
        region_agg[region] = {"total": int(total), "operators": ops}

    # 집중도(Concentration) 계산: 특정 사업자의 (해당지역 설치수 / 전국 설치수)
    concentration = {}
    for op in fast_top3_ops:
        op_total_nationwide = int((fast["operator"] == op).sum())
        concentration[op] = {
            "nationwide": op_total_nationwide,
            "GSMA_pct": round(
                region_agg["GSMA"]["operators"][op]["count"] / op_total_nationwide * 100, 2
            ) if op_total_nationwide > 0 else 0,
            "GSMA_plus_metro_pct": round(
                region_agg["GSMA+광역시"]["operators"][op]["count"] / op_total_nationwide * 100, 2
            ) if op_total_nationwide > 0 else 0,
        }

    result["fast_regional"] = region_agg
    result["fast_concentration"] = concentration

    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", help="원본 파일 경로 (생략 시 최신 파일 자동 탐색)")
    parser.add_argument("--year-month", help="저장 월 지정 (YYYY-MM, 생략 시 오늘 날짜의 월)")
    parser.add_argument("--dry-run", action="store_true",
                        help="저장 없이 요약만 출력 (로컬 테스트용)")
    args = parser.parse_args()

    # 입력 파일
    if args.input:
        input_path = Path(args.input)
    else:
        input_path = find_latest_raw()
    print(f"입력: {input_path}")

    # 저장 월
    if args.year_month:
        year_month = args.year_month
    else:
        year_month = datetime.now().strftime("%Y-%m")
    print(f"저장 월: {year_month}")
    if args.dry_run:
        print("[DRY-RUN] 파일 저장 없이 요약만 출력합니다.")

    # 처리 파이프라인
    mapping = load_mapping()
    df = load_raw(input_path)
    print(f"원본 행 수: {len(df):,}")

    df = normalize_busiNm(df)
    df = apply_operator_mapping(df, mapping)
    df = determine_charger_type(df)
    df = determine_region(df, mapping)

    snapshot = aggregate_snapshot(df, mapping)
    snapshot["_meta"] = {
        "year_month": year_month,
        "source_file": input_path.name,
        "processed_at": datetime.now().isoformat(),
        "raw_row_count": int(len(df)),
    }

    # 저장
    if not args.dry_run:
        out_path = MONTHLY_DIR / f"{year_month}.json"
        out_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n저장 완료: {out_path}")
    else:
        print(f"\n[DRY-RUN] 저장하지 않음. 실제 저장하려면 --dry-run 없이 실행하세요.")

    # 요약 출력
    print("\n=== 요약 ===")
    print(f"Slow 전체: {snapshot['slow']['total']:,}개")
    print(f"  Top 5: {snapshot['slow']['top5_total']:,} ({snapshot['slow']['top5_ms_pct']}%)")
    for item in snapshot['slow']['top5']:
        print(f"    - {item['operator']}: {item['count']:,}")
    print(f"Fast 전체: {snapshot['fast']['total']:,}개")
    print(f"  Top 5: {snapshot['fast']['top5_total']:,} ({snapshot['fast']['top5_ms_pct']}%)")
    for item in snapshot['fast']['top5']:
        print(f"    - {item['operator']}: {item['count']:,}")


if __name__ == "__main__":
    main()
