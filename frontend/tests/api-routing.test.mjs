import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const clientSource = await readFile(new URL('../src/api/client.js', import.meta.url), 'utf8');
const authSource = await readFile(new URL('../src/api/auth.js', import.meta.url), 'utf8');
const moduleUrl = (source) => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;

for (const [base, apiBase] of [['/', '/api'], ['/nexus/', '/nexus/api']]) {
  for (const role of ['SUPERADMIN', 'ADMIN']) {
    test(`${base} routes CSRF, ${role} login and me through ${apiBase}`, async () => {
      const clientUrl = moduleUrl(clientSource.replaceAll('import.meta.env.BASE_URL', JSON.stringify(base)));
      const { API_BASE } = await import(clientUrl);
      assert.equal(API_BASE, apiBase);
      const { authApi } = await import(moduleUrl(authSource.replace("'./client'", JSON.stringify(clientUrl))));
      const originalFetch = globalThis.fetch;
      const originalDocument = globalThis.document;
      const calls = [];
      globalThis.document = { cookie: '' };
      globalThis.fetch = async (url, options) => {
        calls.push({ url, options });
        return { ok: true, status: 200, json: async () => url.endsWith('/csrf/') ? { csrfToken: 'test-csrf' } : { user: { role } } };
      };
      try {
        assert.equal((await authApi.login('test-account', 'test-only')).user.role, role);
        assert.equal((await authApi.me()).user.role, role);
        assert.deepEqual(calls.map((c) => c.url), [`${apiBase}/auth/csrf/`, `${apiBase}/auth/login/`, `${apiBase}/auth/me/`]);
        assert.ok(calls.every((c) => c.options.credentials === 'same-origin'));
        assert.equal(calls[1].options.headers['X-CSRFToken'], 'test-csrf');
        assert.equal(calls[1].options.method, 'POST');
        assert.deepEqual(JSON.parse(calls[1].options.body), { username: 'test-account', password: 'test-only' });
      } finally {
        globalThis.fetch = originalFetch;
        if (originalDocument === undefined) delete globalThis.document;
        else globalThis.document = originalDocument;
      }
    });
  }
}

test('Vite keeps local proxy and production asset/router base aligned', async () => {
  const { default: config } = await import('../vite.config.js');
  const local = config({ command: 'serve', mode: 'development' });
  const production = config({ command: 'build', mode: 'production' });
  assert.equal(local.base, '/');
  assert.ok(local.server.proxy['/api']);
  assert.equal(production.base, '/nexus/');
});
