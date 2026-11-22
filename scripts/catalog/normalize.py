"""Normalize parsed records into the catalog CSV + an anomalies report."""
import csv
import json
import re
import sys
from collections import Counter

LIST_TAG = '2025'

# Corrections for lines the generic parser cannot read correctly. Keyed by
# (source, item_number, title prefix). Each sets fields on the parsed record.
OVERRIDES = {
    ('lighted', '56.58601', '"Sherlock Holmes'): {
        'title': '"Sherlock Holmes -- 221B Baker Street"',
        'notes': ['Set includes book, building, 2 piece accessory "Elementary My Dear Watson"'],
    },
    ('accessories', '56.58403', '"A Christmas Carol/Reading'): {
        'title': '"A Christmas Carol/Reading By Charles Dickens" set of 4',
        'notes': ['Source also lists two unnamed price lines (1996-2001): $22.50 US/$33.00 CAD and $45.00 US/$65.00 CAD'],
    },
    ('accessories', '56.58404', '"A Christmas Carol/Reading'): {
        'notes': ['Set of 7, Limited to 42,500'],
    },
}
# Records the parser creates from orphaned lines that belong elsewhere (folded into an override above).
DROP = {('accessories', None, 'set of 4')}

SERIES_PATTERNS = [
    (re.compile(r'christmas carol|\(CC\)', re.I), 'A Christmas Carol'),
    (re.compile(r'12th night|twelfth night', re.I), '12th Night'),
    (re.compile(r"12 days of dickens", re.I), "12 Days of Dickens' Village"),
    (re.compile(r'all hallows', re.I), "All Hallows' Eve"),
    (re.compile(r'abington canal', re.I), 'Abington Canal'),
    (re.compile(r'queens port', re.I), 'Queens Port'),
    (re.compile(r'victorian christmas', re.I), 'A Victorian Christmas'),
    (re.compile(r'historical landmark', re.I), 'Historical Landmark Series'),
    (re.compile(r'literary classics', re.I), 'Literary Classics'),
    (re.compile(r'silver (boxed set|series)', re.I), 'Silver Series'),
]

UNQUOTED_SPLIT = re.compile(
    r'\s*(?:,\s*(?:boxed set|set of|numbered|limited|\d+(?:st|nd|rd|th) anniversary|set\b)|\(|\.\s+limited|\s+NA$)',
    re.I,
)


def money(raw):
    if not raw or raw.upper() == 'N/A':
        return ''
    value = raw.replace('$', '').replace(',', '')
    return f'{float(value):.2f}'


def split_name(title):
    """Return (name, remainder) from a source description."""
    t = title.strip()
    if t.startswith('"'):
        end = t.find('"', 1)
        if end == -1:
            return t[1:].strip(), ''
        return t[1:end].strip(), t[end + 1:].strip(' ,')
    if '"' in t:  # missing opening quote: Knottinghill Church" ...
        end = t.find('"')
        return t[:end].strip(), t[end + 1:].strip(' ,')
    m = UNQUOTED_SPLIT.search(t)
    if m and m.start() > 0:
        return t[:m.start()].strip(' ,.'), t[m.start():].strip(' ,.')
    return t, ''


def clean_details(parts):
    text = ' '.join(p for p in parts if p).strip()
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'^\s*NA\s*$', '', text)
    text = text.replace('( ', '(')
    return text.strip(' ,')


def series_for(name, details):
    hay = details + (' ' + name if re.search(r'queens port|abington', name, re.I) else '')
    for pattern, series in SERIES_PATTERNS:
        if pattern.search(hay):
            return series
    return ''


def year(raw, problems, label):
    if raw in (None, '', 'Current'):
        return ''
    y = int(raw)
    if not 1980 <= y <= 2026:
        problems.append(f'{label}: year {raw} in the source is impossible; left blank')
        return ''
    return str(y)


def main(records_path, csv_path, report_path):
    records = json.load(open(records_path))
    rows, problems = [], []
    seq = Counter()
    for rec in records:
        for ok, change in OVERRIDES.items():
            if ok[0] == rec['source'] and ok[1] == rec['item_number'] and rec['title'].startswith(ok[2]):
                rec.update(change)
        if (rec['source'], rec['item_number'], rec['title']) in DROP:
            continue
        seq[rec['source']] += 1
        list_name = 'lighted' if rec['source'] == 'lighted' else 'accessories'
        skey = f"{list_name}-{LIST_TAG}-{seq[rec['source']]:04d}"
        name, rest = split_name(rec['title'])
        if name.endswith(' NA'):
            name = name[:-3]
        details = clean_details([rest, *rec['notes']])
        label = f"{rec['item_number'] or '(no number)'} {name}"
        issued = year(rec['issued'], problems, label)
        retired = year(rec['retired'], problems, label)
        if not issued:
            problems.append(f'{label}: no issue year in source')
        if not rec['item_number']:
            problems.append(f'{label}: listed without an item number')
        category = 'building' if list_name == 'lighted' else 'accessory'
        row = {
            'source_key': skey,
            'list': list_name,
            'item_number': rec['item_number'] or '',
            'name': name,
            'details': details,
            'series': series_for(name, details),
            'year_issued': issued,
            'year_retired': retired,
            'is_current': '1' if rec['retired'] == 'Current' else '0',
            'srp_usd': money(rec['us']),
            'srp_cad': money(rec['cad']),
            'default_category': category,
            'parent_key': '',
            'source_page': rec['page'],
            'source_text': re.sub(r'\s+', ' ', ' '.join([rec['title'], *rec['notes']])).strip(),
        }
        rows.append(row)
        for n, piece in enumerate(rec['pieces'], 1):
            pname, prest = split_name(piece['title'])
            plabel = f"{rec['item_number']} piece {pname}"
            rows.append({
                'source_key': f'{skey}-p{n:02d}',
                'list': list_name,
                'item_number': rec['item_number'] or '',
                'name': pname,
                'details': clean_details([f'Part of "{name}"', prest, *piece['notes']]),
                'series': row['series'],
                'year_issued': year(piece['issued'] or rec['issued'], problems, plabel),
                'year_retired': year(piece['retired'], problems, plabel),
                'is_current': '1' if piece['retired'] == 'Current' else '0',
                'srp_usd': money(piece['us']),
                'srp_cad': money(piece['cad']),
                'default_category': category,
                'parent_key': skey,
                'source_page': rec['page'],
                'source_text': piece['title'],
            })
        for flag in rec['flags']:
            if not flag.startswith('no item number'):
                problems.append(f'{label}: {flag}')

    numbers = Counter(r['item_number'] for r in rows if r['item_number'] and not r['parent_key'])
    for num, count in sorted(numbers.items()):
        if count > 1:
            names = [r['name'] for r in rows if r['item_number'] == num and not r['parent_key']]
            problems.append(f'Item number {num} is used by {count} entries: ' + '; '.join(names))
    for r in rows:
        if not re.fullmatch(r'56\.\d{5}|\d{6,7}(FE)?|', r['item_number']):
            problems.append(f"{r['item_number']} {r['name']}: item number has an unusual format")

    fields = list(rows[0].keys())
    with open(csv_path, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator='\n')
        w.writeheader()
        w.writerows(rows)
    with open(report_path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(problems) + '\n')
    print(len(rows), 'rows;', len(problems), 'problems')


if __name__ == '__main__':
    main(*sys.argv[1:4])
