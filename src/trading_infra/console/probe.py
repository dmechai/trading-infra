"""Verify a configured account against the venue, and read its fee tier.

The console is the only process that holds every account, so it is the only
one that can answer "does this key work?" for all of them. Grid carries the
EVM libraries and arb carries ccxt; neither can validate the other's accounts,
which is why an unverifiable key used to sit in the store looking configured.

Nothing here writes a secret anywhere, and every probe is a read: balances and
fee schedules only. A failure returns a message, never an exception trace, so
the console can render it without leaking key material.
"""
from decimal import Decimal

from ..credentials import CredentialError, load_account
from ..fees import record_account_tier

# Which venues answer a signed fee query, and the market to ask about. A
# venue absent here is validated for connectivity but has no tier to read.
FEE_PROBES = {
    'binance': {'market': 'BTC/USDT:USDT', 'type': 'swap'},
    'okx': {'market': 'BTC-USDT-SWAP', 'type': 'swap'},
    'bitget': {'market': 'BTC/USDT:USDT', 'type': 'swap'},
}

EVM_VENUES = frozenset({'entropy', 'risex', 'risex_testnet', 'arcus',
                        'hyperliquid', 'lighter'})

# secp256k1 group order; a key at or above it is not a valid private key.
_SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def _result(ok, message, **extra):
    return dict(ok=bool(ok), message=message, **extra)


def _check_evm(account):
    """Validate an EVM key and prove it derives the stored address."""
    import re
    key = str(account.get('private_key') or '').strip()
    address = str(account.get('account_address') or '').strip()
    if not re.fullmatch(r'(?:0x)?[0-9a-fA-F]{64}', key):
        return _result(False, '私钥格式无效：需为 64 位十六进制（可带 0x）')
    if not 0 < int(key.removeprefix('0x'), 16) < _SECP256K1_N:
        return _result(False, '私钥不在 secp256k1 有效区间内')
    if address and not re.fullmatch(r'0x[0-9a-fA-F]{40}', address):
        return _result(False, '账户地址格式无效：需为 0x 加 40 位十六进制')
    unavailable = '私钥格式有效；此进程缺少 keccak 后端，无法验证地址是否匹配'
    try:
        from eth_keys import keys
    except ImportError:
        return _result(True, unavailable, derived_address=None)
    try:
        derived = keys.PrivateKey(bytes.fromhex(key.removeprefix('0x'))) \
            .public_key.to_checksum_address()
    except ImportError:
        # eth_keys imports fine but resolves its keccak backend lazily, so a
        # missing one surfaces here. That is this process being incomplete,
        # not the key being bad, and must not condemn a working key.
        return _result(True, unavailable, derived_address=None)
    except Exception as exc:
        return _result(False, f'无法从私钥推导地址：{exc}')
    if address and derived.lower() != address.lower():
        # The commonest real misconfiguration: a key pasted next to someone
        # else's address. It looks configured and signs for the wrong account.
        return _result(False, f'私钥与账户地址不匹配，私钥对应 {derived}',
                       derived_address=derived)
    return _result(True, f'私钥有效，对应地址 {derived}', derived_address=derived)


def _ccxt_client(venue, account, market_type):
    import ccxt
    options = {'defaultType': 'future' if (venue == 'binance' and market_type == 'swap')
               else market_type}
    config = {
        'apiKey': str(account.get('api_key') or ''),
        'secret': str(account.get('secret') or ''),
        'enableRateLimit': True,
        'timeout': 20000,
        'options': options,
    }
    passphrase = str(account.get('passphrase') or '')
    if passphrase:
        config['password'] = passphrase
    client = getattr(ccxt, venue)(config)
    if venue == 'binance':
        # A signed read has no public route, so the whole *.binance.com family
        # is rewritten to the one host that answers from this region.
        from ..venues import reachable_binance_routes
        client.urls['api'] = reachable_binance_routes(client.urls.get('api'))
    return client


def _check_cex(venue, account, market_type):
    try:
        client = _ccxt_client(venue, account, market_type)
    except ImportError:
        return _result(True, '凭据字段齐备；此进程未安装 ccxt，无法联网验证')
    except Exception as exc:
        return _result(False, f'无法构建 {venue} 客户端：{exc}')
    try:
        balance = client.fetch_balance()
    except Exception as exc:
        return _result(False, f'{venue} 拒绝了这把 Key：{type(exc).__name__}: {exc}')
    finally:
        close = getattr(client, 'close', None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
    total = (balance.get('total') or {}).get('USDT')
    return _result(True, 'Key 有效，账户可读',
                   usdt_balance=None if total is None else float(total))


def check_account(venue, label, *, path=None):
    """One account, end to end: fields present, and the venue accepts it."""
    try:
        account = load_account(venue, label, path=path)
    except CredentialError as exc:
        return _result(False, str(exc))
    if venue in EVM_VENUES:
        return _check_evm(account)
    spec = FEE_PROBES.get(venue) or {'type': 'swap'}
    return _check_cex(venue, account, spec['type'])


def probe_fee_tier(venue, label, network='mainnet', *, path=None):
    """Read the account's real fee tier and record it for both desks."""
    spec = FEE_PROBES.get(venue)
    if not spec:
        return _result(False, f'{venue} 没有可查询的账户费率档位')
    try:
        account = load_account(venue, label, path=path)
    except CredentialError as exc:
        return _result(False, str(exc))
    try:
        client = _ccxt_client(venue, account, spec['type'])
    except ImportError:
        return _result(False, '此进程未安装 ccxt，无法查询费率')
    except Exception as exc:
        return _result(False, f'无法构建 {venue} 客户端：{exc}')
    try:
        client.load_markets()
        fees = client.fetch_trading_fee(spec['market'])
    except Exception as exc:
        return _result(False, f'{venue} 未返回费率：{type(exc).__name__}: {exc}')
    finally:
        close = getattr(client, 'close', None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
    maker, taker = fees.get('maker'), fees.get('taker')
    if maker is None or taker is None:
        return _result(False, f'{venue} 返回的费率缺少 maker/taker')
    try:
        stored = record_account_tier(venue, network, maker, taker,
                                     source=f'{venue}_fetch_trading_fee', path=path)
    except Exception as exc:
        return _result(False, str(exc))
    return _result(True,
                   f'账户费率 maker {Decimal(str(maker)) * 10000:.2f} bp / '
                   f'taker {Decimal(str(taker)) * 10000:.2f} bp，两边立即生效',
                   tier=stored)
