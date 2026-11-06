import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from trading_infra import credentials
from trading_infra.credentials import CredentialError, describe, list_accounts, load_account


class CredentialStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'credentials.json'

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, accounts, mode=0o600, **extra):
        self.path.write_text(json.dumps(dict(version=1, accounts=accounts, **extra)))
        os.chmod(self.path, mode)
        return self.path

    def test_importing_the_module_reads_nothing(self):
        # A process that never calls in must not touch the store at all.
        self.assertFalse(hasattr(credentials, '_cache'))
        self.assertIsInstance(credentials.DEFAULT_PATH, Path)

    def test_world_access_is_refused_but_a_secrets_group_is_allowed(self):
        account = {'arcus:arb': {'auth': 'evm_key', 'private_key': 'k'}}
        for mode in (0o644, 0o604, 0o660, 0o666):
            path = self.write(account, mode=mode)
            with self.assertRaises(CredentialError, msg=oct(mode)):
                load_account('arcus', 'arb', path=path)
        # 0640 lets a non-root service read through a dedicated group.
        for mode in (0o600, 0o640):
            path = self.write(account, mode=mode)
            self.assertEqual(load_account('arcus', 'arb', path=path)['private_key'], 'k', oct(mode))

    def test_accounts_are_keyed_by_venue_and_label(self):
        path = self.write({
            'entropy:wash': {'auth': 'evm_key', 'private_key': 'a'},
            'entropy:arb': {'auth': 'evm_key', 'private_key': 'b'},
        })
        self.assertEqual(load_account('entropy', 'wash', path=path)['private_key'], 'a')
        self.assertEqual(load_account('entropy', 'arb', path=path)['private_key'], 'b')
        self.assertEqual(list_accounts(path), ['entropy:arb', 'entropy:wash'])

    def test_an_incomplete_account_fails_before_it_could_sign(self):
        path = self.write({'bitget:funding': {'auth': 'hmac_passphrase', 'api_key': 'k', 'secret': 's'}})
        with self.assertRaises(CredentialError) as caught:
            load_account('bitget', 'funding', path=path)
        self.assertIn('passphrase', str(caught.exception))

    def test_an_unknown_auth_type_is_refused_rather_than_guessed(self):
        path = self.write({'okx:arb': {'auth': 'magic', 'api_key': 'k'}})
        with self.assertRaises(CredentialError):
            load_account('okx', 'arb', path=path)

    def test_a_missing_store_reports_the_path_and_no_secret(self):
        with self.assertRaises(CredentialError) as caught:
            load_account('arcus', 'arb', path=self.path)
        self.assertIn(str(self.path), str(caught.exception))

    def test_describe_reports_readiness_without_values(self):
        path = self.write({
            'risex:arb': {'auth': 'evm_key_address',
                          'private_key': 'SECRETVALUE1', 'account_address': '0xADDRESS1'},
            'bitget:funding': {'auth': 'hmac', 'api_key': 'SECRETVALUE2', 'secret': ''},
        })
        rows = {r['key']: r for r in describe(path)}
        self.assertTrue(rows['risex:arb']['ready'])
        self.assertFalse(rows['bitget:funding']['ready'])
        rendered = json.dumps(rows)
        for value in ('SECRETVALUE1', 'SECRETVALUE2', '0xADDRESS1'):
            self.assertNotIn(value, rendered)

    def test_the_env_override_selects_the_store(self):
        path = self.write({'arcus:arb': {'auth': 'evm_key', 'private_key': 'k'}})
        previous = os.environ.get(credentials.ENV_PATH)
        os.environ[credentials.ENV_PATH] = str(path)
        try:
            self.assertEqual(credentials.store_path(), path)
            self.assertEqual(load_account('arcus', 'arb')['private_key'], 'k')
        finally:
            if previous is None:
                os.environ.pop(credentials.ENV_PATH, None)
            else:
                os.environ[credentials.ENV_PATH] = previous


if __name__ == '__main__':
    unittest.main()


class CredentialWriteTests(unittest.TestCase):
    """Writes must stay atomic and keep the store readable by its group."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'credentials.json'
        self.path.write_text(json.dumps({'version': 1, 'accounts': {}}))
        os.chmod(self.path, 0o640)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_then_load_round_trips(self):
        credentials.save_account('hyperliquid', 'arb', {
            'auth': 'evm_key_address', 'private_key': 'k', 'account_address': '0x1'}, path=self.path)
        loaded = load_account('hyperliquid', 'arb', path=self.path)
        self.assertEqual((loaded['private_key'], loaded['account_address']), ('k', '0x1'))

    def test_save_never_returns_a_secret(self):
        result = credentials.save_account('bitget', 'funding', {
            'auth': 'hmac_passphrase', 'api_key': 'KEY1', 'secret': 'SEC1',
            'passphrase': 'PASS1'}, path=self.path)
        rendered = json.dumps(result)
        for value in ('KEY1', 'SEC1', 'PASS1'):
            self.assertNotIn(value, rendered)

    def test_a_blank_secret_keeps_the_stored_one(self):
        credentials.save_account('hyperliquid', 'arb', {
            'auth': 'evm_key_address', 'private_key': 'original', 'account_address': '0x1'}, path=self.path)
        credentials.save_account('hyperliquid', 'arb', {
            'auth': 'evm_key_address', 'private_key': '', 'account_address': '0x2'}, path=self.path)
        loaded = load_account('hyperliquid', 'arb', path=self.path)
        self.assertEqual(loaded['private_key'], 'original')
        self.assertEqual(loaded['account_address'], '0x2')

    def test_an_incomplete_save_is_refused_and_changes_nothing(self):
        before = self.path.read_text()
        with self.assertRaises(CredentialError):
            credentials.save_account('bitget', 'funding', {
                'auth': 'hmac_passphrase', 'api_key': 'k', 'secret': 's'}, path=self.path)
        self.assertEqual(self.path.read_text(), before)

    def test_the_file_mode_survives_a_write(self):
        credentials.save_account('hyperliquid', 'arb', {
            'auth': 'evm_key_address', 'private_key': 'k', 'account_address': '0x1'}, path=self.path)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o640)

    def test_delete_removes_only_the_named_account(self):
        for label in ('arb', 'acceptance'):
            credentials.save_account('hyperliquid', label, {
                'auth': 'evm_key_address', 'private_key': 'k', 'account_address': '0x1'}, path=self.path)
        credentials.delete_account('hyperliquid', 'arb', path=self.path)
        self.assertEqual(list_accounts(self.path), ['hyperliquid:acceptance'])
        with self.assertRaises(CredentialError):
            credentials.delete_account('hyperliquid', 'arb', path=self.path)
