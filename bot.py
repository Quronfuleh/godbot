import os
import asyncio
import logging
import time
from decimal import Decimal
from typing import List, Dict, Optional, Tuple
from datetime import datetime

# SQLAlchemy
from sqlalchemy import create_engine, Column, String, Integer, DateTime, Float, BigInteger, Index
from sqlalchemy.orm import declarative_base, sessionmaker

# Solana + solders
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.keypair import Keypair

# Pydantic 2.x
from pydantic import BaseModel, field_validator
import base58

# Our aggregator (Raydium/Orca/Saber only)
import aggregator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)

Base = declarative_base()

class Config(BaseModel):
    rpc_url: str
    private_key: bytes
    min_profit: Decimal
    transaction_fee: Decimal
    dry_run: bool = False

    @field_validator("private_key", mode="before")
    def validate_private_key(cls, v: str) -> bytes:
        """
        Decode the base58-encoded 64-byte private key string into raw bytes.
        """
        try:
            v = v.strip()
            decoded = base58.b58decode(v)
            if len(decoded) != 64:
                raise ValueError("Invalid private key length")
            return decoded
        except Exception as e:
            raise ValueError(f"Invalid private key: {str(e)}")

def build_config() -> "Config":
    """
    Reads environment variables and builds the Config object.
    Expects:
      - SOLANA_RPC_URL       (optional)
      - PRIVATE_KEY_BASE58   (the 64-byte key in base58)
      - MIN_PROFIT_SOL       (optional)
      - TRANSACTION_FEE_SOL  (optional)
      - DRY_RUN              (optional)
      - DATABASE_URL         (optional)
    """
    rpc_url = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
    priv_key = os.getenv("PRIVATE_KEY_BASE58", "")
    dry_run_str = os.getenv("DRY_RUN", "False")
    return Config(
        rpc_url=rpc_url,
        private_key=priv_key,
        min_profit=Decimal(os.getenv("MIN_PROFIT_SOL", "0.001")),
        transaction_fee=Decimal(os.getenv("TRANSACTION_FEE_SOL", "0.00002")),
        dry_run=(dry_run_str.lower() in ["true", "1", "yes"])
    )

# ----------------- Database Models -----------------
class ArbitrageOpportunity(Base):
    __tablename__ = "arbitrage_opportunities"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    route = Column(String(255))
    expected_profit = Column(Float)
    actual_profit = Column(Float)
    status = Column(String(255))
    tx_hash = Column(String(255))
    gas_used = Column(BigInteger)
    latency = Column(Float)
    error = Column(String(255))

    __table_args__ = (
        Index('idx_status_created', 'status', 'created_at'),
    )

class WalletState(Base):
    __tablename__ = "wallet_states"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    balance = Column(Float)
    active_positions = Column(Integer)
    health_score = Column(Float)

# ----------------- Solana Manager -----------------
class SolanaManager:
    def __init__(self, config: Config):
        self.client = AsyncClient(config.rpc_url)
        self.keypair = Keypair.from_secret_key(config.private_key)
        self.pubkey = self.keypair.public_key
        logging.info(f"Initialized Solana client with RPC: {config.rpc_url}")

    async def initialize(self):
        """
        Check RPC connection by fetching version info.
        """
        try:
            logging.info("Testing RPC connection with get_version()...")
            resp = await self.client.get_version()
            logging.info(f"RPC version: {resp}")
        except Exception as e:
            logging.error(f"RPC connection failed: {str(e)}")
            raise

    async def get_balance(self) -> Decimal:
        """
        Returns SOL balance (converted from lamports).
        NOTE: On older or advanced solana-py, response can be a raw dict. Adjust as needed.
        """
        try:
            resp = await self.client.get_balance(self.pubkey, commitment=Confirmed)
            lamports = resp["result"]["value"]  # Key into the raw dict
            sol_balance = Decimal(lamports) / Decimal(1_000_000_000)
            logging.info(f"SOL Balance: {sol_balance} (lamports={lamports})")
            return sol_balance
        except Exception as e:
            logging.error(f"Balance check failed: {str(e)}")
            return Decimal(0)

