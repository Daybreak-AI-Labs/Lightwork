const { test } = require('node:test');
const assert = require('node:assert/strict');
const { React, act, create, source, deferred, connection, newer, text } = require('./helpers.cjs');

const first = { ...connection, cacheScope: '11111111-1111-4111-8111-111111111111' };
const second = { ...connection, token: newer.token, cacheScope: '22222222-2222-4222-8222-222222222222' };
const save = (cache, value, settings) => cache.saveBundle(value, settings.baseUrl, settings.cacheScope);
const readCache = (cache, settings) => cache.loadBundle(settings.baseUrl, settings.cacheScope);
const bundle = title => ({ schema: 'maverick-offline/1', goals: [{id:1,title,status:'active'}], glance: {counts:{active:1,pending_approvals:0,open_questions:0},spend:{dollars:0},active:[{id:1,title}]} });
function fixture() {
  const values = new Map();
  const storage = {
    getItem: async key => values.get(key) ?? null,
    setItem: async (key, value) => { values.set(key, value); },
  };
  const cache = source('src/offlineCache.ts', {'@react-native-async-storage/async-storage':storage});
  return {values, storage, cache};
}

test('same-host credential changes cannot load the preceding credential cache', async () => {
  const {cache, values} = fixture();
  await save(cache, bundle('FIRST'), first);
  assert.equal((await readCache(cache, first)).bundle.goals[0].title, 'FIRST');
  assert.equal(await readCache(cache, second), null);
  assert.ok([...values.keys()].every(key => !key.includes(connection.token) && !key.includes(connection.baseUrl)));
  assert.ok([...values.values()].every(value => !value.includes(connection.token)));
});

test('unscoped legacy caches and credentials without a scope fail closed', async () => {
  const {cache, values} = fixture();
  values.set('maverick.offlineBundle.v1.' + encodeURIComponent(connection.baseUrl), JSON.stringify({savedAt:1,bundle:bundle('LEGACY')}));
  assert.equal(await readCache(cache, connection), null);
  const before = values.size;
  await save(cache, bundle('UNSCOPED'), connection);
  assert.equal(values.size, before);
});

test('late offline bundle for an old connection cannot populate the new cache', async () => {
  const {cache} = fixture();
  const pending = deferred();
  const oldWrite = pending.promise.then(value => save(cache, value, first));
  await save(cache, bundle('NEW'), second);
  pending.resolve(bundle('OLD'));
  await oldWrite;
  assert.equal((await readCache(cache, second)).bundle.goals[0].title, 'NEW');
});

for (const screenName of ['RunsListScreen', 'GlanceScreen']) {
  for (const pendingOld of [false, true]) {
    test(`${screenName} hides ${pendingOld ? 'late' : 'already loaded'} old offline data after a same-host token change`, async () => {
      const f = fixture();
      await save(f.cache, bundle('PRIVATE FIRST'), first);
      await save(f.cache, bundle('PRIVATE SECOND'), second);
      const oldRead = deferred(), newRead = deferred();
      const read = f.storage.getItem;
      let reads = 0;
      f.storage.getItem = async key => {
        if (++reads === 1 && pendingOld) await oldRead.promise;
        if (reads > 1) await newRead.promise;
        return read(key);
      };
      const { [screenName]: Screen } = source(`src/screens/${screenName}.tsx`, {
        '../api': {}, '../offlineCache': f.cache,
        '../poll': { usePolling: () => ({data:null,error:'offline'}) },
      });
      let app;
      await act(async () => { app=create(React.createElement(Screen,{settings:first,onOpenRun(){}})); });
      if (!pendingOld) assert.match(text(app), /PRIVATE FIRST/);
      await act(async () => { app.update(React.createElement(Screen,{settings:second,onOpenRun(){}})); });
      assert.doesNotMatch(text(app), /PRIVATE FIRST/);
      await act(async () => { oldRead.resolve(); newRead.resolve(); });
      assert.doesNotMatch(text(app), /PRIVATE FIRST/);
      assert.match(text(app), /PRIVATE SECOND/);
      app.unmount();
    });
  }
}
