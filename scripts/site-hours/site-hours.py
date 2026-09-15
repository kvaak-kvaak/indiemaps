"""Prototype: first-party opening-hours spider for Overture-derived POIs.

For each POI website: fetch homepage -> schema.org JSON-LD hours
(openingHours / openingHoursSpecification, often already OSM-compatible) ->
else discover hours/contact/visit pages (1 hop, same host) -> conservative
regex fallback. Also records existence signals (HTTP 200 + business name or
postcode on page) — a dead domain is itself information.

Usage:
  python3 site-hours.py --in /tmp/hackney_food_sample.json --out /tmp/site_hours.json

Stdlib only. Be polite: small sample sizes, few pages per host, browser UA.
Production use wants robots.txt compliance, crawl-delay, and JS rendering
for SPA sites (recorded here as js_required instead).
"""
import argparse, concurrent.futures, html, http.client, json, re, socket, ssl, threading, time, urllib.parse, urllib.error, urllib.request
from pathlib import Path

UA = {'User-Agent': 'Mozilla/5.0 (compatible; yellowpages-hours-prototype/0.1; +https://github.com/kvaak-kvaak/indiemaps)'}
CONNECT_TIMEOUT = 5   # dead hosts fail here, fast
READ_TIMEOUT = 15     # slow-but-real shared hosting gets room
MAX_BODY = 1_500_000
HOST_GAP = 0.5        # min seconds between hits to the same host

_SSL_CTX = ssl.create_default_context()
_POOL = {}
_POOL_LOCK = threading.Lock()
_HOST_LAST = {}
_HOST_LOCK = threading.Lock()
_HOST_DELAY = {}  # host -> crawl-delay seconds (robots.txt)
_ROBOTS = {}      # host -> (allowed_fn, delay) — fetched once per run
_ROBOTS_LOCK = threading.Lock()
_DEAD = {}  # set from --dead-cache in main(); {domain: {fails, last}}
HOUR_LINK_RE = re.compile(r'hour|opening|open-|/open|contact|visit|find[-\s]?us|location|about', re.I)
DAY = r'(?:mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?|ma(?:nantai)?|ti(?:istai)?|ke(?:skiviikko)?|to(?:rstai)?|pe(?:rjantai)?|la(?:uantai)?|su(?:nnuntai)?)s?'
TIME = r'(\d{1,2})(?:(?::|\.)(\d{2}))?\s*(am|pm)?'  # '.' separator for FI-style 9.00–17.00
SECOND_RANGE_RE = re.compile(rf'^\s*(?:,|and|&|\+)\s*{TIME}\s*(?:-|–|to)\s*{TIME}', re.I)
DAY_WORD_RE = re.compile(r'mon|tue|wed|thu|fri|sat|sun|ma\b|ti\b|ke\b|to\b|pe\b|la\b|su\b', re.I)
DELIVERY_RE = re.compile(r'deliver|take\s?away|collection', re.I)
DINEIN_RE = re.compile(r'dine.?in|eat.?in|restaurant|kitchen|opening|hours|visit', re.I)
DAYSEP = r'(?:\s*([-–&/]|to|through|thru|and)\s*' + DAY + r')?'
TIMESEP = r'(?:\s*(?:-|–|to)\s*)'
RANGE_RE = re.compile(
    rf'({DAY}{DAYSEP})\s*[:\-–]?\s*{TIME}{TIMESEP}{TIME}', re.I)
OSM_DAYS = {'monday': 'Mo', 'mon': 'Mo', 'tuesday': 'Tu', 'tue': 'Tu', 'tues': 'Tu',
            'wednesday': 'We', 'wed': 'We', 'thursday': 'Th', 'thu': 'Th', 'thur': 'Th',
            'thurs': 'Th', 'friday': 'Fr', 'fri': 'Fr', 'saturday': 'Sa', 'sat': 'Sa',
            'sunday': 'Su', 'sun': 'Su',
            'maanantai': 'Mo', 'ma': 'Mo', 'tiistai': 'Tu', 'ti': 'Tu',
            'keskiviikko': 'We', 'ke': 'We', 'torstai': 'Th', 'to': 'Th',
            'perjantai': 'Fr', 'pe': 'Fr', 'lauantai': 'Sa', 'la': 'Sa',
            'sunnuntai': 'Su', 'su': 'Su',
            # 3-letter truncations (regex_hours looks up day[:3])
            'maa': 'Mo', 'tii': 'Tu', 'kes': 'We', 'tor': 'Th',
            'per': 'Fr', 'lau': 'Sa'}
FOOD_TYPES = {'restaurant', 'foodestablishment', 'cafeorcoffeeshop', 'barorpub',
              'bakery', 'icecreamshop', 'fastfoodrestaurant', 'store', 'localbusiness'}


def _host_key(scheme, host, port):
    return f'{scheme}://{host}:{port}'


