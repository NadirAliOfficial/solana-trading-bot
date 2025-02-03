import os
import asyncio
import base58
import base64
import json
from dataclasses import dataclass, field
from typing import List, Optional
import uuid
from solders import message
from solders.pubkey import Pubkey #type:ignore
from solders.keypair import Keypair #type:ignore
from solders.transaction import VersionedTransaction #type:ignore
from solana.rpc.types import TxOpts
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Processed
from jupiter_python_sdk.jupiter import Jupiter, Jupiter_DCA #type:ignore
from dotenv import load_dotenv
import aiohttp

# --------------------------------------------------
# 1) CONFIG / CONSTANTS
# --------------------------------------------------

# Environment Variables
""" PRIVATE_KEY = os.getenv("PRIVATE_KEY")  # Replace with your private key env variable name
SOLANA_RPC_ENDPOINT = os.getenv("SOLANA_RPC_ENDPOINT")  # e.g., "https://api.mainnet-beta.solana.com" """

load_dotenv()

PRIVATE_KEY = os.environ.get("PRIVATE_KEY")
SOLANA_RPC_ENDPOINT = os.environ.get("SOLANA_RPC_ENDPOINT")

# Initialize Keypair
private_key = Keypair.from_bytes(base58.b58decode(PRIVATE_KEY))

# Initialize Solana Async Client
async_client = AsyncClient(SOLANA_RPC_ENDPOINT)

# Initialize Jupiter SDK
""" jupiter = Jupiter(
    async_client=async_client,
    keypair=private_key,
    quote_api_url="https://quote-api.jup.ag/v6/quote",
    swap_api_url="https://quote-api.jup.ag/v6/swap",
    open_order_api_url="https://jup.ag/api/limit/v1/createOrder",
    cancel_orders_api_url="https://jup.ag/api/limit/v1/cancelOrders",
    query_open_orders_api_url="https://jup.ag/api/limit/v1/openOrders?wallet=",
    query_order_history_api_url="https://jup.ag/api/limit/v1/orderHistory",
    query_trade_history_api_url="https://jup.ag/api/limit/v1/tradeHistory"
) """

jupiter = Jupiter(
    async_client=async_client,
    keypair=private_key,
    quote_api_url="https://api.jup.ag/swap/v1/quote",
    swap_api_url="https://api.jup.ag/swap/v1/swap",
    open_order_api_url="https://jup.ag/api/limit/v1/createOrder",
    cancel_orders_api_url="https://jup.ag/api/limit/v1/cancelOrders",
    query_open_orders_api_url="https://jup.ag/api/limit/v1/openOrders?wallet=",
    query_order_history_api_url="https://jup.ag/api/limit/v1/orderHistory",
    query_trade_history_api_url="https://jup.ag/api/limit/v1/tradeHistory"
)

# --------------------------------------------------
# 2) DATA CLASSES
# --------------------------------------------------

@dataclass
class Milestone:
    percentage_gain: float  # e.g., 30.0 for 30%
    is_sold: bool = False
    sold_amount: float = 0.0  # USD amount sold at this milestone

@dataclass
class Trade:
    token_address: str
    entry_price: float  # USD price at which the token was bought
    investment_amount: float  # USD amount invested
    purchase_levels: List[float]  # e.g., [5, 10, 15, 20] percentages
    stop_loss: float  # Percentage drop from entry_price to trigger stop-loss
    milestones: List[Milestone] = field(default_factory=lambda: [
        Milestone(percentage_gain=30.0),
        Milestone(percentage_gain=65.0),
        Milestone(percentage_gain=100.0)
    ])
    trade_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    current_status: str = "OPEN"  # Could be OPEN, STOPPED, COMPLETED

    def update_status(self, new_status: str):
        self.current_status = new_status

# --------------------------------------------------
# 3) PLACEHOLDER FUNCTIONS FOR ADDITIONAL DATA
# --------------------------------------------------

