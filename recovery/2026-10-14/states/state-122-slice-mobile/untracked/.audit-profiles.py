from pathlib import Path
p=Path('apps/mobile-companion/src/settings.ts')
s=p.read_text(encoding='utf-8');s += '''

export type ConnectionProfile = {id: string; name: string};
const PROFILES_KEY = "maverick.profiles.v1";
type StoredProfile = Settings & ConnectionProfile;
type ProfileStore = {active: string; profiles: StoredProfile[]};

function parseProfiles(raw: string | null): ProfileStore {
  if (!raw) return {active: '', profiles: []};
  let value: ProfileStore;
  try { value = JSON.parse(raw); } catch { throw new InvalidSavedConnection('Saved connection profiles are invalid. Reset saved connections to recover.'); }
  if (!value || !Array.isArray(value.profiles) || typeof value.active !== 'string'
      || value.profiles.some(profile => !profile || typeof profile.id !== 'string' || typeof profile.name !== 'string'
        || typeof profile.baseUrl !== 'string' || typeof profile.token !== 'string')) {
    throw new InvalidSavedConnection('Saved connection profiles are invalid. Reset saved connections to recover.');
  }
  return value;
}

export function listProfiles(): Promise<{active: string; profiles: ConnectionProfile[]}> {
  return inOrder(async () => {
    const store = parseProfiles(await SecureStore.getItemAsync(PROFILES_KEY));
    return {active: store.active, profiles: store.profiles.map(({id, name}) => ({id, name}))};
  });
}

export function saveProfile(name: string, settings: Settings, existingId?: string): Promise<void> {
  return inOrder(async () => {
    const title = name.trim();
    if (!title || title.length > 80) throw new Error('Enter a connection name of up to 80 characters.');
    const error = connectionValidationError(settings.baseUrl, settings.token);
    if (error) throw new Error(error);
    const store = parseProfiles(await SecureStore.getItemAsync(PROFILES_KEY));
    const old = store.profiles.find(profile => profile.id === existingId);
    const id = existingId || newCacheScope();
    if (!id) throw new Error('Secure device identity is unavailable. Update the app to save named profiles.');
    const profile: StoredProfile = {...settings, id, name: title, cacheScope: newCacheScope()};
    const profiles = [...store.profiles.filter(entry => entry.id !== id), profile];
    // Active profile and credentials are committed together in this one secure
    // record. The legacy single-connection record remains unchanged until reset.
    await SecureStore.setItemAsync(PROFILES_KEY, JSON.stringify({active: id, profiles}));
    await purgeBundle(old?.cacheScope);
  });
}

export function activateProfile(id: string): Promise<void> {
  return inOrder(async () => {
    const store = parseProfiles(await SecureStore.getItemAsync(PROFILES_KEY));
    const next = store.profiles.find(profile => profile.id === id);
    if (!next) throw new Error('This saved connection is no longer available.');
    const previous = store.profiles.find(profile => profile.id === store.active);
    const cacheScope = newCacheScope();
    const profiles = store.profiles.map(profile => profile.id === id ? {...profile, cacheScope} : profile);
    await SecureStore.setItemAsync(PROFILES_KEY, JSON.stringify({active: id, profiles}));
    await purgeBundle(previous?.cacheScope);
    await purgeBundle(next.cacheScope);
  });
}

export function removeProfile(id: string): Promise<void> {
  return inOrder(async () => {
    const store = parseProfiles(await SecureStore.getItemAsync(PROFILES_KEY));
    const removed = store.profiles.find(profile => profile.id === id);
    const profiles = store.profiles.filter(profile => profile.id !== id);
    const active = store.active === id ? '' : store.active;
    await SecureStore.setItemAsync(PROFILES_KEY, JSON.stringify({active, profiles}));
    await purgeBundle(removed?.cacheScope);
  });
}
'''
s=s.replace('    await SecureStore.getItemAsync(CONNECTION_KEY);','    await SecureStore.getItemAsync(CONNECTION_KEY);\n    await SecureStore.getItemAsync(PROFILES_KEY);\n    await SecureStore.deleteItemAsync(PROFILES_KEY);',1)
s=s.replace('    const record = await SecureStore.getItemAsync(CONNECTION_KEY);','''    const profilesRaw = await SecureStore.getItemAsync(PROFILES_KEY);
    if (profilesRaw !== null) {
      const store = parseProfiles(profilesRaw);
      const selected = store.profiles.find(profile => profile.id === store.active);
      return selected ? {...selected} : {baseUrl: DEFAULT_BASE_URL, token: '', needsSetup: true};
    }
    const record = await SecureStore.getItemAsync(CONNECTION_KEY);''',1)
