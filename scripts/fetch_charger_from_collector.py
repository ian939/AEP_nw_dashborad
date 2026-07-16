"""
충전기 raw 데이터를 ev-charger-collector 레포의 최신 스냅샷에서 가져온다.

직접 환경부 API(data.go.kr B552584)가 게이트웨이 장기 장애로 반복 실패하여,
같은 환경부 데이터를 매일 안정적으로 수집·커밋하는 별도 레포
`ian939/ev-charger-collector` 의 `latest_data.csv.gz` 를 대신 사용한다.

출력은 기존 `fetch_api.py` 와 동일한 `data/raw/raw_YYYYMMDD.parquet` (전 컬럼 문자열)
이라 하위 파이프라인(transform / build_snapshots / build_dashboard_data)은 무수정으로 동작한다.

Usage:
    python fetch_charger_from_collector.py
"""

import io
import sys
import json
import time
import random
from pathlib import Path
from datetime import datetime

import requests
import pandas as pd
import pyarrow.parquet as pq

# collector 의 롤링 최신 전국 스냅샷(gzip CSV, 안정 파일명, 매일 01:00 UTC 갱신).
SRC_URL = "https://raw.githubusercontent.com/ian939/ev-charger-collector/main/latest_data.csv.gz"
RETRY = 5
TIMEOUT = 120
MIN_ROWS = 400_000  # 절대 하한 — 이보다 적으면 부분/깨진 스냅샷으로 보고 실패.
MIN_RATIO_VS_PREV = 0.95  # 직전 스냅샷 대비 이 비율 미만이면 부분수집으로 보고 실패
                          # (절대 하한만으론 "약 3만대 누락(6%)" 같은 부분수집을 못 걸러서 추가).

# collector 가 앞단에 붙인 파생 컬럼 — 드롭해 원본 API raw 형태로 정규화한다.
# (하위의 검증된 zcode→지역, chgerType+output→완속/급속 유도 경로를 그대로 태우기 위함)
DROP_COLS = [
    "권역", "지역명", "운영기관(가공)", "NewbusiNm", "newtype",
    "Kind(new)", "KindDetail(new)", "calc_capacity",
]

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)


def download(url: str) -> bytes:
    """gz 스냅샷을 재시도/백오프와 함께 내려받는다."""
    last_err = None
    for attempt in range(RETRY):
        try:
            r = requests.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            return r.content
        except Exception as e:
            last_err = e
            if attempt == RETRY - 1:
                break
            wait = min(30, 2 ** attempt) + random.uniform(0, 2)
            print(f"  [download] attempt {attempt+1}/{RETRY} failed: {e}. retry in {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"collector 스냅샷 다운로드 실패(재시도 {RETRY}회 소진): {last_err}")


def prev_raw_rows(exclude_path: Path):
    """target 을 제외한 가장 최근(날짜 큰) raw_*.parquet 의 행 수를 메타데이터로 싸게 읽는다.
    없으면 None. 부분수집이 직전 스냅샷을 조용히 덮어쓰는 것을 막는 상대 게이트용."""
    cands = sorted(p for p in RAW_DIR.glob("raw_*.parquet") if p.resolve() != exclude_path.resolve())
    if not cands:
        return None, None
    prev = cands[-1]
    try:
        return prev.name, pq.ParquetFile(prev).metadata.num_rows
    except Exception:
        return prev.name, None


def main():
    today = datetime.now().strftime("%Y%m%d")
    out_path = RAW_DIR / f"raw_{today}.parquet"

    print(f"collector 충전기 스냅샷 수집 시작 ({today}).")
    print(f"소스: {SRC_URL}")

    blob = download(SRC_URL)
    print(f"다운로드 완료: {len(blob):,} bytes")

    # 전 컬럼 문자열로 읽어 chgerType('06')·zcode('11')·kind 코드의 앞자리 0/포맷을 보존한다.
    df = pd.read_csv(
        io.BytesIO(blob), compression="gzip", dtype=str, encoding="utf-8-sig"
    )
    print(f"원본 행 수: {len(df):,}, 컬럼 수: {len(df.columns)}")

    # 완전성 게이트 1) 절대 하한
    if len(df) < MIN_ROWS:
        print(f"ERROR: 행 수 {len(df):,} < 최소 {MIN_ROWS:,} — 부분/깨진 스냅샷으로 판단, 발행 중단.")
        sys.exit(1)

    # 완전성 게이트 2) 직전 스냅샷 대비 상대 하한 (부분수집이 완전 데이터를 덮어쓰는 것 방지)
    prev_name, prev_rows = prev_raw_rows(out_path)
    if prev_rows:
        floor = int(prev_rows * MIN_RATIO_VS_PREV)
        print(f"직전 스냅샷 {prev_name}: {prev_rows:,}행 (하한 {floor:,} = {MIN_RATIO_VS_PREV:.0%})")
        if len(df) < floor:
            print(f"ERROR: 행 수 {len(df):,} < 직전 대비 하한 {floor:,} — 부분수집 의심, 발행 중단. "
                  f"collector 재수집 후 재실행하세요.")
            sys.exit(1)

    # collector 파생 컬럼 드롭(존재하는 것만)
    drop = [c for c in DROP_COLS if c in df.columns]
    if drop:
        df = df.drop(columns=drop)
        print(f"파생 컬럼 드롭: {drop}")

    # busiId 는 하위 파이프라인의 유일한 하드 필수 컬럼 — 없으면 즉시 실패.
    if "busiId" not in df.columns:
        print("ERROR: busiId 컬럼이 없습니다 — 스냅샷 스키마 이상.")
        sys.exit(1)

    df.to_parquet(out_path, index=False)
    print(f"\n저장 완료: {out_path}")
    print(f"  - 행 수: {len(df):,}")
    print(f"  - 컬럼: {list(df.columns)}")

    meta = {
        "fetched_at": datetime.now().isoformat(),
        "row_count": int(len(df)),
        "prev_file": prev_name,
        "prev_row_count": prev_rows,
        "source": "ian939/ev-charger-collector",
        "source_file": "latest_data.csv.gz",
        "file": out_path.name,
    }
    (RAW_DIR / f"meta_{today}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
