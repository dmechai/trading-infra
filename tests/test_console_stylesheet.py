"""The console stylesheet must stay free of dead rules and cramped rows.

Grid's stylesheet is kept honest by a test because 87 orphaned classes had
accumulated there. This one starts with the same guard rather than waiting
for the same drift.
"""
import re
from importlib.resources import files

STATIC = files('trading_infra.console').joinpath('static')
CSS = (STATIC / 'styles.css').read_text(encoding='utf-8')
MARKUP = ''.join((STATIC / name).read_text(encoding='utf-8')
                 for name in ('index.html', 'login.html', 'app.js'))


def _rem(value):
    """Resolve a spacing token to rem."""
    match = re.search(rf'^\s*{re.escape(value)}:\s*([0-9.]+)rem;', CSS, re.M)
    assert match, f'{value} is not declared'
    return float(match.group(1))


def test_no_orphaned_custom_properties():
    declared = set(re.findall(r'^\s*(--[a-z0-9-]+):', CSS, re.M))
    used = set(re.findall(r'var\((--[a-z0-9-]+)', CSS))
    assert not declared - used, sorted(declared - used)
    assert not used - declared, sorted(used - declared)


def test_no_orphaned_classes():
    classes = set(re.findall(r'\.([a-z][a-z0-9-]*)', CSS))
    orphans = sorted(name for name in classes if name not in MARKUP)
    assert not orphans, orphans


def test_table_rows_are_not_cramped():
    """Row padding is the thing that made six columns feel tight."""
    cell = re.search(r'^td \{ padding: var\((--s[0-9])\)', CSS, re.M)
    assert cell, 'td padding is not a single spacing token'
    assert _rem(cell.group(1)) >= 0.875, 'table cells need at least 14px of padding'


def test_body_and_prose_have_breathing_room():
    leading = re.search(r'--row-leading:\s*([0-9.]+);', CSS)
    assert leading and float(leading.group(1)) >= 1.6, 'body leading is too tight'
    # The explanatory paragraphs under each panel are the longest prose here.
    for cls in ('subtle', 'negative', 'positive', 'result'):
        rule = re.search(rf'\.{cls} \{{[^}}]*line-height:\s*([0-9.]+)', CSS)
        assert rule, f'.{cls} sets no line-height'
        assert float(rule.group(1)) >= 1.7, f'.{cls} leading is too tight'


def test_form_fields_are_separated():
    gap = re.search(r'\.account-form \{ display: grid; gap: var\((--s[0-9])\)', CSS)
    assert gap, 'account-form gap is not a spacing token'
    assert _rem(gap.group(1)) >= 1.0, 'stacked inputs need at least 16px between them'


def test_the_layout_reflows_on_a_phone():
    assert '@media (max-width: 40rem)' in CSS


def test_password_floor_is_eight_and_stated_in_the_ui():
    from trading_infra.console import auth
    assert auth.MIN_PASSWORD_LENGTH == 8
    assert '至少 8 位' in (STATIC / 'login.html').read_text(encoding='utf-8')
    assert '至少 8 位' in (STATIC / 'app.js').read_text(encoding='utf-8')
