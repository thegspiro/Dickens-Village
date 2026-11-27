"""Dump each PDF line as column-classified fragments for inspection.

usage: dump_lines.py PDF US_MAX_X OUT.json [--retired-x X] [--section REGEX] [--skip REGEX ...]

US_MAX_X separates the US and Canadian price columns. --retired-x is where the
"Year Retired" column starts (the 2025 Dickens' Village PDFs use 388; the
General Village Accessories PDF puts retired years at 387, so it needs a lower
value). A line matching --section is written as {"page": n, "section": text}
instead of an item line, so later steps can tell which section of the PDF a
piece came from. Lines matching --skip (page titles) are left out.
"""
import argparse
import json
import re

import pdfplumber

NUM_RE = re.compile(r'^(56\.\d{5}|\d{6,8}(FE)?)$')


def lines(path, us_max, retired_x=388.0, section_re=None, skip_res=()):
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
                if section_re and section_re.search(text):
                    out.append({'page': pno, 'section': text})
                    continue
                if 'department56.com' in text or re.fullmatch(r'(Page )?\d+ of \d+', text) \
                   or text.startswith('Item #') \
                   or text in ('Lighted Items', 'Accessories') or text.startswith('Year Year') \
                   or any(s.search(text) for s in skip_res):
                    continue
                rec = {'page': pno, 'box': False, 'num': None, 'desc': [], 'issued': None, 'retired': None, 'us': None, 'cad': None}
                for w in ws:
                    t, x = w['text'], w['x0']
                    if t == '□' or t == '':
                        rec['box'] = True
                    elif x < 75 and NUM_RE.match(t) and rec['num'] is None and not rec['desc']:
                        rec['num'] = t
                    elif 340 <= x < retired_x and re.fullmatch(r'\d{4}', t):
                        rec['issued'] = t
                    elif retired_x <= x < 440 and re.fullmatch(r'\d{4}|Current', t):
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
    parser = argparse.ArgumentParser()
    parser.add_argument('pdf')
    parser.add_argument('us_max', type=float)
    parser.add_argument('dest')
    parser.add_argument('--retired-x', type=float, default=388.0)
    parser.add_argument('--section')
    parser.add_argument('--skip', action='append', default=[])
    args = parser.parse_args()
    section = re.compile(args.section) if args.section else None
    skips = [re.compile(s) for s in args.skip]
    with open(args.dest, 'w') as fh:
        json.dump(lines(args.pdf, args.us_max, args.retired_x, section, skips), fh, indent=0)
