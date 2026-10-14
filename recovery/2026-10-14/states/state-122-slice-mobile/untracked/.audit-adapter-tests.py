import sys
from pathlib import Path

root = Path(__file__).resolve().parent
for package in (root / 'packages').iterdir():
    if package.is_dir():
        sys.path.insert(0, str(package))
sys.path.insert(0, str(root / 'apps/installer-cli'))
import maverick
import maverick_dashboard

# Use this slice's changed sources first and the integrated sibling only for
# newly added collaborator dependency modules absent from the slice baseline.
integrated = root.parent / 'Lightwork-staging/packages'
maverick.__path__.append(str(integrated / 'maverick-core/maverick'))
maverick_dashboard.__path__.append(str(integrated / 'maverick-dashboard/maverick_dashboard'))
import pytest
raise SystemExit(pytest.main(['-q', '--basetemp=' + str(root.parent / 'audit/mobile-pytest-adapters-5'),
    'packages/maverick-dashboard/tests/test_product_operations_notifications.py',
    'packages/maverick-dashboard/tests/test_product_operations_personal_notifications.py']))
