const { test } = require('node:test');
const assert = require('node:assert/strict');
const { source } = require('./helpers.cjs');

test('legacy invalid addresses cannot send a token before settings are corrected', async () => {
  const api = source('src/api.ts', { fetch: () => assert.fail('invalid connection must never request') });
  for (const baseUrl of ['http://remote.example.test', 'ftp://localhost',
    'https://user:password@example.test', // pragma: allowlist secret - synthetic invalid URL fixture
    'https://example.test?redirect=elsewhere']) {
    assert.equal(api.isTokenTransportSafe(baseUrl), false);
    await assert.rejects(api.listGoals({ baseUrl, token: 'synthetic-token' }));
  }
});

test('request guard uses the same secure addresses accepted by connection setup', async () => {
  const calls = [];
  const api = source('src/api.ts', { fetch: async (...args) => {
    calls.push(args);
    return { ok: true, json: async () => [] };
  } });
  for (const baseUrl of ['https://dashboard.example.test', 'http://127.0.0.1:8765', 'http://[::1]:8765']) {
    assert.equal(api.isTokenTransportSafe(baseUrl), true);
    await api.listGoals({ baseUrl, token: 'synthetic-token' });
  }
  assert.equal(calls.length, 3);
  assert.equal(calls[0][0], 'https://dashboard.example.test/api/v1/goals?limit=50');
  assert.equal(calls[0][1].method, 'GET');
  assert.equal(calls[0][1].headers.Authorization, 'Bearer synthetic-token');
});