# ----------------- Arbitrage Engine -----------------
class ArbitrageEngine:
    def __init__(self, config: Config):
        self.config = config
        self.solana = SolanaManager(config)

        db_url = os.getenv("DATABASE_URL", "sqlite:///arbitrage.db")
        engine = create_engine(db_url)
        Base.metadata.create_all(engine)
        self.session = sessionmaker(bind=engine)()

    async def find_arbitrage_cycles(self, token_pairs: List[Tuple[str, str]]) -> List[Dict]:
        """
        Check aggregator data for profitable spread. Return a list of potential trades.
        """
        ops = []

        # 1) Check wallet balance first
        wallet_balance = await self.solana.get_balance()
        if wallet_balance <= Decimal(0):
            logging.error("No SOL balance available.")
            return ops

        # 2) For each pair, see if aggregator shows a profitable spread
        for pair in token_pairs:
            aggregator_key = "SOL_USDC"  # just an example name
            spread_data = aggregator.get_best_spread_for_pair(aggregator_key)
            if not spread_data:
                logging.info(f"No aggregator data for {aggregator_key}")
                continue

            spread = Decimal(spread_data["spread"])
            net_profit = spread - self.config.transaction_fee
            logging.info(f"[{aggregator_key}] Spread={spread:.6f}, NetProfit={net_profit:.6f}")
            if net_profit > self.config.min_profit:
                ops.append({
                    "pair": pair,
                    "profit": net_profit,
                    "spread_info": spread_data
                })

        return sorted(ops, key=lambda x: x["profit"], reverse=True)

    async def execute_arbitrage(self, opportunity: Dict) -> bool:
        """
        Executes the actual trade (buy on the dex with lowest_ask, sell on dex with highest_bid).
        """
        start_time = time.time()
        try:
            if self.config.dry_run:
                logging.info(f"DRY RUN => {opportunity}")
                tx_hash = "DRY_RUN_HASH"
                latency = time.time() - start_time
                self._record_trade(opportunity, tx_hash, 0, latency)
                return True

            spread_info = opportunity["spread_info"]
            buy_dex = spread_info["lowest_ask_dex"]
            sell_dex = spread_info["highest_bid_dex"]
            logging.info(f"REAL TRADE => Buy on {buy_dex}, Sell on {sell_dex}")

            # Placeholder for real on-chain instructions with Raydium or other DEX.
            raise NotImplementedError("Replace with your on-chain swap/AMM instructions")

        except Exception as e:
            latency = time.time() - start_time
            self._record_error(opportunity, str(e), latency)
            return False

    def _record_trade(self, opp: Dict, tx_hash: str, gas_used: int, latency: float):
        logging.info(
            f"Trade success: {tx_hash}, profit={opp['profit']}, latency={latency:.2f}s"
        )
        arb = ArbitrageOpportunity(
            route=str(opp["pair"]),
            expected_profit=float(opp["profit"]),
            actual_profit=0.0,
            status="success",
            tx_hash=tx_hash,
            gas_used=gas_used,
            latency=latency
        )
        self.session.add(arb)
        self.session.commit()

    def _record_error(self, opp: Dict, error: str, latency: float):
        logging.error(f"Trade failed for {opp.get('pair')}, error={error}, latency={latency:.2f}s")
        arb = ArbitrageOpportunity(
            route=str(opp.get("pair")),
            expected_profit=float(opp.get("profit", 0.0)),
            actual_profit=0.0,
            status="error",
            tx_hash="",
            gas_used=0,
            latency=latency,
            error=error
        )
        self.session.add(arb)
        self.session.commit()

# ----------------- Additional Classes -----------------
class RiskManager:
    def __init__(self, config: Config):
        self.config = config
        self.max_daily_loss = Decimal("0.1")
        self.position_size = Decimal("0.01")
        self.stop_loss = Decimal("0.05")

    async def check_risk_parameters(self) -> bool:
        """
        If it passes your custom risk checks, return True.
        """
        # For example, verify daily PnL, wallet health, etc.
        return True

class PerformanceMonitor:
    def __init__(self):
        self.metrics = {
            "opportunities_checked": 0,
            "trades_executed": 0,
            "success_rate": 0.0,
            "avg_profit": 0.0,
            "total_gas_used": 0
        }
        self.success_count = 0

    def update_metrics(self, success: bool, profit: float, gas_used: int):
        self.metrics["opportunities_checked"] += 1
        if success:
            self.metrics["trades_executed"] += 1
            self.success_count += 1
        # Could compute avg profit, success rate, etc.

    def generate_report(self):
        if self.metrics["opportunities_checked"] > 0:
            self.metrics["success_rate"] = (
                self.success_count / self.metrics["opportunities_checked"]
            )
        logging.info(f"Performance metrics: {self.metrics}")

# ----------------- Main -----------------
async def main():
    # 1) Build config from environment
    config = build_config()

    # 2) Create engine + risk manager + performance monitor
    arbitrage_engine = ArbitrageEngine(config)
    await arbitrage_engine.solana.initialize()  # checks RPC
    risk_manager = RiskManager(config)
    monitor = PerformanceMonitor()

    # 3) e.g. track SOL / USDC
    token_pairs = [
        ("So11111111111111111111111111111111111111112",
         "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB")
    ]

    # 4) Start aggregator loop in background
    collector_task = asyncio.create_task(aggregator.aggregator_loop())

    try:
        while True:
            try:
                opportunities = await arbitrage_engine.find_arbitrage_cycles(token_pairs)
                for opp in opportunities:
                    if await risk_manager.check_risk_parameters():
                        success = await arbitrage_engine.execute_arbitrage(opp)
                        monitor.update_metrics(success, float(opp["profit"]), 0)

                # Sleep between checks
                await asyncio.sleep(5)
            except KeyboardInterrupt:
                logging.info("Shutting down gracefully.")
                break
            except Exception as e:
                logging.error(f"Critical error: {e}, retrying in 10s.")
                await asyncio.sleep(10)
    finally:
        collector_task.cancel()
        monitor.generate_report()
        await arbitrage_engine.solana.client.close()

if __name__ == "__main__":
    asyncio.run(main())
