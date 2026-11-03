"""Public REST transport, with explicit timeout and no implicit write retries."""
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class PublicJSONClient:
    def __init__(self, api_url: str, *, timeout: float = 8):
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.api_url = api_url
        self.timeout = timeout

    def get_json(self, path: str, **params):
        url = self.api_url + path
        if params:
            url += "?" + urlencode(params)
        request = Request(url, headers={
            "Accept": "application/json", "User-Agent": "TradingInfra/0.1",
        })
        with urlopen(request, timeout=self.timeout) as response:
            return json.load(response)
