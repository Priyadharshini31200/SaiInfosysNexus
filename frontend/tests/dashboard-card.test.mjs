import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import * as React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { transformWithOxc } from 'vite';

const source = (await readFile(new URL('../src/components/ProjectUsageCard.jsx', import.meta.url), 'utf8'))
  .replace(/^import .*;\r?\n/gm, '').replace('export const', 'const').replace('export default function', 'function');
const { code } = await transformWithOxc(source, 'ProjectUsageCard.jsx', { jsx: { runtime: 'classic' } });
const Card = new Function('React', 'styles', `${code}; return ProjectUsageCard;`)(React, {});
const project = { code: 'gstr2tally', label: 'GSTR2Tally', available: true, used: 1234, not_used: 5, expired: 6, expiring: 7 };

test('each project renders exactly Used / Not Used / Expire from supplied counts', () => {
  const html = renderToStaticMarkup(React.createElement(Card, { project, onSelect() {} }));
  assert.equal((html.match(/<button/g) || []).length, 3);
  assert.ok(html.includes('1,234'));
  assert.ok(html.includes('>Not Used<'));
  assert.ok(html.includes('>Expire<'));
  assert.ok(!html.includes('Expiring This Month'));
});

test('count clicks preserve project and exact report category; report grants disable links', () => {
  const calls = [];
  const tree = Card({ project, onSelect: (...args) => calls.push(args), canOpen: (category) => category !== 'expired' });
  const buttons = tree.props.children[1].props.children;
  assert.deepEqual(buttons.map((button) => button.props.disabled), [false, false, true]);
  buttons.forEach((button) => button.props.onClick());
  assert.deepEqual(calls, [['gstr2tally', 'used'], ['gstr2tally', 'not-used'], ['gstr2tally', 'expired']]);
});

test('unavailable data keeps three muted placeholders, a warning icon, no buttons and no real counts', () => {
  const html = renderToStaticMarkup(React.createElement(Card, { project: { ...project, available: false, message: 'Data unavailable' } }));
  assert.ok(html.includes('title="Data unavailable"'));
  assert.ok(html.includes('⚠'));
  assert.ok(!html.includes('<button'));
  assert.equal((html.match(/unavailable<\/span>|: unavailable"/g) || []).length, 3);
  assert.equal((html.match(/>0<\/span>/g) || []).length, 3);
  assert.ok(!html.includes('1,234'));
});

test('partial Nexus-only counts keep their note; complete counts show none', () => {
  const note = 'Counts are for customers registered in Nexus. The legacy PRN database cannot be read, so older licences are not included.';
  const partial = renderToStaticMarkup(React.createElement(Card, { project: { ...project, complete: false, legacy_included: false, note }, onSelect() {} }));
  assert.ok(partial.includes(`title="${note}"`));
  assert.ok(partial.includes('ⓘ'));
  assert.ok(!partial.includes('<p'));  // an icon with a tooltip, not a paragraph
  assert.equal((partial.match(/<button/g) || []).length, 3);
  for (const extra of [{ complete: true }, {}]) {
    assert.ok(!renderToStaticMarkup(React.createElement(Card, { project: { ...project, ...extra, note }, onSelect() {} })).includes('legacy PRN'));
  }
});

const dashboardSource = (await readFile(new URL('../src/pages/Dashboard.jsx', import.meta.url), 'utf8'))
  .replace(/^import .*;\r?\n/gm, '').replace('export default function', 'function');
const transformed = await transformWithOxc(dashboardSource, 'Dashboard.jsx', { jsx: { runtime: 'classic' } });
test('dashboard drilldowns apply exact project/status and expired-only filters', () => {
  const paths = [];
  const Dashboard = new Function('React', 'useState', 'useEffect', 'useNavigate', 'useAuth', 'dashboardApi', 'Alert', 'ProjectUsageCard', 'useSyncChanges', 'styles', `${transformed.code}; return Dashboard;`)(
    React, () => [{ loading: false, error: '', projects: [project] }, () => {}], () => {}, () => (path) => paths.push(path), () => ({ can: () => true }), {}, 'Alert', 'ProjectUsageCard', () => '', {},
  );
  const tree = Dashboard();
  const card = tree.props.children[2].props.children[0];
  for (const category of ['used', 'not-used', 'expired']) card.props.onSelect(project.code, category);
  assert.deepEqual(paths, ['/reports/usage?project=gstr2tally&status=used', '/reports/usage?project=gstr2tally&status=not-used', '/reports/license-expiry?product=gstr2tally&state=expired']);
});
