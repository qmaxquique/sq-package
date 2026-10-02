import csv
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from report import Exporter, ExportError, finding_csv, render_html


class FakeClient:
    def __init__(self):
        self.calls = []
        self.changed = False
        self.analysis_calls = 0
        self.hotspots = [{'key': 'h', 'component': 'p:a', 'message': 'Review', 'status': 'TO_REVIEW', 'vulnerabilityProbability': 'HIGH'}]
        self.issues = [dict(key=str(n), component='p:a' if n < 3 else 'p:b',
                            message='=HYPERLINK("bad")', severity='MAJOR', rule='php:S1') for n in range(5)]

    def request(self, path, params):
        self.calls.append((path, params))
        if path == 'api/project_analyses/search':
            self.analysis_calls += 1
            return {'analyses': [{'key': 'new' if self.changed and self.analysis_calls > 1 else 'analysis', 'date': '2026-10-01'}]}
        if path == 'api/components/show':
            return {'component': {'key': 'p', 'name': '<script>alert(1)</script>'}}
        if path == 'api/measures/component':
            return {'component': {'measures': [{'metric': 'ncloc', 'value': '10'}]}}
        if path == 'api/qualitygates/project_status':
            return {'projectStatus': {'status': 'ERROR', 'conditions': []}}
        if path == 'api/components/tree':
            rows = [{'key': 'p:a', 'path': 'a.php', 'qualifier': 'FIL'}, {'key': 'p:b', 'path': 'b.php', 'qualifier': 'FIL'}]
            field = 'components'
        elif path == 'api/issues/search':
            rows = self.issues
            field = 'issues'
            if params.get('onComponentOnly'):
                rows = [r for r in rows if r['component'] in params['components'].split(',')]
        elif path == 'api/hotspots/search':
            field = 'hotspots'
            rows = self.hotspots
            if params.get('files'):
                rows = [r for r in rows if {'p:a': 'a.php', 'p:b': 'b.php'}[r['component']] in params['files'].split(',')]
        else:
            raise AssertionError(path)
        p, size = int(params.get('p', 1)), int(params.get('ps', 2))
        return {'paging': {'total': len(rows)}, field: rows[(p - 1) * size:p * size]}


class ReportTests(unittest.TestCase):
    def collect(self, client=None):
        client = client or FakeClient()
        with patch('report.PAGE_SIZE', 2), patch('report.SEARCH_LIMIT', 3):
            return Exporter(client, 'p', 'http://localhost:9000').collect()

    def test_paginated_partition_includes_every_issue_once(self):
        client = FakeClient()
        data = self.collect(client)
        self.assertEqual({r['key'] for r in data['issues']}, {'0', '1', '2', '3', '4'})
        self.assertEqual(len(data['hotspots']), 1)
        self.assertTrue(any(p.get('onComponentOnly') for _, p in client.calls))
        self.assertTrue(any(p.get('p') == 2 for _, p in client.calls))

    def test_single_component_over_limit_fails_explicitly(self):
        client = FakeClient()
        for row in client.issues:
            row['component'] = 'p:a'
        with self.assertRaisesRegex(ExportError, 'export aborted'):
            self.collect(client)

    def test_changed_analysis_rejected(self):
        client = FakeClient()
        client.changed = True
        with self.assertRaisesRegex(ExportError, 'new analysis'):
            self.collect(client)

    def test_missing_component_findings_rejected(self):
        client = FakeClient()
        client.issues[0]['component'] = 'p:missing'
        with self.assertRaisesRegex(ExportError, 'Incomplete'):
            self.collect(client)

    def test_timeout_rejected(self):
        with self.assertRaisesRegex(ExportError, 'timed out'):
            Exporter(FakeClient(), 'p', 'http://localhost:9000', timeout=-1).collect()

    def test_hotspot_search_limit_uses_file_partitions(self):
        client = FakeClient()
        client.hotspots = [dict(key='h' + str(n), component='p:a' if n < 3 else 'p:b') for n in range(5)]
        data = self.collect(client)
        self.assertEqual({r['key'] for r in data['hotspots']}, {'h0', 'h1', 'h2', 'h3', 'h4'})
        self.assertTrue(any(p.get('files') for _, p in client.calls))

    def test_zero_findings_still_exports(self):
        client = FakeClient()
        client.issues = []
        client.hotspots = []
        data = self.collect(client)
        self.assertEqual(len(list(csv.reader(io.StringIO(finding_csv(data))))), 1)

    def test_missing_completed_analysis_rejected(self):
        client = FakeClient()
        original = client.request
        client.request = lambda path, params: {'analyses': []} if path == 'api/project_analyses/search' else original(path, params)
        with self.assertRaisesRegex(ExportError, 'No completed analysis'):
            self.collect(client)

    def test_html_is_escaped_and_links_are_relative(self):
        page = render_html(self.collect())
        self.assertNotIn('<script>alert(1)</script>', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertIn('href="issues.csv"', page)
        self.assertIn('FAILED', page)

    def test_csv_preserves_multiline_and_neutralizes_formulas(self):
        data = self.collect()
        data['issues'][0]['message'] = '  =1+2\n"quoted", value'
        rows = list(csv.DictReader(io.StringIO(finding_csv(data))))
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]['message'], "'  =1+2\n\"quoted\", value")
        self.assertEqual(rows[0]['file'], 'a.php')
        hotspots = list(csv.DictReader(io.StringIO(finding_csv(data, hotspot=True))))
        self.assertEqual(hotspots[0]['type'], 'SECURITY_HOTSPOT')
        self.assertEqual(hotspots[0]['severity'], 'HIGH')


if __name__ == '__main__':
    unittest.main()
