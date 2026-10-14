from pathlib import Path
p=Path('apps/mobile-companion/tests/audit-recovery.test.cjs');s=p.read_text(encoding='utf-8');s+='''

test('named profiles retain separate credentials and remove only the chosen profile', async () => {
  const values = new Map();
  const settings = source('src/settings.ts', {
    'expo-secure-store': {
      getItemAsync: async key => values.get(key) ?? null,
      setItemAsync: async (key, value) => { values.set(key, value); },
      deleteItemAsync: async key => { values.delete(key); },
    },
    './offlineCache': {purgeBundle: async () => {}, purgeAppBundles: async () => {}},
  });
  await settings.saveProfile('First workspace', connection);
  await settings.saveProfile('Second workspace', newer);
  const profiles = await settings.listProfiles();
  assert.equal(profiles.profiles.length, 2);
  const first = profiles.profiles.find(profile => profile.name === 'First workspace');
  await settings.activateProfile(first.id);
  assert.equal((await settings.loadSettings()).token, connection.token);
  await settings.removeProfile(first.id);
  assert.equal((await settings.loadSettings()).needsSetup, true);
  const remaining = await settings.listProfiles();
  assert.equal(remaining.profiles.length, 1);
  await settings.activateProfile(remaining.profiles[0].id);
  assert.equal((await settings.loadSettings()).token, newer.token);
});
''';p.write_text(s,encoding='utf-8',newline='\n')
p=Path('apps/mobile-companion/src/settings.ts');s=p.read_text(encoding='utf-8').replace('    await purgeBundle(old?.cacheScope);', '    await purgeBundle(old?.cacheScope);\n    const previousActive = store.profiles.find(entry => entry.id === store.active);\n    if (previousActive?.id !== id) await purgeBundle(previousActive?.cacheScope);');p.write_text(s,encoding='utf-8',newline='\n')
p=Path('apps/mobile-companion/README.md');s=p.read_text(encoding='utf-8');s+='''
Named connections can be added, switched and removed in Settings; their tokens
remain in the same device secure store. Switching clears the previous offline
copy and resets the visible task. The initial connection screen is also the
mobile setup flow for the offline-saving choice. Native screens follow the
system light/dark preference and system text size. Product copy is currently
English; selecting other languages must wait for reviewed translations.
''';p.write_text(s,encoding='utf-8',newline='\n')
