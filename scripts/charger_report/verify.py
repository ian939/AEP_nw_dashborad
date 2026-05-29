# 속도분기 분석 결과를 독립적 방법으로 재계산해 교차검증하는 스크립트
import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd
import config as C

DATA = C.DATA_DIR
OUT = C.OUT_DIR
LABELS = json.load(open(C.MANIFEST, encoding='utf-8'))['labels']
TRANS = list(zip(LABELS[:-1], LABELS[1:]))
SPEEDS = C.SPEEDS
dfs = {lb: pd.read_parquet(os.path.join(DATA, f'{lb}.parquet')) for lb in LABELS}
rep = json.load(open(os.path.join(OUT, 'report_data.json'), encoding='utf-8'))
ok = True


def seg_row(rows, seg):
    return next(r for r in rows if r['구간'] == seg)


print('=== 1. 속도별 merge-indicator 재계산 == report (충전기 신규/철거) ===')
for sp in SPEEDS:
    sub = {lb: dfs[lb][dfs[lb]['newtype'] == sp] for lb in LABELS}
    for a, b in TRANS:
        seg = f'{a}→{b}'
        m = sub[a][['key']].assign(_a=1).merge(sub[b][['key']].assign(_b=1), on='key', how='outer')
        new = int(m['_a'].isna().sum()); rm = int(m['_b'].isna().sum())
        row = seg_row(rep['by_speed'][sp]['summary_charger'], seg)
        match = (new == row['신규'] and rm == row['철거'])
        ok &= match
        print(f'  [{sp}] {seg}: 재계산 {new:,}/{rm:,} | report {row["신규"]:,}/{row["철거"]:,} -> {"OK" if match else "MISMATCH"}')

print('\n=== 2. 완속net + 급속net == 전체net (타입전환 보존 항등식) ===')
for a, b in TRANS:
    seg = f'{a}→{b}'
    ka, kb = set(dfs[a]['key']), set(dfs[b]['key'])
    overall_net = len(kb - ka) - len(ka - kb)
    sp_net = sum(seg_row(rep['by_speed'][sp]['summary_charger'], seg)['순증감'] for sp in SPEEDS)
    # 타입전환 추정: (완속신규+급속신규) - 전체신규
    sp_new = sum(seg_row(rep['by_speed'][sp]['summary_charger'], seg)['신규'] for sp in SPEEDS)
    switch = sp_new - len(kb - ka)
    m = (sp_net == overall_net)
    ok &= m
    print(f'  {seg}: 속도net합={sp_net:+,} 전체net={overall_net:+,} -> {"OK" if m else "FAIL"} (타입전환 추정 {switch}건)')

print('\n=== 3. 차원별 합계 == 해당 속도 구간 총계 ===')
for sp in SPEEDS:
    for a, b in TRANS:
        seg = f'{a}→{b}'
        row = seg_row(rep['by_speed'][sp]['summary_charger'], seg)
        for dim in rep['by_speed'][sp]['dims']:
            rows = rep['by_speed'][sp]['dims'][dim][seg]
            sn = sum(r['신규'] for r in rows); sr = sum(r['철거'] for r in rows)
            m = (sn == row['신규'] and sr == row['철거'])
            ok &= m
            if not m:
                print(f'  [{sp}] {seg} {dim}: {sn}/{sr} != {row["신규"]}/{row["철거"]} MISMATCH')
    print(f'  [{sp}] 전 구간·전 차원 합계 일치')

_LAST = LABELS[-1]
print(f'\n=== 4. CPO holdings[마지막] == {_LAST} 해당 CPO·속도 충전기수 (parquet 직접 카운트) ===')
for sp in SPEEDS:
    sub = dfs[_LAST][dfs[_LAST]['newtype'] == sp]
    for cpo in rep['by_speed'][sp]['cpo']['list']:
        rep_h = rep['by_speed'][sp]['cpo']['data'][cpo]['holdings'][-1]
        direct = int(sub[sub['NewbusiNm'] == cpo]['key'].nunique())
        m = (rep_h == direct)
        ok &= m
        if not m:
            print(f'  [{sp}] {cpo}: report {rep_h} != 직접 {direct} FAIL')
    print(f'  [{sp}] CPO {len(rep["by_speed"][sp]["cpo"]["list"])}개 보유량 일치')

print('\n=== 5. CPO 상면 무결성 (구간내 statId 중복 0) ===')
for sp in SPEEDS:
    bad = 0
    for cpo in rep['by_speed'][sp]['cpo']['list']:
        d = rep['by_speed'][sp]['cpo']['data'][cpo]
        for key in ['new_stations', 'removed_stations']:
            df = pd.DataFrame(d[key])
            if len(df):
                bad += df.groupby('구간')['statId'].apply(lambda s: s.duplicated().sum()).sum()
    ok &= (bad == 0)
    print(f'  [{sp}] 상면 구간내 중복 statId={bad} -> {"OK" if bad==0 else "FAIL"}')

print('\n=== 6. Excel CPO 상면 시트 행수 == JSON 상면 합계 ===')
xls = pd.ExcelFile(os.path.join(OUT, '충전기_신규철거_분석.xlsx'))
en = pd.read_excel(xls, 'CPO_신규상면'); er = pd.read_excel(xls, 'CPO_철거상면')
jn = sum(len(rep['by_speed'][sp]['cpo']['data'][c]['new_stations']) for sp in SPEEDS for c in rep['by_speed'][sp]['cpo']['list'])
jr = sum(len(rep['by_speed'][sp]['cpo']['data'][c]['removed_stations']) for sp in SPEEDS for c in rep['by_speed'][sp]['cpo']['list'])
m1 = len(en) == jn; m2 = len(er) == jr
ok &= m1 and m2
print(f'  신규상면 Excel {len(en):,} == JSON {jn:,} -> {"OK" if m1 else "FAIL"}')
print(f'  철거상면 Excel {len(er):,} == JSON {jr:,} -> {"OK" if m2 else "FAIL"}')

print('\n' + ('✅ 전체 검증 통과' if ok else '❌ 검증 실패 항목 있음'))
