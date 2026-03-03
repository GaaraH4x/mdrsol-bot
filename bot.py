""" import os
import json
import asyncio
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from decimal import Decimal
import io
from collections import defaultdict
from aiohttp import web

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ChatMemberHandler
)
from telegram.constants import ParseMode, ChatType
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.transaction import Transaction
import aiohttp
import base58
from PIL import Image, ImageDraw, ImageFont
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from io import BytesIO
import numpy as np

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
RPC_URL = os.getenv('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY')
JUPITER_API = 'https://quote-api.jup.ag/v6'
DATABASE_URL = os.getenv('DATABASE_URL')
REDIS_URL = os.getenv('REDIS_URL')
COMMUNITY_LINK = os.getenv('COMMUNITY_LINK', 'https://t.me/your_community')
GROUP_CHAT_LINK = os.getenv('GROUP_CHAT_LINK', 'https://t.me/your_group')
X_HANDLE_LINK = os.getenv('X_HANDLE_LINK', 'https://x.com/your_handle')
BIRDEYE_API_KEY = os.getenv('BIRDEYE_API_KEY', '')
FEE_WALLET_ADDRESS = os.getenv('FEE_WALLET_ADDRESS', 'YOUR_SOLANA_WALLET_HERE')

# Revenue Configuration
FEE_TIERS = {
    'standard': {'fee': 0.50, 'name': '🐢 Standard', 'speed': '5-10 sec'},
    'fast': {'fee': 1.50, 'name': '⚡ Fast', 'speed': '2-3 sec'},
    'turbo': {'fee': 3.00, 'name': '🚀 Turbo', 'speed': '1 sec'},
    'ultra': {'fee': 5.00, 'name': '💎 Ultra', 'speed': 'Instant + MEV'},
    'custom': {'fee': 0.0, 'name': '🎯 Custom', 'speed': 'User defined'}
}

SUBSCRIPTION_TIERS = {
    'free': {
        'price': 0,
        'name': 'Free',
        'trades_per_day': 10,
        'trade_fee_discount': 0,
        'features': ['Basic trading', 'Portfolio tracking', 'Price alerts']
    },
    'pro': {
        'price': 14.99,
        'name': '⭐ Pro',
        'trades_per_day': -1,  # unlimited
        'trade_fee_discount': 0.5,  # 50% off
        'features': ['Unlimited trades', '50% fee discount', 'Copy trading', 'Whale tracking', 'Priority support']
    },
    'vip': {
        'price': 49.99,
        'name': '💎 VIP',
        'trades_per_day': -1,
        'trade_fee_discount': 1.0,  # 100% off = free
        'features': ['Zero fees', 'Instant execution', 'Dedicated support', 'Early features', 'Custom strategies']
    }
}

COPY_TRADING_COMMISSION = 0.20  # 20% of profits

# Supported languages
LANGUAGES = {
    'en': 'English',
    'es': 'Español',
    'zh': '中文',
    'ru': 'Русский',
    'fr': 'Français'
}

# Translation dictionary (simplified)
TRANSLATIONS = {
    'en': {
        'welcome': '🤖 *Welcome to Madara Bot!*',
        'features': 'Your AI-Powered Trading Companion',
    },
    'es': {
        'welcome': '🤖 *¡Bienvenido al Bot de Trading de Solana!*',
        'features': 'Tu Compañero de Trading con IA',
    }
}

class Database:
    # Enhanced database operations with PostgreSQL
    def __init__(self):
        self.pool = None
        
    async def connect(self):
        #Initialize database connection pool
        if not DATABASE_URL:
            logger.warning("DATABASE_URL not set. Using in-memory storage.")
            return
        try:
            import asyncpg
            self.pool = await asyncpg.create_pool(DATABASE_URL)
            await self.create_tables()
        except Exception as e:
            logger.error(f"Database connection error: {e}")
    
    async def create_tables(self):
        #Create necessary database tables
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            # Users table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    wallet_address TEXT,
                    encrypted_key TEXT,
                    language TEXT DEFAULT 'en',
                    subscription_tier TEXT DEFAULT 'free',
                    subscription_expires TIMESTAMP,
                    referrer_id BIGINT,
                    total_volume DECIMAL DEFAULT 0,
                    total_fees_paid DECIMAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            # Transactions table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    tx_id TEXT PRIMARY KEY,
                    user_id BIGINT,
                    type TEXT,
                    token_in TEXT,
                    token_out TEXT,
                    amount_in DECIMAL,
                    amount_out DECIMAL,
                    price DECIMAL,
                    fee_paid DECIMAL,
                    fee_tier TEXT,
                    timestamp TIMESTAMP DEFAULT NOW(),
                    status TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            # Revenue tracking
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS revenue (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    type TEXT,
                    amount DECIMAL,
                    description TEXT,
                    timestamp TIMESTAMP DEFAULT NOW()
                )
            ''')
            # Subscriptions
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    tier TEXT,
                    amount DECIMAL,
                    payment_method TEXT,
                    started_at TIMESTAMP DEFAULT NOW(),
                    expires_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            # Portfolio table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS portfolio (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    token_mint TEXT,
                    amount DECIMAL,
                    avg_buy_price DECIMAL,
                    last_updated TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, token_mint)
                )
            ''')
    
    async def save_revenue(self, user_id: int, revenue_type: str, amount: float, description: str):
        #Track revenue 
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO revenue (user_id, type, amount, description)
                VALUES ($1, $2, $3, $4)
            ''', user_id, revenue_type, amount, description)
    
    async def get_total_revenue(self) -> dict:
        #Get total revenue statistics 
        if not self.pool:
            return {}
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT 
                    SUM(amount) as total,
                    SUM(CASE WHEN type = 'trade_fee' THEN amount ELSE 0 END) as trade_fees,
                    SUM(CASE WHEN type = 'subscription' THEN amount ELSE 0 END) as subscriptions,
                    SUM(CASE WHEN type = 'copy_trading' THEN amount ELSE 0 END) as copy_trading
                FROM revenue
            ''')
            return dict(row) if row else {}
    
    async def update_user_subscription(self, user_id: int, tier: str, duration_days: int = 30):
        #Update user subscription 
        if not self.pool:
            return
        expires = datetime.now() + timedelta(days=duration_days)
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE users 
                SET subscription_tier = $1, subscription_expires = $2
                WHERE user_id = $3
            ''', tier, expires, user_id)
    
    async def get_user_subscription(self, user_id: int) -> str:
        #Get user's subscription tier 
        if not self.pool:
            return 'free'
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT subscription_tier, subscription_expires 
                FROM users WHERE user_id = $1
            ''', user_id)
            if not row:
                return 'free'
            if row['subscription_expires'] and row['subscription_expires'] < datetime.now():
                # Subscription expired
                await self.update_user_subscription(user_id, 'free')
                return 'free'
            return row['subscription_tier'] or 'free'
    
    async def save_transaction(self, tx_data: dict):
        #Save transaction with fee tracking 
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO transactions (tx_id, user_id, type, token_in, token_out, 
                                        amount_in, amount_out, price, fee_paid, fee_tier, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ''', tx_data['tx_id'], tx_data['user_id'], tx_data['type'], 
                tx_data['token_in'], tx_data['token_out'], tx_data['amount_in'],
                tx_data['amount_out'], tx_data['price'], tx_data.get('fee_paid', 0),
                tx_data.get('fee_tier', 'standard'), tx_data['status'])
    
    async def get_portfolio(self, user_id: int) -> List[dict]:
        #Get user's portfolio 
        if not self.pool:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('SELECT * FROM portfolio WHERE user_id = $1', user_id)
            return [dict(row) for row in rows]

class RedisCache:
    #Redis caching for improved performance 
    def __init__(self):
        self.redis = None
        
    async def connect(self):
        #Connect to Redis 
        if not REDIS_URL:
            logger.warning("REDIS_URL not set. Caching disabled.")
            return
        try:
            import aioredis
            self.redis = await aioredis.create_redis_pool(REDIS_URL)
        except Exception as e:
            logger.error(f"Redis connection error: {e}")

class ChartGenerator:
    #Generate beautiful portfolio and P&L charts 
    
    @staticmethod
    def create_pnl_chart(portfolio_data: List[dict], user_stats: dict) -> BytesIO:
        #Create stunning P&L chart 
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), 
                                        facecolor='#0a0e27', gridspec_kw={'height_ratios': [2, 1]})
        
        bg_color = '#0a0e27'
        text_color = '#e0e6ed'
        profit_color = '#00ff88'
        loss_color = '#ff4757'
        
        # Main P&L Chart
        ax1.set_facecolor(bg_color)
        total_pnl = user_stats.get('total_pnl', 0)
        total_value = user_stats.get('total_value', 0)
        
        if portfolio_data:
            tokens = [p['token_symbol'] for p in portfolio_data]
            values = [p['current_value'] for p in portfolio_data]
            colors = plt.cm.viridis(np.linspace(0.3, 0.9, len(tokens)))
            
            wedges, texts, autotexts = ax1.pie(values, labels=tokens, autopct='%1.1f%%',
                                                colors=colors, startangle=90,
                                                textprops={'color': text_color, 'fontsize': 11},
                                                wedgeprops={'linewidth': 2, 'edgecolor': bg_color})
            
            for autotext in autotexts:
                autotext.set_color('white')
                autotext.set_fontweight('bold')
        
        ax1.set_title('Portfolio Composition', color=text_color, fontsize=16, fontweight='bold', pad=20)
        
        # P&L Summary
        ax2.set_facecolor(bg_color)
        ax2.axis('off')
        
        info_text = f#
        💰 Total Portfolio Value: ${total_value:,.2f}
        
        {'📈' if total_pnl >= 0 else '📉'} Total P&L: ${total_pnl:,.2f}
        
        🏆 Best Performer: {user_stats.get('best_token', 'N/A')}
        📊 Total Trades: {user_stats.get('total_trades', 0)}
         
        
        ax2.text(0.5, 0.5, info_text, transform=ax2.transAxes,
                fontsize=12, color=text_color, ha='center', va='center',
                bbox=dict(boxstyle='round,pad=1', facecolor='#1e2742', 
                         edgecolor=profit_color if total_pnl >= 0 else loss_color, 
                         linewidth=3, alpha=0.8),
                family='monospace', fontweight='bold')
        
        plt.tight_layout()
        
        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=150, facecolor=bg_color, bbox_inches='tight')
        buffer.seek(0)
        plt.close()
        
        return buffer

class UserWallet:
    #Wallet management with encryption 
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.keypair: Optional[Keypair] = None
        self.pubkey: Optional[Pubkey] = None
    
    def create_wallet(self) -> str:
        #Create new Solana wallet 
        self.keypair = Keypair()
        self.pubkey = self.keypair.pubkey()
        return str(self.pubkey)
    
    def import_wallet(self, private_key: str) -> str:
        #Import wallet from private key 
        try:
            decoded = base58.b58decode(private_key)
            self.keypair = Keypair.from_bytes(decoded)
            self.pubkey = self.keypair.pubkey()
            return str(self.pubkey)
        except Exception as e:
            raise ValueError(f"Invalid private key: {str(e)}")
    
    def export_private_key(self) -> str:
        #Export wallet private key 
        if not self.keypair:
            raise ValueError("No wallet loaded")
        return base58.b58encode(bytes(self.keypair)).decode('utf-8')

class TradingEngine:
    #Enhanced trading engine with fee collection 
    def __init__(self, database: Database):
        self.client = AsyncClient(RPC_URL)
        self.database = database
        self.active_orders: Dict[int, List] = defaultdict(list)
        self.price_alerts: Dict[int, List] = defaultdict(list)
    
    async def collect_fee(self, user_wallet: UserWallet, fee_amount: float, fee_tier: str) -> bool:
        #Collect trading fee and send to fee wallet 
        try:
            # In production, you'd send SOL from user to your fee wallet
            # This is a simplified version
            logger.info(f"Fee collected: ${fee_amount} ({fee_tier}) from user {user_wallet.user_id}")
            
            # Track revenue
            await self.database.save_revenue(
                user_wallet.user_id,
                'trade_fee',
                fee_amount,
                f'Trading fee - {fee_tier}'
            )
            
            return True
        except Exception as e:
            logger.error(f"Fee collection error: {e}")
            return False
    
    async def get_token_price(self, token_mint: str) -> Optional[float]:
        #Get current token price 
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{JUPITER_API}/quote"
                params = {
                    'inputMint': 'So11111111111111111111111111111111111111112',
                    'outputMint': token_mint,
                    'amount': '1000000000',
                    'slippageBps': '50'
                }
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        out_amount = int(data.get('outAmount', 0))
                        return out_amount / 1e9 if out_amount else None
        except Exception as e:
            logger.error(f"Error getting token price: {e}")
        return None
    
    async def execute_swap(self, wallet: UserWallet, input_mint: str, 
                          output_mint: str, amount: float, fee_tier: str = 'standard') -> dict:
        #Execute swap with fee collection 
        try:
            # Get fee configuration
            fee_config = FEE_TIERS.get(fee_tier, FEE_TIERS['standard'])
            fee_amount = fee_config['fee']
            
            # Collect fee first
            fee_collected = await self.collect_fee(wallet, fee_amount, fee_tier)
            if not fee_collected:
                return {'success': False, 'error': 'Fee collection failed'}
            
            # Execute swap via Jupiter
            async with aiohttp.ClientSession() as session:
                quote_url = f"{JUPITER_API}/quote"
                quote_params = {
                    'inputMint': input_mint,
                    'outputMint': output_mint,
                    'amount': str(int(amount * 1e9)),
                    'slippageBps': '50'
                }
                
                async with session.get(quote_url, params=quote_params) as response:
                    quote = await response.json()
                
                swap_url = f"{JUPITER_API}/swap"
                swap_payload = {
                    'quoteResponse': quote,
                    'userPublicKey': str(wallet.pubkey),
                    'wrapAndUnwrapSol': True
                }
                
                async with session.post(swap_url, json=swap_payload) as response:
                    swap_data = await response.json()
                
                return {
                    'success': True,
                    'quote': quote,
                    'transaction': swap_data.get('swapTransaction'),
                    'fee_paid': fee_amount,
                    'fee_tier': fee_tier
                }
        except Exception as e:
            logger.error(f"Swap error: {e}")
            return {'success': False, 'error': str(e)}
    
    async def get_balance(self, wallet: UserWallet) -> float:
        #Get SOL balance 
        try:
            balance = await self.client.get_balance(wallet.pubkey, commitment=Confirmed)
            return balance.value / 1e9
        except Exception as e:
            logger.error(f"Balance error: {e}")
            return 0.0

class SolanaBot:
    #Main bot class with all revenue features 
    def __init__(self):
        self.database = Database()
        self.trading_engine = TradingEngine(self.database)
        self.user_wallets: Dict[int, UserWallet] = {}
        self.user_settings: Dict[int, dict] = {}
        self.cache = RedisCache()
        self.chart_generator = ChartGenerator()
        self.referral_system: Dict[int, List[int]] = defaultdict(list)
    
    async def initialize(self):
        #Initialize bot components 
        await self.database.connect()
        await self.cache.connect()
        logger.info("Bot initialized successfully")
    
    def get_user_wallet(self, user_id: int) -> UserWallet:
        #Get or create user wallet 
        if user_id not in self.user_wallets:
            self.user_wallets[user_id] = UserWallet(user_id)
        return self.user_wallets[user_id]
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        #Start command with revenue features highlighted 
        user = update.effective_user
        
        # Check for referral
        if context.args and context.args[0].startswith('ref_'):
            referrer_id = int(context.args[0].replace('ref_', ''))
            self.referral_system[referrer_id].append(user.id)
        
        keyboard = [
            [InlineKeyboardButton("💰 Wallet", callback_data='wallet'),
             InlineKeyboardButton("📊 Trade", callback_data='trade')],
            [InlineKeyboardButton("📈 Portfolio", callback_data='portfolio'),
             InlineKeyboardButton("💎 Subscription", callback_data='subscription')],
            [InlineKeyboardButton("📢 Community", url=COMMUNITY_LINK),
             InlineKeyboardButton("💬 Group", url=GROUP_CHAT_LINK)],
            [InlineKeyboardButton("🐦 Follow on X", url=X_HANDLE_LINK)],
            [InlineKeyboardButton("📊 My Earnings", callback_data='my_earnings'),
             InlineKeyboardButton("⚙️ Settings", callback_data='settings')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        tier = await self.database.get_user_subscription(user.id)
        tier_info = SUBSCRIPTION_TIERS.get(tier, SUBSCRIPTION_TIERS['free'])
        
        welcome_text = (
            f"🤖 *Welcome to Madara Bot!*\n\n"
            f"👤 {user.first_name}\n"
            f"💎 Tier: {tier_info['name']}\n\n"
            f"*🚀 Start Trading Now!*\n"
            f"• Lightning-fast execution\n"
            f"• Beautiful P&L charts\n"
            f"• Whale tracking\n"
            f"• Copy trading\n\n"
            f"💰 *Fee Tiers:*\n"
            f"🐢 Standard: $0.50\n"
            f"⚡ Fast: $1.50\n"
            f"🚀 Turbo: $3.00\n"
            f"💎 Ultra: $5.00\n\n"
            f"_Upgrade to Pro/VIP for discounts!_"
        )
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def subscription_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        #Subscription upgrade menu 
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        current_tier = await self.database.get_user_subscription(user_id)
        
        keyboard = [
            [InlineKeyboardButton("⭐ Upgrade to Pro ($14.99/mo)", callback_data='sub_pro')],
            [InlineKeyboardButton("💎 Upgrade to VIP ($49.99/mo)", callback_data='sub_vip')],
            [InlineKeyboardButton("📊 Compare Plans", callback_data='sub_compare')],
            [InlineKeyboardButton("◀️ Back", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        tier_info = SUBSCRIPTION_TIERS[current_tier]
        
        sub_text = (
            f"*💎 Your Subscription*\n\n"
            f"Current: {tier_info['name']}\n\n"
            f"*Benefits:*\n"
        )
        
        for feature in tier_info['features']:
            sub_text += f"✅ {feature}\n"
        
        sub_text += "\n_Upgrade for more features and lower fees!_"
        
        await query.edit_message_text(sub_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def fee_selection_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        #Show fee tier selection before trade 
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        tier = await self.database.get_user_subscription(user_id)
        discount = SUBSCRIPTION_TIERS[tier]['trade_fee_discount']
        
        keyboard = []
        for key, config in FEE_TIERS.items():
            if key == 'custom':
                continue
            final_fee = config['fee'] * (1 - discount)
            button_text = f"{config['name']} - ${final_fee:.2f} ({config['speed']})"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f'fee_select_{key}')])
        
        keyboard.append([InlineKeyboardButton("🎯 Custom Fee", callback_data='fee_custom')])
        keyboard.append([InlineKeyboardButton("◀️ Back", callback_data='trade')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        fee_text = (
            f"*⚡ Select Fee Tier*\n\n"
            f"Your discount: {discount*100:.0f}%\n\n"
            f"Higher fees = Faster execution\n"
            f"Ultra tier includes MEV protection\n\n"
            f"_Choose your speed:_"
        )
        
        await query.edit_message_text(fee_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def my_earnings_dashboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        #Show creator earnings dashboard (admin only) 
        query = update.callback_query
        await query.answer()
        
        # Check if user is bot owner/admin
        if query.from_user.id != int(os.getenv('ADMIN_USER_ID', '0')):
            await query.edit_message_text("❌ Admin access only!")
            return
        
        revenue = await self.database.get_total_revenue()
        
        earnings_text = (
            f"*💰 Revenue Dashboard*\n\n"
            f"*Total Revenue:* ${revenue.get('total', 0):,.2f}\n\n"
            f"*Breakdown:*\n"
            f"📊 Trade Fees: ${revenue.get('trade_fees', 0):,.2f}\n"
            f"💎 Subscriptions: ${revenue.get('subscriptions', 0):,.2f}\n"
            f"🐋 Copy Trading: ${revenue.get('copy_trading', 0):,.2f}\n\n"
            f"_Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}_"
        )
        
        keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data='my_earnings')],
                   [InlineKeyboardButton("◀️ Back", callback_data='main_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(earnings_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        #Handle all callbacks 
        query = update.callback_query
        data = query.data
        
        if data == 'subscription':
            await self.subscription_menu(update, context)
        elif data == 'fee_selection':
            await self.fee_selection_menu(update, context)
        elif data == 'my_earnings':
            await self.my_earnings_dashboard(update, context)
        # Add more handlers...
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        #Handle errors#
        logger.error(f"Update {update} caused error {context.error}")

# Health check endpoint for railway
async def health_check(request):
    #Health check endpoint 
    return web.Response(text="OK", status=200)

async def start_web_server():
    #Start health check web server 
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.getenv('PORT', 8080)))
    await site.start()
    logger.info(f"Web server started on port {os.getenv('PORT', 8080)}")

async def main():
    #Main function 
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        return
    
    # Start health check server
    await start_web_server()
    
    bot = SolanaBot()
    await bot.initialize()
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CallbackQueryHandler(bot.callback_handler))
    application.add_error_handler(bot.error_handler)
    
    # Start bot
    logger.info("🚀 Bot starting on railway...")
    await application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    asyncio.run(main())
    
     """
     
