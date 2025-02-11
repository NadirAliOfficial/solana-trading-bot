import os
import asyncio
import time
import base58
import json
from dataclasses import dataclass, field
from typing import List, Optional
import uuid

import aiohttp
from dotenv import load_dotenv

from solders.keypair import Keypair
from solders.pubkey import Pubkey  # type: ignore
from solanatracker import SolanaTracker  # Ensure this package is installed
from solana.rpc.types import TxOpts
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Processed

# --------------------------------------------------
# CONFIG / CONSTANTS
# --------------------------------------------------

load_dotenv()

# Environment variables
PRIVATE_KEY = os.environ.get("PRIVATE_KEY")  # Base58 private key string
SOLANA_RPC_ENDPOINT = os.environ.get("SOLANA_RPC_ENDPOINT")  # e.g., "https://rpc.solanatracker.io/public?advancedTx=true"

# Initialize keypair using solders
private_key = Keypair.from_base58_string(PRIVATE_KEY)

# Optionally, create an async client if needed elsewhere
async_client = AsyncClient(SOLANA_RPC_ENDPOINT)

# Initialize SolanaTracker with your RPC endpoint
solana_tracker = SolanaTracker(private_key, SOLANA_RPC_ENDPOINT)

# --------------------------------------------------
# ADVANCED TOKEN FILTERING FUNCTIONS
# --------------------------------------------------

TOKEN_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
TOKEN_PAIRS_URL_TEMPLATE = "https://api.dexscreener.com/token-pairs/v1/solana/{tokenAddress}"

MIN_LIQUIDITY_USD = 10000     # e.g., must have at least $10k liquidity
MIN_TX_COUNT_24H = 300        # e.g., at least 300 transactions in 24h
TOP_HOLDER_MAX_PERCENT = 30   # e.g., top holder must have less than 30% supply
REQUIRED_LIQUIDITY_LOCK = True

WEIGHT_LIQUIDITY = 0.4
WEIGHT_VOLUME = 0.3
WEIGHT_TX_COUNT = 0.2
WEIGHT_HOLDER_DISTRIB = 0.1

MIN_SCORE_THRESHOLD = 2000

def compute_token_score(volume_24h, liquidity_usd, tx_count, top_holder_pct, historical_data=None):
    base_score = (
        (WEIGHT_LIQUIDITY * liquidity_usd) +
        (WEIGHT_VOLUME    * volume_24h) +
        (WEIGHT_TX_COUNT  * tx_count) -
        (WEIGHT_HOLDER_DISTRIB * top_holder_pct)
    )
    if historical_data:
        bonus = compute_historical_bonus(historical_data)
        base_score += bonus
    return base_score

def compute_historical_bonus(historical_data):
    if len(historical_data) < 2:
        return 0
    volumes = [day["volume"] for day in historical_data]
    if volumes[-1] > volumes[0]:
        return 100
    return 0

async def fetch_solana_token_profiles():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(TOKEN_PROFILES_URL, timeout=10) as response:
                all_profiles = await response.json()
    except Exception as e:
        print(f"[ERROR] Could not fetch token profiles: {e}")
        return []
    # Filter only tokens on Solana
    solana_profiles = [p for p in all_profiles if p.get("chainId") == "solana"]
    return solana_profiles

async def fetch_pairs_for_token(token_address):
    url = TOKEN_PAIRS_URL_TEMPLATE.format(tokenAddress=token_address)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                return await resp.json()
    except Exception as e:
        print(f"[ERROR] Failed to fetch pairs for {token_address}: {e}")
        return []

async def fetch_holder_distribution(token_address):
    data = {
        "topHolders": [
            {"address": "WhaleA", "percentage": 15.0},
            {"address": "WhaleB", "percentage": 12.5}
        ],
        "totalHolders": 650
    }
    return data

async def check_liquidity_lock(token_address):
    """
    Returns whether the token's liquidity is locked.
    Replace this with your own logic or API call.
    """
    return True

async def fetch_transaction_count(token_address):
    """
    Returns the 24h transaction count for the token.
    Replace with actual implementation.
    """
    return 500

async def fetch_historical_data(token_address):
    historical = [
        {"timestamp": 1690000000, "volume": 30000, "liquidity": 9000},
        {"timestamp": 1690086400, "volume": 35000, "liquidity": 10000},
        {"timestamp": 1690172800, "volume": 40000, "liquidity": 11000},
    ]
    return historical

