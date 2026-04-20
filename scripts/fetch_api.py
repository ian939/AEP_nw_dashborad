"""
환경부 공공 OpenAPI로부터 전기차 충전소 데이터 수집

API: 한국환경공단_전기자동차 충전소 정보
https://www.data.go.kr/data/15076352/openapi.do

페이지당 9999개 제한이 있어서 페이징 처리 필요.
전체 ~50만건 수집에 수십분 소요 가능.

Usage:
    python fetch_api.py
    # 환경변수 MOE_API_KEY 필요 (GitHub Secrets에 등록)
"""

import os
import sys
import time
import json
from pathlib import Path
from datetime import datetime
from xml.etree import ElementTree as ET

import requests
import pandas as pd

API_KEY = os.environ.get("MOE_API_KEY")
if not API_KEY:
    print("ERROR: MOE_API_KEY 환경변수가 설정되지 않았습니다.")
    print("GitHub Actions → Secrets 에 MOE_API_KEY 를 등록하세요.")
    sys.exit(1)

API_URL = "http://apis.data.go.kr/B552584/EvCharger/getChargerInfo"
PAGE_SIZE = 9999  # API 최대값
MAX_PAGES = 100   # 안전 장치 (~99만건까지)
RETRY = 3
TIMEOUT = 60

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)


def fetch_page(page_no: int) -> tuple[list[dict], int]:
    """단일 페이지 수집. (rows, totalCount) 반환."""
    params = {
        "serviceKey": API_KEY,
        "pageNo": page_no,
        "numOfRows": PAGE_SIZE,
        "dataType": "XML",
    }

    last_err = None
    for attempt in range(RETRY):
        try:
            r = requests.get(API_URL, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            root = ET.fromstring(r.content)

            # 에러 체크
            result_code = root.findtext(".//resultCode")
            if result_code and result_code != "00":
                msg = root.findtext(".//resultMsg") or "unknown"
                raise RuntimeError(f"API error {result_code}: {msg}")

            total = int(root.findtext(".//totalCount") or "0")
            rows = []
            for item in root.findall(".//item"):
                row = {child.tag: (child.text or "") for child in item}
                rows.append(row)
            return rows, total
        except Exception as e:
            last_err = e
            wait = 2 ** attempt
            print(f"  [page {page_no}] attempt {attempt+1}/{RETRY} failed: {e}. retry in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"page {page_no} failed after {RETRY} retries: {last_err}")


def main():
    today = datetime.now().strftime("%Y%m%d")
    out_path = RAW_DIR / f"raw_{today}.parquet"

    print(f"환경부 API 수집 시작 ({today})")
    print(f"출력: {out_path}")

    all_rows = []
    page = 1
    total_expected = None

    while page <= MAX_PAGES:
        print(f"페이지 {page} 수집 중...")
        rows, total = fetch_page(page)
        if total_expected is None:
            total_expected = total
            print(f"전체 예상 건수: {total:,}")

        if not rows:
            print(f"페이지 {page} 빈 응답 → 종료")
            break

        all_rows.extend(rows)
        print(f"  누적 {len(all_rows):,} / {total_expected:,}")

        if len(all_rows) >= total_expected:
            break

        page += 1
        time.sleep(0.5)  # API 부하 방지

    if not all_rows:
        print("ERROR: 수집된 데이터가 없습니다.")
        sys.exit(1)

    # DataFrame으로 변환 & 저장
    df = pd.DataFrame(all_rows)
    df.to_parquet(out_path, index=False)
    print(f"\n저장 완료: {out_path}")
    print(f"  - 행 수: {len(df):,}")
    print(f"  - 컬럼: {list(df.columns)}")

    # 메타 파일도 기록
    meta = {
        "fetched_at": datetime.now().isoformat(),
        "row_count": len(df),
        "expected_total": total_expected,
        "file": out_path.name,
    }
    (RAW_DIR / f"meta_{today}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
