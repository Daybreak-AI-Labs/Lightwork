from pathlib import Path
p=Path('apps/mobile-companion/tests/helpers.cjs');s=p.read_text(encoding='utf-8').replace("'ScrollView', 'FlatList'", "'ScrollView', 'FlatList', 'Switch'")
s=s.replace("native.StyleSheet =", """native.useColorScheme = () => 'light';
const lifecycleListeners = new Set();
native.AppState = {
  currentState: 'active',
  addEventListener(_name, fn) { lifecycleListeners.add(fn); return {remove() { lifecycleListeners.delete(fn); }}; },
  emit(state) { this.currentState = state; lifecycleListeners.forEach(fn => fn(state)); },
};
native.BackHandler = {addEventListener() { return {remove() {}}; }};
native.Alert = {alert() {}};
native.Linking = {openURL: async () => {}};
native.StyleSheet =""")
s=s.replace('({ data, renderItem, ListEmptyComponent })', '({ data, renderItem, ListEmptyComponent, ListHeaderComponent, ListFooterComponent })')
s=s.replace("return React.createElement('FlatList', null, data.length", "return React.createElement('FlatList', null, ListHeaderComponent, data.length").replace(': ListEmptyComponent);', ': ListEmptyComponent, ListFooterComponent);')
s=s.replace("    if (name.endsWith('/offlineCache') &&", "    if (name.endsWith('/offlineCache') &&")
pos=s.index("    if (name.endsWith('/components/ConnectionNotice'))")
s=s[:pos]+"""    if (name.endsWith('/offlineCache')) return {
      purgeBundle: async () => {}, purgeAppBundles: async () => {}, useCachedBundle: () => null,
      useConnectionCache: () => ({data: null, savedAt: null}),
    };
"""+s[pos:]
s=s.replace('module.exports = { React,', 'module.exports = { native, React,');p.write_text(s,encoding='utf-8',newline='\n')
p=Path('apps/mobile-companion/tests/settings.test.cjs');s=p.read_text(encoding='utf-8').replace('    getItemAsync: async key => values.get(key) ?? null,','    getItemAsync: async key => values.get(key) ?? null,\n    deleteItemAsync: async key => { values.delete(key); },');p.write_text(s,encoding='utf-8',newline='\n')