async def advanced_filter_solana_tokens():
    solana_profiles = await fetch_solana_token_profiles()
    print(f"Found {len(solana_profiles)} Solana token profiles.")
    valid_tokens = []
    
    for profile in solana_profiles:
        token_address = profile.get("tokenAddress", "")
        if not token_address:
            continue

        pairs = await fetch_pairs_for_token(token_address)
        if not pairs:
            continue

        best_pair = max(pairs, key=lambda p: p.get("volume", {}).get("h24", 0))
        volume_24h = best_pair.get("volume", {}).get("h24", 0)
        liquidity_usd = best_pair.get("liquidity", {}).get("usd", 0)

        if liquidity_usd < MIN_LIQUIDITY_USD:
            continue

        tx_count_24h = await fetch_transaction_count(token_address)
        if tx_count_24h < MIN_TX_COUNT_24H:
            continue

        holder_info = await fetch_holder_distribution(token_address)
        top_holders = holder_info.get("topHolders", [])
        if top_holders:
            max_holder_pct = max([h["percentage"] for h in top_holders])
            if max_holder_pct > TOP_HOLDER_MAX_PERCENT:
                continue
        else:
            max_holder_pct = 0

        if REQUIRED_LIQUIDITY_LOCK and not await check_liquidity_lock(token_address):
            continue

        historical_data = await fetch_historical_data(token_address)
        score = compute_token_score(volume_24h, liquidity_usd, tx_count_24h, max_holder_pct, historical_data)

        if score >= MIN_SCORE_THRESHOLD:
            valid_tokens.append({
                "tokenAddress": token_address,
                "score": score,
                "volume_24h": volume_24h,
                "liquidity_usd": liquidity_usd,
                "tx_count_24h": tx_count_24h,
                "top_holder_pct": max_holder_pct
            })

    valid_tokens.sort(key=lambda x: x["score"], reverse=True)
    print("\n=== ADVANCED FILTERING RESULTS ===")
    if not valid_tokens:
        print("No tokens passed the advanced filters.")
    else:
        for idx, t in enumerate(valid_tokens, start=1):
            print(f"{idx}. {t['tokenAddress']} - Score: {t['score']:.2f}, Vol: {t['volume_24h']}, Liq: {t['liquidity_usd']}, TxCount: {t['tx_count_24h']}, TopHolder: {t['top_holder_pct']}%")
    return valid_tokens

# --------------------------------------------------
# DATA CLASSES FOR TRADING
# --------------------------------------------------

@dataclass
class Milestone:
    percentage_gain: float  # e.g., 30.0 for 30%
    is_sold: bool = False
    sold_amount: float = 0.0

@dataclass
class Trade:
    token_address: str
    entry_price: float  # Price at which token was bought (USD)
    investment_amount: float  # USD amount invested
    purchase_levels: List[float]  # e.g., [5, 10, 15, 20] percentages
    stop_loss: float  # Percentage drop to trigger stop-loss
    milestones: List[Milestone] = field(default_factory=lambda: [
        Milestone(percentage_gain=30.0),
        Milestone(percentage_gain=65.0),
        Milestone(percentage_gain=100.0)
    ])
    trade_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    current_status: str = "OPEN"

    def update_status(self, new_status: str):
        self.current_status = new_status

# --------------------------------------------------
# HELPER FUNCTIONS (e.g., current price and investment amount)
# --------------------------------------------------

async def fetch_current_price(token_address: str) -> float:
    try:
        url = f"https://api.dexscreener.com/token-pairs/v1/solana/{token_address}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                data = await response.json()
                response_data = data[0]
                price = float(response_data['priceUsd'])
                return price
    except Exception as e:
        print(f"[ERROR] Could not fetch current price for {token_address}: {e}")
        return 0.0

def calculate_investment_amount(total_budget: float, percentage: float) -> float:
    return (percentage / 100) * total_budget

# --------------------------------------------------
# NEW SWAP FUNCTION USING SOLANATRACKER
# --------------------------------------------------

async def perform_swap(
    from_token: str,
    to_token: str,
    amount: float,
    slippage: int = 30,
    priority_fee: float = 0.005,
    force_legacy: bool = True
) -> bool:
    """
    Uses SolanaTracker to get swap instructions and perform a swap.
    """
    start_time = time.time()
    try:
        swap_response = await solana_tracker.get_swap_instructions(
            from_token,        # e.g., token to swap FROM
            to_token,          # e.g., token to swap TO
            amount,            # Amount to swap (in token units)
            slippage,          # Slippage (percentage)
            str(private_key.pubkey()),  # Payer public key
            priority_fee,      # Priority fee
            force_legacy,      # Force legacy transaction if needed
        )
    except Exception as e:
        print(f"[ERROR] Failed to get swap instructions: {e}")
        return False

    custom_options = {
        "send_options": {"skip_preflight": True, "max_retries": 5},
        "confirmation_retries": 50,
        "confirmation_retry_timeout": 1000,
        "last_valid_block_height_buffer": 200,
        "commitment": "processed",
        "resend_interval": 1500,
        "confirmation_check_interval": 100,
        "skip_confirmation_check": False,
    }

    try:
        send_time = time.time()
        txid = await solana_tracker.perform_swap(swap_response, options=custom_options)
        end_time = time.time()
        elapsed_time = end_time - start_time

        print("Transaction ID:", txid)
        print("Transaction URL:", f"https://solscan.io/tx/{txid}")
        print(f"Swap completed in {elapsed_time:.2f} seconds")
        print(f"Transaction finished in {end_time - send_time:.2f} seconds")
        return True
    except Exception as e:
        end_time = time.time()
        elapsed_time = end_time - start_time
        print("Swap failed:", str(e))
        print(f"Time elapsed before failure: {elapsed_time:.2f} seconds")
        return False

