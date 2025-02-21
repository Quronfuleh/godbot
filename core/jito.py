import os
import json
import requests
from typing import Dict, Any, List, Optional

class JitoBlockEngineClient:
    def __init__(self, block_engine_url: str, auth_key: Optional[str] = None):
        """
        Client for interacting with the Jito Block Engine.
        
        Args:
            block_engine_url: Base URL of the Jito Block Engine (e.g., "https://mainnet.block-engine.jito.wtf")
            auth_key: Optional authentication key (not needed for public endpoints)
        """
        # Remove trailing slash if present.
        self.base_url = block_engine_url.rstrip('/')
        self.headers = {"Content-Type": "application/json"}
        # Only add the auth header if an auth key is provided
        if auth_key:
            self.headers["x-jito-auth"] = auth_key

    def send_transaction(self, transaction: str, encoding: str = "base58") -> Dict[str, Any]:
        """
        Sends a single transaction to the Jito Block Engine.
        
        This method acts as a proxy to Solana's sendTransaction RPC, with MEV protection.
        It always sets skip_preflight=true on the backend.
        
        Args:
            transaction: The signed transaction encoded as a string (base58 or base64).
            encoding: The encoding used for the transaction data. Options: "base58" or "base64".
                      Defaults to "base58". Use "base64" for faster processing.
        Returns:
            A dictionary representing the JSON-RPC response. If the transaction was sent as
            a bundle, the bundle ID may also be available in the HTTP header "x-bundle-id".
        """
        endpoint = f"{self.base_url}/api/v1/transactions"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                transaction,
                {"encoding": encoding}
            ]
        }
        response = requests.post(endpoint, headers=self.headers, json=payload)
        response.raise_for_status()
        result = response.json()
        # Check for an optional bundle ID in the headers (if sent as a bundle)
        bundle_id = response.headers.get("x-bundle-id")
        if bundle_id:
            result["bundle_id"] = bundle_id
        return result

    def send_bundle(self, transactions: List[str], encoding: str = "base58") -> Dict[str, Any]:
        """
        Sends a bundle (up to 5 transactions) to the Jito Block Engine.
        
        Bundles execute atomically and in sequence. If any transaction fails,
        the entire bundle is rejected.
        
        Args:
            transactions: A list of fully-signed transactions encoded as strings.
            encoding: The encoding used for the transaction data. Options: "base58" or "base64".
                      Defaults to "base58". Use "base64" for faster processing.
        Returns:
            A dictionary representing the JSON-RPC response containing the bundle ID.
        """
        endpoint = f"{self.base_url}/api/v1/bundles"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendBundle",
            "params": [
                transactions,
                {"encoding": encoding}
            ]
        }
        response = requests.post(endpoint, headers=self.headers, json=payload)
        response.raise_for_status()
        return response.json()

    def get_bundle_statuses(self, bundle_ids: List[str]) -> Dict[str, Any]:
        """
        Retrieves the statuses of submitted bundle(s).
        
        This functions similarly to Solana's getSignatureStatuses, returning details
        such as the slot in which the bundle was processed and its confirmation status.
        
        Args:
            bundle_ids: A list of bundle IDs to query (maximum of 5).
        Returns:
            A dictionary representing the JSON-RPC response with bundle status details.
        """
        endpoint = f"{self.base_url}/api/v1/getBundleStatuses"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBundleStatuses",
            "params": [bundle_ids]
        }
        response = requests.post(endpoint, headers=self.headers, json=payload)
        response.raise_for_status()
        return response.json()

    def get_tip_accounts(self) -> Dict[str, Any]:
        """
        Retrieves the tip accounts designated for bundle tips.
        
        Tip accounts are where SOL is transferred as part of the transaction tip to 
        incentivize validators. Use this method to retrieve the current tip accounts.
        
        Returns:
            A dictionary representing the JSON-RPC response containing a list of tip accounts.
        """
        endpoint = f"{self.base_url}/api/v1/bundles"
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTipAccounts",
            "params": []
        }
        response = requests.post(endpoint, headers=self.headers, json=payload)
        response.raise_for_status()
        return response.json()


def test_connection(block_engine_url: str):
    """
    Tests the connection to the Jito Block Engine by retrieving tip accounts.
    
    Args:
        block_engine_url: Base URL of the Jito Block Engine.
    """
    try:
        print(f"Testing connection to Jito Block Engine at: {block_engine_url}")
        client = JitoBlockEngineClient(block_engine_url)
        
        # Retrieve tip accounts to verify connectivity.
        tip_accounts = client.get_tip_accounts()
        print("Tip Accounts retrieved successfully:")
        print(json.dumps(tip_accounts, indent=2))
    except requests.exceptions.RequestException as e:
        print(f"Connection failed: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    # Replace with your desired Block Engine URL.
    # For example, use the mainnet endpoint:
    BLOCK_ENGINE_URL = "https://mainnet.block-engine.jito.wtf"
    
    # Test the connection to verify the Block Engine is reachable.
    test_connection(BLOCK_ENGINE_URL)
    
    # Example usage: Sending a transaction (uncomment and provide a valid transaction)
    # client = JitoBlockEngineClient(BLOCK_ENGINE_URL)
    # transaction_data = "<your-transaction-data>"  # base58 or base64 encoded string
    # tx_response = client.send_transaction(transaction_data, encoding="base64")
    # print("Transaction response:", json.dumps(tx_response, indent=2))
    
    # Example usage: Sending a bundle (uncomment and provide valid transactions)
    # bundle_txs = ["<tx1>", "<tx2>"]  # List of up to 5 transactions
    # bundle_response = client.send_bundle(bundle_txs, encoding="base64")
    # print("Bundle response:", json.dumps(bundle_response, indent=2))
    
    # Example usage: Querying bundle statuses (uncomment and provide bundle IDs)
    # bundle_ids = ["<bundle_id>"]
    # statuses = client.get_bundle_statuses(bundle_ids)
    # print("Bundle statuses:", json.dumps(statuses, indent=2))
