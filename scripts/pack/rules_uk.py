#!/usr/bin/env python3
"""UK-general POI consolidation rules, codified from frozen Southend review
rounds (duplicates-services R1/R2/R3, station/facility guards, service-host
attachment, CH evidence attachment).

Design: pure functions over plain record dicts
  {id, source, name, address, postcode, category, extra?}
Every rule returns (decision, rule_name, evidence_dict) or None, where
decision is 'merge' | 'attach' | 'separate' (explicit, audited reject).
None means "no decision" -> human review. Nothing here moves a pin, deletes
a record, or invents coordinates; merges/attachments are identity operations
only, applied by the caller, which must log rule_name + evidence.

Country handling: matching behavior is parameterized by a country config
(stopwords, company suffixes, postcode shape, address-number order).
Locality vocabulary (town/district words that must never count as shared
brand vocabulary) is supplied per area — never hardcoded per town (the
hand-list approach cannot survive UK-wide, let alone Europe). With no
locality provider, rules degrade safe (fewer merges, more review).

Covered approval patterns and their counterexamples (all from review):
- same-premises exact-name merge (R1..R3 groups), vs Fireaway 356/376-378
  (different numbers -> separate), Swagger/Southend (town-word-only -> separate)
- station guard (NaPTAN/ATCO/CRS join identifier-bearers only)
- facility protections (medical/vet/social never merge into shops)
- service-host attachment (same-listing-URL within 30m consolidates;
  JustPark distinct URLs stay separate; ambiguous hosts stay unresolved)
- CH evidence attachment (exact name + same postcode + numbered street,
  Active status or explicit exception; proximity alone transfers nothing)
- turnover pairs (same premises, different names: Zinnia/Mimosa,
  Cricketeers/Kb Kitchen) -> never merge (stale-occupant handling owns them)
"""

import re

# --------------------------------------------------------------------------
# Country configs
# --------------------------------------------------------------------------

GB = {
    'code': 'GB',
    'company_suffixes': ['ltd', 'limited', 'plc', 'llp', 'cic', 'cio',
                         'inc', 'corp', 'co'],
    'articles': ['the'],
    # Generic trade descriptors: stripped for name comparison ONLY when a
    # distinctive core remains (>=4 chars); never sufficient alone.
    'descriptors': ['restaurant', 'cafe', 'coffee', 'bar', 'pub', 'takeaway',
                    'kitchen', 'food', 'pizza', 'burger', 'kebab', 'sushi',
                    'bakery', 'express', 'limited'],
    'postcode_re': re.compile(
        r'^[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}$', re.I),
    'address_number': 'leading',  # "534 Rayleigh Road"
    'locality_provider': None,  # plugged per area; None = safe fallback
}

FI = {
    'code': 'FI',
    'company_suffixes': ['oy', 'ab', 'ky', 'tmi', 'ry'],
    'articles': [],
    'descriptors': ['ravintola', 'kahvila', 'baari', 'pubi', 'pizzeria',
                    'restaurant', 'cafe', 'bar'],
    'postcode_re': re.compile(r'^\d{5}$'),
    'address_number': 'trailing',  # "Hämeentie 62"
    'locality_provider': None,
}

COUNTRIES = {'GB': GB, 'FI': FI}

# Category pairs that must never merge (facility protections).
# Order-insensitive: looked up in both orientations.
PROTECTED_MERGES = frozenset([
    ('medical', 'shop'), ('veterinary', 'shop'), ('social', 'shop'),
    ('medical', 'restaurant'), ('veterinary', 'restaurant'),
])


def norm_name(name, cfg=GB):
    # Apostrophes are REMOVED (Alvaro's -> alvaros), not spaced: possessive
    # endings must not split the stem.
    s = (name or '').casefold().replace('&', ' and ')
    s = s.replace("'", '').replace('’', '')
    s = re.sub(r'[^\w ]', ' ', s, flags=re.U)
    return ' '.join(w for w in re.sub(
        r'\b(' + '|'.join(cfg['company_suffixes'] + cfg['articles']) + r')\b',
        ' ', s).split())


def distinctive(name, cfg=GB):
    """Name core with generic descriptors removed; '' if nothing remains."""
    words = [w for w in norm_name(name, cfg).split()
             if w not in cfg['descriptors']]
    return ' '.join(words)


def house_number(addr, cfg=GB):
    a = addr or ''
    if cfg['address_number'] == 'trailing':
        m = re.search(r'(\d+[a-z]?)\s*$', a.strip(), re.I)
        return m.group(1).lower() if m else None
    m = re.match(r'\s*(\d+[a-z]?)', a, re.I)
    if m:
        return m.group(1).lower()
    m = re.search(r',\s*(\d+[a-z]?)\b', a)
    return m.group(1).lower() if m else None


