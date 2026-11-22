"""Dump each PDF line as column-classified fragments for inspection."""
import json, re, sys
import pdfplumber

NUM_RE = re.compile(r'^(56\.\d{5}|\d{6,8}(FE)?)$')

def lines(path, us_max):
    out = []
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            words = page.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False)
            rows = []
            for w in sorted(words, key=lambda w: (round(w['top']), w['x0'])):
                for r in rows:
                    if abs(r['top'] - w['top']) < 3:
                        r['words'].append(w); break
                else:
                    rows.append({'top': w['top'], 'words': [w]})
            for r in sorted(rows, key=lambda r: r['top']):
                ws = sorted(r['words'], key=lambda w: w['x0'])
                text = ' '.join(w['text'] for w in ws)
                if 'department56.com' in text or re.fullmatch(r'\d+ of \d+', text) or text.startswith('Item #') \
                   or text in ('Lighted Items', 'Accessories') or text.startswith('Year Year'):
                    continue
                rec = {'page': pno, 'box': False, 'num': None, 'desc': [], 'issued': None, 'retired': None, 'us': None, 'cad': None}
                for w in ws:
                    t, x = w['text'], w['x0']
                    if t == '□' or t == '':
                        rec['box'] = True
                    elif x < 75 and NUM_RE.match(t) and rec['num'] is None and not rec['desc']:
                        rec['num'] = t
                    elif 340 <= x < 388 and re.fullmatch(r'\d{4}', t):
                        rec['issued'] = t
                    elif 388 <= x < 440 and re.fullmatch(r'\d{4}|Current', t):
                        rec['retired'] = t
                    elif x >= 430 and re.fullmatch(r'\$[\d,.]+|N/A|n/a', t):
                        if x < us_max and rec['us'] is None:
                            rec['us'] = t
                        else:
                            rec['cad'] = t
                    else:
                        rec['desc'].append(t)
                rec['desc'] = ' '.join(rec['desc'])
                out.append(rec)
    return out

if __name__ == '__main__':
    path, us_max, dest = sys.argv[1], float(sys.argv[2]), sys.argv[3]
    json.dump(lines(path, us_max), open(dest, 'w'), indent=0)
