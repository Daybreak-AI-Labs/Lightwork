from pathlib import Path
p=Path('apps/mobile-companion/tests/cache.test.cjs');s=p.read_text(encoding='utf-8')
s=s.replace("const bundle = title => ({ schema: 'maverick-offline/1',", "const policy = {allowed:true, max_age_seconds:86400, max_bytes:2097152, revision:'a'.repeat(64)};\nconst bundle = title => ({ schema: 'maverick-offline/1', as_of:Date.now()/1000, cache_policy:policy,")
s=s.replace("    getItem: async key => values.get(key) ?? null,", "    getItem: async key => values.get(key) ?? null,\n    removeItem: async key => {values.delete(key);},")
s+='''

test('server disablement removes a saved copy and missing policy cannot create one', async () => {
  const {cache, values} = fixture();
  await save(cache, bundle('PRIVATE'), first);
  assert.ok(await readCache(cache, first));
  await save(cache, {...bundle('PRIVATE'), cache_policy:{...policy,allowed:false}}, first);
  assert.equal(await readCache(cache, first), null);
  assert.equal(values.size, 0);
  assert.equal(await save(cache, {...bundle('UNVERIFIED'),cache_policy:undefined}, first), false);
});

test('organization retention ceiling rejects an old snapshot even when just downloaded', async () => {
  const {cache} = fixture();
  const old = {...bundle('EXPIRED'),as_of:Date.now()/1000-61,cache_policy:{...policy,max_age_seconds:60}};
  assert.equal(await save(cache, old, first), false);
  assert.equal(await readCache(cache, first), null);
});

test('late cache write cannot survive policy clearing or delete a newer permitted snapshot', async () => {
  const {cache, storage} = fixture();
  const pending = deferred(), write = storage.setItem; let calls = 0;
  storage.setItem = async (...args) => { if (++calls === 1) await pending.promise; await write(...args); };
  const old = save(cache,bundle('OLD'),first);
  await Promise.resolve();
  const clearing = save(cache,{...bundle('DISABLED'),cache_policy:{...policy,allowed:false}},first);
  const current = save(cache,bundle('CURRENT'),first);
  pending.resolve(); await Promise.all([old,clearing,current]);
  assert.equal((await readCache(cache,first)).bundle.goals[0].title,'CURRENT');
});
'''
p.write_text(s,encoding='utf-8',newline='\n')
p=Path('apps/mobile-companion/tests/audit-recovery.test.cjs');s=p.read_text(encoding='utf-8')
s=s.replace("cache.saveBundle({schema: 'maverick-offline/1', goals: []}", "cache.saveBundle({schema: 'maverick-offline/1', as_of:Date.now()/1000, goals: [], cache_policy:{allowed:true,max_age_seconds:86400,max_bytes:2097152,revision:'a'.repeat(64)}}")
s=s.replace("  await cache.purgeBundle(scope);\n  write.resolve(); await saving;", "  await Promise.resolve();\n  const purging = cache.purgeBundle(scope);\n  write.resolve(); await Promise.all([saving,purging]);")
p.write_text(s,encoding='utf-8',newline='\n')
