#!/usr/bin/env python3
"""Regression tests for rules_uk. Cases model frozen Southend review
evidence (names/addresses/patterns from the manifests); synthetic rows
stand in for snapshot records so the file needs no 100 MB fixtures.
Full manifest-replay validation runs against the frozen snapshots in the
fresh-southend tree (other chat); these tests pin the rule semantics.

Run: python3 scripts/pack/rules_uk_test.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rules_uk import (
    GB, FI, decide_pair, norm_name, distinctive, house_number,
    rule_same_premises_exact_name, rule_station_guard, rule_facility_protect,
    rule_service_host_attach, rule_ch_evidence_attach, rule_turnover_guard,
    locality_from_area, same_brand,
)


def rec(id, source, name, address='', postcode='', category='restaurant', **kw):
    d = dict(id=id, source=source, name=name, address=address,
             postcode=postcode, category=category)
    d.update(kw)
    return d


class TestNorm(unittest.TestCase):
    def test_company_suffixes(self):
        self.assertEqual(norm_name("ALVAROS RESTAURANT LIMITED"), 'alvaros restaurant')
        self.assertEqual(norm_name("The Dog Cafe"), 'dog cafe')

    def test_distinctive_core(self):
        self.assertEqual(distinctive("Tesco Express"), 'tesco')
        self.assertEqual(distinctive("Pizza Express"), '')
        # Town words are NOT stripped here by design: locality handling
        # lives at the call site (locality param), so bare town names
        # survive normalization and can never match alone downstream.
        self.assertEqual(distinctive("Southend"), 'southend')

    def test_house_numbers(self):
        self.assertEqual(house_number('534 Rayleigh Road, SS9 5HX'), '534')
        self.assertEqual(house_number('The Slug And Lettuce, 6 - 8 Southchurch Road'), '6')
        self.assertIsNone(house_number('Adventure Island, Marine Parade'))
        self.assertEqual(house_number('Hämeentie 62', FI), '62')


class TestSamePremisesExactName(unittest.TestCase):
    def test_merge_same_postcode(self):
        a = rec('fsa-1', 'fsa', 'Alvaro’s', '32 St Helens Road', 'SS0 7LB')
        b = rec('osm-1', 'osm', 'Alvaros', '32 Saint Helens Road', 'SS0 7LB')
        d = rule_same_premises_exact_name(a, b)
        self.assertEqual(d[0], 'merge')

    def test_fireaway_branches_stay_separate(self):
        # Same brand, different housenumbers: indistinguishable paperwork,
        # must NOT auto-merge (approved rejection pattern).
        a = rec('fsa-1', 'fsa', 'Fireaway', '356 London Road', 'SS0 7HZ')
        b = rec('fsa-2', 'fsa', 'Fireaway Southend', '376-378 London Road', 'SS0 7HZ')
        self.assertIsNone(rule_same_premises_exact_name(a, b))

    def test_swagger_town_word_only(self):
        a = rec('x', 'osm', 'Swagger of Southend', '1 High Street', 'SS1 1JE')
        b = rec('y', 'fsa', 'Fireaway Southend', '2 High Street', 'SS1 1JE')
        self.assertIsNone(rule_same_premises_exact_name(a, b))

    def test_tesco_needs_more_than_prefix(self):
        # 'Tesco Express' vs 'Tesco' must not merge on prefix alone.
        a = rec('x', 'atp', 'Tesco Express', '10 High Street', 'SS1 1JE')
        b = rec('x2', 'osm', 'Tesco', '10 High Street', 'SS1 1JE')
        self.assertIsNone(rule_same_premises_exact_name(a, b))

    def test_same_source_never(self):
        a = rec('x', 'osm', 'Same Name', '1 Road', 'SS1 1AA')
        b = rec('y', 'osm', 'Same Name', '1 Road', 'SS1 1AA')
        self.assertIsNone(rule_same_premises_exact_name(a, b))


class TestTurnoverGuard(unittest.TestCase):
    def test_numbered_premises_turnover(self):
        # Slug pattern: same housenumber + dissimilar names -> separate.
        a = rec('fsa-1', 'fsa', 'Slug and Lettuce', '6 Southchurch Road', 'SS1 2XX')
        b = rec('osm-1', 'osm', 'Skylahs Bar', '6 Southchurch Road', 'SS1 2XX')
        d = rule_turnover_guard(a, b)
        self.assertEqual(d[0], 'separate')

    def test_numberless_premises_unhandled(self):
        # Zinnia/Mimosa have no housenumbers; postcode-only dissimilarity
        # is neighbours, not turnover (39% false-fire measured). This guard
        # stays silent; fhrs:id merge + turnover_watch own the pattern.
        a = rec('fsa-1', 'fsa', 'Zinnia Restaurant', 'Clifftown Shore', 'SS1 1FU')
        b = rec('osm-1', 'osm', 'Mimosa', 'Clifftown Shore', 'SS1 1FU')
        self.assertIsNone(rule_turnover_guard(a, b))

    def test_agreeing_names_not_turnover(self):
        a = rec('x', 'fsa', 'Alvaros', '32 Road', 'SS0 7LB')
        b = rec('y', 'osm', 'Alvaros', '32 Road', 'SS0 7LB')
        self.assertIsNone(rule_turnover_guard(a, b))


class TestStationAndFacility(unittest.TestCase):
    def test_station_shop_separate(self):
        a = rec('x', 'osm', 'Central Station', 'Station Road', 'SS1 1AA',
                category='station', extra={'crs': 'ABC'})
        b = rec('y', 'atp', 'Central Station Shop', 'Station Road', 'SS1 1AA',
                category='shop')
        self.assertEqual(rule_station_guard(a, b)[0], 'separate')

    def test_vet_shop_separate(self):
        a = rec('x', 'osm', 'Happy Vets', '1 Road', 'SS1 1AA', category='veterinary')
        b = rec('y', 'osm', 'Happy Shop', '1 Road', 'SS1 1AA', category='shop')
        self.assertEqual(rule_facility_protect(a, b)[0], 'separate')


class TestServiceHost(unittest.TestCase):
    def test_same_url_attaches(self):
        s = rec('atp:justpark/1', 'atp', 'JustPark A', '1 Road', 'SS1 1AA', category='service')
        h = rec('osm-1', 'osm', 'Car Park A', '1 Road', 'SS1 1AA', category='services')
        d = rule_service_host_attach(s, h, distance_m=12, same_url=True)
        self.assertEqual(d[0], 'attach')

    def test_far_apart_never(self):
        s = rec('s', 'atp', 'JustPark A', '1 Road', 'SS1 1AA', category='service')
        h = rec('h', 'osm', 'Car Park A', '1 Road', 'SS1 1AA', category='services')
        self.assertIsNone(rule_service_host_attach(s, h, distance_m=500, same_url=True))


class TestCHEvidence(unittest.TestCase):
    def test_alvaros_pattern(self):
        place = rec('osm:node/596366561', 'osm', "Alvaro's", '32 St Helens Road', 'SS0 7LB')
        co = {'company_number': '16256734', 'company_name': 'ALVAROS RESTAURANT LIMITED',
              'company_status': 'Active', 'registered_address': '32 - 34 ST. HELENS ROAD',
              'postcode': 'SS0 7LB'}
        d = rule_ch_evidence_attach(place, co)
        self.assertEqual(d[0], 'attach')

    def test_proximity_alone_transfers_nothing(self):
        place = rec('x', 'osm', 'Toulouse', '1 Road', 'SS1 1AA')
        co = {'company_number': '1', 'company_name': 'UNRELATED LTD',
              'company_status': 'Active', 'registered_address': '9 Other Road',
              'postcode': 'SS9 9ZZ'}
        self.assertIsNone(rule_ch_evidence_attach(place, co))

    def test_dissolved_needs_exception(self):
        place = rec('x', 'osm', 'Old Name', '1 Road', 'SS1 1AA')
        co = {'company_number': '9', 'company_name': 'Old Name Ltd',
              'company_status': 'Dissolved', 'registered_address': '1 Road',
              'postcode': 'SS1 1AA'}
        self.assertIsNone(rule_ch_evidence_attach(place, co))
        d = rule_ch_evidence_attach(place, co, exceptions=('9',))
        self.assertEqual(d[0], 'attach')


class TestDecidePair(unittest.TestCase):
    def test_vetoes_first(self):
        # Facility protection beats name agreement in evaluation order.
        a = rec('x', 'osm', 'Happy Vets', '1 Road', 'SS1 1AA', category='veterinary')
        b = rec('y', 'osm', 'Happy Vets', '1 Road', 'SS1 1AA', category='shop')
        d = decide_pair(a, b)
        self.assertEqual(d[1], 'facility_protect')


class TestLocalityProvider(unittest.TestCase):
    def test_unlisted_town_covered(self):
        # Basildon appears in no hand list; the descriptor provider still
        # blocks town-word-only collisions there.
        loc = locality_from_area('eu/gb/england/essex/basildon', 'Basildon')
        self.assertIn('basildon', loc)
        a = {'name': 'Basildon Bakery'}
        b = {'name': 'Basildon Cafe'}
        self.assertFalse(same_brand(a, b, locality=loc))

    def test_distinct_brands_unaffected(self):
        loc = locality_from_area('eu/gb/england/essex/basildon', 'Basildon')
        a = {'name': "Greggs Basildon"}
        b = {'name': "Greggs"}
        # shared distinctive brand token survives locality filtering
        self.assertTrue(same_brand(a, b, locality=loc))


if __name__ == '__main__':
    unittest.main(verbosity=1)
