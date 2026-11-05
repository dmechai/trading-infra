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