def _checkout(key):
    with _POOL_LOCK:
        lst = _POOL.get(key)
        if lst:
            return lst.pop()
    return None


def _checkin(key, conn):
    with _POOL_LOCK:
        lst = _POOL.setdefault(key, [])
        if len(lst) < 4:
            lst.append(conn)
        else:
            try:
                conn.close()
            except Exception:
                pass


def _polite(host):
    with _HOST_LOCK:
        gap = max(HOST_GAP, _HOST_DELAY.get(host, 0))
        last = _HOST_LAST.get(host, 0)
        wait = gap - (time.time() - last)
        _HOST_LAST[host] = time.time() + max(0, wait)
    if wait > 0:
        time.sleep(wait)


def _robots_rules(scheme, host, port):
    """Fetch+parse robots.txt once per host. Returns (disallows, delay).
    Unreachable robots = allow (standard). Only honors User-agent: *."""
    key = _host_key(scheme, host, port)
    with _ROBOTS_LOCK:
        if key in _ROBOTS:
            return _ROBOTS[key]
    disallows, delay, in_star = [], 0, False
    try:
        cls = http.client.HTTPSConnection if scheme == 'https' else http.client.HTTPConnection
        kw = {'timeout': 8}
        if scheme == 'https':
            kw['context'] = _SSL_CTX
        c = cls(host, port, **kw)
        c.request('GET', '/robots.txt', headers={'User-Agent': UA['User-Agent']})
        r = c.getresponse()
        body = r.read(100_000).decode('utf-8', 'replace') if r.status == 200 else ''
        try:
            c.close()
        except Exception:
            pass
        for line in body.splitlines():
            line = line.split('#', 1)[0].strip()
            if not line or ':' not in line:
                continue
            k, v = line.split(':', 1)
            k, v = k.strip().lower(), v.strip()
            if k == 'user-agent':
                in_star = (v == '*')
            elif in_star and k == 'disallow' and v:
                disallows.append(v)
            elif in_star and k == 'crawl-delay':
                try:
                    delay = max(delay, float(v))
                except ValueError:
                    pass
    except Exception:
        pass
    with _ROBOTS_LOCK:
        _ROBOTS[key] = (disallows, delay)
        if delay:
            _HOST_DELAY[host] = max(_HOST_DELAY.get(host, 0), min(delay, 30))
    return disallows, delay


def _allowed(scheme, host, port, path):
    disallows, _ = _robots_rules(scheme, host, port)
    return not any(path.startswith(d) for d in disallows)


def _new_conn(scheme, host, port):
    cls = http.client.HTTPSConnection if scheme == 'https' else http.client.HTTPConnection
    kw = {'timeout': CONNECT_TIMEOUT}
    if scheme == 'https':
        kw['context'] = _SSL_CTX
    return cls(host, port, **kw)


def fetch(url, _hops=0):
    """Pooled keep-alive fetch. 5s connect / 15s read. Manual redirect chain
    (301/302/303/307/308). Returns {ok, status, url, html, ms} or {ok, error}."""
    if url and not re.match(r'^https?://', url, re.I):
        url = 'http://' + url
    t0 = time.time()
    try:
        parts = urllib.parse.urlsplit(url)
        scheme = (parts.scheme or 'http').lower()
        host = parts.hostname or ''
        port = parts.port or (443 if scheme == 'https' else 80)
        if not host or scheme not in ('http', 'https'):
            return {'ok': False, 'error': f'ValueError: bad url {url[:80]}'}
        if _hops > 5:
            return {'ok': False, 'error': 'ValueError: redirect loop'}
        _polite(host)
        key = _host_key(scheme, host, port)
        path = parts.path or '/'
        if parts.query:
            path += '?' + parts.query
        if not _allowed(scheme, host, port, path):
            return {'ok': False, 'error': 'robots: disallowed by robots.txt'}
        conn = _checkout(key)
        fresh = conn is None
        if fresh:
            conn = _new_conn(scheme, host, port)
        try:
            conn.request('GET', path, headers={'User-Agent': UA['User-Agent'],
                                               'Accept': 'text/html,application/xhtml+xml',
                                               'Connection': 'keep-alive'})
            try:
                conn.sock.settimeout(READ_TIMEOUT)
            except Exception:
                pass
            resp = conn.getresponse()
            status = resp.status
            if status in (301, 302, 303, 307, 308):
                loc = resp.getheader('Location')
                try:
                    conn.close()
                except Exception:
                    pass
                if not loc:
                    return {'ok': False, 'error': f'HTTPError: redirect {status} without Location'}
                return fetch(urllib.parse.urljoin(url, loc), _hops + 1)
            if status != 200:
                try:
                    conn.close()
                except Exception:
                    pass
                return {'ok': False, 'status': status,
                        'error': f'HTTPError: HTTP Error {status}: {resp.reason}'}
            raw = resp.read(MAX_BODY)
            try:
                if not resp.will_close:
                    _checkin(key, conn)
                else:
                    conn.close()
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            if not fresh:
                # pooled connection may have gone stale — one retry, fresh
                return fetch(url, _hops)
            raise
        ct = resp.getheader('Content-Type') or ''
        m = re.search(r'charset=([\w-]+)', ct)
        enc = m.group(1) if m else 'utf-8'
        try:
            html_text = raw.decode(enc, 'replace')
        except Exception:
            html_text = raw.decode('utf-8', 'replace')
        return {'ok': True, 'status': 200, 'url': url,
                'html': html_text, 'ms': int((time.time() - t0) * 1000)}
    except (socket.timeout, TimeoutError) as e:
        return {'ok': False, 'error': f'timeout: {e}'}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}'}


