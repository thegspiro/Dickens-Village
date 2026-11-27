"""Turn column-classified PDF lines into catalog records (sets + pieces).

usage:
    build_records.py LIGHTED_LINES.json ACCESSORIES_LINES.json OUT.json
    build_records.py --out OUT.json SOURCE=LINES.json [SOURCE=LINES.json ...]

The first form is the one used for the 2025 Dickens' Village lists (sources
"lighted" and "accessories"). The second names each input's source, which
normalize.py's profile maps to a catalog list. Section headings written by
dump_lines.py --section are stored on each record as "section".
"""
import json
import re
import sys

SET_RE = re.compile(r'\b(set|sets)\b', re.I)


def is_start(r):
    return bool(r['num'])


def priced(r):
    return bool(r['us'] or r['cad'])


def build(lines, source):
    records = []
    pending_prefix = []
    cur = None
    section = None
    for i, r in enumerate(lines):
        if 'section' in r:
            # A new heading: nothing before it continues past it.
            section = r['section']
            pending_prefix = []
            cur = None
            continue
        nxt = next((n for n in lines[i + 1:] if 'section' not in n), None)
        if is_start(r):
            desc = ' '.join([*pending_prefix, r['desc']]).strip()
            pending_prefix = []
            cur = {
                'source': source, 'item_number': r['num'], 'title': desc, 'notes': [],
                'issued': r['issued'], 'retired': r['retired'], 'us': r['us'], 'cad': r['cad'],
                'pieces': [], 'flags': [], 'page': r['page'],
            }
            if section is not None:
                cur['section'] = section
            if not priced(r):
                cur['flags'].append('start line without price')
            records.append(cur)
            continue
        # line without an item number
        if r['box'] and not priced(r):
            if r['desc']:
                pending_prefix.append(r['desc'])
            continue
        if (not priced(r) and r['desc'] and nxt and is_start(nxt) and not nxt['desc']
                and r['issued'] is None):
            pending_prefix.append(r['desc'])
            continue
        if cur is None:
            continue
        if priced(r):
            if not r['desc']:
                cur['flags'].append(f"price-only line ignored: {r}")
                continue
            if SET_RE.search(cur['title']):
                cur['pieces'].append({'title': r['desc'], 'issued': r['issued'], 'retired': r['retired'],
                                      'us': r['us'], 'cad': r['cad'], 'notes': []})
            else:
                # a separate product listed without its own item number
                cur = {
                    'source': source, 'item_number': None, 'title': r['desc'], 'notes': [],
                    'issued': r['issued'], 'retired': r['retired'], 'us': r['us'], 'cad': r['cad'],
                    'pieces': [], 'flags': ['no item number in source'], 'page': r['page'],
                }
                if section is not None:
                    cur['section'] = section
                records.append(cur)
            continue
        # unpriced continuation line
        target = cur['pieces'][-1] if cur['pieces'] and not r['issued'] else cur
        if r['issued'] and cur['issued'] is None:
            cur['issued'] = r['issued']
        if r['desc']:
            if target is cur and cur['title'].count('"') % 2 == 1:
                cur['title'] += ' ' + r['desc']
            elif target is cur and not cur['title']:
                cur['title'] = r['desc']
            elif (target is cur and not cur['notes'] and '"' not in cur['title']
                  and re.match(r'^(of \d+|\d[\d,]*|[a-z(]|pcs|edition|Carol\)|Christmas Carol\)|Exclusive\)|Program)', r['desc'])):
                cur['title'] += ' ' + r['desc']
            else:
                target['notes'].append(r['desc'])
    return records


def main(argv):
    if argv and argv[0] == '--out':
        dest, inputs = argv[1], []
        for spec in argv[2:]:
            source, _, path = spec.partition('=')
            if not source or not path:
                raise SystemExit(f'expected SOURCE=LINES.json, got {spec!r}')
            inputs.append((path, source))
    elif len(argv) == 3:
        dest, inputs = argv[2], [(argv[0], 'lighted'), (argv[1], 'accessories')]
    else:
        raise SystemExit(__doc__)
    out = []
    for path, source in inputs:
        with open(path) as fh:
            out.extend(build(json.load(fh), source))
    with open(dest, 'w') as fh:
        json.dump(out, fh, indent=1)
    print(len(out), 'records', sum(len(r['pieces']) for r in out), 'pieces')


if __name__ == '__main__':
    main(sys.argv[1:])