import os
import json
import asyncio
import logging
import sys
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from decimal import Decimal
import io
from collections import defaultdict
from aiohttp import web
import signal

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatPermissions
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ChatMemberHandler
)
from telegram.constants import ParseMode, ChatType
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.transaction import Transaction
import aiohttp
import base58
from PIL import Image, ImageDraw, ImageFont
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from io import BytesIO
import numpy as np

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# Configuration with validation
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
RPC_URL = os.getenv('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY')
JUPITER_API = 'https://quote-api.jup.ag/v6'
DATABASE_URL = os.getenv('DATABASE_URL')
REDIS_URL = os.getenv('REDIS_URL')
COMMUNITY_LINK = os.getenv('COMMUNITY_LINK', 'https://t.me/your_community')
GROUP_CHAT_LINK = os.getenv('GROUP_CHAT_LINK', 'https://t.me/your_group')
X_HANDLE_LINK = os.getenv('X_HANDLE_LINK', 'https://x.com/your_handle')
BIRDEYE_API_KEY = os.getenv('BIRDEYE_API_KEY', '')
FEE_WALLET_ADDRESS = os.getenv('FEE_WALLET_ADDRESS', 'YOUR_SOLANA_WALLET_HERE')
PORT = int(os.getenv('PORT', 8080))

