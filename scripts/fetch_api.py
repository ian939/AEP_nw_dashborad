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
import random
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

# data.go.kr 게이트웨이가 무거운 요청(numOfRows=9999)에 502/read-timeout(응답 멈춤)을
# 자주 낸다. 대응: (1) 페이지를 가볍게(4000) → 서버 부하·응답시간 ↓, (2) https,
# (3) 짧은 timeout(30s)으로 행 걸린 요청을 빨리 포기하고 재시도, (4) 부분 실패 허용(main).
API_URL = "https://apis.data.go.kr/B552584/EvCharger/getChargerInfo"
PAGE_SIZE = 4000  # 9999(최대)는 게이트웨이가 자주 timeout → 가볍게
MAX_PAGES = 400   # 안전 장치 (~160만건까지)
RETRY = 6         # 게이트웨이 일시 장애(502/timeout)를 견디기 위해 넉넉히
TIMEOUT = 30      # 서버가 응답을 멈추면(hang) 빨리 포기하고 재시도하는 게 유리
MIN_COMPLETENESS = 0.98  # 이 비율 미만 수집 시 발행 중단(누락 과다)

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
            if attempt == RETRY - 1:
                print(f"  [page {page_no}] attempt {attempt+1}/{RETRY} failed: {e}. (마지막 시도)")
                break
            # 지수 백오프(최대 30s) + 지터 — 게이트웨이 blip 을 넉넉히 넘긴다.
            wait = min(30, 2 ** attempt) + random.uniform(0, 2)
            print(f"  [page {page_no}] attempt {attempt+1}/{RETRY} failed: {e}. retry in {wait:.1f}s")
            time.sleep(wait)
    raise RuntimeError(f"page {page_no} failed after {RETRY} retries: {last_err}")


def main():
    today = datetime.now().strftime("%Y%m%d")
    out_path = RAW_DIR / f"raw_{today}.parquet"

    print(f"환경부 API 수집 시작 ({today})")
    print(f"출력: {out_path}")

    # 1페이지는 총건수 확보에 필수 — 실패 시 진행 불가.
    all_rows, total_expected = fetch_page(1)
    print(f"전체 예상 건수: {total_expected:,}")
    print(f"  누적 {len(all_rows):,} / {total_expected:,}")

    n_pages = min(MAX_PAGES, (total_expected + PAGE_SIZE - 1) // PAGE_SIZE)
    skipped = []
    for page in range(2, n_pages + 1):
        print(f"페이지 {page}/{n_pages} 수집 중...")
        try:
            rows, _ = fetch_page(page)
        except Exception as e:
            # 한 페이지가 재시도를 소진해도 전체를 죽이지 않고 건너뛴다(부분 실패 허용).
            skipped.append(page)
            print(f"  [page {page}] 최종 실패 → 건너뜀: {e}")
            continue
        if not rows:
            print(f"페이지 {page} 빈 응답 → 종료")
            break
        all_rows.extend(rows)
        print(f"  누적 {len(all_rows):,} / {total_expected:,}")
        time.sleep(0.5)  # API 부하 방지

    if not all_rows:
        print("ERROR: 수집된 데이터가 없습니다.")
        sys.exit(1)

    completeness = len(all_rows) / total_expected if total_expected else 1.0
    if skipped:
        print(f"\n⚠ 건너뛴 페이지 {len(skipped)}개: {skipped} (게이트웨이 지속 오류)")
    print(f"수집 완료율: {completeness*100:.2f}% ({len(all_rows):,}/{total_expected:,})")
    if completeness < MIN_COMPLETENESS:
        print(f"ERROR: 완료율 {completeness*100:.1f}% < {MIN_COMPLETENESS*100:.0f}% — 누락 과다로 발행 중단. 재실행 권장.")
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
        "completeness": round(completeness, 4),
        "skipped_pages": skipped,
        "file": out_path.name,
    }
    (RAW_DIR / f"meta_{today}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