def visible_text(html_text, keep_alt=True):
    txt = re.sub(r'<script.*?</script>', ' ', html_text, flags=re.S | re.I)
    txt = re.sub(r'<style.*?</style>', ' ', txt, flags=re.S | re.I)
    if keep_alt:
        # logo-as-image sites carry the business name in alt/title attributes
        txt = re.sub(r'''\b(?:alt|title)=["']([^"']+)["']''',
                     lambda m: ' ' + m.group(1) + ' ', txt, flags=re.I)
    txt = re.sub(r'<[^>]+>', ' ', txt)
    return html.unescape(re.sub(r'\s+', ' ', txt))


def norm(s):
    return re.sub(r'[^a-z0-9åäö ]', ' ', (s or '').lower())  # åäö retained (FI/SE names)


def page_identity(html_text):
    """Who the site claims to be: <title>, og:site_name, JSON-LD names.
    Brand pages always carry this even when body text is a JS shell."""
    bits = []
    m = re.search(r'<title>(.*?)</title>', html_text, flags=re.S | re.I)
    if m:
        bits.append(visible_text(m.group(1)))
    bits.append(meta_content(html_text, 'og:site_name'))
    for node in iter_jsonld(html_text):
        nm = node.get('name')
        if isinstance(nm, str):
            bits.append(nm)
    return ' '.join(b for b in bits if b)


def name_on_page(name, text, postcode, html_text=''):
    """True = confirmed (body text or page identity), False = contradicted
    (substantial human text, absent everywhere), None = can't tell."""
    nt = set(w for w in norm(name).split() if len(w) > 2 and w not in ('the', 'and'))
    if not nt:
        return None
    t = norm(text)
    ident = norm(page_identity(html_text)) if html_text else ''
    pt = set(t.split())
    hit = 0
    for w in nt:
        if w in t or w in ident:
            hit += 1
            continue
        # possessives/plurals: 'mcdonalds' vs page 'mcdonald'
        pool = (pt | set(ident.split())) if ident else pt
        if any((p.startswith(w[:6]) or w.startswith(p[:6])) for p in pool if len(p) >= 6 and len(w) >= 6):
            hit += 1
    if hit / len(nt) >= 0.5:
        return True
    if postcode and postcode.replace(' ', '').lower() in t.replace(' ', ''):
        return True
    if len(text or '') < 300 or (html_text and js_hint(html_text)):
        return None
    return False


def iter_jsonld(html_text):
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                         html_text, flags=re.S | re.I):
        try:
            data = json.loads(m.group(1).strip())
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                yield node
                for v in node.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)


def to_minutes(h, m, ap):
    h, m = int(h), int(m or 0)
    if not ap:
        return h * 60 + m
    if ap == 'pm' and h < 12:
        h += 12
    if ap == 'am' and h == 12:
        h = 0
    return h * 60 + m


def infer_range(h1, m1, ap1, h2, m2, ap2):
    """Resolve bare start hours: '8 - 9pm' -> 20-21, '9 - 5pm' -> 9-17."""
    ap1, ap2 = (ap1 or '').lower(), (ap2 or '').lower()
    if not ap1 and ap2 == 'pm' and int(h1) < int(h2 or 0) <= 12:
        ap1 = 'pm'
    if not ap1 and not ap2:
        if int(h1) == 0 or int(h2 or 0) == 0:
            pass  # explicit midnight ('00:30') = 24h clock, no inference
        elif int(h2 or 0) > 12:
            pass  # 24h clock evidence: bare '12:00-22:00' is noon-22, not 00-22
        elif int(h1) >= int(h2 or 0):
            ap1, ap2 = 'am', 'pm'
        else:
            ap1, ap2 = 'am', 'am'
    o, c = to_minutes(h1, m1, ap1 or None), to_minutes(h2, m2, ap2 or None)
    # overnight wrap (22:00-02:00) is real for bars; keep, don't validate away
    return o, c