# Validate critical environment variables
def validate_environment():
    """Validate required environment variables"""
    errors = []
    
    if not TELEGRAM_TOKEN:
        errors.append("❌ TELEGRAM_BOT_TOKEN is not set!")
    elif len(TELEGRAM_TOKEN) < 30:
        errors.append("❌ TELEGRAM_BOT_TOKEN looks invalid (too short)")
    
    if not ENCRYPTION_KEY:
        errors.append("❌ ENCRYPTION_KEY is not set!")
    elif len(ENCRYPTION_KEY) < 20:
        errors.append("❌ ENCRYPTION_KEY looks invalid (too short)")
    
    if not FEE_WALLET_ADDRESS or FEE_WALLET_ADDRESS == 'YOUR_SOLANA_WALLET_HERE':
        logger.warning("⚠️ FEE_WALLET_ADDRESS not set - fees won't be collected!")
    
    if errors:
        for error in errors:
            logger.error(error)
        logger.error("\n📋 Please set these environment variables in Railway:")
        logger.error("   Dashboard → Your Service → Variables → + New Variable")
        return False
    
    logger.info("✅ All required environment variables are set!")
    return True

# Revenue Configuration
FEE_TIERS = {
    'standard': {'fee': 0.50, 'name': '🐢 Standard', 'speed': '5-10 sec'},
    'fast': {'fee': 1.50, 'name': '⚡ Fast', 'speed': '2-3 sec'},
    'turbo': {'fee': 3.00, 'name': '🚀 Turbo', 'speed': '1 sec'},
    'ultra': {'fee': 5.00, 'name': '💎 Ultra', 'speed': 'Instant + MEV'},
    'custom': {'fee': 0.0, 'name': '🎯 Custom', 'speed': 'User defined'}
}

