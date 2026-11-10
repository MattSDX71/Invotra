"""Fast, dependency-light checks for the UI-only release."""
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / 'templates'


def read(name):
    return (TEMPLATES / name).read_text(encoding='utf8')


def test_all_templates_parse():
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)))
    for template in TEMPLATES.glob('*.html'):
        env.parse(template.read_text(encoding='utf8'))


def test_super_admin_workspaces_are_present():
    source = read('super_admin_dashboard.html')
    for panel in ['organization', 'people', 'catalog', 'notifications', 'requests']:
        assert f'data-panel="{panel}"' in source
    assert 'Assign Existing User' not in source
    for action in ['create_company', 'create_parent', 'create_sub', 'create_user', 'create_gl', 'save_notifications']:
        assert f'value="{action}"' in source


def test_user_access_and_security_controls_remain():
    source = read('super_admin_dashboard.html')
    for endpoint in ['super_admin_delete_user', 'super_admin_delete_gl', 'super_admin_sub_request', 'super_admin_user_request', 'test_email']:
        assert endpoint in source
    assert 'name="csrf_token"' in source
    assert 'data-parent-select' in source
    assert 'name="sub_location_ids"' in source


def test_navigation_and_scroll_maintained():
    base = read('base.html')
    assert 'data-preserve-scroll' in base
    assert 'sessionStorage' in base
    assert 'data-submit-on-change' in base
    assert 'aria-expanded' in base
    assert 'mobile-menu-button' in base
    assert 'user-dropdown' in base
    assert 'location.pathname' in base
    assert 'invotra-ui.css' in base
    for name in ['admin_dashboard.html', 'super_admin_dashboard.html']:
        assert 'data-preserve-scroll' in read(name)


def test_inventory_remains_available():
    assert 'inventory_start' in read('base.html')
    assert 'inventory_session' in read('dashboard.html')
    scan = read('inventory_session.html')
    for amount in ['0.25', '0.5', '0.75', '1']:
        assert f"'{amount}'" in scan
    assert 'inventory_scan' in scan
    assert 'inventory_submit' in scan
    assert 'inventory_unknown_submit' in scan


def test_mobile_styles_and_branding():
    css = (ROOT / 'static' / 'invotra-ui.css').read_text(encoding='utf8')
    assert '@media(max-width:800px)' in css
    assert '.sidebar.is-open' in css
    assert '.workspace-panel[hidden]' in css
    assert (ROOT / 'static' / 'invotra-logo-white.png').is_file()