def spec_to_osm(specs):
    """openingHoursSpecification -> OSM-ish string."""
    by_day = {}
    for sp in specs if isinstance(specs, list) else [specs]:
        if not isinstance(sp, dict):
            continue
        days = sp.get('dayOfWeek', [])
        if isinstance(days, str):
            days = [days]
        oh = f"{sp.get('opens', '')}-{sp.get('closes', '')}"
        if not re.match(r'\d{2}:\d{2}-\d{2}:\d{2}', oh):
            continue
        for d in days:
            key = d.rsplit('/', 1)[-1][:2].title()
            if key in ('Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'):
                by_day[key] = oh
    if not by_day:
        return ''
    order = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']
    groups, cur = [], None
    for d in order:
        if d not in by_day:
            if cur:
                groups.append(cur); cur = None
            continue
        if cur and cur[1] == by_day[d] and order.index(d) == order.index(cur[2]) + 1:
            cur = (cur[0], cur[1], d)
        else:
            if cur:
                groups.append(cur)
            cur = (d, by_day[d], d)
    if cur:
        groups.append(cur)
    return '; '.join(f'{a}-{b} {h}' if a != b else f'{a} {h}' for a, h, b in groups)


def jsonld_hours(html_text):
    """Returns [(label, hours)] — label keeps branch context for multi-site pages."""
    found = []
    for node in iter_jsonld(html_text):
        types = node.get('@type', [])
        types = [types] if isinstance(types, str) else types
        if not any(str(t).lower() in FOOD_TYPES for t in types):
            continue
        label = node.get('name') or node.get('branch') or ''
        addr = node.get('address')
        if isinstance(addr, dict):
            label += ' | ' + (addr.get('streetAddress', '') or '')
        oh = node.get('openingHours')
        if isinstance(oh, str):
            oh = [oh]
        if isinstance(oh, list):
            for x in oh:
                if isinstance(x, str) and x.strip(' ,'):
                    found.append((label[:80], x.strip()))
        spec = spec_to_osm(node.get('openingHoursSpecification'))
        if spec:
            found.append((label[:80], spec))
    return found


def links(html_text, base):
    out = []
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html_text, flags=re.S | re.I):
        href, label = m.group(1), visible_text(m.group(2))
        if href.startswith(('mailto:', 'tel:', '#', 'javascript:')):
            continue
        url = urllib.parse.urljoin(base, href)
        if urllib.parse.urlparse(url).netloc != urllib.parse.urlparse(base).netloc:
            continue
        if HOUR_LINK_RE.search(href) or HOUR_LINK_RE.search(label):
            out.append(url)
    seen, uniq = set(), []
    for u in out:
        if u not in seen:
            seen.add(u); uniq.append(u)
    return uniq[:3]


def section_kind(text, pos):
    """Dine-in vs delivery/takeaway from nearest preceding section header.
    Compound headers ('Delivery Hours') count as delivery even though they
    contain the word 'hours'."""
    KW = re.compile(r'(delivery|take\s?away|collection|dine.?in|eat.?in|restaurant|kitchen|opening|hours|visit)\b', re.I)
    window = text[max(0, pos - 400):pos]
    ms = list(KW.finditer(window))
    if not ms:
        return 'dine-in'
    last = ms[-1]
    head = window[max(0, last.start() - 20):last.end()]
    if DELIVERY_RE.search(head):
        return 'delivery'
    if last.group(1).lower().startswith(('deliver', 'take', 'collection')):
        return 'delivery'
    return 'dine-in'


def expand_days(expr):
    order = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']
    out = []
    for chunk in expr.split(','):
        c = chunk.strip()
        if '-' in c:
            a, b = [x.strip()[:2].title() for x in c.split('-', 1)]
            if a in order and b in order:
                i, j = order.index(a), order.index(b)
                k = i
                while True:
                    out.append(order[k])
                    if k == j:
                        break
                    k = (k + 1) % 7
        elif c[:2].title() in order:
            out.append(c[:2].title())
    return out


def parse_table(osm_str):
    """OSM-ish hours string -> {day: sorted range tuples} for comparison."""
    table = {}
    for part in (osm_str or '').split(';'):
        m = re.match(r'\s*([A-Za-z,\-]+)\s+(\d{2}:\d{2})-(\d{2}:\d{2})', part.strip())
        if not m:
            continue
        for d in expand_days(m.group(1)):
            table.setdefault(d, []).append((m.group(2), m.group(3)))
    return {d: sorted(v) for d, v in table.items()}


