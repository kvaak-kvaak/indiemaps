#!/usr/bin/env python3
"""Seed areas.json: bboxes from Nominatim + FSA authority ids from the FHRS list.

  python3 resolve.py   # writes areas.json (keeps hand entries, fills the rest)

Phase 1: Essex (14 LADs) + Greater London (33 boroughs). Polite: 1 req/s.
"""
import json, re, time, urllib.request, urllib.parse
from pathlib import Path

HERE = Path(__file__).parent
ESSEX = ['Basildon', 'Braintree', 'Brentwood', 'Castle Point', 'Chelmsford',
         'Colchester', 'Epping Forest', 'Harlow', 'Maldon', 'Rochford',
         'Tendring', 'Uttlesford', 'Southend-on-Sea', 'Thurrock']
LONDON = ['City of London', 'Barking and Dagenham', 'Barnet', 'Bexley', 'Brent',
          'Bromley', 'Camden', 'Croydon', 'Ealing', 'Enfield', 'Greenwich',
          'Hackney', 'Hammersmith and Fulham', 'Haringey', 'Harrow', 'Havering',
          'Hillingdon', 'Hounslow', 'Islington', 'Kensington and Chelsea',
          'Kingston upon Thames', 'Lambeth', 'Lewisham', 'Merton', 'Newham',
          'Redbridge', 'Richmond upon Thames', 'Southwark', 'Sutton',
          'Tower Hamlets', 'Waltham Forest', 'Wandsworth', 'Westminster']
UA = {'User-Agent': 'yellowpages-pack-builder/0.1 (research prototype)'}


def slug(s):
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', s.lower())).strip('-')


def nominatim_bbox(q):
    url = ('https://nominatim.openstreetmap.org/search?format=json&limit=1&countrycodes=gb&q='
           + urllib.parse.quote(q))
    req = urllib.request.Request(url, headers=UA)
    d = json.load(urllib.request.urlopen(req, timeout=30))
    if not d:
        return None, None
    s, n, w, e = map(float, d[0]['boundingbox'])
    pad_lat, pad_lon = (n - s) * 0.03 + 0.002, (e - w) * 0.03 + 0.002
    return [round(w - pad_lon, 5), round(s - pad_lat, 5),
            round(e + pad_lon, 5), round(n + pad_lat, 5)], d[0]['display_name']


def fsa_ids():
    url = 'https://api1-ratings.food.gov.uk/authorities/xml'
    xml = urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=60).read().decode('utf-8')
    out = {}
    for m in re.finditer(r'<Name>(.*?)</Name>.*?<FileName>(.*?)</FileName>', xml):
        name, fn = m.group(1), m.group(2)
        mm = re.search(r'FHRS(\d+)en-GB', fn)
        key = re.sub(r'[^a-z]', '', name.lower())
        if mm and key not in out:
            out[key] = int(mm.group(1))
    return out


def main():
    areas = {}
    if (HERE / 'areas.json').exists():
        areas = json.load(open(HERE / 'areas.json'))
    fsa = fsa_ids()
    print(f'FSA authorities: {len(fsa)}')

    def add(parent, name):
        pid = f'{parent}/{slug(name)}'
        if pid in areas and areas[pid].get('bbox'):
            print(f'keep {pid}')
            return
        bbox, disp = nominatim_bbox(name + ', UK')
        time.sleep(1.1)
        if not bbox:
            print(f'NOMINATIM MISS: {name}')
            return
        key = re.sub(r'[^a-z]', '', name.lower())
        fid = fsa.get(key)
        areas[pid] = {'name': name, 'bbox': bbox, 'fsa': fid}
        print(f'{pid} bbox={bbox} fsa={fid} ({(disp or "")[:60]})')

    for n in ESSEX:
        if n == 'Southend-on-Sea':
            continue
        add('eu/gb/england/essex', n)
    for n in LONDON:
        if n == 'Hackney':
            continue
        add('eu/gb/england/greater-london', n)
    json.dump(areas, open(HERE / 'areas.json', 'w'), indent=1)
    print(f'areas.json: {len(areas)} areas')


if __name__ == '__main__':
    main()
