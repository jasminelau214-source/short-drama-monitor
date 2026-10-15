import { test } from 'node:test';
import assert from 'node:assert/strict';
import { webcrypto, createHash } from 'node:crypto';
import { createHandler } from './auth_probe.mjs';

const token = 'a'.repeat(64), hash = createHash('sha256').update(token).digest('hex');
const id = '11111111-1111-4111-8111-111111111111';
const email = 'journal-' + id + '@jsm-v2-test.invalid';
const config = { projectUrl: 'https://adxlwjpqifejdcfozpuo.supabase.co',
  challengeHash: hash, subjectMarker: hash, email, issuedAt: 100, expiresAt: 10000 };
function fixture(options = {}) {
  const calls = [], envReads = [];
  const deps = { now: () => 200, crypto: webcrypto,
    env: name => { envReads.push(name); return {
      SUPABASE_URL: config.projectUrl,
      SUPABASE_SECRET_KEYS: '{"default":"sb_secret_SYNTHETIC"}',
      SUPABASE_PUBLISHABLE_KEYS: '{"default":"sb_publishable_SYNTHETIC"}',
    }[name]; },
    fetch: async (url, req) => {
      calls.push({ url, ...req });
      if (url.endsWith('token?grant_type=password')) return Response.json({
        user: { id }, access_token: 'synthetic.session.jwt', expires_at: 1000,
      });
      return Response.json({ id, email, app_metadata: { jsm_probe: hash } });
    }, ...options };
  return { handler: createHandler(config, deps), calls, envReads };
}
const request = (body = { action: 'create' }, authorization = 'Bearer ' + token) =>
  new Request(config.projectUrl, { method: 'POST',
    headers: { Authorization: authorization }, body: JSON.stringify(body) });

test('missing or wrong challenge never reads privileged environment or calls Auth', async () => {
  for (const token of ['', 'Bearer ' + 'b'.repeat(64)]) {
    const f = fixture();
    assert.equal((await f.handler(request(undefined, token))).status, 401);
    assert.equal(f.calls.length, 0); assert.equal(f.envReads.length, 0);
  }
});
test('expired or excessive lifetime configuration denies before secret access', async () => {
  const f = fixture({ now: () => config.expiresAt });
  assert.equal((await f.handler(request())).status, 410);
  assert.equal(f.envReads.length, 0);
  const handler = createHandler({ ...config, expiresAt: 2000000 }, {
    now: () => 200, crypto: webcrypto, env: () => assert.fail(), fetch: () => assert.fail(),
  });
  assert.equal((await handler(request())).status, 410);
});
test('arbitrary target, subject and oversized or malformed request denied before secret access', async () => {
  for (const body of [{ action: 'create', email: 'attacker@example.test' },
    { action: 'cleanup', user_id: '../../other' }, { action: 'unknown' },
    { action: 'create', padding: 'x'.repeat(300) }]) {
    const f = fixture(); assert.equal((await f.handler(request(body))).status, 400);
    assert.equal(f.envReads.length, 0); assert.equal(f.calls.length, 0);
  }
});
test('production or another project environment refused before Auth calls', async () => {
  const f = fixture({ env: () => 'https://zxxuuhpqcvnohtelqgzp.supabase.co' });
  assert.equal((await f.handler(request())).status, 403); assert.equal(f.calls.length, 0);
});
test('fixed confirmed test user and normal session only; no admin key or password returned', async () => {
  const f = fixture(), result = await f.handler(request()), body = await result.json();
  assert.equal(result.status, 200); assert.equal(result.headers.get('Cache-Control'), 'no-store');
  assert.deepEqual(body, { user_id: id, access_token: 'synthetic.session.jwt', expires_at: 1000 });
  assert.equal(f.calls.length, 2);
  const create = JSON.parse(f.calls[0].body);
  assert.equal(create.email, email); assert.equal(create.email_confirm, true);
  assert.equal(create.password.length, 64); assert.equal(create.app_metadata.jsm_probe, hash);
  assert.equal(f.calls[0].headers.apikey, 'sb_secret_SYNTHETIC');
  assert.equal(f.calls[1].headers.apikey, 'sb_publishable_SYNTHETIC');
  assert.equal(f.calls.every(c => c.redirect === 'error'), true);
  assert.equal(JSON.stringify(body).includes(create.password), false);
});
test('failed sign-in cleans newly created user and sanitizes upstream failure', async () => {
  const f = fixture();
  const original = f.handler;
  const calls = [];
  const handler = createHandler(config, {
    now: () => 200, crypto: webcrypto,
    env: name => ({ SUPABASE_URL: config.projectUrl, SUPABASE_SERVICE_ROLE_KEY: 'synthetic.admin.jwt',
      SUPABASE_ANON_KEY: 'synthetic.anon.jwt' })[name],
    fetch: async (url, req) => {
      calls.push({ url, ...req });
      return url.includes('/token?') ? new Response('SECRET', { status: 500 }) : Response.json({ id, email });
    },
  });
  const result = await handler(request());
  assert.equal(result.status, 503); assert.deepEqual(await result.json(), { error: 'AUTH_PROBE_OPERATION_FAILED' });
  assert.equal(calls.at(-1).method, 'DELETE'); assert.equal(calls.at(-1).url.endsWith('/' + id), true);
});
test('cleanup refuses a different user or missing protected probe marker', async () => {
  const f = fixture({ fetch: async () => Response.json({ id, email, app_metadata: {} }) });
  assert.equal((await f.handler(request({ action: 'cleanup', user_id: id }))).status, 403);
});
test('cleanup deletes only fixed test email and protected marker', async () => {
  const f = fixture();
  assert.equal((await f.handler(request({ action: 'cleanup', user_id: id }))).status, 200);
  assert.equal(f.calls.length, 2); assert.equal(f.calls[1].method, 'DELETE');
  assert.equal(f.calls[1].url.endsWith('/' + id), true);
});
test('a fresh authentication challenge can clean the fixed previous probe subject', async () => {
  const f = fixture(), next = 'b'.repeat(64);
  const nextHash = createHash('sha256').update(next).digest('hex');
  const calls = [];
  const handler = createHandler({ ...config, challengeHash: nextHash }, {
    now: () => 200, crypto: webcrypto,
    env: name => ({ SUPABASE_URL: config.projectUrl, SUPABASE_SERVICE_ROLE_KEY: 'synthetic.admin.jwt',
      SUPABASE_ANON_KEY: 'synthetic.anon.jwt' })[name],
    fetch: async (url, req) => {
      calls.push({ url, ...req });
      return Response.json({ id, email, app_metadata: { jsm_probe: hash } });
    },
  });
  assert.equal((await handler(request({ action: 'cleanup', user_id: id }, 'Bearer ' + next))).status, 200);
  assert.equal(calls.at(-1).method, 'DELETE');
});
