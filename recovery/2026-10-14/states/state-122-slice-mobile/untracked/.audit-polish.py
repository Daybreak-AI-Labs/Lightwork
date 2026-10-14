from pathlib import Path
p=Path('apps/mobile-companion/src/screens/RunsListScreen.tsx');s=p.read_text(encoding='utf-8');a=s.index('  return (\n    <View style={styles.fill}>');b=s.index('      <FlatList',a);header=s[a:b].replace('  return (\n    <View style={styles.fill}>','  const header = <>');s=s[:a]+header+'''    </>;
  return (
    <View style={styles.fill}>
'''+s[b:];s=s.replace('      <FlatList\n        data={status ? rows.filter(row => row.status === status) : rows}', '''      <FlatList
        ListHeaderComponent={header}
        data={rows.filter(row => (!status || row.status === status) && (!offline || !search || `${row.title} ${row.result || ''}`.toLowerCase().includes(search.toLowerCase())))}''');s=s.replace("(value || 'All statuses')", "(value ? statusLabel(value) : 'All statuses')");s=s.replace('  const cached = useCachedBundle', '''  useEffect(() => {
    if (accessDenied || unsupported) { requestOwner.current += 1; pageRequest.current?.abort(); setOlder([]); }
  }, [accessDenied, unsupported]);
  const cached = useCachedBundle''');p.write_text(s,encoding='utf-8',newline='\n')
p=Path('apps/mobile-companion/src/components/ui.tsx');s=p.read_text(encoding='utf-8').replace('  const tone = statusTone(props.status);','  const baseTone = statusTone(props.status);\n  const tone = ({"#1a7f37": theme.success, "#9a6700": theme.text, "#cf222e": theme.danger} as Record<string, string>)[baseTone] || theme.muted;');p.write_text(s,encoding='utf-8',newline='\n')
p=Path('apps/mobile-companion/src/screens/RunDetailScreen.tsx');s=p.read_text(encoding='utf-8').replace('<Pressable onPress={onBack}', '<Pressable style={{minHeight: 48, justifyContent: "center"}} onPress={onBack}');p.write_text(s,encoding='utf-8',newline='\n')
p=Path('apps/mobile-companion/App.tsx');s=p.read_text(encoding='utf-8').replace('      setSettings(loaded);','      setRun(null); setSettings(loaded);').replace("tab !== 'settings' ? <Text style={styles.loading}","tab !== 'settings' ? <Text style={styles.cacheStatus}").replace('  loading: {', '  cacheStatus: {padding: 6, textAlign: "center", color: theme.muted},\n  loading: {');p.write_text(s,encoding='utf-8',newline='\n')
p=Path('apps/mobile-companion/src/screens/GlanceScreen.tsx');s=p.read_text(encoding='utf-8').replace('{ Pressable, ScrollView', '{ Linking, Pressable, ScrollView').replace('Banner, EmptyState','Banner, Button, EmptyState');s=s.replace('      {props.liveBundle ? <Text style={styles.note}>Attention counts', '''      <Button label="Review approvals and questions on dashboard" kind="secondary" onPress={() => { void Linking.openURL(settings.baseUrl.replace(/\\/+$/, '') + '/action-center'); }} />
      {props.liveBundle ? <Text style={styles.note}>Attention counts''');p.write_text(s,encoding='utf-8',newline='\n')