p.write_text(s,encoding='utf-8',newline='\n')
p=Path('apps/mobile-companion/src/screens/SettingsScreen.tsx');s=p.read_text(encoding='utf-8');s=s.replace('loadSettings, resetSettings, saveSettings','activateProfile, listProfiles, loadSettings, removeProfile, resetSettings, saveProfile, saveSettings, type ConnectionProfile');s=s.replace('  const [baseUrl,','  const [profiles, setProfiles] = useState<ConnectionProfile[]>([]), [profileId, setProfileId] = useState<string | undefined>();\n  const [profileName, setProfileName] = useState("");\n  const [baseUrl,',1);s=s.replace('      setOfflineEnabled(s.offlineEnabled !== false);','''      setOfflineEnabled(s.offlineEnabled !== false);
      void listProfiles().then(store => {
        if (!current) return;
        setProfiles(store.profiles); setProfileId(store.active || undefined);
        setProfileName(store.profiles.find(profile => profile.id === store.active)?.name || '');
      }, () => { if (current) setError('Could not read saved connections. Unlock your device and retry.'); });''');s=s.replace('await saveSettings({ baseUrl: address, token, offlineEnabled });','''if (profileName.trim()) await saveProfile(profileName, {baseUrl: address, token, offlineEnabled}, profileId);
      else if (profiles.length) { setError('Enter a name for this connection.'); return; }
      else await saveSettings({ baseUrl: address, token, offlineEnabled });''');s=s.replace('  function checked():','''  function switchProfile(id: string): void {
    Alert.alert('Switch saved connection?', 'Unsaved connection edits will be discarded and the previous offline cache removed.', [
      {text: 'Cancel', style: 'cancel'}, {text: 'Switch', onPress: () => {
        edited(() => {});
        void activateProfile(id).then(() => {setLoadAttempt(value => value + 1); props.onSaved();}, () => setError('Could not switch the connection. Unlock the device and retry.'));
      }},
    ]);
  }
  function removeSelected(): void {
    if (!profileId) return;
    Alert.alert('Remove this saved connection?', 'Only this connection and its cached task data will be removed.', [
      {text: 'Cancel', style: 'cancel'}, {text: 'Remove', style: 'destructive', onPress: () => {
        void removeProfile(profileId).then(() => {setLoadAttempt(value => value + 1); props.onSaved();}, () => setError('Could not remove this connection. Unlock the device and retry.'));
      }},
    ]);
  }
  function checked():''');s=s.replace('      <Text style={styles.label} nativeID="address-label">','''      {profiles.length ? <Text style={styles.label}>Saved connections</Text> : null}
      {profiles.map(profile => <Button key={profile.id} label={(profileId === profile.id ? 'Current: ' : 'Switch to ') + profile.name} kind="secondary" disabled={disabled || testing || profileId === profile.id} onPress={() => switchProfile(profile.id)} />)}
      {profileId ? <Button label="Remove this saved connection" kind="secondary" disabled={disabled || testing} onPress={removeSelected} /> : null}
      <Button label="Add another connection" kind="secondary" disabled={disabled || testing} onPress={() => edited(() => {setProfileId(undefined); setProfileName(''); setBaseUrl(''); setToken('');})} />
      <Text style={styles.label}>Connection name (optional for a single connection)</Text>
      <TextInput style={styles.input} accessibilityLabel="Connection name" maxLength={80} editable={!disabled} value={profileName} onChangeText={setProfileName} />
      <Text style={styles.fieldHelp}>Choose a name to keep this connection securely alongside your other deployments. The app follows your device appearance and text size; interface language is currently English.</Text>
      <Text style={styles.label} nativeID="address-label">''');p.write_text(s,encoding='utf-8',newline='\n')