def regex_hours(text):
    # hyphen-times first ("2-30 pm" -> "2:30 pm", meridiem required — safe)
    text = re.sub(r'(\d{1,2})-(\d{2})(?=\s*(?:am|pm)\b)', r'\1:\2', text, flags=re.I)
    hits = []
    for m in RANGE_RE.finditer(text):
        day, sep, h1, m1, ap1, h2, m2, ap2 = m.groups()
        # Day words from the day expression ('Mon-Fri', 'ma-pe', 'Mon and Fri'):
        # spaceless hyphenates must split ('ma-pe' -> ma, pe), connector words
        # ('and') are ignored, lookup is on the 3-letter stem.
        words = [w for w in re.findall(r'[a-zåäö]+', day.lower()) if w[:3] in OSM_DAYS]
        if not words:
            continue
        d1, d2 = OSM_DAYS[words[0][:3]], OSM_DAYS[words[-1][:3]]
        ap2 = ap2 or ap1
        ranges = []
        try:
            o, c = infer_range(h1, m1, ap1, h2, m2, ap2)
            if o == 0 and c > 12 * 60 and re.search(r'\b12\s*am\b', m.group(0), re.I):
                o = 12 * 60  # "12am - 11pm" means noon; a real "12am-4am" keeps 00:00
            if 0 <= o <= 24 * 60 and 0 <= c <= 24 * 60 and o != c:
                ranges.append((o, c))
        except Exception:
            pass
        # second range, same days ("12pm-2:30pm, 5:30pm-10:30pm")
        tail = text[m.end():m.end() + 60]
        m2 = SECOND_RANGE_RE.match(tail)
        if m2 and not DAY_WORD_RE.match(tail.strip()[:3]):
            try:
                o2, c2 = infer_range(*m2.groups())
                if 0 <= o2 <= 24 * 60 and 0 <= c2 <= 24 * 60 and o2 != c2:
                    ranges.append((o2, c2))
            except Exception:
                pass
        if not ranges:
            continue
        # 'and' joins discrete days (Mon and Fri), ranges join spans (Mo-Fr)
        span = f'{d1},{d2}' if (sep or '').lower() == 'and' and d1 != d2 else (f'{d1}-{d2}' if d1 != d2 else d1)
        kind = section_kind(text, m.start())
        for o, c in ranges:
            # OSM syntax accepts overnight spill (e.g. Fr 18:00-02:00)
            hits.append({'osm': f'{span} {o//60:02d}:{o%60:02d}-{c//60:02d}:{c%60:02d}',
                         'raw': (m.group(0) + (m2.group(0) if len(ranges) > 1 else '')).strip()[:100],
                         'kind': kind})
    return hits


def ranges_overlap(a, b):
    """Two (open,close) minute-ranges sharing interior time but differing."""
    if a == b:
        return False
    return max(a[0], b[0]) < min(a[1], b[1])


def self_contradicts(osm_str):
    """Same day stated with overlapping-but-different hours (bar vs kitchen
    piles, summer/winter leftovers). Split shifts (lunch/dinner) are fine."""
    t = parse_table(osm_str)
    return any(ranges_overlap(a, b)
               for ranges in t.values() for a in ranges for b in ranges)


def specificity(osm_str):
    """Detail score: ranges + day-groups. More maintained schedules score higher."""
    t = parse_table(osm_str)
    return sum(len(v) for v in t.values()) + len(t)


def meta_content(html_text, *names):
    """First matching <meta name|property=... content=...> value."""
    for m in re.finditer(r'<meta[^>]+>', html_text, flags=re.I):
        tag = m.group(0)
        key = re.search(r'''(?:name|property)=["']([^"']+)["']''', tag, re.I)
        val = re.search(r'''content=["']([^"']+)["']''', tag, re.I)
        if key and val and key.group(1).strip().lower() in names:
            return html.unescape(val.group(1).strip())
    return ''


def js_hint(html_text):
    low = html_text.lower()
    noscriptish = len(visible_text(html_text)) < 300
    spa = 'id="root"' in low or 'id="app"' in low or '__next_data__' in low or 'nuxt' in low
    return bool(noscriptish and spa)


SOCIAL_RE = re.compile(r'facebook\.com|instagram\.com|twitter\.com|x\.com/|tiktok\.com', re.I)
MARKET_RE = re.compile(r'deliveroo\.|just-eat|ubereats|tripadvisor\.|yelp\.com', re.I)
DEAD_COOLDOWN_DAYS = 7


def site_kind(url):
    u = (url or '').lower()
    if SOCIAL_RE.search(u):
        return 'social-only'
    if MARKET_RE.search(u):
        return 'marketplace'
    return 'own-domain'


def dead_host(url, cache):
    """Network-level failures cached per domain (not HTTP statuses)."""
    try:
        host = (urllib.parse.urlsplit(url).hostname or '').lower()
    except Exception:
        return False
    rec = cache.get(host)
    if not rec or rec.get('fails', 0) < 2:
        return False
    try:
        age = (time.time() - rec.get('last', 0)) / 86400
    except Exception:
        return False
    return age < DEAD_COOLDOWN_DAYS


def note_dead(url, error, cache):
    if re.match(r'^(URLError|timeout|TimeoutError|ConnectionResetError|SSLError|OSError|socket|ConnectionRefused)', error or ''):
        try:
            host = (urllib.parse.urlsplit(url).hostname or '').lower()
        except Exception:
            return
        if host:
            rec = cache.get(host, {'fails': 0, 'last': 0})
            rec['fails'] += 1
            rec['last'] = time.time()
            cache[host] = rec


