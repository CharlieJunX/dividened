"""Render validated data, never model-supplied HTML or invented URLs."""
import html
import json
from pathlib import Path
from .evidence import norm


def review_status(snapshot):
    if snapshot['risk_status'] in ('HIGH_RISK', 'WARNING'):
        return '위험 요인 우선 확인'
    if (snapshot['risk_status'] not in ('NORMAL', 'WARNING') or snapshot['failed'] or
            snapshot['metrics']['investment_score']['value'] is None or
            snapshot['evaluation_status'] == 'UNRATED' or
            (snapshot['metrics']['coverage']['value'] or 0) < 80):
        return '데이터 부족'
    if snapshot['candidate'] and snapshot['metrics']['investment_score']['value'] is not None:
        return '추가 검토 후보'
    return '관찰 유지'


def report_object(snapshot, narrative, report_id, created_at, mode, usage, validation):
    return {'report_id': report_id, 'instrument_id': snapshot['instrument_id'], 'as_of': snapshot['as_of'],
            'created_at': created_at, 'mode': mode, 'summary': narrative['summary'],
            'quantitative_snapshot': snapshot, 'investment_thesis': narrative['investment_thesis'],
            'counterarguments': narrative['counterarguments'], 'related_events': narrative['related_events'],
            'scenarios': narrative['scenarios'], 'backtest_context': snapshot['backtest'],
            'monitoring_items': narrative['monitoring_items'], 'review_status': review_status(snapshot),
            'limitations': narrative['limitations'], 'sources': {d['id']: {'title': d['title'], 'url': d['url'],
                'publisher': d['publisher'], 'version': d['version'], 'published_at': d['published_at'],
                'full_text_verified': d['full_text_verified']} for d in snapshot['documents']},
            'model_usage': usage, 'validation': validation}


def source_text(report, ids):
    source = report['sources']
    names = [source[i]['title'] + ' (' + source[i]['url'] + ')' if i in source else i for i in ids]
    return '; '.join(names) or '확인 가능한 원문 출처 없음'


def markdown(report):
    s = report['quantitative_snapshot']
    lines = [f"# AI 투자 검토 리포트: {s['name']}", '',
             f"종목: {s['instrument_id']} · 기준일: {s['as_of']} · 검토 상태: **{report['review_status']}**", '',
             '## 핵심 요약', '']
    lines += ['- ' + x for x in report['summary'][:5]]
    lines += ['', '## 정량 현황', '', '| 지표 | 값 | 단위 | 기간 | 출처 | 공개일/시각 |', '|---|---:|---|---|---|---|']
    for key, m in s['metrics'].items():
        value = m['value'] if m['value'] is not None else '평가 불가: ' + m['missing_reason']
        lines.append('| ' + ' | '.join(str(x).replace('|', '\\|').replace('\n', ' ') for x in
            (key, value, m['unit'] or '', m['period'] or '', m['source'] or '', m['published_at'] or '미확인')) + ' |')
    lines += ['', f"펀더멘털: {s['risk_status']} · 종합 등급: {s['investment_grade'] or 'N/A'} · 배당 저평가: {s['dividend_undervalued']}", '']
    for title, key in [('투자 근거', 'investment_thesis'), ('반대 근거와 위험', 'counterarguments'),
                       ('관련 사건', 'related_events')]:
        lines += ['## ' + title, '']
        for x in report[key]:
            lines.append('- [' + x['kind'] + '] ' + x['text'] + ' — 근거: ' + source_text(report, x['evidence_ids']))
        if not report[key]:
            lines.append('확인 가능한 근거 없음')
        lines.append('')
    lines += ['## 조건별 시나리오', '']
    for label, key in [('낙관', 'optimistic'), ('기준', 'base'), ('비관', 'pessimistic')]:
        lines.append('- ' + label + ': ' + report['scenarios'][key])
    lines += ['', '## 백테스트 참고', '']
    context = report['backtest_context']
    if context['status'] == '참고 가능':
        lines.append(f"전략: {context['strategy']} · 기간: {context['period'][0]}~{context['period'][1]} · 종목 집합: {context['universe']}")
        for months, values in context['horizons'].items():
            lines.append(f"- {months}개월: 완료 표본 {values['complete']}개, 평균 순수익률 {values['mean_net_pct']}, 중앙값 {values['median_net_pct']}, 승률 {values['win_rate_pct']}")
        lines.append('과거 표본 성과는 해당 종목의 미래 상승 확률이 아닙니다.')
        lines.append('거래비용 가정: ' + json.dumps(context.get('costs', {}), ensure_ascii=False))
        lines += ['- ' + str(x) for x in context.get('limitations', [])]
    else:
        lines.append(context['status'] + ': ' + context['reason'])
    lines += ['', '## 다음 확인 사항', ''] + ['- ' + x for x in report['monitoring_items'][:5]]
    lines += ['', '## 한계', ''] + ['- ' + x for x in report['limitations']]
    lines += ['', f"생성 시각: {report['created_at']} · 생성 방식: {report['mode']}"]
    return '\n'.join(lines)