# --------------------------------------------------
# TRADE MANAGEMENT SYSTEM
# --------------------------------------------------

class TradeManager:
    def __init__(self):
        self.active_trades = {}
        self.lock = asyncio.Lock()

    async def add_trade(self, trade: Trade):
        async with self.lock:
            self.active_trades[trade.trade_id] = trade
            print(f"[INFO] Trade {trade.trade_id} added for token {trade.token_address}.")

    async def remove_trade(self, trade_id: str):
        async with self.lock:
            if trade_id in self.active_trades:
                del self.active_trades[trade_id]
                print(f"[INFO] Trade {trade_id} removed.")

    async def monitor_trades(self):
        while True:
            async with self.lock:
                for trade in list(self.active_trades.values()):
                    current_price = await fetch_current_price(trade.token_address)
                    await self.evaluate_trade(trade, current_price)
            await asyncio.sleep(60)

    async def evaluate_trade(self, trade: Trade, current_price: float):
        if trade.current_status != "OPEN":
            return

        pct_change = ((current_price - trade.entry_price) / trade.entry_price) * 100

        if pct_change <= -trade.stop_loss:
            print(f"[ALERT] Trade {trade.trade_id} hit stop-loss. Liquidating position.")
            await self.liquidate_trade(trade, reason="STOP_LOSS")
            return

        for milestone in trade.milestones:
            if not milestone.is_sold and pct_change >= milestone.percentage_gain:
                sell_amount = trade.investment_amount * (milestone.percentage_gain / 100)
                await self.execute_sell(trade, sell_amount, milestone)
                break

    async def execute_sell(self, trade: Trade, amount: float, milestone: Milestone):
        # Here we swap the token to SOL (example)
        success = await perform_swap(
            from_token=trade.token_address,
            to_token="So11111111111111111111111111111111111111112",  # Example output token (SOL)
            amount=amount,
            slippage=30
        )
        if success:
            milestone.is_sold = True
            milestone.sold_amount = amount
            print(f"[INFO] Sold ${amount} of token {trade.token_address} at milestone {milestone.percentage_gain}%.")
            if all(m.is_sold for m in trade.milestones):
                trade.update_status("COMPLETED")
                await self.remove_trade(trade.trade_id)
                print(f"[INFO] Trade {trade.trade_id} completed.")

    async def liquidate_trade(self, trade: Trade, reason: str):
        success = await perform_swap(
            from_token=trade.token_address,
            to_token="So11111111111111111111111111111111111111112",
            amount=trade.investment_amount,
            slippage=30
        )
        if success:
            trade.update_status("STOPPED")
            await self.remove_trade(trade.trade_id)
            print(f"[INFO] Trade {trade.trade_id} liquidated due to {reason}.")

# --------------------------------------------------
# MAIN EXECUTION FLOW
# --------------------------------------------------

async def main():
    trade_manager = TradeManager()

    # First, get the list of valid tokens from your advanced filter.
    valid_tokens = await advanced_filter_solana_tokens()

    if not valid_tokens:
        print("[INFO] No tokens met the criteria. Exiting.")
        return

    # Start monitoring trades in the background.
    asyncio.create_task(trade_manager.monitor_trades())

    # Define your total budget (in USD) and investment percentages.
    total_budget = 10  # USD
    investment_percentages = [5, 10, 15, 20]

    # For each token that passed the advanced filter, set up trades.
    for token in valid_tokens:
        token_address = token["tokenAddress"]
        for pct in investment_percentages:
            investment_amount = calculate_investment_amount(total_budget, pct)
            if investment_amount > 0:
                current_price = await fetch_current_price(token_address)
                if current_price <= 0:
                    continue
                trade = Trade(
                    token_address=token_address,
                    entry_price=current_price,
                    investment_amount=investment_amount,
                    purchase_levels=investment_percentages,
                    stop_loss=10  # Example: 10% stop-loss
                )
                await trade_manager.add_trade(trade)

    # Keep the script running.
    while True:
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
