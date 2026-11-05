"""Read-only resolver for exchange account credentials.

This module holds no secrets and reads nothing at import time. A process that
never calls `load_account` never touches the store, which is what keeps the
public market scanner free of credential material even though it imports this
package.

The store is keyed by venue *and account label* because one venue routinely
holds several isolated accounts: the self-wash account must stay separate from
an arbitrage account, and an acceptance probe from both.
"""
from pathlib import Path
import json
import os
import stat
import tempfile

DEFAULT_PATH = Path('/etc/dmechai/credentials.json')
ENV_PATH = 'TRADING_INFRA_CREDENTIALS'

# Every field a venue needs, so a half-configured account fails before it is
# ever used to sign something.
REQUIRED = {
    'evm_key': ('private_key',),
    'evm_key_address': ('private_key', 'account_address'),
    'hmac': ('api_key', 'secret'),
    'hmac_passphrase': ('api_key', 'secret', 'passphrase'),
}


class CredentialError(Exception):
    """Configuration is missing, unreadable or unsafe. Never carries a secret."""


def store_path(path=None):
    return Path(path or os.environ.get(ENV_PATH) or DEFAULT_PATH)


def _read(path):
    target = store_path(path)
    try:
        info = target.stat()
    except OSError as exc:
        raise CredentialError(f'Credential store unavailable: {target}') from exc
    if info.st_mode & (stat.S_IWGRP | stat.S_IXGRP | stat.S_IRWXO):
        # Group *read* is allowed so a dedicated secrets group can share the
        # store with a non-root service. Anything wider - group write, or any
        # access for other users - is treated as compromised, not as usable.
        raise CredentialError(
            f'Credential store must be 0600 or 0640 with a dedicated group: {target}')
    try:
        data = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise CredentialError(f'Credential store is not readable JSON: {target}') from exc
    accounts = data.get('accounts')
    if not isinstance(accounts, dict):
        raise CredentialError(f'Credential store has no accounts map: {target}')
    return accounts


def list_accounts(path=None):
    """Account keys only. Values never leave this module through here."""
    return sorted(_read(path))


def load_account(venue, label='default', *, path=None):
    """Return one account's fields, validated against its declared auth type."""
    key = f'{venue}:{label}'
    accounts = _read(path)
    entry = accounts.get(key)
    if not isinstance(entry, dict):
        raise CredentialError(f'No credentials configured for {key}')
    auth = entry.get('auth')
    required = REQUIRED.get(auth)
    if required is None:
        raise CredentialError(f'{key} declares an unknown auth type')
    missing = [field for field in required if not str(entry.get(field) or '').strip()]
    if missing:
        raise CredentialError(f'{key} is missing: {", ".join(missing)}')
    resolved = dict(entry)
    resolved.setdefault('venue', venue)
    resolved.setdefault('label', label)
    return resolved


def describe(path=None):
    """Which accounts exist and whether each is complete. No secret values."""
    rows = []
    for key in list_accounts(path):
        venue, _, label = key.partition(':')
        try:
            load_account(venue, label or 'default', path=path)
            rows.append(dict(key=key, venue=venue, label=label or 'default', ready=True, error=None))
        except CredentialError as exc:
            rows.append(dict(key=key, venue=venue, label=label or 'default', ready=False, error=str(exc)))
    return rows


# Fields that are secret and must never be returned to a caller that only
# wants to know whether an account is configured.
SECRET_FIELDS = frozenset({'private_key', 'secret', 'passphrase', 'api_key', 'account_address'})


def _load_document(path=None):
    target = store_path(path)
    if not target.is_file():
        return target, {'version': 1, 'accounts': {}}
    _read(path)  # Re-uses the permission and shape checks.
    return target, json.loads(target.read_text(encoding='utf-8'))


