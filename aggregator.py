# aggregator.py

import asyncio
import aiohttp
import logging
from typing import Dict, Optional

# We'll store the final aggregator data here, keyed by pair_name.
GLOBAL_DEX_DATA = {}

# ===================== DEX Fetch Functions (HTTP) =====================
async def fetch_raydium_pool(session: aiohttp.ClientSession, pair: str) -> Optional[Dict]:
    """
    Fetches the price from Raydium's /pairs endpoint.

    Example: pair = "SOL-USDC"
      => queries https://api.raydium.io/pairs,
         searches for {"name": "SOL-USDC"}, then returns a {bid, ask} dict
         if found.
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
                    # A simplistic "spread" approximation: +/- 0.1%
                    spread = 0.001 * price
                    return {"bid": price - spread, "ask": price + spread}
            return None
    except Exception as e:
        logging.error(f"Raydium fetch error for {pair}: {e}")
        return None

async def fetch_orca_pool(session: aiohttp.ClientSession, pool_id: str) -> Optional[Dict]:
    """
    Fetches the price from Orca's /allPools endpoint.

    Example: pool_id = "SOL-USDC"
      => queries https://api.orca.so/allPools,
         searches in data["pools"] for the item with id or name = "SOL-USDC",
         then returns a {bid, ask} dict if found.
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
    """
    Fetches the price from Saber.

    Example: pool_id = "SaberPoolIDHere"
      => queries https://quote-api.saber.so/quote?poolId={pool_id},
         expects a "midPrice" field, from which we produce a {bid, ask} dict.
    """
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

# ===================== Aggregator Loop =====================
async def aggregator_loop():
    """
    Continuously collects data for 'SOL_USDC' across:
      - Raydium
      - Orca
      - Saber

    (No Serum in this version.)
    """
    token_pairs = {
        "SOL_USDC": {
            "raydium_pair": "SOL-USDC",
            "orca_pool":   "SOL-USDC",
            "saber_pool":  None  # Put a valid Saber pool ID if you want Saber data
        }
    }

    while True:
        async with aiohttp.ClientSession() as session:
            pair_name = "SOL_USDC"
            pair_info = token_pairs[pair_name]

            # 1) Raydium
            raydium_data = await fetch_raydium_pool(session, pair_info["raydium_pair"])

            # 2) Orca
            orca_data = await fetch_orca_pool(session, pair_info["orca_pool"])

            # 3) Saber
            saber_data = None
            if pair_info["saber_pool"]:
                saber_data = await fetch_saber_pool(session, pair_info["saber_pool"])

            # Store results in our global data dict
            GLOBAL_DEX_DATA[pair_name] = {
                "Raydium": raydium_data,
                "Orca": orca_data,
                "Saber": saber_data
            }

        # Sleep a bit before the next fetch
        await asyncio.sleep(5)

def get_best_spread_for_pair(pair_name: str) -> Optional[Dict]:
    """
    Returns a dict containing the best spread (highest bid - lowest ask)
    among Raydium, Orca, Saber for the specified pair_name.
    """
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

        # track best ask
        if lowest_ask is None or ask < lowest_ask:
            lowest_ask = ask
            lowest_ask_dex = dex_name

        # track best bid
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

# ===================== Test Run Entry Point =====================
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

    async def run_agg():
        await aggregator_loop()

    asyncio.run(run_agg())