SUBSCRIPTION_TIERS = {
    'free': {
        'price': 0,
        'name': 'Free',
        'trades_per_day': 10,
        'trade_fee_discount': 0,
        'features': ['Basic trading', 'Portfolio tracking', 'Price alerts']
    },
    'pro': {
        'price': 14.99,
        'name': '⭐ Pro',
        'trades_per_day': -1,
        'trade_fee_discount': 0.5,
        'features': ['Unlimited trades', '50% fee discount', 'Copy trading', 'Whale tracking', 'Priority support']
    },
    'vip': {
        'price': 49.99,
        'name': '💎 VIP',
        'trades_per_day': -1,
        'trade_fee_discount': 1.0,
        'features': ['Zero fees', 'Instant execution', 'Dedicated support', 'Early features', 'Custom strategies']
    }
}

COPY_TRADING_COMMISSION = 0.20

# Supported languages
LANGUAGES = {
    'en': 'English',
    'es': 'Español',
    'zh': '中文',
    'ru': 'Русский',
    'fr': 'Français'
}

TRANSLATIONS = {
    'en': {
        'welcome': '🤖 *Welcome to Madara Bot!*',
        'features': 'Your AI-Powered Trading Companion',
    },
    'es': {
        'welcome': '🤖 *¡Bienvenido al Bot de Trading de Solana!*',
        'features': 'Tu Compañero de Trading con IA',
    }
}

class Database:
    """Enhanced database with better error handling"""
    def __init__(self):
        self.pool = None
        self.connected = False
        
    async def connect(self):
        """Initialize database with retry logic"""
        if not DATABASE_URL:
            logger.warning("⚠️ DATABASE_URL not set. Using in-memory storage.")
            logger.warning("   Data will be lost on restart!")
            return
        
        max_retries = 3
        retry_delay = 2
        
        for attempt in range(max_retries):
            try:
                import asyncpg
                logger.info(f"🔄 Connecting to database... (attempt {attempt + 1}/{max_retries})")
                self.pool = await asyncpg.create_pool(
                    DATABASE_URL,
                    min_size=1,
                    max_size=10,
                    command_timeout=60
                )
                await self.create_tables()
                self.connected = True
                logger.info("✅ Database connected successfully!")
                return
            except Exception as e:
                logger.error(f"❌ Database connection attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    logger.info(f"⏳ Retrying in {retry_delay} seconds...")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logger.error("❌ Failed to connect to database after all retries")
                    logger.warning("⚠️ Bot will run without database (in-memory mode)")
    
    async def create_tables(self):
        """Create tables with error handling"""
        if not self.pool:
            return
        
        try:
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT PRIMARY KEY,
                        username TEXT,
                        wallet_address TEXT,
                        encrypted_key TEXT,
                        language TEXT DEFAULT 'en',
                        subscription_tier TEXT DEFAULT 'free',
                        subscription_expires TIMESTAMP,
                        referrer_id BIGINT,
                        total_volume DECIMAL DEFAULT 0,
                        total_fees_paid DECIMAL DEFAULT 0,
                        created_at TIMESTAMP DEFAULT NOW()
                    )
                ''')
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS transactions (
                        tx_id TEXT PRIMARY KEY,
                        user_id BIGINT,
                        type TEXT,
                        token_in TEXT,
                        token_out TEXT,
                        amount_in DECIMAL,
                        amount_out DECIMAL,
                        price DECIMAL,
                        fee_paid DECIMAL,
                        fee_tier TEXT,
                        timestamp TIMESTAMP DEFAULT NOW(),
                        status TEXT
                    )
                ''')
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS revenue (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT,
                        type TEXT,
                        amount DECIMAL,
                        description TEXT,
                        timestamp TIMESTAMP DEFAULT NOW()
                    )
                ''')
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS portfolio (
                        id SERIAL PRIMARY KEY,
                        user_id BIGINT,
                        token_mint TEXT,
                        amount DECIMAL,
                        avg_buy_price DECIMAL,
                        last_updated TIMESTAMP DEFAULT NOW(),
                        UNIQUE(user_id, token_mint)
                    )
                ''')
                logger.info("✅ Database tables created/verified")
        except Exception as e:
            logger.error(f"❌ Error creating tables: {e}")
    
    async def save_revenue(self, user_id: int, revenue_type: str, amount: float, description: str):
        """Track revenue with error handling"""
        if not self.pool or not self.connected:
            logger.warning(f"⚠️ Cannot save revenue (no database): ${amount}")
            return
        
        try:
            async with self.pool.acquire() as conn:
                await conn.execute('''
                    INSERT INTO revenue (user_id, type, amount, description)
                    VALUES ($1, $2, $3, $4)
                ''', user_id, revenue_type, amount, description)
                logger.info(f"💰 Revenue saved: ${amount} from user {user_id}")
        except Exception as e:
            logger.error(f"❌ Error saving revenue: {e}")
    
    async def get_total_revenue(self) -> dict:
        """Get revenue with fallback"""
        if not self.pool or not self.connected:
            return {'total': 0, 'trade_fees': 0, 'subscriptions': 0, 'copy_trading': 0}
        
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow('''
                    SELECT 
                        COALESCE(SUM(amount), 0) as total,
                        COALESCE(SUM(CASE WHEN type = 'trade_fee' THEN amount ELSE 0 END), 0) as trade_fees,
                        COALESCE(SUM(CASE WHEN type = 'subscription' THEN amount ELSE 0 END), 0) as subscriptions,
                        COALESCE(SUM(CASE WHEN type = 'copy_trading' THEN amount ELSE 0 END), 0) as copy_trading
                    FROM revenue
                ''')
                return dict(row) if row else {'total': 0, 'trade_fees': 0, 'subscriptions': 0, 'copy_trading': 0}
        except Exception as e:
            logger.error(f"❌ Error getting revenue: {e}")
            return {'total': 0, 'trade_fees': 0, 'subscriptions': 0, 'copy_trading': 0}
    
    async def get_user_subscription(self, user_id: int) -> str:
        """Get subscription with fallback"""
        if not self.pool or not self.connected:
            return 'free'
        
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow('''
                    SELECT subscription_tier, subscription_expires 
                    FROM users WHERE user_id = $1
                ''', user_id)
                if not row:
                    return 'free'
                if row['subscription_expires'] and row['subscription_expires'] < datetime.now():
                    return 'free'
                return row['subscription_tier'] or 'free'
        except Exception as e:
            logger.error(f"❌ Error getting subscription: {e}")
            return 'free'
    
    async def get_portfolio(self, user_id: int) -> List[dict]:
        """Get portfolio with error handling"""
        if not self.pool or not self.connected:
            return []
        
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch('SELECT * FROM portfolio WHERE user_id = $1', user_id)
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"❌ Error getting portfolio: {e}")
            return []
    
    async def close(self):
        """Close database connection"""
        if self.pool:
            await self.pool.close()
            logger.info("✅ Database connection closed")

class RedisCache:
    """Redis with better error handling"""
    def __init__(self):
        self.redis = None
        self.connected = False
        
    async def connect(self):
        """Connect to Redis with retry"""
        if not REDIS_URL:
            logger.warning("⚠️ REDIS_URL not set. Caching disabled.")
            return
        
        try:
            import aioredis
            self.redis = await aioredis.create_redis_pool(REDIS_URL)
            self.connected = True
            logger.info("✅ Redis connected!")
        except Exception as e:
            logger.warning(f"⚠️ Redis connection failed: {e}")
            logger.warning("   Bot will work without caching")
    
    async def close(self):
        """Close Redis connection"""
        if self.redis:
            self.redis.close()
            await self.redis.wait_closed()
            logger.info("✅ Redis connection closed")

