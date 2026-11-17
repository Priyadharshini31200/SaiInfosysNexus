import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { transformWithOxc } from 'vite';

// Exercise the drawer's async handlers without a browser or additional dependencies.
const source = (await readFile(new URL('../src/components/ConfigurationDrawer.jsx', import.meta.url), 'utf8'))
  .replace(/^import .*;\r?\n/gm, '')
  .replace('export default function', 'function');
const { code } = await transformWithOxc(source, 'ConfigurationDrawer.jsx', { jsx: { runtime: 'classic', pragma: 'h' } });
const createComponent = new Function('useState', 'useRef', 'useEffect', 'useCallback', 'useAuth', 'configApi', 'tallyApi', 'h', 'styles', 'Drawer', 'Alert', 'PasswordField', 'SettingsIcon', 'FolderIcon', 'KeyIcon', 'UserIcon', `${code.replaceAll('React.Fragment', '"Fragment"')}; return ConfigurationDrawer;`);
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const payload = (enabled = false) => ({ admins: [{ id: 1, username: 'first', grants: { project: { view: enabled } } }, { id: 2, username: 'second', grants: {} }], projects: [{ code: 'project', label: 'Project', menus: [{ module: 'project', label: 'Dashboard', actions: ['view'] }] }], actions: { view: 'View' } });
// Tally SL permissions load alongside the PRN grants; these tests exercise the PRN save logic, so the Tally API answers with no products.
const tallyStub = { permissions: async () => ({ admins: [], products: [], actions: {} }), savePermissions: async () => ({ admins: [], products: [], actions: {} }) };
function harness(api, role = 'SUPERADMIN') {
  const slots = []; let cursor = 0, effect, started = false, closed = 0;
  const state = (value) => { const i = cursor++; if (!(i in slots)) slots[i] = value; return [slots[i], (next) => { slots[i] = typeof next === 'function' ? next(slots[i]) : next; }]; };
  const ref = (value) => { const i = cursor++; if (!(i in slots)) slots[i] = { current: value }; return slots[i]; };
  const h = (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity).filter(Boolean) });
  const Component = createComponent(state, ref, (fn) => { if (!started) effect = fn; }, (fn) => fn, () => ({ user: { role }, refresh: async () => {} }), api, tallyStub, h, {}, 'Drawer', 'Alert', 'PasswordField', 'SettingsIcon', 'FolderIcon', 'KeyIcon', 'UserIcon');
  const render = () => { cursor = 0; return Component({ open: true, onClose: () => closed++ }); };
  const find = (node, predicate) => { if (!node || typeof node !== 'object') return; if (predicate(node)) return node; for (const child of node.children || []) { const found = find(child, predicate); if (found) return found; } };
  return { render, find, start: async () => { render(); if (effect) effect(); started = true; await new Promise(setImmediate); }, get closed() { return closed; } };
}
const checkbox = (app) => app.find(app.render(), (n) => n.props.type === 'checkbox');
const select = (app) => app.find(app.render(), (n) => n.type === 'select');

test('save is optimistic, serialized, blocks account switching/closing, and uses server-confirmed grants', async () => {
  const pending = deferred(); let saves = 0, reads = 0;
  const app = harness({ projectPermissions: async () => { reads++; return payload(); }, saveProjectPermissions: (id, grants) => { saves++; assert.equal(id, 1); assert.deepEqual(grants, [{ module: 'project', actions: ['view'] }]); return pending.promise; } });
  await app.start();
  const handler = checkbox(app).props.onChange;
  const saving = handler({ target: { checked: true } });
  assert.equal(checkbox(app).props.checked, true);
  handler({ target: { checked: false } });
  await select(app).props.onChange({ target: { value: '2' } });
  app.render().props.onClose();
  assert.equal(saves, 1); assert.equal(reads, 1); assert.equal(app.closed, 0);
  pending.resolve(payload(true)); await saving;
  assert.equal(checkbox(app).props.checked, true);
  assert.ok(app.find(app.render(), (n) => n.children?.some((c) => typeof c === 'string' && c.includes('Saved'))));
  app.render().props.onClose(); assert.equal(app.closed, 1);
});

test('failed saves restore the previous value and show an error', async () => {
  const app = harness({ projectPermissions: async () => payload(), saveProjectPermissions: async () => { throw new Error('Server unavailable'); } });
  await app.start(); await checkbox(app).props.onChange({ target: { checked: true } });
  assert.equal(checkbox(app).props.checked, false);
  assert.ok(app.find(app.render(), (n) => n.type === 'Alert' && n.children[0].includes('Server unavailable')));
});

test('switching Admin clears old permissions until a fresh fetch finishes', async () => {
  const pending = deferred(); let reads = 0;
  const app = harness({ projectPermissions: () => ++reads === 1 ? Promise.resolve(payload(true)) : pending.promise });
  await app.start(); assert.equal(checkbox(app).props.checked, true);
  const switching = select(app).props.onChange({ target: { value: '2' } });
  assert.equal(checkbox(app), undefined);
  pending.resolve(payload(true)); await switching;
  assert.equal(checkbox(app).props.checked, false); assert.equal(select(app).props.value, '2');
});

test('Admin role renders no permissions drawer and makes no request', async () => {
  let reads = 0;
  const app = harness({ projectPermissions: async () => { reads++; return payload(); } }, 'ADMIN');
  await app.start(); assert.equal(app.render(), null); assert.equal(reads, 0);
});

