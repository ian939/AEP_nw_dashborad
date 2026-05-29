# 환경부 raw parquet(또는 collector 월말 parquet)에서 analyze.py가 먹는 슬림 스냅샷을 생성·롤링 관리하는 추출기
"""
섹션⑥ 신규/철거 리포트용 월별 스냅샷 빌더.

모드:
  --seed [--collector-data DIR] [--year YYYY]
      collector 분석/data/{MM월말}.parquet 를 읽어 슬림 스냅샷 4개(Feb-26..)로 시드.
      (월말 파생 컬럼을 그대로 사용 → 검증된 기존 리포트와 동일 수치 재현)
  --ingest <raw_YYYYMMDD.parquet>
      환경부 raw parquet → 컬럼 파생(완속·급속/사업자/지역/카테고리) → 슬림 스냅샷.
      라벨=파일 날짜의 당월(MMM-YY). 스토어에 upsert 후 N_MONTHS로 prune, manifest 갱신.

산출: data/charger_snapshots/{LABEL}.parquet + _manifest.json
슬림 컬럼 = config.DETAIL_COLS (analyze.py/verify.py 입력 계약).
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))   # transform.apply_operator_mapping 재사용
import transform  # noqa: E402
import config as C  # noqa: E402

SLIM_COLS = C.DETAIL_COLS  # ['key','statId','chgerId','statNm','addr','NewbusiNm','지역명','권역','Kind','KindDetail','newtype','output']

# ===== 캘리브레이션 코드표 (collector 04월말 parquet 대조로 확정; kind purity 1.000) =====
SLOW_CHGER_TYPES = {"02", "07", "08"}  # 08=DC콤보(완속). collector newtype과 99.96%→100% 정렬

ZCODE_TO_SIDO = {
    '11': '서울특별시', '26': '부산광역시', '27': '대구광역시', '28': '인천광역시',
    '29': '광주광역시', '30': '대전광역시', '31': '울산광역시', '36': '세종특별자치시',
    '41': '경기도', '43': '충청북도', '44': '충청남도', '46': '전라남도',
    '47': '경상북도', '48': '경상남도', '50': '제주특별자치도', '51': '강원특별자치도',
    '52': '전북특별자치도',
}
ZCODE_TO_REGION = {
    '11': '수도권', '28': '수도권', '41': '수도권',
    '26': '5대광역시', '27': '5대광역시', '29': '5대광역시', '30': '5대광역시', '31': '5대광역시',
    '36': '지방', '43': '지방', '44': '지방', '46': '지방', '47': '지방',
    '48': '지방', '50': '지방', '51': '지방', '52': '지방',
}
KIND_MAP = {
    'A0': '공공시설', 'B0': '주차시설', 'C0': '휴게시설', 'D0': '관광시설',
    'E0': '상업시설', 'F0': '차량정비시설', 'G0': '기타시설', 'H0': '공동주택시설',
    'I0': '근린생활시설', 'J0': '교육문화시설',
}
KINDDETAIL_MAP = {
    "A001": "관공서", "A002": "주민센터", "A003": "공공기관", "A004": "지자체시설",
    "B001": "공영주차장", "B002": "공원주차장", "B003": "환승주차장", "B004": "일반주차장",
    "C001": "고속도로 휴게소", "C002": "지방도로 휴게소", "C003": "쉼터",
    "D001": "공원", "D002": "전시관", "D003": "민속마을", "D004": "생태공원", "D005": "홍보관",
    "D006": "관광안내소", "D007": "관광지", "D008": "박물관", "D009": "유적지",
    "E001": "마트(쇼핑몰)", "E002": "백화점", "E003": "숙박시설", "E004": "골프장(CC)",
    "E005": "카페", "E006": "음식점", "E007": "주유소", "E008": "영화관",
    "F001": "서비스센터", "F002": "정비소",
    "G001": "군부대", "G002": "야영장", "G003": "공중전화부스", "G004": "기타",
    "G005": "오피스텔", "G006": "단독주택",
    "H001": "아파트", "H002": "빌라", "H003": "사업장(사옥)", "H004": "기숙사", "H005": "연립주택",
    "I001": "병원", "I002": "종교시설", "I003": "보건소", "I004": "경찰서", "I005": "도서관",
    "I006": "복지관", "I007": "수련원", "I008": "금융기관",
    "J001": "학교", "J002": "교육원", "J003": "학원", "J004": "공연장", "J005": "관람장",
    "J006": "동식물원", "J007": "경기장",
}

MONTH_ABBR = ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
ABBR_NUM = {a: i for i, a in enumerate(MONTH_ABBR) if a}


def label_for(year: int, month: int) -> str:
    return f"{MONTH_ABBR[month]}-{year % 100:02d}"


def label_sort_key(label: str):
    abbr, yy = label.split('-')
    return (2000 + int(yy)) * 12 + ABBR_NUM[abbr]


def derive_from_raw(raw: pd.DataFrame) -> pd.DataFrame:
    """환경부 raw parquet → 슬림 스냅샷(SLIM_COLS). 파생 로직은 collector 기준에 정렬."""
    mapping = transform.load_mapping()
    df = transform.apply_operator_mapping(raw, mapping)  # busiId→operator (bnm fallback)
    df['NewbusiNm'] = df['operator'].replace(C.NAME_MAP)

    df['key'] = df['statId'].astype(str) + '|' + df['chgerId'].astype(str)

    ct = df['chgerType'].astype(str).str.strip()
    out = pd.to_numeric(df.get('output'), errors='coerce')
    df['newtype'] = ((ct.isin(SLOW_CHGER_TYPES)) | (out == 30)).map({True: '완속', False: '급속'})

    z = df['zcode'].astype(str).str.strip()
    df['지역명'] = z.map(ZCODE_TO_SIDO).fillna('(미상)')
    df['권역'] = z.map(ZCODE_TO_REGION).fillna('지방')

    df['Kind'] = df['kind'].astype(str).str.strip().map(KIND_MAP).fillna('(미상)')
    df['KindDetail'] = df['kindDetail'].astype(str).str.strip().map(KINDDETAIL_MAP)
    df['KindDetail'] = df['KindDetail'].fillna(df['kindDetail'])
    df['output'] = out
    return df[SLIM_COLS].copy()


def derive_from_collector(df: pd.DataFrame) -> pd.DataFrame:
    """collector 월말 parquet(파생 컬럼 보유) → 슬림 스냅샷. 재파생 없이 컬럼 선택."""
    d = df.copy()
    if 'key' not in d.columns:
        d['key'] = d['statId'].astype(str) + '|' + d['chgerId'].astype(str)
    if 'KindDetail' not in d.columns:
        d['KindDetail'] = ''
    if 'output' not in d.columns:
        d['output'] = pd.NA
    return d[SLIM_COLS].copy()


def write_snapshot(slim: pd.DataFrame, label: str):
    path = Path(C.DATA_DIR) / f"{label}.parquet"
    slim.to_parquet(path, index=False)
    print(f"  저장: {path.name}  ({len(slim):,} 행)")


def update_manifest():
    """디렉터리의 parquet 라벨을 시간순 정렬 → 최근 N_MONTHS만 manifest에 기록(초과분 삭제)."""
    snaps = sorted(
        (p.stem for p in Path(C.DATA_DIR).glob('*.parquet')),
        key=label_sort_key,
    )
    keep = snaps[-C.N_MONTHS:]
    drop = snaps[:-C.N_MONTHS]
    for lb in drop:
        (Path(C.DATA_DIR) / f"{lb}.parquet").unlink()
        print(f"  prune: {lb} 제거")
    Path(C.MANIFEST).write_text(json.dumps({'labels': keep}, ensure_ascii=False), encoding='utf-8')
    print(f"  manifest labels = {keep}")


def cmd_seed(collector_data: Path, year: int):
    mani = json.loads((collector_data / '_manifest.json').read_text(encoding='utf-8'))
    print(f"[seed] collector labels: {mani['labels']}")
    for cl in mani['labels']:
        m = re.match(r'(\d{2})월말', cl)
        if not m:
            print(f"  [skip] 비표준 라벨: {cl}")
            continue
        month = int(m.group(1))
        label = label_for(year, month)
        df = pd.read_parquet(collector_data / f"{cl}.parquet")
        write_snapshot(derive_from_collector(df), label)
    update_manifest()
    print("[seed] 완료")


def cmd_ingest(raw_path: Path):
    m = re.search(r'raw_(\d{4})(\d{2})(\d{2})', raw_path.name)
    if not m:
        sys.exit(f"raw 파일명에서 날짜를 못 읽음: {raw_path.name}")
    year, month = int(m.group(1)), int(m.group(2))
    label = label_for(year, month)
    print(f"[ingest] {raw_path.name} → 라벨 {label}")
    raw = pd.read_parquet(raw_path)
    write_snapshot(derive_from_raw(raw), label)
    update_manifest()
    print("[ingest] 완료")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', action='store_true', help='collector 월말 parquet로 시드')
    ap.add_argument('--collector-data',
                    default=str(REPO_ROOT.parent.parent / '102.ev-charger-collector-main' / '분석' / 'data'),
                    help='collector 분석/data 경로 (--seed)')
    ap.add_argument('--year', type=int, default=2026, help='시드 라벨 연도 (--seed)')
    ap.add_argument('--ingest', metavar='RAW_PARQUET', help='환경부 raw parquet 경로')
    args = ap.parse_args()

    if args.seed:
        cmd_seed(Path(args.collector_data), args.year)
    elif args.ingest:
        cmd_ingest(Path(args.ingest))
    else:
        ap.error('--seed 또는 --ingest 중 하나를 지정하세요.')


if __name__ == '__main__':
    main()
