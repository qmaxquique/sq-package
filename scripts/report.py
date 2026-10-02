"""Read-only SonarQube exports; no third-party dependencies."""
import csv
from collections import Counter
from datetime import datetime, timezone
import html
import io
import time
from urllib.parse import urlencode


class ExportError(Exception):
    pass


PAGE_SIZE = 500
SEARCH_LIMIT = 10000
METRICS = 'ncloc,files,coverage,duplicated_lines_density,bugs,vulnerabilities,code_smells,security_hotspots,sqale_index,reliability_rating,security_rating,sqale_rating'
METRIC_LABELS = {
    'ncloc': 'Lines of code', 'files': 'Files', 'coverage': 'Coverage (%)',
    'duplicated_lines_density': 'Duplicated lines (%)', 'bugs': 'Bugs',
    'vulnerabilities': 'Vulnerabilities', 'code_smells': 'Code smells',
    'security_hotspots': 'Security hotspots', 'sqale_index': 'Estimated technical debt (minutes)',
    'reliability_rating': 'Reliability rating', 'security_rating': 'Security rating',
    'sqale_rating': 'Maintainability rating',
}


def metric_value(metric):
    value = metric.get('value', '')
    if metric['metric'].endswith('_rating'):
        return {'1.0': 'A', '2.0': 'B', '3.0': 'C', '4.0': 'D', '5.0': 'E'}.get(value, value)
    return value


class Exporter:
    def __init__(self, client, key, web_url, timeout=600):
        self.client = client
        self.key = key
        self.web_url = web_url
        self.deadline = time.monotonic() + timeout

    def request(self, path, parameters):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ExportError('Report export timed out. Increase SONAR_REPORT_TIMEOUT and retry.')
        self.client.timeout = min(10, remaining)
        return self.client.request(path, parameters)

    def pages(self, path, parameters, field, limited=False):
        rows = []
        page = 1
        expected = None
        while True:
            response = self.request(path, dict(parameters, p=page, ps=PAGE_SIZE))
            total = response.get('paging', {}).get('total')
            if not isinstance(total, int) or total < 0:
                raise ExportError('Invalid pagination response from ' + path)
            if expected is None:
                expected = total
                if limited and total > SEARCH_LIMIT:
                    return None
            elif total != expected:
                raise ExportError('Findings changed during export. Retry after analysis and issue edits finish.')
            batch = response.get(field, [])
            if not isinstance(batch, list) or (not batch and len(rows) < total):
                raise ExportError('Incomplete response from ' + path)
            rows.extend(batch)
            if len(rows) >= total:
                if len(rows) != total:
                    raise ExportError('Inconsistent result count from ' + path)
                return rows
            page += 1

    def partition(self, values, fetch):
        rows = fetch(values)
        if rows is not None:
            return rows
        if len(values) <= 1:
            raise ExportError('One component exceeds the 10,000-finding API limit; export aborted to avoid truncation.')
        middle = len(values) // 2
        return self.partition(values[:middle], fetch) + self.partition(values[middle:], fetch)

    def count(self, path, parameters):
        response = self.request(path, dict(parameters, ps=1))
        total = response.get('paging', {}).get('total')
        if not isinstance(total, int):
            raise ExportError('Invalid finding count from ' + path)
        return total

    def analysis(self):
        rows = self.request('api/project_analyses/search', {'project': self.key, 'ps': 1}).get('analyses', [])
        if not rows or not rows[0].get('key'):
            raise ExportError('No completed analysis found. Run ./sonar scan first.')
        return rows[0]

    def collect(self):
        analysis = self.analysis()
        component = self.request('api/components/show', {'component': self.key})['component']
        measures = self.request('api/measures/component', {'component': self.key, 'metricKeys': METRICS})['component']['measures']
        gate = self.request('api/qualitygates/project_status', {'analysisId': analysis['key']})['projectStatus']
        issue_parameters = {'components': self.key, 's': 'CREATION_DATE', 'asc': 'true'}
        hotspot_parameters = {'project': self.key}
        issue_total = self.count('api/issues/search', issue_parameters)
        hotspot_total = self.count('api/hotspots/search', hotspot_parameters)
        components = self.pages('api/components/tree', {'component': self.key, 'strategy': 'all'}, 'components')
        by_key = {item['key']: item for item in [component] + components}
        print('Exporting {} issues and {} security hotspots...'.format(issue_total, hotspot_total), flush=True)
        issues = self.pages('api/issues/search', issue_parameters, 'issues', limited=True)
        if issues is None:
            issues = []
            keys = list(by_key)
            for offset in range(0, len(keys), 50):
                issues.extend(self.partition(keys[offset:offset + 50], lambda group: self.pages(
                    'api/issues/search', dict(issue_parameters, components=','.join(group), onComponentOnly='true'),
                    'issues', limited=True)))
                if offset % 1000 == 0:
                    print('Exported {} issues...'.format(len(issues)), flush=True)
        hotspots = self.pages('api/hotspots/search', hotspot_parameters, 'hotspots', limited=True)
        if hotspots is None:
            hotspots = []
            files = [item['path'] for item in components if item.get('qualifier') == 'FIL']
            for offset in range(0, len(files), 50):
                hotspots.extend(self.partition(files[offset:offset + 50], lambda group: self.pages(
                    'api/hotspots/search', dict(hotspot_parameters, files=','.join(group)), 'hotspots', limited=True)))
        for rows, total, path, parameters in (
                (issues, issue_total, 'api/issues/search', issue_parameters),
                (hotspots, hotspot_total, 'api/hotspots/search', hotspot_parameters)):
            if len(rows) != total or len({row['key'] for row in rows}) != total or self.count(path, parameters) != total:
                raise ExportError('Incomplete or changing findings; export aborted. Retry when the project is idle.')
        if self.analysis()['key'] != analysis['key']:
            raise ExportError('A new analysis completed during export. Retry to get a consistent report.')
        return {'project': component, 'analysis': analysis, 'gate': gate, 'measures': measures,
                'issues': issues, 'hotspots': hotspots, 'components': by_key,
                'generated_at': datetime.now(timezone.utc).isoformat(),
                'dashboard_url': self.web_url + '/dashboard?' + urlencode({'id': self.key})}