class ChartGenerator:
    """Generate charts with error handling"""
    
    @staticmethod
    def create_pnl_chart(portfolio_data: List[dict], user_stats: dict) -> Optional[BytesIO]:
        """Create chart with fallback"""
        try:
            fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), 
                                            facecolor='#0a0e27', gridspec_kw={'height_ratios': [2, 1]})
            
            bg_color = '#0a0e27'
            text_color = '#e0e6ed'
            profit_color = '#00ff88'
            loss_color = '#ff4757'
            
            ax1.set_facecolor(bg_color)
            total_pnl = user_stats.get('total_pnl', 0)
            total_value = user_stats.get('total_value', 0)
            
            if portfolio_data:
                tokens = [p['token_symbol'] for p in portfolio_data]
                values = [p['current_value'] for p in portfolio_data]
                colors = plt.cm.viridis(np.linspace(0.3, 0.9, len(tokens)))
                
                wedges, texts, autotexts = ax1.pie(values, labels=tokens, autopct='%1.1f%%',
                                                    colors=colors, startangle=90,
                                                    textprops={'color': text_color, 'fontsize': 11},
                                                    wedgeprops={'linewidth': 2, 'edgecolor': bg_color})
                
                for autotext in autotexts:
                    autotext.set_color('white')
                    autotext.set_fontweight('bold')
            
            ax1.set_title('Portfolio Composition', color=text_color, fontsize=16, fontweight='bold', pad=20)
            
            ax2.set_facecolor(bg_color)
            ax2.axis('off')
            
            info_text = f"""
            💰 Total Value: ${total_value:,.2f}
            {'📈' if total_pnl >= 0 else '📉'} P&L: ${total_pnl:,.2f}
            🏆 Best: {user_stats.get('best_token', 'N/A')}
            📊 Trades: {user_stats.get('total_trades', 0)}
            """
            
            ax2.text(0.5, 0.5, info_text, transform=ax2.transAxes,
                    fontsize=12, color=text_color, ha='center', va='center',
                    bbox=dict(boxstyle='round,pad=1', facecolor='#1e2742', 
                             edgecolor=profit_color if total_pnl >= 0 else loss_color, 
                             linewidth=3, alpha=0.8),
                    family='monospace', fontweight='bold')
            
            plt.tight_layout()
            
            buffer = BytesIO()
            plt.savefig(buffer, format='png', dpi=150, facecolor=bg_color, bbox_inches='tight')
            buffer.seek(0)
            plt.close()
            
            return buffer
        except Exception as e:
            logger.error(f"❌ Chart generation error: {e}")
            plt.close('all')  # Clean up
            return None

class UserWallet:
    """Wallet management"""
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.keypair: Optional[Keypair] = None
        self.pubkey: Optional[Pubkey] = None
    
    def create_wallet(self) -> str:
        """Create wallet"""
        self.keypair = Keypair()
        self.pubkey = self.keypair.pubkey()
        return str(self.pubkey)
    
    def import_wallet(self, private_key: str) -> str:
        """Import wallet"""
        try:
            decoded = base58.b58decode(private_key)
            self.keypair = Keypair.from_bytes(decoded)
            self.pubkey = self.keypair.pubkey()
            return str(self.pubkey)
        except Exception as e:
            raise ValueError(f"Invalid private key: {str(e)}")
    
    def export_private_key(self) -> str:
        """Export key"""
        if not self.keypair:
            raise ValueError("No wallet loaded")
        return base58.b58encode(bytes(self.keypair)).decode('utf-8')

class TradingEngine:
    """Trading with error handling"""
    def __init__(self, database: Database):
        self.client = AsyncClient(RPC_URL)
        self.database = database
        self.active_orders: Dict[int, List] = defaultdict(list)
        self.price_alerts: Dict[int, List] = defaultdict(list)
    
    async def collect_fee(self, user_wallet: UserWallet, fee_amount: float, fee_tier: str) -> bool:
        """Collect fee"""
        try:
            logger.info(f"💰 Fee collected: ${fee_amount} ({fee_tier}) from user {user_wallet.user_id}")
            await self.database.save_revenue(
                user_wallet.user_id,
                'trade_fee',
                fee_amount,
                f'Trading fee - {fee_tier}'
            )
            return True
        except Exception as e:
            logger.error(f"❌ Fee collection error: {e}")
            return False
    
    async def get_token_price(self, token_mint: str) -> Optional[float]:
        """Get price with timeout"""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                url = f"{JUPITER_API}/quote"
                params = {
                    'inputMint': 'So11111111111111111111111111111111111111112',
                    'outputMint': token_mint,
                    'amount': '1000000000',
                    'slippageBps': '50'
                }
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        out_amount = int(data.get('outAmount', 0))
                        return out_amount / 1e9 if out_amount else None
        except asyncio.TimeoutError:
            logger.error("⏱️ Price fetch timeout")
        except Exception as e:
            logger.error(f"❌ Price fetch error: {e}")
        return None
    
    async def get_balance(self, wallet: UserWallet) -> float:
        """Get balance"""
        try:
            balance = await self.client.get_balance(wallet.pubkey, commitment=Confirmed)
            return balance.value / 1e9
        except Exception as e:
            logger.error(f"❌ Balance error: {e}")
            return 0.0

class SolanaBot:
    """Main bot with Railway optimizations"""
    def __init__(self):
        self.database = Database()
        self.trading_engine = TradingEngine(self.database)
        self.user_wallets: Dict[int, UserWallet] = {}
        self.user_settings: Dict[int, dict] = {}
        self.cache = RedisCache()
        self.chart_generator = ChartGenerator()
        self.referral_system: Dict[int, List[int]] = defaultdict(list)
        self.is_shutting_down = False
    
    async def initialize(self):
        """Initialize with better logging"""
        logger.info("🚀 Initializing bot...")
        await self.database.connect()
        await self.cache.connect()
        logger.info("✅ Bot initialized successfully!")
    
    async def shutdown(self):
        """Graceful shutdown"""
        if self.is_shutting_down:
            return
        
        self.is_shutting_down = True
        logger.info("🛑 Shutting down gracefully...")
        
        await self.database.close()
        await self.cache.close()
        
        logger.info("✅ Shutdown complete")
    
    def get_user_wallet(self, user_id: int) -> UserWallet:
        """Get wallet"""
        if user_id not in self.user_wallets:
            self.user_wallets[user_id] = UserWallet(user_id)
        return self.user_wallets[user_id]
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command"""
        user = update.effective_user
        
        if context.args and context.args[0].startswith('ref_'):
            referrer_id = int(context.args[0].replace('ref_', ''))
            self.referral_system[referrer_id].append(user.id)
        
        keyboard = [
            [InlineKeyboardButton("💰 Wallet", callback_data='wallet'),
             InlineKeyboardButton("📊 Trade", callback_data='trade')],
            [InlineKeyboardButton("📈 Portfolio", callback_data='portfolio'),
             InlineKeyboardButton("💎 Subscription", callback_data='subscription')],
            [InlineKeyboardButton("📢 Community", url=COMMUNITY_LINK),
             InlineKeyboardButton("💬 Group", url=GROUP_CHAT_LINK)],
            [InlineKeyboardButton("🐦 Follow on X", url=X_HANDLE_LINK)],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        tier = await self.database.get_user_subscription(user.id)
        tier_info = SUBSCRIPTION_TIERS.get(tier, SUBSCRIPTION_TIERS['free'])
        
        welcome_text = (
            f"🤖 *Welcome to Madara Bot!*\n\n"
            f"👤 {user.first_name}\n"
            f"💎 Tier: {tier_info['name']}\n\n"
            f"🚀 Start trading now!"
        )
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle callbacks"""
        query = update.callback_query
        await query.answer()
        
        await query.edit_message_text("Feature coming soon! 🚀")
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"❌ Update {update} caused error {context.error}")
        
        if update and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "❌ An error occurred. Please try again later."
                )
            except:
                pass

