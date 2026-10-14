from pathlib import Path
root=Path("C:\\Users\\Cstep\\Documents\\Codex\\2026-09-10\\new-chat\\work\\lightwork-2529-integration-20261005")
path=root/'packages/maverick-dashboard/maverick_dashboard/product_operations.py'
text=path.read_text(encoding='utf-8');assert text.count('        product_operations_commitment_capability,')==2
text=text.replace('        product_operations_commitment_capability,','        product_operations_commitment_capability,\n        product_operations_commitment_economic_alternatives,')
path.write_text(text,encoding='utf-8',newline='\n')
print('Financial comparison operations registered.')

