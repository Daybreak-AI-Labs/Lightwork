from pathlib import Path
p=Path('apps/mobile-companion/src/theme.ts');s=p.read_text(encoding='utf-8');s='import { useColorScheme } from "react-native";\n'+s;s=s.replace('  text:', '  background: "#ffffff",\n  onAccent: "#ffffff",\n  result: "#ddf4ff",\n  text:',1);s+='''
export type Palette = typeof theme;
const dark: Palette = {
  background: '#111820', onAccent: '#111820', result: '#17334a',
  text: '#f0f4f8', muted: '#b7c5d3', border: '#657789', accent: '#8cc8ff',
  success: '#79dda0', danger: '#ff9b9b', dangerBg: '#3a1922',
  warnBg: '#382d0f', warnText: '#ffe49c', surface: '#202b37',
};
export function useTheme(): Palette { return useColorScheme() === 'dark' ? dark : theme; }
''';p.write_text(s,encoding='utf-8',newline='\n')
for p in [*Path('apps/mobile-companion/src/screens').glob('*.tsx'), *Path('apps/mobile-companion/src/components').glob('*.tsx')]:
 s=p.read_text(encoding='utf-8')
 if 'import { theme }' not in s:continue
 s=s.replace('import { theme }','import { useTheme, type Palette }')
 s=s.replace('): React.JSX.Element {', '): React.JSX.Element {\n  const theme = useTheme(), styles = stylesFor(theme);')
 s=s.replace('const styles = StyleSheet.create({','const stylesFor = (theme: Palette) => StyleSheet.create({')
 s=s.replace('fill: { flex: 1 }','fill: { flex: 1, backgroundColor: theme.background }').replace('backgroundColor: "#ffffff"','backgroundColor: theme.background').replace('backgroundColor: "#ddf4ff"','backgroundColor: theme.result').replace('color: "#ffffff"','color: theme.onAccent')
 s=s.replace('actions: { flexDirection: "row",','actions: { flexDirection: "row", flexWrap: "wrap",').replace('primary: { backgroundColor:', 'primary: { minHeight: 48, justifyContent: "center", backgroundColor:').replace('secondary: { borderRadius:', 'secondary: { minHeight: 48, justifyContent: "center", borderRadius:').replace('show: {','show: { minHeight: 48, minWidth: 48,')
 p.write_text(s,encoding='utf-8',newline='\n')
p=Path('apps/mobile-companion/App.tsx');s=p.read_text(encoding='utf-8').replace('import type { Settings }', 'import { useTheme, type Palette } from "./src/theme";\nimport type { Settings }').replace('export default function App(): React.JSX.Element {','export default function App(): React.JSX.Element {\n  const theme = useTheme(), styles = stylesFor(theme);').replace('const styles = StyleSheet.create({','const stylesFor = (theme: Palette) => StyleSheet.create({').replace('fill: { flex: 1 }','fill: { flex: 1, backgroundColor: theme.background }').replace('color: "#57606a"','color: theme.muted').replace('color: "#cf222e"','color: theme.danger').replace('backgroundColor: "#0969da"','backgroundColor: theme.accent').replace('color: "#ffffff"','color: theme.onAccent').replace('borderTopColor: "#d0d7de"','borderTopColor: theme.border').replace('color: "#0969da"','color: theme.accent');p.write_text(s,encoding='utf-8',newline='\n')
