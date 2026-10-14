from pathlib import Path
p=Path('apps/mobile-companion/src/offlineCache.ts');s=p.read_text(encoding='utf-8').replace('const retiredScopes','let cacheGeneration = 0;\nconst retiredScopes');s=s.replace('  const entry: CachedBundle =','  const generation = cacheGeneration;\n  const entry: CachedBundle =');s=s.replace('if (retiredScopes.has(key)) await', 'if (retiredScopes.has(key) || generation !== cacheGeneration) await');s=s.replace('export async function purgeAppBundles(): Promise<void> {','export async function purgeAppBundles(): Promise<void> {\n  cacheGeneration += 1;');p.write_text(s,encoding='utf-8',newline='\n')
p=Path('apps/mobile-companion/src/settings.ts');s=p.read_text(encoding='utf-8').replace('    const old = store.profiles.find(profile => profile.id === existingId);','''    if (!store.profiles.length) {
      const raw = await SecureStore.getItemAsync(CONNECTION_KEY);
      if (raw) {
        let legacy: Settings;
        try { legacy = JSON.parse(raw); } catch { throw new InvalidSavedConnection('The previous saved connection is unreadable. Reset it before creating profiles.'); }
        if (!legacy || typeof legacy.baseUrl !== 'string' || typeof legacy.token !== 'string') throw new InvalidSavedConnection('The previous saved connection is invalid.');
        if (legacy.baseUrl !== settings.baseUrl || legacy.token !== settings.token) {
          const legacyId = newCacheScope();
          if (!legacyId) throw new Error('Secure device identity is unavailable.');
          store.profiles.push({...legacy, id: legacyId, name: 'Previous connection'});
        }
      }
    }
    const old = store.profiles.find(profile => profile.id === existingId);''');p.write_text(s,encoding='utf-8',newline='\n')
p=Path('apps/mobile-companion/tests/helpers.cjs');s=p.read_text(encoding='utf-8').replace("    if (Object.hasOwn(mocks, name)) return mocks[name];", """    if (Object.hasOwn(mocks, name)) {
      if (name.endsWith('/settings')) return {listProfiles: async () => ({active: '', profiles: []}), InvalidSavedConnection: class extends Error {}, ...mocks[name]};
      return mocks[name];
    }""");p.write_text(s,encoding='utf-8',newline='\n')
p=Path('packages/maverick-dashboard/maverick_dashboard/static/lightwork-read-aloud.js');s=p.read_text(encoding='utf-8').replace('    if (!button) return;','    if (!button || button.dataset.readAloudBound) return;\n    button.dataset.readAloudBound = "true";');s=s.replace("    window.addEventListener('pagehide'", "    document.addEventListener('lightwork:answer-ready', function () { stop(); }, {once: true});\n    window.addEventListener('pagehide'");p.write_text(s,encoding='utf-8',newline='\n')
p=Path('packages/maverick-dashboard/maverick_dashboard/templates/chat_goal.html');s=p.read_text(encoding='utf-8').replace("      if (window.lwReadAloud) window.lwReadAloud(document.getElementById('read-aloud'));", "      function setupReadAloud() { if (window.lwReadAloud) window.lwReadAloud(document.getElementById('read-aloud')); }\n      setupReadAloud();\n      document.addEventListener('lightwork:answer-ready', setupReadAloud);");p.write_text(s,encoding='utf-8',newline='\n')