# Health check for Railway
async def health_check(request):
    """Health endpoint"""
    return web.Response(text="OK", status=200)

async def start_web_server():
    """Start health check server"""
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"✅ Health check server running on port {PORT}")
    return runner

async def main():
    """Main function with Railway optimizations"""
    # Validate environment
    if not validate_environment():
        logger.error("🛑 Cannot start bot - missing required environment variables")
        sys.exit(1)
    
    logger.info("=" * 50)
    logger.info("🤖 Madara Trading Bot")
    logger.info("=" * 50)
    logger.info(f"📍 Platform: Railway")
    logger.info(f"🔌 Port: {PORT}")
    logger.info(f"🗄️  Database: {'Connected' if DATABASE_URL else 'In-memory'}")
    logger.info(f"⚡ Redis: {'Enabled' if REDIS_URL else 'Disabled'}")
    logger.info("=" * 50)
    
    # Start health check server
    web_runner = await start_web_server()
    
    # Initialize bot
    bot = SolanaBot()
    await bot.initialize()
    
    # Build application
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CallbackQueryHandler(bot.callback_handler))
    application.add_error_handler(bot.error_handler)
    
    # Handle shutdown signals
    async def shutdown_handler(signum, frame):
        logger.info(f"🛑 Received signal {signum}")
        await bot.shutdown()
        await web_runner.cleanup()
        sys.exit(0)
    
    # Register signal handlers
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda s, f: asyncio.create_task(shutdown_handler(s, f)))
    
    # Start bot
    logger.info("🚀 Bot starting on Railway...")
    logger.info("✅ Ready to receive messages!")
    
    try:
        await application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}")
        await bot.shutdown()
        await web_runner.cleanup()
        sys.exit(1)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        sys.exit(1)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ChatMemberHandler
)
from telegram.constants import ParseMode, ChatType
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.transaction import Transaction
import aiohttp
import base58
from PIL import Image, ImageDraw, ImageFont
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from io import BytesIO
import numpy as np

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
RPC_URL = os.getenv('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')
ENCRYPTION_KEY = os.getenv('ENCRYPTION_KEY')
JUPITER_API = 'https://quote-api.jup.ag/v6'
DATABASE_URL = os.getenv('DATABASE_URL')
REDIS_URL = os.getenv('REDIS_URL')
COMMUNITY_LINK = os.getenv('COMMUNITY_LINK', 'https://t.me/your_community')
GROUP_CHAT_LINK = os.getenv('GROUP_CHAT_LINK', 'https://t.me/your_group')
X_HANDLE_LINK = os.getenv('X_HANDLE_LINK', 'https://x.com/your_handle')
BIRDEYE_API_KEY = os.getenv('BIRDEYE_API_KEY', '')
FEE_WALLET_ADDRESS = os.getenv('FEE_WALLET_ADDRESS', 'YOUR_SOLANA_WALLET_HERE')

# Revenue Configuration
FEE_TIERS = {
    'standard': {'fee': 0.50, 'name': '🐢 Standard', 'speed': '5-10 sec'},
    'fast': {'fee': 1.50, 'name': '⚡ Fast', 'speed': '2-3 sec'},
    'turbo': {'fee': 3.00, 'name': '🚀 Turbo', 'speed': '1 sec'},
    'ultra': {'fee': 5.00, 'name': '💎 Ultra', 'speed': 'Instant + MEV'},
    'custom': {'fee': 0.0, 'name': '🎯 Custom', 'speed': 'User defined'}
}

SUBSCRIPTION_TIERS = {
    'free': {
        'price': 0,
        'name': 'Free',
        'trades_per_day': 10,
        'trade_fee_discount': 0,
        'features': ['Basic trading', 'Portfolio tracking', 'Price alerts']
    },
    'pro': {
        'price': 14.99,
        'name': '⭐ Pro',
        'trades_per_day': -1,  # unlimited
        'trade_fee_discount': 0.5,  # 50% off
        'features': ['Unlimited trades', '50% fee discount', 'Copy trading', 'Whale tracking', 'Priority support']
    },
    'vip': {
        'price': 49.99,
        'name': '💎 VIP',
        'trades_per_day': -1,
        'trade_fee_discount': 1.0,  # 100% off = free
        'features': ['Zero fees', 'Instant execution', 'Dedicated support', 'Early features', 'Custom strategies']
    }
}

COPY_TRADING_COMMISSION = 0.20  # 20% of profits

# Supported languages
LANGUAGES = {
    'en': 'English',
    'es': 'Español',
    'zh': '中文',
    'ru': 'Русский',
    'fr': 'Français'
}

# Translation dictionary (simplified)
TRANSLATIONS = {
    'en': {
        'welcome': '🤖 *Welcome to Madara Bot!*',
        'features': 'Your AI-Powered Trading Companion',
    },
    'es': {
        'welcome': '🤖 *¡Bienvenido al Bot de Trading de Solana!*',
        'features': 'Tu Compañero de Trading con IA',
    }
}

