"""Normalize parsed records into the catalog CSV + an anomalies report.

usage: normalize.py RECORDS.json OUT.csv ISSUES.txt [--profile NAME]

A profile says which catalog list each record source becomes, the source_key
prefix and default category for it, the corrections for lines the parser
cannot read, and which extra checks to run. The default profile,
dickens-2025, reproduces data/catalog/dickens_village_2025.csv exactly.
"""
import csv
import json
import re
import sys
from collections import Counter

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

# General Village Accessories (2021). Only lines whose name the parser would get
# wrong are corrected; spelling in the source is kept as printed.
GENERAL_2021_OVERRIDES = {
    ('general', '4030905', '""Yosemite'): {
        'title': '"Yosemite Spruce Tree", set of 2',
        'report': 'source text starts with two quote marks',
    },
    ('general', '4038818', '""Glitzy'): {
        'title': '"Glitzy Holiday Trees", set of 2',
        'report': 'source text starts with two quote marks',
    },
    ('general', '4038841', '"Tudor Gardens Corner Hedge'): {
        'title': '"Tudor Gardens Corner Hedge", set of 4',
        'report': 'source text has the closing quote after "set of 4" and a stray backtick',
    },
    ('general', '4047588', 'Tudor Gardens Trees'): {
        'title': 'Tudor Gardens Trees',
        'report': 'US price is printed as "22..50" in the description column; US price left blank',
    },
    ('general', '6005517', 'Peppermint Pups 2020'): {
        'title': 'Peppermint Pups',
        'issued': '2020',
        'report': 'issue year 2020 is printed in the description column with a stray backtick',
    },
}

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


# source -> (catalog list name, source_key prefix, default category)
PROFILES = {
    'dickens-2025': {
        'sources': {
            'lighted': ('lighted', 'lighted-2025', 'building'),
            'accessories': ('accessories', 'accessories-2025', 'accessory'),
        },
        'overrides': OVERRIDES, 'drop': DROP, 'sections': None,
        'years': (1980, 2026), 'checks': False,
    },
    'general-2021': {
        'sources': {'general': ('general-accessories', 'general-2021', 'accessory')},
        'overrides': GENERAL_2021_OVERRIDES, 'drop': set(), 'sections': None,
        'years': (1975, 2021), 'checks': True,
    },
}


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


def year(raw, problems, label, years=(1980, 2026)):
    if raw in (None, '', 'Current'):
        return ''
    y = int(raw)
    if not years[0] <= y <= years[1]:
        problems.append(f'{label}: year {raw} in the source is impossible; left blank')
        return ''
    return str(y)


def _same_listing(a, b):
    keys = ('item_number', 'issued', 'retired', 'us', 'cad')
    return all(a[k] == b[k] for k in keys) and split_name(a['title'])[0].lower() == split_name(b['title'])[0].lower() \
        and not a['pieces'] and not b['pieces']


def main(records_path, csv_path, report_path, profile_name='dickens-2025'):
    profile = PROFILES[profile_name]
    years = profile['years']
    records = json.load(open(records_path))
    rows, problems = [], []
    seq = Counter()
    kept = []
    for rec in records:
        source_text = re.sub(r'\s+', ' ', ' '.join([rec['title'], *rec['notes']])).strip()
        for ok, change in profile['overrides'].items():
            if ok[0] == rec['source'] and ok[1] == rec['item_number'] and rec['title'].startswith(ok[2]):
                change = dict(change)
                report = change.pop('report', None)
                rec.update(change)
                if report:
                    rec['source_text'] = source_text
                    problems.append(f"{rec['item_number']} {split_name(rec['title'])[0]}: {report}")
        if (rec['source'], rec['item_number'], rec['title']) in profile['drop']:
            continue
        if profile['checks']:
            twin = next((k for k in kept if _same_listing(k, rec)), None)
            if twin is not None:
                problems.append(f"{rec['item_number']} {split_name(rec['title'])[0]}: listed again on page "
                                f"{rec['page']} with the same years and prices; kept the entry from page {twin['page']}")
                continue
            kept.append(rec)
        seq[rec['source']] += 1
        list_name, key_prefix, category = profile['sources'][rec['source']]
        skey = f"{key_prefix}-{seq[rec['source']]:04d}"
        name, rest = split_name(rec['title'])
        if name.endswith(' NA'):
            name = name[:-3]
        details = clean_details([rest, *rec['notes']])
        label = f"{rec['item_number'] or '(no number)'} {name}"
        issued = year(rec['issued'], problems, label, years)
        retired = year(rec['retired'], problems, label, years)
        if not issued:
            problems.append(f'{label}: no issue year in source')
        if not rec['item_number']:
            problems.append(f'{label}: listed without an item number')
        if profile['checks']:
            us, cad = money(rec['us']), money(rec['cad'])
            if us and cad and float(us) > 0 and not 0.8 <= float(cad) / float(us) <= 3:
                problems.append(f'{label}: US price ${us} and Canadian price ${cad} do not fit together; '
                                'one may be misprinted (kept as printed)')
        series = series_for(name, details)
        if profile['sections'] and rec.get('section'):
            series = profile['sections'][rec['section']]
        row = {
            'source_key': skey,
            'list': list_name,
            'item_number': rec['item_number'] or '',
            'name': name,
            'details': details,
            'series': series,
            'year_issued': issued,
            'year_retired': retired,
            'is_current': '1' if rec['retired'] == 'Current' else '0',
            'srp_usd': money(rec['us']),
            'srp_cad': money(rec['cad']),
            'default_category': category,
            'parent_key': '',
            'source_page': rec['page'],
            'source_text': rec.get('source_text') or re.sub(r'\s+', ' ', ' '.join([rec['title'], *rec['notes']])).strip(),
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
                'year_issued': year(piece['issued'] or rec['issued'], problems, plabel, years),
                'year_retired': year(piece['retired'], problems, plabel, years),
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
    args = sys.argv[1:]
    profile = 'dickens-2025'
    if '--profile' in args:
        at = args.index('--profile')
        profile = args[at + 1]
        del args[at:at + 2]
    if len(args) != 3 or profile not in PROFILES:
        raise SystemExit(__doc__ + '\nprofiles: ' + ', '.join(PROFILES))
    main(*args, profile_name=profile)
