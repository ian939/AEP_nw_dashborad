# parquet 스냅샷을 완속/급속으로 분리 비교해 신규/철거·CPO 분석을 산출하고 Excel/JSON을 생성하는 스크립트
import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd
import config as C

DATA = C.DATA_DIR
OUT = C.OUT_DIR
LABELS = json.load(open(C.MANIFEST, encoding='utf-8'))['labels']   # extract.py가 선정한 월말 순서
TRANSITIONS = list(zip(LABELS[:-1], LABELS[1:]))
SPEEDS = C.SPEEDS
CPO_LISTS = C.CPO_LISTS
DIMS = C.DIMS
DETAIL_COLS = C.DETAIL_COLS


def load():
    return {lb: pd.read_parquet(os.path.join(DATA, f'{lb}.parquet')) for lb in LABELS}


def agg_counts(df_new, df_rm, col):
    n = df_new.groupby(col, dropna=False).size().rename('신규') if len(df_new) else pd.Series(dtype=int, name='신규')
    r = df_rm.groupby(col, dropna=False).size().rename('철거') if len(df_rm) else pd.Series(dtype=int, name='철거')
    t = pd.concat([n, r], axis=1).fillna(0).astype(int)
    t['순증감'] = t['신규'] - t['철거']
    return t.sort_values('순증감', ascending=False)


def table_records(t):
    tt = t.reset_index()
    tt.columns = ['label', '신규', '철거', '순증감']
    tt['label'] = tt['label'].where(tt['label'].notna(), '(미상)')
    return tt.to_dict(orient='records')


def stations_of(df):
    """해당 (이미 속도/CPO 필터된) df의 충전소 statId 집합."""
    return set(df['statId'])


def station_detail(df_src, statids, label):
    """주어진 statId 목록에 대한 상면 상세 (상면명/주소/충전기수)."""
    sub = df_src[df_src['statId'].isin(statids)]
    g = sub.groupby('statId').agg(statNm=('statNm', 'first'), addr=('addr', 'first'),
                                  충전기수=('key', 'nunique')).reset_index()
    g.insert(0, '구간', label)
    return g[['구간', 'statId', 'statNm', 'addr', '충전기수']]


def analyze_speed(dfs, speed):
    sub = {lb: dfs[lb][dfs[lb]['newtype'] == speed].copy() for lb in LABELS}

    summary_charger, summary_station = [], []
    dim_results = {name: {} for name in DIMS}
    detail_new, detail_rm = [], []

    for a, b in TRANSITIONS:
        da, db = sub[a], sub[b]
        ka, kb = set(da['key']), set(db['key'])
        new_keys, rm_keys = kb - ka, ka - kb
        df_new = db[db['key'].isin(new_keys)]
        df_rm = da[da['key'].isin(rm_keys)]
        seg = f'{a}→{b}'

        summary_charger.append({'구간': seg, '신규': len(df_new), '철거': len(df_rm),
                                '순증감': len(df_new) - len(df_rm)})
        sa, sb = stations_of(da), stations_of(db)
        new_st, rm_st = sb - sa, sa - sb
        summary_station.append({'구간': seg, '신규': len(new_st), '철거': len(rm_st),
                                '순증감': len(new_st) - len(rm_st)})

        for name, col in DIMS.items():
            dim_results[name][seg] = agg_counts(df_new, df_rm, col)

        d1 = df_new[DETAIL_COLS].copy(); d1.insert(0, '구간', seg)
        d2 = df_rm[DETAIL_COLS].copy(); d2.insert(0, '구간', seg)
        detail_new.append(d1); detail_rm.append(d2)

    # CPO 분석
    cpo_data = {}
    cpo_new_rows, cpo_rm_rows = [], []
    for cpo in CPO_LISTS[speed]:
        c = {lb: sub[lb][sub[lb]['NewbusiNm'] == cpo] for lb in LABELS}
        trend = []
        new_stations, removed_stations = [], []
        for a, b in TRANSITIONS:
            ka, kb = set(c[a]['key']), set(c[b]['key'])
            nnew, nrm = len(kb - ka), len(ka - kb)
            trend.append({'구간': f'{a}→{b}', '신규': nnew, '철거': nrm, '순증감': nnew - nrm})
            sa, sb = stations_of(c[a]), stations_of(c[b])
            seg = f'{a}→{b}'
            ns = station_detail(c[b], sb - sa, seg)
            rs = station_detail(c[a], sa - sb, seg)
            new_stations += ns.to_dict(orient='records')
            removed_stations += rs.to_dict(orient='records')
            ns2 = ns.copy(); ns2.insert(0, 'CPO', cpo); ns2.insert(1, '속도', speed); cpo_new_rows.append(ns2)
            rs2 = rs.copy(); rs2.insert(0, 'CPO', cpo); rs2.insert(1, '속도', speed); cpo_rm_rows.append(rs2)
        holdings = [int(c[lb]['key'].nunique()) for lb in LABELS]
        cpo_data[cpo] = {'trend': trend, 'holdings': holdings,
                         'new_stations': new_stations, 'removed_stations': removed_stations}

    totals = {lb: {'충전기': int(sub[lb]['key'].nunique()), '상면': int(sub[lb]['statId'].nunique())} for lb in LABELS}

    result = {
        'summary_charger': summary_charger,
        'summary_station': summary_station,
        'dims': {name: {seg: table_records(t) for seg, t in dim_results[name].items()} for name in DIMS},
        'totals_by_label': totals,
        'cpo': {'list': CPO_LISTS[speed], 'data': cpo_data},
        '_detail_new': pd.concat(detail_new, ignore_index=True),
        '_detail_rm': pd.concat(detail_rm, ignore_index=True),
        '_cpo_new_rows': pd.concat(cpo_new_rows, ignore_index=True) if cpo_new_rows else pd.DataFrame(),
        '_cpo_rm_rows': pd.concat(cpo_rm_rows, ignore_index=True) if cpo_rm_rows else pd.DataFrame(),
    }
    return result


