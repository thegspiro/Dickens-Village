"""Turn column-classified PDF lines into catalog records (sets + pieces)."""
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
    for i, r in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else None
        if is_start(r):
            desc = ' '.join([*pending_prefix, r['desc']]).strip()
            pending_prefix = []
            cur = {
                'source': source, 'item_number': r['num'], 'title': desc, 'notes': [],
                'issued': r['issued'], 'retired': r['retired'], 'us': r['us'], 'cad': r['cad'],
                'pieces': [], 'flags': [], 'page': r['page'],
            }
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


if __name__ == '__main__':
    # usage: build_records.py LIGHTED_LINES.json ACCESSORIES_LINES.json OUT.json
    out = []
    for path, source in ((sys.argv[1], 'lighted'), (sys.argv[2], 'accessories')):
        out.extend(build(json.load(open(path)), source))
    json.dump(out, open(sys.argv[3], 'w'), indent=1)
    print(len(out), 'records', sum(len(r['pieces']) for r in out), 'pieces')