class Database:
    """Enhanced database operations with PostgreSQL"""
    def __init__(self):
        self.pool = None
        
    async def connect(self):
        """Initialize database connection pool"""
        if not DATABASE_URL:
            logger.warning("DATABASE_URL not set. Using in-memory storage.")
            return
        try:
            import asyncpg
            self.pool = await asyncpg.create_pool(DATABASE_URL)
            await self.create_tables()
        except Exception as e:
            logger.error(f"Database connection error: {e}")
    
    async def create_tables(self):
        """Create necessary database tables"""
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            # Users table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    wallet_address TEXT,
                    encrypted_key TEXT,
                    language TEXT DEFAULT 'en',
                    subscription_tier TEXT DEFAULT 'free',
                    subscription_expires TIMESTAMP,
                    referrer_id BIGINT,
                    total_volume DECIMAL DEFAULT 0,
                    total_fees_paid DECIMAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            # Transactions table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS transactions (
                    tx_id TEXT PRIMARY KEY,
                    user_id BIGINT,
                    type TEXT,
                    token_in TEXT,
                    token_out TEXT,
                    amount_in DECIMAL,
                    amount_out DECIMAL,
                    price DECIMAL,
                    fee_paid DECIMAL,
                    fee_tier TEXT,
                    timestamp TIMESTAMP DEFAULT NOW(),
                    status TEXT,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            # Revenue tracking
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS revenue (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    type TEXT,
                    amount DECIMAL,
                    description TEXT,
                    timestamp TIMESTAMP DEFAULT NOW()
                )
            ''')
            # Subscriptions
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS subscriptions (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    tier TEXT,
                    amount DECIMAL,
                    payment_method TEXT,
                    started_at TIMESTAMP DEFAULT NOW(),
                    expires_at TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            # Portfolio table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS portfolio (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    token_mint TEXT,
                    amount DECIMAL,
                    avg_buy_price DECIMAL,
                    last_updated TIMESTAMP DEFAULT NOW(),
                    UNIQUE(user_id, token_mint)
                )
            ''')
    
    async def save_revenue(self, user_id: int, revenue_type: str, amount: float, description: str):
        """Track revenue"""
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO revenue (user_id, type, amount, description)
                VALUES ($1, $2, $3, $4)
            ''', user_id, revenue_type, amount, description)
    
    async def get_total_revenue(self) -> dict:
        """Get total revenue statistics"""
        if not self.pool:
            return {}
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT 
                    SUM(amount) as total,
                    SUM(CASE WHEN type = 'trade_fee' THEN amount ELSE 0 END) as trade_fees,
                    SUM(CASE WHEN type = 'subscription' THEN amount ELSE 0 END) as subscriptions,
                    SUM(CASE WHEN type = 'copy_trading' THEN amount ELSE 0 END) as copy_trading
                FROM revenue
            ''')
            return dict(row) if row else {}
    
    async def update_user_subscription(self, user_id: int, tier: str, duration_days: int = 30):
        """Update user subscription"""
        if not self.pool:
            return
        expires = datetime.now() + timedelta(days=duration_days)
        async with self.pool.acquire() as conn:
            await conn.execute('''
                UPDATE users 
                SET subscription_tier = $1, subscription_expires = $2
                WHERE user_id = $3
            ''', tier, expires, user_id)
    
    async def get_user_subscription(self, user_id: int) -> str:
        """Get user's subscription tier"""
        if not self.pool:
            return 'free'
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow('''
                SELECT subscription_tier, subscription_expires 
                FROM users WHERE user_id = $1
            ''', user_id)
            if not row:
                return 'free'
            if row['subscription_expires'] and row['subscription_expires'] < datetime.now():
                # Subscription expired
                await self.update_user_subscription(user_id, 'free')
                return 'free'
            return row['subscription_tier'] or 'free'
    
    async def save_transaction(self, tx_data: dict):
        """Save transaction with fee tracking"""
        if not self.pool:
            return
        async with self.pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO transactions (tx_id, user_id, type, token_in, token_out, 
                                        amount_in, amount_out, price, fee_paid, fee_tier, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ''', tx_data['tx_id'], tx_data['user_id'], tx_data['type'], 
                tx_data['token_in'], tx_data['token_out'], tx_data['amount_in'],
                tx_data['amount_out'], tx_data['price'], tx_data.get('fee_paid', 0),
                tx_data.get('fee_tier', 'standard'), tx_data['status'])
    
    async def get_portfolio(self, user_id: int) -> List[dict]:
        """Get user's portfolio"""
        if not self.pool:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch('SELECT * FROM portfolio WHERE user_id = $1', user_id)
            return [dict(row) for row in rows]

class RedisCache:
    """Redis caching for improved performance"""
    def __init__(self):
        self.redis = None
        
    async def connect(self):
        """Connect to Redis"""
        if not REDIS_URL:
            logger.warning("REDIS_URL not set. Caching disabled.")
            return
        try:
            import aioredis
            self.redis = await aioredis.create_redis_pool(REDIS_URL)
        except Exception as e:
            logger.error(f"Redis connection error: {e}")

class ChartGenerator:
    """Generate beautiful portfolio and P&L charts"""
    
    @staticmethod
    def create_pnl_chart(portfolio_data: List[dict], user_stats: dict) -> BytesIO:
        """Create stunning P&L chart"""
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10), 
                                        facecolor='#0a0e27', gridspec_kw={'height_ratios': [2, 1]})
        
        bg_color = '#0a0e27'
        text_color = '#e0e6ed'
        profit_color = '#00ff88'
        loss_color = '#ff4757'
        
        # Main P&L Chart
        ax1.set_facecolor(bg_color)
        total_pnl = user_stats.get('total_pnl', 0)
        total_value = user_stats.get('total_value', 0)
        
        if portfolio_data:
            tokens = [p['token_symbol'] for p in portfolio_data]
            values = [p['current_value'] for p in portfolio_data]
            colors = plt.cm.viridis(np.linspace(0.3, 0.9, len(tokens)))
            
            wedges, texts, autotexts = ax1.pie(values, labels=tokens, autopct='%1.1f%%',
                                                colors=colors, startangle=90,
                                                textprops={'color': text_color, 'fontsize': 11},
                                                wedgeprops={'linewidth': 2, 'edgecolor': bg_color})
            
            for autotext in autotexts:
                autotext.set_color('white')
                autotext.set_fontweight('bold')
        
        ax1.set_title('Portfolio Composition', color=text_color, fontsize=16, fontweight='bold', pad=20)
        
        # P&L Summary
        ax2.set_facecolor(bg_color)
        ax2.axis('off')
        
        info_text = f"""
        💰 Total Portfolio Value: ${total_value:,.2f}
        
        {'📈' if total_pnl >= 0 else '📉'} Total P&L: ${total_pnl:,.2f}
        
        🏆 Best Performer: {user_stats.get('best_token', 'N/A')}
        📊 Total Trades: {user_stats.get('total_trades', 0)}
        """
        
        ax2.text(0.5, 0.5, info_text, transform=ax2.transAxes,
                fontsize=12, color=text_color, ha='center', va='center',
                bbox=dict(boxstyle='round,pad=1', facecolor='#1e2742', 
                         edgecolor=profit_color if total_pnl >= 0 else loss_color, 
                         linewidth=3, alpha=0.8),
                family='monospace', fontweight='bold')
        
        plt.tight_layout()
        
        buffer = BytesIO()
        plt.savefig(buffer, format='png', dpi=150, facecolor=bg_color, bbox_inches='tight')
        buffer.seek(0)
        plt.close()
        
        return buffer

class UserWallet:
    """Wallet management with encryption"""
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.keypair: Optional[Keypair] = None
        self.pubkey: Optional[Pubkey] = None
    
    def create_wallet(self) -> str:
        """Create new Solana wallet"""
        self.keypair = Keypair()
        self.pubkey = self.keypair.pubkey()
        return str(self.pubkey)
    
    def import_wallet(self, private_key: str) -> str:
        """Import wallet from private key"""
        try:
            decoded = base58.b58decode(private_key)
            self.keypair = Keypair.from_bytes(decoded)
            self.pubkey = self.keypair.pubkey()
            return str(self.pubkey)
        except Exception as e:
            raise ValueError(f"Invalid private key: {str(e)}")
    
    def export_private_key(self) -> str:
        """Export wallet private key"""
        if not self.keypair:
            raise ValueError("No wallet loaded")
        return base58.b58encode(bytes(self.keypair)).decode('utf-8')

class TradingEngine:
    """Enhanced trading engine with fee collection"""
    def __init__(self, database: Database):
        self.client = AsyncClient(RPC_URL)
        self.database = database
        self.active_orders: Dict[int, List] = defaultdict(list)
        self.price_alerts: Dict[int, List] = defaultdict(list)
    
    async def collect_fee(self, user_wallet: UserWallet, fee_amount: float, fee_tier: str) -> bool:
        """Collect trading fee and send to fee wallet"""
        try:
            # In production, you'd send SOL from user to your fee wallet
            # This is a simplified version
            logger.info(f"Fee collected: ${fee_amount} ({fee_tier}) from user {user_wallet.user_id}")
            
            # Track revenue
            await self.database.save_revenue(
                user_wallet.user_id,
                'trade_fee',
                fee_amount,
                f'Trading fee - {fee_tier}'
            )
            
            return True
        except Exception as e:
            logger.error(f"Fee collection error: {e}")
            return False
    
    async def get_token_price(self, token_mint: str) -> Optional[float]:
        """Get current token price"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{JUPITER_API}/quote"
                params = {
                    'inputMint': 'So11111111111111111111111111111111111111112',
                    'outputMint': token_mint,
                    'amount': '1000000000',
                    'slippageBps': '50'
                }
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()
                        out_amount = int(data.get('outAmount', 0))
                        return out_amount / 1e9 if out_amount else None
        except Exception as e:
            logger.error(f"Error getting token price: {e}")
        return None
    
    async def execute_swap(self, wallet: UserWallet, input_mint: str, 
                          output_mint: str, amount: float, fee_tier: str = 'standard') -> dict:
        """Execute swap with fee collection"""
        try:
            # Get fee configuration
            fee_config = FEE_TIERS.get(fee_tier, FEE_TIERS['standard'])
            fee_amount = fee_config['fee']
            
            # Collect fee first
            fee_collected = await self.collect_fee(wallet, fee_amount, fee_tier)
            if not fee_collected:
                return {'success': False, 'error': 'Fee collection failed'}
            
            # Execute swap via Jupiter
            async with aiohttp.ClientSession() as session:
                quote_url = f"{JUPITER_API}/quote"
                quote_params = {
                    'inputMint': input_mint,
                    'outputMint': output_mint,
                    'amount': str(int(amount * 1e9)),
                    'slippageBps': '50'
                }
                
                async with session.get(quote_url, params=quote_params) as response:
                    quote = await response.json()
                
                swap_url = f"{JUPITER_API}/swap"
                swap_payload = {
                    'quoteResponse': quote,
                    'userPublicKey': str(wallet.pubkey),
                    'wrapAndUnwrapSol': True
                }
                
                async with session.post(swap_url, json=swap_payload) as response:
                    swap_data = await response.json()
                
                return {
                    'success': True,
                    'quote': quote,
                    'transaction': swap_data.get('swapTransaction'),
                    'fee_paid': fee_amount,
                    'fee_tier': fee_tier
                }
        except Exception as e:
            logger.error(f"Swap error: {e}")
            return {'success': False, 'error': str(e)}
    
    async def get_balance(self, wallet: UserWallet) -> float:
        """Get SOL balance"""
        try:
            balance = await self.client.get_balance(wallet.pubkey, commitment=Confirmed)
            return balance.value / 1e9
        except Exception as e:
            logger.error(f"Balance error: {e}")
            return 0.0

class SolanaBot:
    """Main bot class with all revenue features"""
    def __init__(self):
        self.database = Database()
        self.trading_engine = TradingEngine(self.database)
        self.user_wallets: Dict[int, UserWallet] = {}
        self.user_settings: Dict[int, dict] = {}
        self.cache = RedisCache()
        self.chart_generator = ChartGenerator()
        self.referral_system: Dict[int, List[int]] = defaultdict(list)
    
    async def initialize(self):
        """Initialize bot components"""
        await self.database.connect()
        await self.cache.connect()
        logger.info("Bot initialized successfully")
    
    def get_user_wallet(self, user_id: int) -> UserWallet:
        """Get or create user wallet"""
        if user_id not in self.user_wallets:
            self.user_wallets[user_id] = UserWallet(user_id)
        return self.user_wallets[user_id]
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command with revenue features highlighted"""
        user = update.effective_user
        
        # Check for referral
        if context.args and context.args[0].startswith('ref_'):
            referrer_id = int(context.args[0].replace('ref_', ''))
            self.referral_system[referrer_id].append(user.id)
        
        keyboard = [
            [InlineKeyboardButton("💰 Wallet", callback_data='wallet'),
             InlineKeyboardButton("📊 Trade", callback_data='trade')],
            [InlineKeyboardButton("📈 Portfolio", callback_data='portfolio'),
             InlineKeyboardButton("💎 Subscription", callback_data='subscription')],
            [InlineKeyboardButton("📢 Community", url=COMMUNITY_LINK),
             InlineKeyboardButton("💬 Group", url=GROUP_CHAT_LINK)],
            [InlineKeyboardButton("🐦 Follow on X", url=X_HANDLE_LINK)],
            [InlineKeyboardButton("📊 My Earnings", callback_data='my_earnings'),
             InlineKeyboardButton("⚙️ Settings", callback_data='settings')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        tier = await self.database.get_user_subscription(user.id)
        tier_info = SUBSCRIPTION_TIERS.get(tier, SUBSCRIPTION_TIERS['free'])
        
        welcome_text = (
            f"🤖 *Welcome to Madara Bot!*\n\n"
            f"👤 {user.first_name}\n"
            f"💎 Tier: {tier_info['name']}\n\n"
            f"*🚀 Start Trading Now!*\n"
            f"• Lightning-fast execution\n"
            f"• Beautiful P&L charts\n"
            f"• Whale tracking\n"
            f"• Copy trading\n\n"
            f"💰 *Fee Tiers:*\n"
            f"🐢 Standard: $0.50\n"
            f"⚡ Fast: $1.50\n"
            f"🚀 Turbo: $3.00\n"
            f"💎 Ultra: $5.00\n\n"
            f"_Upgrade to Pro/VIP for discounts!_"
        )
        
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def subscription_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Subscription upgrade menu"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        current_tier = await self.database.get_user_subscription(user_id)
        
        keyboard = [
            [InlineKeyboardButton("⭐ Upgrade to Pro ($14.99/mo)", callback_data='sub_pro')],
            [InlineKeyboardButton("💎 Upgrade to VIP ($49.99/mo)", callback_data='sub_vip')],
            [InlineKeyboardButton("📊 Compare Plans", callback_data='sub_compare')],
            [InlineKeyboardButton("◀️ Back", callback_data='main_menu')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        tier_info = SUBSCRIPTION_TIERS[current_tier]
        
        sub_text = (
            f"*💎 Your Subscription*\n\n"
            f"Current: {tier_info['name']}\n\n"
            f"*Benefits:*\n"
        )
        
        for feature in tier_info['features']:
            sub_text += f"✅ {feature}\n"
        
        sub_text += "\n_Upgrade for more features and lower fees!_"
        
        await query.edit_message_text(sub_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def fee_selection_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show fee tier selection before trade"""
        query = update.callback_query
        await query.answer()
        
        user_id = query.from_user.id
        tier = await self.database.get_user_subscription(user_id)
        discount = SUBSCRIPTION_TIERS[tier]['trade_fee_discount']
        
        keyboard = []
        for key, config in FEE_TIERS.items():
            if key == 'custom':
                continue
            final_fee = config['fee'] * (1 - discount)
            button_text = f"{config['name']} - ${final_fee:.2f} ({config['speed']})"
            keyboard.append([InlineKeyboardButton(button_text, callback_data=f'fee_select_{key}')])
        
        keyboard.append([InlineKeyboardButton("🎯 Custom Fee", callback_data='fee_custom')])
        keyboard.append([InlineKeyboardButton("◀️ Back", callback_data='trade')])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        fee_text = (
            f"*⚡ Select Fee Tier*\n\n"
            f"Your discount: {discount*100:.0f}%\n\n"
            f"Higher fees = Faster execution\n"
            f"Ultra tier includes MEV protection\n\n"
            f"_Choose your speed:_"
        )
        
        await query.edit_message_text(fee_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def my_earnings_dashboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show creator earnings dashboard (admin only)"""
        query = update.callback_query
        await query.answer()
        
        # Check if user is bot owner/admin
        if query.from_user.id != int(os.getenv('ADMIN_USER_ID', '0')):
            await query.edit_message_text("❌ Admin access only!")
            return
        
        revenue = await self.database.get_total_revenue()
        
        earnings_text = (
            f"*💰 Revenue Dashboard*\n\n"
            f"*Total Revenue:* ${revenue.get('total', 0):,.2f}\n\n"
            f"*Breakdown:*\n"
            f"📊 Trade Fees: ${revenue.get('trade_fees', 0):,.2f}\n"
            f"💎 Subscriptions: ${revenue.get('subscriptions', 0):,.2f}\n"
            f"🐋 Copy Trading: ${revenue.get('copy_trading', 0):,.2f}\n\n"
            f"_Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}_"
        )
        
        keyboard = [[InlineKeyboardButton("🔄 Refresh", callback_data='my_earnings')],
                   [InlineKeyboardButton("◀️ Back", callback_data='main_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(earnings_text, reply_markup=reply_markup, parse_mode='Markdown')
    
    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle all callbacks"""
        query = update.callback_query
        data = query.data
        
        if data == 'subscription':
            await self.subscription_menu(update, context)
        elif data == 'fee_selection':
            await self.fee_selection_menu(update, context)
        elif data == 'my_earnings':
            await self.my_earnings_dashboard(update, context)
        # Add more handlers...
    
    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors"""
        logger.error(f"Update {update} caused error {context.error}")

# Health check endpoint for Koyeb
async def health_check(request):
    """Health check endpoint"""
    return web.Response(text="OK", status=200)

async def start_web_server():
    """Start health check web server"""
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/', health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.getenv('PORT', 8080)))
    await site.start()
    logger.info(f"Web server started on port {os.getenv('PORT', 8080)}")

async def main():
    """Main function"""
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        return
    
    # Start health check server
    await start_web_server()
    
    bot = SolanaBot()
    await bot.initialize()
    
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", bot.start))
    application.add_handler(CallbackQueryHandler(bot.callback_handler))
    application.add_error_handler(bot.error_handler)
    
    # Start bot
    logger.info("🚀 Bot starting on Koyeb...")
    await application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    asyncio.run(main())