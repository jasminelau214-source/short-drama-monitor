import unittest

from research_validation import CORE_FIELDS, OPTIONAL_FIELDS, validate_research_payload


def valid_payload():
    payload = {field: '有证据内容' for field in CORE_FIELDS + OPTIONAL_FIELDS}
    payload.update({
        'genre': '现代都市',
        'audience': '女频',
        'canonicalTitle': 'Example Drama',
        'newnessResolution': 'new',
        'confidence': 'high',
        'missingFields': [],
        'auditNotes': [],
        'sourceUrls': ['https://example.com/drama'],
        'needsGPT': False,
    })
    return payload


class ResearchValidationTests(unittest.TestCase):
    def test_valid_contract_passes(self):
        payload = valid_payload()
        result = validate_research_payload(payload, allowed_source_urls=payload['sourceUrls'])
        self.assertTrue(result['ok'])
        self.assertEqual(result['errors'], [])

    def test_old_free_text_genre_is_blocked(self):
        payload = valid_payload()
        payload['genre'] = '现代都市 / 黑帮爱情'
        result = validate_research_payload(payload, allowed_source_urls=payload['sourceUrls'])
        self.assertFalse(result['ok'])
        self.assertTrue(any(x.startswith('invalid_genre:') for x in result['errors']))

    def test_missing_required_key_is_blocked(self):
        payload = valid_payload()
        del payload['canonicalTitle']
        result = validate_research_payload(payload, allowed_source_urls=payload['sourceUrls'])
        self.assertFalse(result['ok'])
        self.assertIn('canonicalTitle', result['missingKeys'])

    def test_untrusted_source_url_is_blocked(self):
        payload = valid_payload()
        payload['sourceUrls'] = ['https://other.example/bad']
        result = validate_research_payload(
            payload,
            allowed_source_urls={'https://example.com/drama'},
        )
        self.assertFalse(result['ok'])
        self.assertTrue(any(x.startswith('sourceUrls_outside_evidence:') for x in result['errors']))


if __name__ == '__main__':
    unittest.main()