def csv_cell(value):
    value = str(value if value is not None else '')
    # Prevent spreadsheet formula execution, including whitespace-prefixed formulas.
    if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')):
        return "'" + value
    return value


def issue_url(data, issue, hotspot=False):
    return data['dashboard_url'].split('/dashboard?')[0] + ('/security_hotspots?' if hotspot else '/project/issues?') + urlencode({
        'id': data['project']['key'], 'hotspots' if hotspot else 'issues': issue['key']})


def finding_csv(data, hotspot=False):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream)
    fields = ['key', 'type', 'severity', 'impacts', 'rule', 'file', 'line', 'status', 'resolution',
              'message', 'effort', 'created_at', 'updated_at', 'url']
    writer.writerow(fields)
    for row in data['hotspots' if hotspot else 'issues']:
        component = data['components'].get(row.get('component'), {})
        values = [row['key'], 'SECURITY_HOTSPOT' if hotspot else row.get('type'),
                  row.get('vulnerabilityProbability') if hotspot else row.get('severity'),
                  '; '.join(i['softwareQuality'] + ':' + i['severity'] for i in row.get('impacts', [])),
                  row.get('ruleKey', row.get('rule')), component.get('path', row.get('component')),
                  row.get('line'), row.get('issueStatus', row.get('status')), row.get('resolution'),
                  row.get('message'), row.get('effort'), row.get('creationDate'), row.get('updateDate'),
                  issue_url(data, row, hotspot)]
        writer.writerow([csv_cell(value) for value in values])
    return stream.getvalue()