def postcode_norm(pc):
    return re.sub(r'\s+', '', str(pc or '')).upper() or None


def same_postcode(a, b):
    pa, pb = postcode_norm(a.get('postcode')), postcode_norm(b.get('postcode'))
    return bool(pa and pb and pa == pb)


def street_key(addr):
    """Lowercased street text after the leading number, or None."""
    a = (addr or '').strip()
    m = re.match(r'^\s*\d+[a-z]?\s*,?\s*(.*)$', a, re.I)
    tail = (m.group(1) if m else a).split(',')[0]
    tail = re.sub(r'\s+', ' ', re.sub(r'[^a-zåäö ]', ' ', tail.lower())).strip()
    return tail or None


def locality_words(area, cfg=GB):
    """Town/district words for an area. Supplied by the caller (boundary
    data at fetch time). Empty set when unknown — rules then merge less,
    never more."""
    prov = cfg.get('locality_provider')
    if prov is None:
        return set()
    return set(prov(area, cfg))


def locality_from_area(area_id='', area_name='', cfg=GB):
    """Boundary-free fallback provider: distinctive tokens of the area's
    own id segments + display name (country/region generics excluded).
    Covers new towns with zero hand lists; boundary-derived providers
    plug in via cfg['locality_provider'] for richer vocab. Example:
    Basildon (unlisted in any hand list) -> {'basildon'} blocks
    'Basildon'-only name collisions the same way DUP_STOP blocks
    'Southend'-only ones."""
    GENERIC_GEO = {'england', 'scotland', 'wales', 'ireland', 'uk',
                   'north', 'south', 'east', 'west', 'greater', 'new', 'old',
                   'upon', 'on', 'sea', 'city', 'town', 'london', 'essex',
                   'finland', 'suomi'}
    toks = set()
    for chunk in (area_id.replace('/', ' ').replace('-', ' ').split()
                  + area_name.replace('-', ' ').split()):
        w = chunk.strip().lower()
        if len(w) >= 4 and w not in GENERIC_GEO:
            toks.add(w)
    return toks


def brand_tokens(name, cfg=GB, locality=frozenset()):
    toks = {w for w in norm_name(name, cfg).split() if len(w) >= 6}
    toks = {w[:-1] if w.endswith('s') and not w.endswith('ss') else w
            for w in toks}
    return {w for w in toks if w not in locality}


def same_brand(a, b, cfg=GB, locality=frozenset()):
    ta, tb = brand_tokens(a.get('name'), cfg, locality), brand_tokens(b.get('name'), cfg, locality)
    if ta & tb:
        return True
    ca = norm_name(a.get('name'), cfg).replace(' ', '')
    cb = norm_name(b.get('name'), cfg).replace(' ', '')
    return min(len(ca), len(cb)) >= 4 and ca == cb


# --------------------------------------------------------------------------
# Rules. Each takes two records (+cfg / area) and returns
# (decision, rule_name, evidence) or None.
# --------------------------------------------------------------------------

def rule_turnover_guard(a, b, cfg=GB, locality=frozenset()):
    """Same premises, dissimilar names (Zinnia/Mimosa, Cricketeers/Kb
    Kitchen): NEVER merge. Returns explicit 'separate' so the outcome is
    audited rather than silently queued."""
    if not (same_postcode(a, b)):
        return None
    ha, hb = house_number(a.get('address'), cfg), house_number(b.get('address'), cfg)
    if not (ha and hb and ha == hb):
        # Postcode-only dissimilarity proves nothing: a postcode covers a
        # whole parade (measured: 39% false-fire rate in Basildon when this
        # required postcode alone). Turnover needs premises-level evidence;
        # numberless addresses (Zinnia/Mimosa) stay with other machinery
        # (fhrs:id merge + turnover_watch display), never this guard.
        return None
    if same_brand(a, b, cfg, locality):
        return None
    na, nb = norm_name(a.get('name'), cfg), norm_name(b.get('name'), cfg)
    if na and nb and na != nb:
        return ('separate', 'turnover_guard',
                {'premises': [a.get('address'), b.get('address')],
                 'names': [a.get('name'), b.get('name')]})
    return None


