"""
Configuration settings for WhatsApp Bot
Loads from environment variables
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env file - check multiple locations for compiled binary support
def find_env_file():
    """Find .env file in various locations"""
    possible_paths = [
        Path.cwd() / '.env',                    # Current working directory
        Path(__file__).parent / '.env',         # Script directory (development)
        Path('/opt/whatsbot/.env'),             # Fixed install location
        Path.home() / '.whatsbot' / '.env',     # User home directory
    ]

    # For compiled binary, also check the executable's directory
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent
        possible_paths.insert(0, exe_dir / '.env')

    for path in possible_paths:
        if path.exists():
            return path
    return None

env_path = find_env_file()
if env_path:
    load_dotenv(env_path)


class Settings:
    """Application settings loaded from environment"""

    # Database
    DATABASE_URL: str = os.getenv('DATABASE_URL', 'mysql+pymysql://root:@localhost/whatsbot')

    # License Server
    LICENSE_SERVER_URL: str = os.getenv('LICENSE_SERVER_URL', 'https://lic.proxpanel.com')
    LICENSE_KEY: str = os.getenv('LICENSE_KEY', '')
    SITE_DOMAIN: str = os.getenv('SITE_DOMAIN', '')
    LICENSE_CHECK_ENABLED: bool = os.getenv('LICENSE_CHECK_ENABLED', 'true').lower() == 'true'

    # Bot Info
    BOT_VERSION: str = os.getenv('BOT_VERSION', '1.0.0')

    # Store Information
    STORE_NAME: str = os.getenv('STORE_NAME', 'Librairie Memoires')
    STORE_LOCATION: str = os.getenv('STORE_LOCATION', 'Tripoli, Lebanon')
    STORE_PHONE: str = os.getenv('STORE_PHONE', '+961 6 123456')
    STORE_WEBSITE: str = os.getenv('STORE_WEBSITE', '')
    STORE_HOURS: str = os.getenv('STORE_HOURS', 'Mon-Sat: 9AM-8PM')
    CURRENCY: str = os.getenv('CURRENCY', 'LBP')

    # ProxSMS WhatsApp API
    WHATSAPP_API_URL: str = os.getenv('WHATSAPP_API_URL', 'https://api.proxsms.com/send')
    WHATSAPP_ACCOUNT_ID: str = os.getenv('WHATSAPP_ACCOUNT_ID', '')
    WHATSAPP_SEND_SECRET: str = os.getenv('WHATSAPP_SEND_SECRET', '')
    WHATSAPP_WEBHOOK_SECRET: str = os.getenv('WHATSAPP_WEBHOOK_SECRET', '')

    # Anthropic Claude API
    ANTHROPIC_API_KEY: str = os.getenv('ANTHROPIC_API_KEY', '')
    ANTHROPIC_API_URL: str = os.getenv('ANTHROPIC_API_URL', 'https://api.anthropic.com/v1/messages')
    ANTHROPIC_MODEL: str = os.getenv('ANTHROPIC_MODEL', 'claude-3-haiku-20240307')
    ANTHROPIC_MAX_TOKENS: int = int(os.getenv('ANTHROPIC_MAX_TOKENS', '500'))

    # Brains ERP API
    BRAINS_API_BASE: str = os.getenv('BRAINS_API_BASE', '')
    API_TIMEOUT_SECONDS: int = int(os.getenv('API_TIMEOUT_SECONDS', '30'))
    API_RETRY_ATTEMPTS: int = int(os.getenv('API_RETRY_ATTEMPTS', '3'))

    # App Settings
    DEBUG: bool = os.getenv('DEBUG', 'false').lower() == 'true'
    HOST: str = os.getenv('HOST', '0.0.0.0')
    PORT: int = int(os.getenv('PORT', '8000'))


# Global settings instance
settings = Settings()
