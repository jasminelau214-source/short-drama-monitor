/** Temporary, staging-only Auth provisioning for live journal verification.
 * Not an application endpoint or auto-deployed Supabase function.
 * A trusted operator injects a fresh 256-bit challenge HASH and <=30min expiry.
 * The clear challenge and all session credentials stay outside source/logs.
 * Deploy the inert handler after revoking the worker and cleaning up the user.
 */
export function createHandler(config, deps) {
  const expectedUrl = 'https://adxlwjpqifejdcfozpuo.supabase.co';
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const reply = (status, value) => Response.json(value, {
    status, headers: { 'Cache-Control': 'no-store', 'Pragma': 'no-cache' },
  });
  const validConfig = config.projectUrl === expectedUrl
    && /^[0-9a-f]{64}$/.test(config.challengeHash)
    && /^[0-9a-f]{64}$/.test(config.subjectMarker)
    && /^journal-[0-9a-f-]{36}@jsm-v2-test\.invalid$/.test(config.email)
    && Number.isSafeInteger(config.expiresAt)
    && Number.isSafeInteger(config.issuedAt)
    && config.expiresAt > config.issuedAt
    && config.expiresAt - config.issuedAt <= 30 * 60 * 1000;
  return async function handler(req) {
    if (!validConfig || deps.now() < config.issuedAt || deps.now() >= config.expiresAt)
      return reply(410, { error: 'PROBE_DISABLED_OR_EXPIRED' });
    if (req.method !== 'POST') return reply(405, { error: 'POST_REQUIRED' });
    const authorization = req.headers.get('Authorization') ?? '';
    if (!/^Bearer [0-9a-f]{64}$/.test(authorization)) return reply(401, { error: 'UNAUTHORIZED' });
    const digest = Array.from(new Uint8Array(await deps.crypto.subtle.digest(
      'SHA-256', new TextEncoder().encode(authorization.slice(7)))))
      .map(x => x.toString(16).padStart(2, '0')).join('');
    let diff = 0;
    for (let i = 0; i < 64; i++) diff |= digest.charCodeAt(i) ^ config.challengeHash.charCodeAt(i);
    if (diff !== 0) return reply(401, { error: 'UNAUTHORIZED' });
    let body;
    try {
      const raw = await req.text();
      if (raw.length > 256) throw Error();
      body = JSON.parse(raw);
      if (!body || Array.isArray(body) || typeof body !== 'object'
          || !['create', 'cleanup'].includes(body.action)
          || Object.keys(body).sort().join(',') !== (body.action === 'create' ? 'action' : 'action,user_id')
          || (body.action === 'cleanup' && !uuid.test(body.user_id))) throw Error();
    } catch { return reply(400, { error: 'INVALID_PROBE_REQUEST' }); }
    // Authentication, expiry and input checks precede privileged secret access.
    if (deps.env('SUPABASE_URL') !== expectedUrl) return reply(403, { error: 'PROJECT_REFUSED' });
    let adminKey, publicKey;
    try {
      adminKey = JSON.parse(deps.env('SUPABASE_SECRET_KEYS') || '{}').default
        || deps.env('SUPABASE_SERVICE_ROLE_KEY');
      publicKey = JSON.parse(deps.env('SUPABASE_PUBLISHABLE_KEYS') || '{}').default
        || deps.env('SUPABASE_ANON_KEY');
      if (!adminKey || !publicKey) throw Error();
    } catch { return reply(503, { error: 'PROBE_KEYS_UNAVAILABLE' }); }
    async function auth(path, data, key = adminKey, method = 'POST') {
      const headers = { apikey: key, 'Content-Type': 'application/json' };
      if (!key.startsWith('sb_')) headers.Authorization = 'Bearer ' + key;
      const res = await deps.fetch(expectedUrl + '/auth/v1/' + path, {
        method, headers, ...(data === undefined ? {} : { body: JSON.stringify(data) }),
        redirect: 'error', signal: AbortSignal.timeout(15000),
      });
      if (!res.ok) throw Error('AUTH_PROBE_OPERATION_FAILED');
      return res.status === 204 ? {} : await res.json();
    }
    try {
      if (body.action === 'cleanup') {
        const result = await auth('admin/users/' + body.user_id, undefined, adminKey, 'GET');
        const user = result.user ?? result;
        if (user.id !== body.user_id || user.email !== config.email
            || user.app_metadata?.jsm_probe !== config.subjectMarker)
          return reply(403, { error: 'CLEANUP_SUBJECT_REFUSED' });
        await auth('admin/users/' + body.user_id, undefined, adminKey, 'DELETE');
        return reply(200, { cleaned: true });
      }
      const password = Array.from(deps.crypto.getRandomValues(new Uint8Array(32)))
        .map(x => x.toString(16).padStart(2, '0')).join('');
      const result = await auth('admin/users', {
        email: config.email, password, email_confirm: true,
        app_metadata: { jsm_probe: config.subjectMarker },
      });
      const user = result.user ?? result;
      if (!uuid.test(user.id) || user.email !== config.email) throw Error();
      try {
        const session = await auth('token?grant_type=password', {
          email: config.email, password,
        }, publicKey);
        if (session.user?.id !== user.id || typeof session.access_token !== 'string'
            || session.access_token.split('.').length !== 3) throw Error();
        return reply(200, { user_id: user.id, access_token: session.access_token,
          expires_at: session.expires_at });
      } catch {
        await auth('admin/users/' + user.id, undefined, adminKey, 'DELETE');
        throw Error();
      }
    } catch { return reply(503, { error: 'AUTH_PROBE_OPERATION_FAILED' }); }
  };
}

export const disabledHandler = () => Response.json({ error: 'PROBE_PERMANENTLY_DISABLED' }, {
  status: 410, headers: { 'Cache-Control': 'no-store' },
});
