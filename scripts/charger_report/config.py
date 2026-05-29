# 섹션⑥ 신규/철거 리포트 파이프라인 공통 설정 — 경로·CPO 명단·차원·컬럼맵 (analyze.py/verify.py가 import)
# 분석 로직(analyze/verify)은 collector SSOT 그대로. 본 파일은 AEP repo 경로/명단만 보유한다.
import os

# ===== 경로 (AEP repo 기준) =====
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo/
DATA_DIR = os.path.join(ROOT, 'data', 'charger_snapshots')   # 라벨별 슬림 parquet + _manifest.json
OUT_DIR = os.path.join(ROOT, 'scripts', 'charger_report', 'output')  # report_data.json + xlsx (비커밋 작업물)
MANIFEST = os.path.join(DATA_DIR, '_manifest.json')

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(OUT_DIR, exist_ok=True)

# ===== 비교 윈도우 =====
N_MONTHS = 4   # 최근 월 스냅샷 개수. 4개 -> 3개 전환구간(롤링).

# ===== 분석 차원 (표시명 -> 컬럼) =====
DIMS = {'사업자': 'NewbusiNm', '카테고리': 'Kind', '지역': '지역명', '권역': '권역'}

# ===== 속도 구분 (newtype 값) =====
SPEEDS = ['완속', '급속']

# ===== 충전기 상세 목록 컬럼 =====
DETAIL_COLS = ['key', 'statId', 'chgerId', 'statNm', 'addr', 'NewbusiNm',
               '지역명', '권역', 'Kind', 'KindDetail', 'newtype', 'output']

# ===== 주요 CPO 명단 (데이터 사업자명 NewbusiNm 기준; collector config와 동일) =====
CPO_LISTS = {
    '완속': ['GS차지비', '파워큐브', '에버온', 'LG유플러스', '플러그링크',
             '한국전자금융', '스타코프', '휴맥스이브이', '이지차저', '현대엔지니어링'],
    '급속': ['채비', 'SK일렉링크', '이브이시스', '휴맥스이브이', 'GS차지비',
             '펌프킨', '한국전기차충전서비스', '이지차저', '엘에스이링크', '파킹클라우드'],
}

# 이미지/원천 표기 -> 데이터 NewbusiNm 정규화 (CPO exact-string 매칭용)
NAME_MAP = {
    'LG유플러스 볼트업': 'LG유플러스',
    '볼트업': 'LG유플러스',
    '펌킨': '펌프킨',
}

# 충전기 고유키 컬럼
KEY_COLS = ('statId', 'chgerId')

# ===== seed용: collector 월말 xlsx 원본 헤더 인덱스 -> 분석 컬럼명 (collector config.COLS와 동일) =====
# xlsx는 파생 컬럼을 이미 갖고 있어 재파생 없이 직접 슬림화한다.
XLSX_COLS = {
    0: '권역', 1: '지역명', 2: '운영기관', 3: 'NewbusiNm', 4: 'newtype',
    5: 'Kind', 6: 'KindDetail', 7: 'statNm', 8: 'addr', 9: 'statId',
    10: 'chgerId', 17: 'busiId', 19: 'busiNm', 27: 'output', 29: 'zcode',
    37: 'delYn', 44: 'calc_capacity',
}