def head_image(url):
    """HEAD-check an image URL: raster type + sane size. Returns bytes or 0."""
    try:
        req = urllib.request.Request(url, headers=UA, method='HEAD')
        with urllib.request.urlopen(req, timeout=10) as r:
            ct = (r.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            if ct not in ('image/jpeg', 'image/png', 'image/webp', 'image/gif'):
                return 0
            ln = r.headers.get('Content-Length')
            return int(ln) if ln and ln.isdigit() else -1
    except Exception:
        return 0


MENU_NOISE_RE = re.compile(r'/offers?/|/blog|/news|/careers|/jobs|/gift', re.I)


def site_extras(html_text, base_url):
    """Images, menu links, description, cuisine, price — first-party enrichment."""
    out = {'images': [], 'menu': [], 'description': '', 'cuisine': [],
           'price_range': ''}
    seen_img = set()
    # og:image first (built for link previews = best single thumbnail)
    og = meta_content(html_text, 'og:image')
    if og:
        u = urllib.parse.urljoin(base_url, og)
        w = meta_content(html_text, 'og:image:width')
        h = meta_content(html_text, 'og:image:height')
        out['images'].append({'url': u, 'kind': 'og:image',
                              'w': int(w) if w.isdigit() else None,
                              'h': int(h) if h.isdigit() else None})
        seen_img.add(u)
    for node in iter_jsonld(html_text):
        types = node.get('@type', [])
        types = [types] if isinstance(types, str) else types
        low = [str(t).lower() for t in types]
        for key, kind in (('image', 'json-ld'), ('photo', 'json-ld'), ('logo', 'logo')):
            imgs = node.get(key, [])
            imgs = [imgs] if isinstance(imgs, str) else (imgs if isinstance(imgs, list) else [])
            for im in imgs:
                u = im.get('url') if isinstance(im, dict) else im
                if isinstance(u, str) and u.startswith('http') and u not in seen_img:
                    seen_img.add(u)
                    out['images'].append({'url': urllib.parse.urljoin(base_url, u), 'kind': kind,
                                          'w': None, 'h': None})
        if any(t in FOOD_TYPES for t in low):
            for c in node.get('servesCuisine', []) if isinstance(node.get('servesCuisine', []), list) \
                    else [node.get('servesCuisine')]:
                if isinstance(c, str) and c.strip() and c.strip() not in out['cuisine']:
                    out['cuisine'].append(c.strip()[:60])
            pr = node.get('priceRange')
            if isinstance(pr, str) and pr.strip() and not out['price_range']:
                out['price_range'] = pr.strip()[:20]
            desc = node.get('description')
            if isinstance(desc, str) and desc.strip() and not out.get('ld_description'):
                out['ld_description'] = desc.strip()[:400]
    touch = re.search(r'''<link[^>]+rel=["']apple-touch-icon["'][^>]+href=["']([^"']+)["']''',
                      html_text, flags=re.I)
    if touch and touch.group(1) not in seen_img:
        out['images'].append({'url': urllib.parse.urljoin(base_url, touch.group(1)),
                              'kind': 'touch-icon', 'w': None, 'h': None})
    # menu links: link TEXT must be menu-like; fragments/JS/nav-buttons excluded
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
                         html_text, flags=re.S | re.I):
        href, label = m.group(1).strip(), visible_text(m.group(2)).strip().lower()
        if not href or href.startswith(('mailto:', 'tel:', '#', 'javascript:')):
            continue
        if MENU_NOISE_RE.search(href):
            continue
        if not re.search(r'\bmenus?\b|\bfood\b|\beat\b|\bcatering\b', label) and \
           not re.search(r'menu|food-menu|our-food|eat', href, re.I):
            continue
        if re.search(r'nav|header|hamburger|toggle', m.group(0)[:120], re.I) and \
           not href.lower().endswith(('.pdf', '/menu', '/menu/', '/menus')):
            continue
        url = urllib.parse.urljoin(base_url, href)
        if urllib.parse.urlparse(url).netloc != urllib.parse.urlparse(base_url).netloc \
           and 'deliveroo' not in url and 'flipdish' not in url:
            continue
        if url not in [x['url'] for x in out['menu']]:
            kind = 'pdf' if url.lower().endswith('.pdf') else \
                   ('third-party' if 'deliveroo' in url or 'flipdish' in url else 'page')
            out['menu'].append({'url': url, 'label': label[:40] or 'Menu', 'kind': kind})
        if len(out['menu']) >= 3:
            break
    desc = meta_content(html_text, 'og:description', 'description')
    if desc:
        out['description'] = desc[:300]
    return out
    low = html_text.lower()
    noscriptish = len(visible_text(html_text)) < 300
    spa = 'id="root"' in low or 'id="app"' in low or '__next_data__' in low or 'nuxt' in low
    return bool(noscriptish and spa)