def render_html(data):
    def esc(value):
        return html.escape(str(value if value is not None else ''), quote=True)

    def table(headers, rows):
        return '<table><thead><tr>' + ''.join('<th>' + esc(h) + '</th>' for h in headers) + '</tr></thead><tbody>' + ''.join(
            '<tr>' + ''.join('<td>' + esc(cell) + '</td>' for cell in row) + '</tr>' for row in rows) + '</tbody></table>'

    gate = data['gate']
    issues = data['issues']
    counts = Counter(row.get('severity', 'UNKNOWN') for row in issues)
    top = sorted(issues, key=lambda row: ({'BLOCKER': 0, 'CRITICAL': 1, 'MAJOR': 2, 'MINOR': 3, 'INFO': 4}.get(row.get('severity'), 5), row['key']))[:100]
    sections = [
        '<h1>' + esc(data['project'].get('name', data['project']['key'])) + '</h1>',
        '<p>Analysis: ' + esc(data['analysis'].get('date')) + ' · Exported: ' + esc(data['generated_at']) + '</p>',
        '<p>Analysis ID: ' + esc(data['analysis']['key']) + '</p>',
        '<p>Quality gate: <strong>' + esc({'OK': 'PASSED', 'ERROR': 'FAILED', 'NONE': 'NOT CONFIGURED'}.get(gate['status'], gate['status'])) + '</strong></p>',
        '<p><a href="' + esc(data['dashboard_url']) + '">Open SonarQube dashboard</a></p>',
        '<p>' + str(len(issues)) + ' issues · ' + str(len(data['hotspots'])) + ' security hotspots</p>',
        '<p><a href="issues.csv" download>Download all issues (CSV)</a> · <a href="security-hotspots.csv" download>Download security hotspots (CSV)</a></p>',
        '<p>This report reflects current issue review states for the latest completed analysis. Links to SonarQube require the local server and login. CSV files contain all findings, including resolved issues returned by the API. No source files or credentials are embedded.</p>',
        '<h2>Metrics</h2>', table(['Metric', 'Value'], [(METRIC_LABELS.get(m['metric'], m['metric']), metric_value(m)) for m in data['measures']]),
        '<p>Coverage uses configured reports; without reports, it does not represent executed tests.</p>',
        '<h2>Quality gate conditions</h2>', table(['Metric', 'Status', 'Value', 'Threshold'], [(c['metricKey'], c['status'], c.get('actualValue'), c.get('errorThreshold')) for c in gate.get('conditions', [])]),
        '<p>No evaluated conditions were returned for this analysis.</p>' if not gate.get('conditions') else '',
        '<h2>Issues by severity</h2>', table(['Severity', 'Count'], sorted(counts.items())),
        '<h2>First 100 issues, ordered by severity</h2>',
        table(['Severity', 'File', 'Line', 'Rule', 'Status', 'Message'], [(row.get('severity'), data['components'].get(row.get('component'), {}).get('path', row.get('component')), row.get('line'), row.get('rule'), row.get('issueStatus', row.get('status')), row.get('message')) for row in top]),
        '<h2>Security hotspots</h2><p>Hotspots require review; they are not automatically confirmed vulnerabilities. Full details are in security-hotspots.csv.</p>',
    ]
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SonarQube report — ' + esc(data['project']['key']) + '</title><style>body{font:16px system-ui,sans-serif;max-width:1200px;margin:40px auto;padding:0 20px;color:#182230}table{border-collapse:collapse;width:100%;font-size:14px;margin:16px 0}th,td{text-align:left;border:1px solid #ccd4df;padding:8px;overflow-wrap:anywhere}th{background:#eef2f7}a{color:#075bc7}@media print{body{margin:0;padding:0}thead{display:table-header-group}tr{break-inside:avoid}}</style></head><body>' + '\n'.join(sections) + '</body></html>\n'
