from pathlib import Path
p=Path('packages/maverick-dashboard/maverick_dashboard/templates/chat_goal.html');s=p.read_text(encoding='utf-8');a=s.index('      var speakBtn = document.getElementById(\'read-aloud\');');b=s.index('    })();',a);s=s[:a]+'''      if (window.lwReadAloud) window.lwReadAloud(document.getElementById('read-aloud'));
'''+s[b:];s=s.replace('  <script>\n', '  <script src="{{ asset(\'lightwork-read-aloud.js\') }}"></script>\n  <script>\n',1);p.write_text(s,encoding='utf-8')