def verdict(jsonld, regex_hits):
    """Cross-check structured vs visible hours from the SAME site.
    Returns (primary, alt, internal_conflict). Specificity wins ties broken
    toward JSON-LD; delivery-only regex never becomes primary."""
    jl_str = '; '.join(dict.fromkeys(h for _, h in jsonld))
    dine = [h['osm'] for h in regex_hits if h['kind'] == 'dine-in']
    rx_str = '; '.join(dict.fromkeys(dine))
    if jl_str and rx_str:
        jt, rt = parse_table(jl_str), parse_table(rx_str)
        shared = set(jt) & set(rt)
        agree = bool(shared) and all(jt[d] == rt[d] for d in shared) and \
            abs(len(set(jt) ^ set(rt))) <= 1
        if agree:
            return ({'osm': jl_str, 'method': 'json-ld', 'kind': 'dine-in'},
                    None, False)
        rx_bad = self_contradicts(rx_str)
        sj, sr = specificity(jl_str), specificity(rx_str)
        if not rx_bad and sr >= sj:
            return ({'osm': rx_str, 'method': 'regex', 'kind': 'dine-in'},
                    {'osm': jl_str, 'method': 'json-ld', 'kind': 'dine-in'}, True)
        return ({'osm': jl_str, 'method': 'json-ld', 'kind': 'dine-in'},
                {'osm': rx_str, 'method': 'regex', 'kind': 'dine-in'}, True)
    if jl_str:
        return ({'osm': jl_str, 'method': 'json-ld', 'kind': 'dine-in'}, None, False)
    if rx_str:
        return ({'osm': rx_str, 'method': 'regex', 'kind': 'dine-in'}, None, False)
    return (None, None, False)
    low = html_text.lower()
    noscriptish = len(visible_text(html_text)) < 300
    spa = 'id="root"' in low or 'id="app"' in low or '__next_data__' in low or 'nuxt' in low
    return bool(noscriptish and spa)