def main():
    dfs = load()
    by_speed = {sp: analyze_speed(dfs, sp) for sp in SPEEDS}

    # ---- 콘솔 요약 ----
    for sp in SPEEDS:
        for r in by_speed[sp]['summary_charger']:
            print(f'[{sp}] {r["구간"]}: 충전기 신규 {r["신규"]:,} 철거 {r["철거"]:,} (순증 {r["순증감"]:+,})')

    # ---- Excel ----
    xlsx_path = os.path.join(OUT, '충전기_신규철거_분석.xlsx')
    with pd.ExcelWriter(xlsx_path, engine='openpyxl') as w:
        for sp in SPEEDS:
            r = by_speed[sp]
            pd.DataFrame(r['summary_charger']).to_excel(w, sheet_name=f'요약_{sp}_충전기', index=False)
            pd.DataFrame(r['summary_station']).to_excel(w, sheet_name=f'요약_{sp}_상면', index=False)
            r['_detail_new'].to_excel(w, sheet_name=f'{sp}_신규상세', index=False)
            r['_detail_rm'].to_excel(w, sheet_name=f'{sp}_철거상세', index=False)
        # CPO 상면 (완속/급속 합본)
        cpo_new = pd.concat([by_speed[sp]['_cpo_new_rows'] for sp in SPEEDS], ignore_index=True)
        cpo_rm = pd.concat([by_speed[sp]['_cpo_rm_rows'] for sp in SPEEDS], ignore_index=True)
        cpo_new.to_excel(w, sheet_name='CPO_신규상면', index=False)
        cpo_rm.to_excel(w, sheet_name='CPO_철거상면', index=False)
    print('Excel 저장:', xlsx_path)

    # ---- JSON (내부 DataFrame 제거 후 직렬화) ----
    clean = {}
    for sp in SPEEDS:
        clean[sp] = {k: v for k, v in by_speed[sp].items() if not k.startswith('_')}
    report = {
        'speeds': SPEEDS,
        'labels': LABELS,
        'transitions': [f'{a}→{b}' for a, b in TRANSITIONS],
        'by_speed': clean,
    }
    json_path = os.path.join(OUT, 'report_data.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print('JSON 저장:', json_path)
    print('DONE')


if __name__ == '__main__':
    main()
