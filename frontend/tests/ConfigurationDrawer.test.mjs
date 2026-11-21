import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { transformWithOxc } from 'vite';
import * as permissionModel from '../src/components/config/permissionModel.js';

async function load(file, bindings) {
  const input = await readFile(new URL('../src/components/' + file, import.meta.url), 'utf8');
  const source = input.replace(/^import .*;\r?\n/gm, '')
    .replace('import.meta.env.BASE_URL', '"/nexus/"')
    .replace('export default function', 'function');
  const { code } = await transformWithOxc(source, file, { jsx: { runtime: 'classic', pragma: 'h' } });
  const name = file.split('/').at(-1).replace('.jsx', '');
  return new Function(...Object.keys(bindings), code.replaceAll('React.Fragment', '"Fragment"') + '\nreturn ' + name)(...Object.values(bindings));
}

function hooks() {
  const values = [], effects = [];
  let cursor = 0;
  const h = (type, props, ...children) => ({ type, props: props || {}, children: children.flat(Infinity).filter(Boolean) });
  const bindings = {
    h,
    styles: {},
    useState(initial) {
      const i = cursor++;
      if (!(i in values)) values[i] = initial;
      return [values[i], (next) => { values[i] = typeof next === 'function' ? next(values[i]) : next; }];
    },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!values[i] || deps.some((dep, j) => dep !== values[i][j])) {
        values[i] = deps;
        effects.push(fn);
      }
    },
    useMemo: (fn) => fn(),
    useCallback: (fn) => fn,
  };
  function render(Component, props) {
    cursor = 0;
    Component(props);
    for (const fn of effects.splice(0)) fn();
    cursor = 0;
    return Component(props);
  }
  return { bindings, render };
}

function find(node, predicate) {
  if (!node || typeof node !== 'object') return undefined;
  if (predicate(node)) return node;
  for (const child of node.children) {
    const found = find(child, predicate);
    if (found) return found;
  }
  return undefined;
}
const button = (tree, label) => find(tree, (n) => n.type === 'button' && n.children.includes(label));
const editor = (tree) => find(tree, (n) => n.type === 'PermissionEditor');
const select = (tree) => find(tree, (n) => n.type === 'select');
const staff = [
  { id: 1, username: 'first', full_name: 'First', email: '', phone: '', is_active: true, role: 'ADMIN', projects: ['project'], module_grants: {} },
  { id: 2, username: 'second', full_name: 'Second', email: '', phone: '', is_active: true, role: 'ADMIN', projects: ['project'], module_grants: { project: ['view'] } },
];

async function access(configApi) {
  const state = hooks();
  const noop = () => {};
  const Component = await load('config/AccessTab.jsx', {
    ...state.bindings, ...permissionModel, configApi, PermissionEditor: 'PermissionEditor',
    window: { addEventListener: noop, removeEventListener: noop, confirm: () => true },
  });
  const props = { staff, catalog: [], labels: {}, selectedId: 1, onSelect: (id) => { props.selectedId = id; }, onDirty: noop, onSaved: async () => {} };
  return () => state.render(Component, props);
}

test('saving permissions sends the edited grants and disables saving and account switching until the request settles', async () => {
  let finish;
  const writes = [];
  const pending = new Promise((resolve) => { finish = resolve; });
  const render = await access({ updateStaff: (...args) => { writes.push(args); return pending; } });
  editor(render()).props.onChange({ ...editor(render()).props.value, module_grants: { project: ['view'] } });
  const save = button(render(), 'Save Changes').props.onClick();
  assert.equal(select(render()).props.disabled, true);
  assert.equal(button(render(), 'Saving…').props.disabled, true);
  assert.equal(writes.length, 1);
  assert.equal(writes[0][0], 1);
  assert.deepEqual(writes[0][1].module_grants, { project: ['view'] });
  finish();
  await save;
  assert.equal(select(render()).props.disabled, false);
  assert.match(find(render(), (n) => n.props.role === 'status').children[0], /Changes saved/);
});

test('failed saves show an error and keep unsaved changes available for retry or discard', async () => {
  const render = await access({ updateStaff: async () => { throw new Error('Server unavailable'); } });
  editor(render()).props.onChange({ ...editor(render()).props.value, module_grants: { project: ['view'] } });
  await button(render(), 'Save Changes').props.onClick();
  assert.equal(find(render(), (n) => n.props.role === 'alert').children[0], 'Server unavailable');
  assert.deepEqual(editor(render()).props.value.module_grants, { project: ['view'] });
  button(render(), 'Discard Changes').props.onClick();
  assert.deepEqual(editor(render()).props.value.module_grants, {});
  assert.equal(button(render(), 'Save Changes').props.disabled, true);
});

test('switching accounts replaces the previous account grants with the selected account grants', async () => {
  const render = await access({});
  assert.deepEqual(editor(render()).props.value.module_grants, {});
  select(render()).props.onChange({ target: { value: '2' } });
  assert.deepEqual(editor(render()).props.value.module_grants, { project: ['view'] });
  assert.equal(select(render()).props.value, 2);
});

test('restricted Admin role has no configuration drawer and makes no staff request', async () => {
  const state = hooks();
  let reads = 0;
  const Drawer = await load('ConfigurationDrawer.jsx', {
    ...state.bindings,
    configApi: { staff: async () => { reads += 1; } },
    useAuth: () => ({ user: { role: 'ADMIN' } }),
  });
  assert.equal(state.render(Drawer, { open: true }), null);
  assert.equal(reads, 0);
});
