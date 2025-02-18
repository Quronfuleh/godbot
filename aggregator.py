# aggregator.py

import asyncio
import aiohttp
import logging
from typing import Dict, Optional

# ----- PySerum -----
from pyserum.connection import conn as serum_conn
from pyserum.market import Market

# ----- Older Solana PublicKey -----
# (available in solana <= 0.26.x or so)
from solana.publickey import PublicKey

# We'll store the final aggregator data here
GLOBAL_DEX_DATA = {}

# ========== On-Chain Serum with pyserum ==========
async def fetch_serum_orderbook_onchain(
    market_conn, market_address: str, program_id: str
) -> Optional[Dict]:
    """
    Using pyserum to read an on-chain Serum market. We'll parse the
    best bid and best ask from the local orderbook data.
    """
    try:
        market_pubkey = PublicKey(market_address)
        program_pubkey = PublicKey(program_id)

        # Market.load is synchronous in pyserum 0.5.x
        market = Market.load(market_conn, market_pubkey, program_pubkey)

        bids = market.load_bids()
        asks = market.load_asks()
        if not bids or not asks:
            return None

        # Best bid is the highest price in bids
        best_bid_price = max(bids, key=lambda o: o.price).price if bids else 0
        # Best ask is the lowest price in asks
        best_ask_price = min(asks, key=lambda o: o.price).price if asks else 0

        if best_bid_price <= 0 or best_ask_price <= 0:
            return None

        return {"bid": float(best_bid_price), "ask": float(best_ask_price)}

    except Exception as e:
        logging.error(f"Serum On-Chain fetch error: {e}")
        return None

# ========== Raydium, Orca, Saber from HTTP approach ==========
async def fetch_raydium_pool(session: aiohttp.ClientSession, pair: str) -> Optional[Dict]:
    """
    Example: pair = "SOL-USDC"
    Hits https://api.raydium.io/pairs
    """
    url = "https://api.raydium.io/pairs"
    try:
        async with session.get(url, timeout=5) as resp:
            data = await resp.json()
            for pool in data:
                if pool.get("name", "").upper() == pair.upper():
                    price = float(pool.get("price", 0))
                    if price <= 0:
                        return None
                    # A simplistic "spread" approximation
                    spread = 0.001 * price
                    return {"bid": price - spread, "ask": price + spread}
            return None
    except Exception as e:
        logging.error(f"Raydium fetch error for {pair}: {e}")
        return None

async def fetch_orca_pool(session: aiohttp.ClientSession, pool_id: str) -> Optional[Dict]:
    """
    Example: pool_id = "SOL-USDC"
    Hits https://api.orca.so/allPools
    """
    url = "https://api.orca.so/allPools"
    try:
        async with session.get(url, timeout=5) as resp:
            data = await resp.json()
            pools = data.get("pools", [])
            for pool in pools:
                if (pool.get("id", "").upper() == pool_id.upper() or
                    pool.get("name", "").upper() == pool_id.upper()):
                    price = float(pool.get("price", 0))
                    if price <= 0:
                        return None
                    spread = 0.001 * price
                    return {"bid": price - spread, "ask": price + spread}
            return None
    except Exception as e:
        logging.error(f"Orca fetch error for {pool_id}: {e}")
        return None

async def fetch_saber_pool(session: aiohttp.ClientSession, pool_id: str) -> Optional[Dict]:
    if not pool_id:
        return None
    url = f"https://quote-api.saber.so/quote?poolId={pool_id}"
    try:
        async with session.get(url, timeout=5) as resp:
            data = await resp.json()
            mid_price = float(data.get("midPrice", 0))
            if mid_price <= 0:
                return None
            spread = 0.001 * mid_price
            return {"bid": mid_price - spread, "ask": mid_price + spread}
    except Exception as e:
        logging.error(f"Saber fetch error for {pool_id}: {e}")
        return None

# ========== Aggregator Loop ==========
async def aggregator_loop():
    """
    Continuously collects data for 'SOL_USDC' across:
      - Serum on-chain (via pyserum + older solana)
      - Raydium, Orca, Saber (HTTP calls)
    """
    # Example Serum market + program ID for SOL/USDC on mainnet
    serum_market_address = "9wFFujE8LgvC3KfCwNa1m3FvyjFFNt8kAG29o5Tg9SrC"
    serum_program_id = "9xQeWvG816bUx9EPXr7ip1BKEPDCbmPonf7D7uXAb7w"

    # We'll use pyserum's Connection (conn) to the Solana cluster
    rpc_url = "https://api.mainnet-beta.solana.com"
    serum_connection = serum_conn(rpc_url)

    # For Raydium/Orca/Saber pairs:
    token_pairs = {
        "SOL_USDC": {
            "raydium_pair": "SOL-USDC",
            "orca_pool":   "SOL-USDC",
            "saber_pool":  None
        }
    }

    while True:
        async with aiohttp.ClientSession() as session:
            # 1) Serum On-Chain
            serum_data = await fetch_serum_orderbook_onchain(
                market_conn=serum_connection,
                market_address=serum_market_address,
                program_id=serum_program_id
            )

            # 2) Raydium/Orca/Saber
            pair_name = "SOL_USDC"
            pair_info = token_pairs[pair_name]

            raydium_data = await fetch_raydium_pool(session, pair_info["raydium_pair"])
            orca_data = await fetch_orca_pool(session, pair_info["orca_pool"])
            saber_data = None
            if pair_info["saber_pool"]:
                saber_data = await fetch_saber_pool(session, pair_info["saber_pool"])

            # Combine all into a global dict
            GLOBAL_DEX_DATA[pair_name] = {
                "Serum": serum_data,
                "Raydium": raydium_data,
                "Orca": orca_data,
                "Saber": saber_data
            }

        # Sleep a few seconds before fetching again
        await asyncio.sleep(5)

def get_best_spread_for_pair(pair_name: str) -> Optional[Dict]:
    dex_data = GLOBAL_DEX_DATA.get(pair_name, {})
    if not dex_data:
        return None

    lowest_ask = None
    highest_bid = None
    lowest_ask_dex = None
    highest_bid_dex = None

    for dex_name, orderbook in dex_data.items():
        if not orderbook:
            continue
        ask = orderbook.get("ask")
        bid = orderbook.get("bid")
        if ask is None or bid is None:
            continue
        if lowest_ask is None or ask < lowest_ask:
            lowest_ask = ask
            lowest_ask_dex = dex_name
        if highest_bid is None or bid > highest_bid:
            highest_bid = bid
            highest_bid_dex = dex_name

    if lowest_ask is None or highest_bid is None:
        return None

    spread = highest_bid - lowest_ask
    return {
        "lowest_ask": lowest_ask,
        "lowest_ask_dex": lowest_ask_dex,
        "highest_bid": highest_bid,
        "highest_bid_dex": highest_bid_dex,
        "spread": spread
    }

# Simple test-run entry point
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

    async def run_agg():
        await aggregator_loop()

    asyncio.run(run_agg())