def html_report(report):
    # Escaped preformatted Markdown is deliberately inert; no model HTML is interpreted.
    body = html.escape(markdown(report))
    return ('<!doctype html><html lang="ko"><meta charset="utf-8"><title>투자 검토 리포트</title>'
            '<style>body{font:16px/1.6 system-ui,sans-serif;max-width:980px;margin:30px auto;padding:24px;color:#1d3447}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f7fa;padding:20px;border-radius:10px}</style>'
            '<h1>투자 검토 리포트</h1><pre>' + body + '</pre></html>')


def telegram(report, max_units=3400):
    s = report['quantitative_snapshot']
    thesis = report['investment_thesis'][0]['text'] if report['investment_thesis'] else '확인 가능한 근거 없음'
    risk = report['counterarguments'][0]['text'] if report['counterarguments'] else '확인 가능한 반대 근거 없음'
    source = next(iter(report['sources'].values()), None)
    text = '\n'.join(['[AI 투자 검토 리포트]', f"{s['name']} / {s['market']} / {s['as_of']}",
                     f"점수: {s['metrics']['investment_score']['value'] if s['metrics']['investment_score']['value'] is not None else 'N/A'} / 펀더멘털: {s['risk_status']}",
                     '선정 이유: ' + ('기존 투자 후보 조건 충족' if s['candidate'] else '관심·위험 확인'),
                     '핵심 근거: ' + thesis, '반대 근거: ' + risk,
                     '다음 확인: ' + (report['monitoring_items'][0] if report['monitoring_items'] else '공식 공시'),
                     '주요 출처: ' + (source['url'] if source else '기존 정량 분석'),
                     '검토 상태: ' + report['review_status']])
    if len(text.encode('utf-16-le')) // 2 > max_units:
        # Keep the most essential lines and trim at codepoint boundaries.
        shortened = []
        for line in text.splitlines():
            remaining = max_units - len(('\n'.join(shortened) + '\n').encode('utf-16-le')) // 2
            clipped = ''
            for char in line:
                if len((clipped + char).encode('utf-16-le')) // 2 > max(0, remaining - 2):
                    break
                clipped += char
            if clipped:
                shortened.append(clipped)
        text = '\n'.join(shortened)
    return text


def write(directory, report, config):
    directory = Path(directory)
    if directory.exists():
        raise FileExistsError('Report folder already exists')
    temporary = directory.with_name(directory.name + '.tmp')
    temporary.mkdir(parents=True)
    try:
        (temporary / 'input.json').write_text(json.dumps(report['quantitative_snapshot'], ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        (temporary / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        (temporary / 'validation.json').write_text(json.dumps(report['validation'], ensure_ascii=False, indent=2), encoding='utf-8')
        (temporary / 'report.md').write_text(markdown(report), encoding='utf-8')
        (temporary / 'report.html').write_text(html_report(report), encoding='utf-8')
        (temporary / 'telegram_preview.txt').write_text(telegram(report, config['telegram']['maximum_utf16_units']), encoding='utf-8')
        (temporary / 'config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(directory)
    except BaseException:
        import shutil
        shutil.rmtree(temporary, ignore_errors=True)
        raise