def process(poi):
    t_start = time.time()
    res = {'name': poi['name'], 'site': poi['website'], 'postcode': poi.get('postcode'),
           'pages': [], 'jsonld': [], 'regex': [], 'name_on_page': False,
           'js_required': False, 'fetch_ok': False, 'ms_fetch': 0, 'ms_parse': 0,
           'crawled_at': time.strftime('%Y-%m-%d')}
    kind = site_kind(poi['website'])
    if kind != 'own-domain':
        res['skipped'] = kind  # social/marketplace presence: unscrapable, not a failure
        res['ms_parse'] = int((time.time() - t_start) * 1000)
        return res
    if dead_host(poi['website'], _DEAD):
        res['skipped'] = 'dead-domain'
        res['ms_parse'] = int((time.time() - t_start) * 1000)
        return res
    home = fetch(poi['website'])
    res['ms_fetch'] += home.get('ms', 0)
    res['pages'].append({'url': poi['website'], 'ok': home.get('ok'),
                         'status': home.get('status'), 'final': home.get('url'),
                         'error': home.get('error')})
    if not home.get('ok'):
        return res
    res['fetch_ok'] = True
    html0 = home['html']
    res['name_on_page'] = name_on_page(poi['name'], visible_text(html0)[:20000], poi.get('postcode'), html0)
    ident = page_identity(html0)
    res['identity'] = ident[:120]
    pt = set(w for w in norm(poi['name']).split() if len(w) >= 4 and w not in ('the', 'and', 'southend', 'Leigh', 'sea', 'old'))
    it = set(w for w in norm(ident).split() if len(w) >= 4)
    res['identity_match'] = bool(ident.strip() and (pt & it))
    try:
        res['extras'] = site_extras(html0, home.get('url') or poi['website'])
        # validate top image picks (HEAD only): raster + 8KB..8MB.
        # ok=True validated, None = unknown size (chunked), False = confirmed bad
        for im in (res['extras'].get('images') or [])[:2]:
            im['bytes'] = head_image(im['url'])
            b = im['bytes']
            im['ok'] = True if isinstance(b, int) and 8000 <= b <= 8_000_000 else (None if b == -1 else False)
    except Exception as e:
        res['extras'] = {'images': [], 'menu': [], 'description': '', 'cuisine': [],
                         'price_range': '', 'error': str(e)[:100]}
    home_text = visible_text(html0)[:30000]
    res['jsonld'] = jsonld_hours(html0)
    extra_texts = []
    for url in links(html0, home.get('url') or poi['website']):
        pg = fetch(url)
        res['ms_fetch'] += pg.get('ms', 0)
        res['pages'].append({'url': url, 'ok': pg.get('ok'), 'status': pg.get('status')})
        if pg.get('ok'):
            extra_texts.append(visible_text(pg['html'])[:30000])
            jl = jsonld_hours(pg['html'])
            if jl and not res['jsonld']:
                res['jsonld'] = jl
                res['jsonld_source'] = url
    seen_snips = set()
    for t in [home_text] + extra_texts:
        for h in regex_hours(t):
            if h['osm'] not in seen_snips:
                seen_snips.add(h['osm'])
                res['regex'].append(h)
    res['regex'] = res['regex'][:10]
    primary, alt, conflict = verdict(res['jsonld'], res['regex'])
    res['hours_primary'] = primary
    res['hours_alt'] = alt
    res['internal_conflict'] = conflict
    if not primary:
        res['js_required'] = js_hint(html0)
    res['ms_parse'] = int((time.time() - t_start) * 1000) - res['ms_fetch']
    note_dead(poi['website'], (res['pages'][0].get('error') if res['pages'] else ''), _DEAD)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--in', dest='inp', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--workers', type=int, default=24)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--resume', action='store_true',
                    help='skip URLs already present in --out (checkpoint resume)')
    ap.add_argument('--max-age', type=int, default=0,
                    help='with --resume, re-crawl successes older than N days (0 = keep all)')
    ap.add_argument('--dead-cache', default='',
                    help='JSON file persisting dead domains (7-day cooldown)')
    a = ap.parse_args()
    global _DEAD
    _DEAD = {}
    if a.dead_cache:
        try:
            _DEAD = json.load(open(a.dead_cache))
        except Exception:
            pass
    t_all = time.time()
    pois = json.load(open(a.inp))
    if a.limit:
        pois = pois[:a.limit]
    done = {}
    if a.resume:
        try:
            for r in json.load(open(a.out)):
                done[r.get('site') or r.get('url')] = r
        except Exception:
            pass
    todo, out, n_keep = [], [], 0
    today = time.strftime('%Y-%m-%d')
    for p in pois:
        key = p.get('website') or p.get('url')
        prev = done.get(key)
        age_ok = True
        if prev and a.max_age:
            try:
                age_ok = (time.mktime(time.strptime(today, '%Y-%m-%d')) -
                          time.mktime(time.strptime(prev.get('crawled_at', today), '%Y-%m-%d'))) / 86400 <= a.max_age
            except Exception:
                age_ok = False
        if prev and prev.get('fetch_ok') and age_ok:
            out.append(prev)
            n_keep += 1
        else:
            todo.append(p)  # failures + skips + stale re-run (cheap); fresh successes kept
    print(f'resume: {n_keep} kept, {len(todo)} to do', flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, r in enumerate(ex.map(process, todo)):
            out.append(r)
            if (i + 1) % 25 == 0:
                json.dump(out, open(a.out, 'w'), indent=1)  # checkpoint
                print(f'{i+1}/{len(todo)}', flush=True)
    json.dump(out, open(a.out, 'w'), indent=1)
    if a.dead_cache:
        Path(a.dead_cache).parent.mkdir(parents=True, exist_ok=True)
        json.dump(_DEAD, open(a.dead_cache, 'w'), indent=1)
        print(f'dead domains cached: {len(_DEAD)}', flush=True)
    n = len(out)
    fok = sum(1 for r in out if r['fetch_ok'])
    jl = sum(1 for r in out if r['jsonld'])
    rx = sum(1 for r in out if r['regex'])
    nm = sum(1 for r in out if r['name_on_page'])
    js = sum(1 for r in out if r['js_required'])
    cf = sum(1 for r in out if r.get('internal_conflict'))
    pr = sum(1 for r in out if r.get('hours_primary'))
    ex = [r.get('extras') or {} for r in out]
    print(f'sites: {n} | fetched: {fok} | json-ld hours: {jl} | regex hours: {rx} | primary: {pr} | internal-conflicts: {cf} | name-on-page: {nm} | js-required: {js}')
    print(f'extras: images={sum(1 for e in ex if e.get("images"))} menu={sum(1 for e in ex if e.get("menu"))} description={sum(1 for e in ex if e.get("description"))} cuisine={sum(1 for e in ex if e.get("cuisine"))} price={sum(1 for e in ex if e.get("price_range"))}')
    sk = {}
    for r in out:
        if r.get('skipped'):
            sk[r['skipped']] = sk.get(r['skipped'], 0) + 1
        for p in r.get('pages', [])[1:]:
            if (p.get('error') or '').startswith('robots:'):
                sk['robots-subpage'] = sk.get('robots-subpage', 0) + 1
                break
    if sk:
        print(f'skipped pre-fetch: {sk}')
    mf = sorted([r.get('ms_fetch', 0) for r in out if r.get('fetch_ok')])
    mp = sorted([r.get('ms_parse', 0) for r in out if r.get('fetch_ok')])
    def pct(a, q):
        return a[min(len(a) - 1, int(len(a) * q))] if a else 0
    wall = time.time() - t_all
    print(f'wall {wall:.0f}s | {len(todo)/max(wall,1):.1f} sites/s | '
          f'fetch ms p50/p95: {pct(mf, .5)}/{pct(mf, .95)} | parse ms p50/p95: {pct(mp, .5)}/{pct(mp, .95)}')


if __name__ == '__main__':
    main()