def _write_document(target, document):
    """Atomic replace that preserves the store's mode and owning group."""
    try:
        info = target.stat()
        mode, gid = stat.S_IMODE(info.st_mode), info.st_gid
    except OSError:
        mode, gid = 0o600, -1
    handle, temporary = tempfile.mkstemp(dir=str(target.parent), prefix='.credentials-')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        if gid != -1:
            os.chown(temporary, info.st_uid, gid)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def save_account(venue, label, fields, *, path=None):
    """Create or update one account. Blank fields leave the stored value alone."""
    auth = str(fields.get('auth') or '').strip()
    if auth not in REQUIRED:
        raise CredentialError(f'Unknown auth type for {venue}:{label}')
    target, document = _load_document(path)
    accounts = document.setdefault('accounts', {})
    entry = dict(accounts.get(f'{venue}:{label}') or {})
    entry.update(venue=venue, label=label, auth=auth)
    for key, value in fields.items():
        if key in {'venue', 'label'}:
            continue
        text = '' if value is None else str(value).strip()
        # An empty secret means "keep what is stored", never "erase it".
        if key in SECRET_FIELDS and not text:
            continue
        entry[key] = text if key != 'enabled' else bool(value)
    missing = [f for f in REQUIRED[auth] if not str(entry.get(f) or '').strip()]
    if missing:
        raise CredentialError(f'{venue}:{label} is missing: {", ".join(missing)}')
    accounts[f'{venue}:{label}'] = entry
    _write_document(target, document)
    return {k: v for k, v in entry.items() if k not in SECRET_FIELDS}


def delete_account(venue, label, *, path=None):
    target, document = _load_document(path)
    if document.get('accounts', {}).pop(f'{venue}:{label}', None) is None:
        raise CredentialError(f'No credentials configured for {venue}:{label}')
    _write_document(target, document)
    return True


# Which consumer needs which account. Declared here, in the one place both
# applications read, so neither has to guess what the other requires.
CONSUMERS = (
    dict(key='entropy:wash', venue='entropy', label='wash', auth='evm_key',
         used_by='Grid 自刷量', purpose='Entropy 同所刷量', required=True),
    dict(key='risex:arb', venue='risex', label='arb', auth='evm_key_address',
         used_by='Grid 跨所套利', purpose='RiseX 腿', required=True),
    dict(key='arcus:arb', venue='arcus', label='arb', auth='evm_key_address',
         used_by='Grid 跨所套利', purpose='Arcus 腿', required=False),
    dict(key='hyperliquid:arb', venue='hyperliquid', label='arb', auth='evm_key_address',
         used_by='Grid 跨所套利', purpose='Hyperliquid 腿（币种机会主要在这里）', required=False),
    dict(key='binance:funding', venue='binance', label='funding', auth='hmac',
         used_by='arb 期现套利', purpose='Binance 现货+永续', required=False),
    dict(key='bitget:funding', venue='bitget', label='funding', auth='hmac_passphrase',
         used_by='arb 期现套利', purpose='Bitget 现货+永续', required=True),
)


def inventory(path=None):
    """What every consumer needs and whether it is configured. No secrets.

    Both Grid and arb render this same list, so filling an account in one
    place is visible from the other instead of each showing a partial view.
    """
    try:
        accounts = _read(path)
        store_error = None
    except CredentialError as exc:
        accounts, store_error = {}, str(exc)
    rows = []
    for spec in CONSUMERS:
        entry = accounts.get(spec['key'])
        ready, problem = False, None
        if entry:
            try:
                load_account(spec['venue'], spec['label'], path=path)
                ready = True
            except CredentialError as exc:
                problem = str(exc)
        rows.append(dict(spec, configured=bool(entry), ready=ready, error=problem,
                         enabled=bool((entry or {}).get('enabled', True))))
    known = {spec['key'] for spec in CONSUMERS}
    for key in sorted(set(accounts) - known):
        venue, _, label = key.partition(':')
        rows.append(dict(key=key, venue=venue, label=label or 'default',
                         auth=(accounts[key] or {}).get('auth'), used_by='—', purpose='其他账户',
                         required=False, configured=True, ready=True, error=None,
                         enabled=bool(accounts[key].get('enabled', True))))
    return dict(store=str(store_path(path)), error=store_error, accounts=rows)


def app_secret(name, *, path=None):
    """One application secret from the store's `app` section, or empty."""
    target = store_path(path)
    if not target.is_file():
        return ''
    try:
        _read(path)
    except CredentialError:
        return ''
    document = json.loads(target.read_text(encoding='utf-8'))
    return str((document.get('app') or {}).get(name) or '').strip()