def rule_same_premises_exact_name(a, b, cfg=GB, locality=frozenset()):
    """R1/R2/R3 core: exact normalized name (+area-unique enforced by the
    caller across the candidate set) with same postcode or same housenumber
    + shared street word. Distinct numbers veto (Fireaway 356 vs 376-378).
    Town-word-only overlap never qualifies (Swagger pattern)."""
    if a.get('source') == b.get('source'):
        return None
    na, nb = norm_name(a.get('name'), cfg), norm_name(b.get('name'), cfg)
    if not na or na != nb:
        return None
    ha, hb = house_number(a.get('address'), cfg), house_number(b.get('address'), cfg)
    if ha and hb and ha != hb:
        return None  # distinct premises numbers veto
    if same_postcode(a, b):
        return ('merge', 'same_premises_exact_name',
                {'name': a.get('name'), 'postcode': a.get('postcode')})
    sa, sb = street_key(a.get('address')), street_key(b.get('address'))
    if ha and hb and ha == hb and sa and sb and sa == sb:
        return ('merge', 'same_premises_exact_name',
                {'name': a.get('name'), 'street': sa})
    return None


def rule_station_guard(a, b, cfg=GB, locality=frozenset()):
    """Transit-identifier records join identifier-bearing counterparts only;
    never shops, platforms, or second station records without crosswalk."""
    def stationish(r):
        tags = r.get('extra') or {}
        if tags.get('station_id') or tags.get('crs') or tags.get('atco'):
            return True
        return (r.get('category') or '') in ('station', 'railway', 'transit')
    sa, sb = stationish(a), stationish(b)
    if not (sa or sb):
        return None
    ide = lambda r: bool((r.get('extra') or {}).get('station_id')
                         or (r.get('extra') or {}).get('crs')
                         or (r.get('extra') or {}).get('atco'))
    if sa and sb and not (ide(a) and ide(b)):
        return ('separate', 'station_guard',
                {'reason': 'identifier required on both sides'})
    if (sa or sb) and not (sa and sb):
        # Exactly one side is station-category: the other side must carry
        # an identifier too (crosswalk case, e.g. NaPTAN node + National
        # Rail record) or the pair stays separate. A station never merges
        # into a shop/platform silently.
        other_has_id = ide(b) if sa else ide(a)
        if not other_has_id:
            return ('separate', 'station_guard',
                    {'reason': 'station_to_non_station_without_id'})
    return None


def rule_facility_protect(a, b, cfg=GB, locality=frozenset()):
    """Medical/veterinary/social facilities never merge into unrelated
    shops (or restaurants). Explicit reject, not silence."""
    pair = tuple(sorted([a.get('category') or '', b.get('category') or '']))
    if pair in PROTECTED_MERGES or pair[::-1] in PROTECTED_MERGES:
        return ('separate', 'facility_protect', {'categories': list(pair)})
    return None


def rule_service_host_attach(service, host, distance_m=None, same_url=False,
                             cfg=GB, locality=frozenset()):
    """Allpoint/Costa/JustPark pattern: a service record attaches to a store
    card (no identity/coordinate change) iff same-listing-URL within 30 m,
    or a unique compatible host under premises rules. Ambiguous hosts and
    distinct listing URLs stay unresolved. JustPark distinct URLs are never
    collapsed by proximity."""
    if distance_m is not None and distance_m > 30:
        return None
    if same_url:
        return ('attach', 'service_same_listing_url',
                {'service': service.get('id'), 'host': host.get('id')})
    return None  # unique-host resolution belongs to the caller with area context


def rule_ch_evidence_attach(place, company, cfg=GB, active_statuses=('Active',),
                            exceptions=()):
    """CH evidence attachment (Alvaro's pattern): exact trading-descriptor-
    stripped name + same postcode + numbered street, Active status (or
    explicit exception entry). Proximity alone transfers nothing (Toulouse
    stays a conflict). Evidence only — never moves pins, never creates."""
    cname = company.get('company_name') or ''
    if norm_name(cname, cfg) != norm_name(place.get('name'), cfg):
        # allow descriptor-stripped core agreement (min 4 chars)
        core_c = distinctive(cname, cfg)
        core_p = distinctive(place.get('name'), cfg)
        if not (core_c and core_c == core_p and len(core_c) >= 4):
            return None
    if not same_postcode({'postcode': company.get('postcode')},
                         {'postcode': place.get('postcode')}):
        return None
    hp = house_number(place.get('address'), cfg)
    hc = house_number(company.get('registered_address'), cfg)
    if not (hp and hc):
        return None  # numbered street required on both sides
    status = company.get('company_status') or ''
    if not (any(s in status for s in active_statuses)
            or company.get('company_number') in exceptions):
        return None
    return ('attach', 'ch_name_and_premises',
            {'company': company.get('company_number'),
             'place': place.get('id')})


def decide_pair(a, b, cfg=GB, locality=frozenset(), **kw):
    """Ordered evaluation: protections and vetoes first, merges/attachments
    after. First non-None decision wins; None means human review."""
    for rule in (rule_facility_protect, rule_station_guard,
                 rule_turnover_guard, rule_same_premises_exact_name):
        r = rule(a, b, cfg, locality)
        if r is not None:
            return r
    return None