async def fetch_holder_distribution(token_address):
    """
    Returns data about token holder distribution.
    Implement this based on your data source.
    """
    # Placeholder data for demonstration:
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
    Returns True/False indicating whether the token's liquidity is locked.
    Implement this based on your data source.
    """
    # Placeholder always returns True for demonstration
    return True

async def fetch_transaction_count(token_address):
    """
    Returns the 24h transaction count for the token.
    Implement this based on your data source.
    """
    # Placeholder returns a static number for demonstration
    return 500

async def fetch_historical_data(token_address):
    """
    Returns historical data for the token.
    Implement this based on your data source.
    """
    # Placeholder historical data
    historical = [
        {"timestamp": 1690000000, "volume": 30000, "liquidity": 9000},
        {"timestamp": 1690086400, "volume": 35000, "liquidity": 10000},
        {"timestamp": 1690172800, "volume": 40000, "liquidity": 11000},
    ]
    return historical

# --------------------------------------------------
# 4) HELPER FUNCTIONS FOR DEXSCREENER
# --------------------------------------------------

TOKEN_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
TOKEN_PAIRS_URL_TEMPLATE = "https://api.dexscreener.com/token-pairs/v1/solana/{tokenAddress}"

MIN_LIQUIDITY_USD = 10000     # must have at least $10k liquidity
MIN_TX_COUNT_24H = 300        # must have at least 300 transactions in 24h
TOP_HOLDER_MAX_PERCENT = 30   # top holder must have < 30% supply
REQUIRED_LIQUIDITY_LOCK = True

WEIGHT_LIQUIDITY = 0.4
WEIGHT_VOLUME = 0.3
WEIGHT_TX_COUNT = 0.2
WEIGHT_HOLDER_DISTRIB = 0.1  # or negative weighting if top holder % is large

MIN_SCORE_THRESHOLD = 2000

def compute_token_score(volume_24h, liquidity_usd, tx_count, top_holder_pct, historical_data=None):
    base_score = (
        (WEIGHT_LIQUIDITY * liquidity_usd) +
        (WEIGHT_VOLUME    * volume_24h) +
        (WEIGHT_TX_COUNT  * tx_count) -
        (WEIGHT_HOLDER_DISTRIB * top_holder_pct)
    )

    # Optionally incorporate historical growth
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

    # Filter to only Solana
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
            continue  # fails liquidity
        
        tx_count_24h = await fetch_transaction_count(token_address)
        if tx_count_24h < MIN_TX_COUNT_24H:
            continue  # fails tx count
        
        holder_info = await fetch_holder_distribution(token_address)
        top_holders = holder_info.get("topHolders", [])
        if top_holders:
            max_holder_pct = max([h["percentage"] for h in top_holders])
            if max_holder_pct > TOP_HOLDER_MAX_PERCENT:
                continue  # fails top-holder distribution
        else:
            max_holder_pct = 0
        
        if REQUIRED_LIQUIDITY_LOCK and not await check_liquidity_lock(token_address):
            continue  # fails liquidity lock
        
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
    
    # Sort final tokens by descending score
    valid_tokens.sort(key=lambda x: x["score"], reverse=True)

    # Print or return the results
    print("\n=== ADVANCED FILTERING RESULTS ===")
    if not valid_tokens:
        print("No tokens passed the advanced filters.")
    else:
        for idx, t in enumerate(valid_tokens, start=1):
            print(f"{idx}. {t['tokenAddress']} - Score: {t['score']:.2f}, "
                  f"Vol: {t['volume_24h']}, Liq: {t['liquidity_usd']}, "
                  f"TxCount: {t['tx_count_24h']}, TopHolder: {t['top_holder_pct']}%")

    return valid_tokens

# --------------------------------------------------
# 6) EXECUTION AND INTEGRATION WITH JUPITER SDK
# --------------------------------------------------

async def execute_swap(jupiter_instance: Jupiter, input_mint: str, output_mint: str, amount: int, slippage_bps: int = 50) -> bool:
    """
    Executes a token swap using Jupiter SDK.
    Returns True if successful, else False.
    """
    try:
        transaction_data = await jupiter_instance.swap(
            input_mint=input_mint,
            output_mint=output_mint,
            amount=amount,
            slippage_bps=slippage_bps
        )
        raw_transaction = VersionedTransaction.from_bytes(base64.b64decode(transaction_data))
        signature = private_key.sign_message(message.to_bytes_versioned(raw_transaction.message))
        signed_txn = VersionedTransaction.populate(raw_transaction.message, [signature])
        opts = TxOpts(skip_preflight=False, preflight_commitment=Processed)
        result = await async_client.send_raw_transaction(txn=bytes(signed_txn), opts=opts)
        transaction_id = json.loads(result.to_json())['result']
        print(f"[SUCCESS] Swap Transaction sent: https://explorer.solana.com/tx/{transaction_id}")
        return True
    except Exception as e:
        print(f"[ERROR] Swap execution failed: {e}")
        return False

async def open_limit_order(jupiter_instance: Jupiter, input_mint: str, output_mint: str, in_amount: int, out_amount: int) -> bool:
    """
    Opens a limit order using Jupiter SDK.
    Returns True if successful, else False.
    """
    try:
        transaction_data = await jupiter_instance.open_order(
            input_mint=input_mint,
            output_mint=output_mint,
            in_amount=in_amount,
            out_amount=out_amount
        )
        raw_transaction = VersionedTransaction.from_bytes(base64.b64decode(transaction_data['transaction_data']))
        signature = private_key.sign_message(message.to_bytes_versioned(raw_transaction.message))
        signed_txn = VersionedTransaction.populate(raw_transaction.message, [signature, transaction_data['signature2']])
        opts = TxOpts(skip_preflight=False, preflight_commitment=Processed)
        result = await async_client.send_raw_transaction(txn=bytes(signed_txn), opts=opts)
        transaction_id = json.loads(result.to_json())['result']
        print(f"[SUCCESS] Limit Order Transaction sent: https://explorer.solana.com/tx/{transaction_id}")
        return True
    except Exception as e:
        print(f"[ERROR] Open limit order failed: {e}")
        return False

async def create_dca_account(jupiter_instance: Jupiter, input_mint: str, output_mint: str, total_in_amount: int,
                             in_amount_per_cycle: int, cycle_frequency: int, min_out_amount_per_cycle: int = 0,
                             max_out_amount_per_cycle: int = 0, start: int = 0) -> Optional[str]:
    """
    Creates a DCA account using Jupiter SDK.
    Returns the DCA account Pubkey if successful, else None.
    """
    try:
        dca_account, txn_hash = await jupiter_instance.dca.create_dca(
            input_mint=Pubkey.from_string(input_mint),
            output_mint=Pubkey.from_string(output_mint),
            total_in_amount=total_in_amount,
            in_amount_per_cycle=in_amount_per_cycle,
            cycle_frequency=cycle_frequency,
            min_out_amount_per_cycle=min_out_amount_per_cycle,
            max_out_amount_per_cycle=max_out_amount_per_cycle,
            start_at=start
        )
        print(f"[SUCCESS] DCA Account created: {dca_account}, Transaction Hash: {txn_hash}")
        return str(dca_account)
    except Exception as e:
        print(f"[ERROR] Create DCA account failed: {e}")
        return None

async def close_dca_account(jupiter_instance: Jupiter, dca_pubkey: str) -> bool:
    """
    Closes a DCA account using Jupiter SDK.
    Returns True if successful, else False.
    """
    try:
        txn_hash = await jupiter_instance.dca.close_dca(
            dca_pubkey=Pubkey.from_string(dca_pubkey)
        )
        print(f"[SUCCESS] DCA Account closed, Transaction Hash: {txn_hash}")
        return True
    except Exception as e:
        print(f"[ERROR] Close DCA account failed: {e}")
        return False

# --------------------------------------------------
# 7) TRADING MANAGEMENT SYSTEM
# --------------------------------------------------

class TradeManager:
    def __init__(self, jupiter_instance: Jupiter):
        self.active_trades = {}
        self.lock = asyncio.Lock()
        self.jupiter = jupiter_instance

    async def add_trade(self, trade: Trade):
        async with self.lock:
            self.active_trades[trade.trade_id] = trade
            print(f"[INFO] Trade {trade.trade_id} added.")

    async def remove_trade(self, trade_id: str):
        async with self.lock:
            if trade_id in self.active_trades:
                del self.active_trades[trade_id]
                print(f"[INFO] Trade {trade_id} removed.")

    async def monitor_trades(self):
        """
        Periodically monitor active trades for price changes to handle milestones and stop-losses.
        """
        while True:
            async with self.lock:
                for trade_id, trade in list(self.active_trades.items()):
                    current_price = await fetch_current_price(trade.token_address)
                    await self.evaluate_trade(trade, current_price)
            await asyncio.sleep(60)  # Wait for 1 minute before next check

    async def evaluate_trade(self, trade: Trade, current_price: float):
        """
        Evaluate the trade based on current price to handle milestones and stop-loss.
        """
        if trade.current_status != "OPEN":
            return

        # Calculate percentage gain/loss
        pct_change = ((current_price - trade.entry_price) / trade.entry_price) * 100

        # Check for stop-loss
        if pct_change <= -trade.stop_loss:
            print(f"[ALERT] Trade {trade.trade_id} hit stop-loss. Liquidating position.")
            await self.liquidate_trade(trade, reason="STOP_LOSS")
            return

        # Check for milestones
        for milestone in trade.milestones:
            if not milestone.is_sold and pct_change >= milestone.percentage_gain:
                sell_amount = trade.investment_amount * (milestone.percentage_gain / 100)
                await self.execute_sell(trade, sell_amount, milestone)
                break  # Sell one milestone at a time

    async def execute_sell(self, trade: Trade, amount: float, milestone: Milestone):
        """
        Execute sell order via Jupiter SDK.
        """
        success = await self.jupiter.swap(
            input_mint=trade.token_address,
            output_mint="So11111111111111111111111111111111111111112",  # Example output mint (SOL)
            amount=int(amount * 1_000_000),  # Assuming amount is in USD and needs to be in smallest units
            slippage_bps=50  # 0.5% slippage; adjust as needed
        )
        if success:
            milestone.is_sold = True
            milestone.sold_amount = amount
            print(f"[INFO] Sold ${amount} of {trade.token_address} at milestone {milestone.percentage_gain}%.")
            
            # Check if all milestones are achieved
            if all(m.is_sold for m in trade.milestones):
                trade.update_status("COMPLETED")
                await self.remove_trade(trade.trade_id)
                print(f"[INFO] Trade {trade.trade_id} completed.")

    async def liquidate_trade(self, trade: Trade, reason: str):
        """
        Liquidate the entire trade position.
        """
        amount = trade.investment_amount  # Liquidate entire investment
        success = await self.jupiter.swap(
            input_mint=trade.token_address,
            output_mint="So11111111111111111111111111111111111111112",  # Example output mint (SOL)
            amount=int(amount * 1_000_000),  # Assuming amount is in USD and needs to be in smallest units
            slippage_bps=50  # 0.5% slippage; adjust as needed
        )
        if success:
            trade.update_status("STOPPED")
            await self.remove_trade(trade.trade_id)
            print(f"[INFO] Trade {trade.trade_id} liquidated due to {reason}.")

# --------------------------------------------------
# 8) PROFIT-TAKING STRATEGY WITH DCA
# --------------------------------------------------

async def setup_dca(jupiter_instance: Jupiter, trade: Trade):
    """
    Sets up DCA for a given trade at predefined milestones.
    """
    for milestone in trade.milestones:
        if not milestone.is_sold:
            dca_pubkey = await create_dca_account(
                jupiter_instance=jupiter_instance,
                input_mint=trade.token_address,
                output_mint="So11111111111111111111111111111111111111112",  # Example output mint (SOL)
                total_in_amount=int(trade.investment_amount * (milestone.percentage_gain / 100) * 1_000_000),
                in_amount_per_cycle=int(trade.investment_amount * (milestone.percentage_gain / 100) / 10 * 1_000_000),  # Example: 10 cycles
                cycle_frequency=60,  # Every 60 seconds; adjust as needed
                min_out_amount_per_cycle=0,
                max_out_amount_per_cycle=0,
                start=0
            )
            if dca_pubkey:
                print(f"[INFO] DCA set up for milestone {milestone.percentage_gain}% at {dca_pubkey}")

# --------------------------------------------------
# 9) MAIN EXECUTION FLOW
# --------------------------------------------------

async def main():
    # Initialize Trade Manager
    trade_manager = TradeManager(jupiter)
    
    # Start Trade Monitoring
    asyncio.create_task(trade_manager.monitor_trades())
    
    # Filter Solana Tokens
    final_list = await advanced_filter_solana_tokens()
    
    # Define Budget and Investment Percentages
    total_budget = 10  # USD
    investment_percentages = [5, 10, 15, 20]
    
    # Set Up Trades
    for token in final_list:
        for pct in investment_percentages:
            investment_amount = calculate_investment_amount(total_budget, pct)
            if investment_amount > 0:
                current_price = await fetch_current_price(token['tokenAddress'])
                trade = Trade(
                    token_address=token['tokenAddress'],
                    entry_price=current_price,
                    investment_amount=investment_amount,
                    purchase_levels=investment_percentages,
                    stop_loss=10  # Example: 10% stop-loss
                )
                await trade_manager.add_trade(trade)
                await setup_dca(jupiter, trade)
    
    # Keep the script running
    while True:
        await asyncio.sleep(60)

# --------------------------------------------------
# 10) HELPER FUNCTIONS
# --------------------------------------------------

async def fetch_current_price(token_address: str) -> float:
    """
    Fetch the current price of the token in USD.
    Implement this function based on your data source.
    """
    try:
        url = f"https://api.dexscreener.com/token-pairs/v1/solana/{token_address}"
        print(f"GET Url {url}")
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                print(f"Getting data")
                data = await response.json()
                print(f"Getting price parameter")
                response = data[0]

                # DEBUG
                # priceUSD = response['priceUsd']
                # print(f"priceUSD is : {priceUSD}")
                # for i, row in enumerate(data):
                    # for j, value in enumerate(row):
                        # print(f"Index [{i}, {j}]: {value}")

                price = float(response['priceUsd'])  # Adjust based on actual response structur
                return price
    except Exception as e:
        print(f"[ERROR] Could not fetch current price for {token_address}: {e}")
        return 0.0

def calculate_investment_amount(total_budget: float, percentage: float) -> float:
    return (percentage / 100) * total_budget

# --------------------------------------------------
# 11) SECURITY CONSIDERATIONS
# --------------------------------------------------

# Ensure all environment variables are securely stored and accessed.
# Never expose your PRIVATE_KEY or other sensitive information.

# --------------------------------------------------
# 12) RUNNING THE SCRIPT
# --------------------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
