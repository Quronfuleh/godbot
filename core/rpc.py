import requests
import base64
from typing import Optional, Dict, Any


class SolanaRPC:
    def __init__(self, rpc_url: str = "https://api.mainnet-beta.solana.com"):
        self.rpc_url = rpc_url

    def _send_request(self, method: str, params: list) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params
        }
        headers = {"Content-Type": "application/json"}
        response = requests.post(self.rpc_url, json=payload, headers=headers)
        response.raise_for_status()
        json_response = response.json()
        if "error" in json_response:
            raise Exception(f"RPC Error: {json_response['error']}")
        return json_response

    def _snake_to_camel(self, key: str) -> str:
        components = key.split('_')
        return components[0] + ''.join(x.title() for x in components[1:])

    def get_balance(self, public_key: str, commitment: str = "confirmed") -> int:
        params = [public_key, {"commitment": commitment}]
        response = self._send_request("getBalance", params)
        return response["result"]["value"]

    def get_recent_blockhash(self, commitment: str = "confirmed") -> str:
        params = [{"commitment": commitment}]
        response = self._send_request("getLatestBlockhash", params)  # Updated method name
        return response["result"]["value"]["blockhash"]

    def send_transaction(self, signed_transaction: bytes, **kwargs) -> str:
        encoded_tx = base64.b64encode(signed_transaction).decode("utf-8")
        options = {self._snake_to_camel(k): v for k, v in kwargs.items()}
        params = [encoded_tx, options]
        response = self._send_request("sendTransaction", params)
        return response["result"]

    def request_airdrop(self, public_key: str, lamports: int, commitment: str = "confirmed") -> str:
        params = [public_key, lamports, {"commitment": commitment}]
        response = self._send_request("requestAirdrop", params)
        return response["result"]

    def get_transaction(self, signature: str, encoding: str = "json", 
                        commitment: str = "confirmed") -> Optional[Dict[str, Any]]:
        params = [signature, {"encoding": encoding, "commitment": commitment}]
        response = self._send_request("getTransaction", params)
        return response.get("result")