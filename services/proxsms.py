"""
ProxSMS WhatsApp Integration
Handles sending messages via ProxSMS API
"""

import httpx
import re
import logging
from typing import Optional, Dict, Any
from config import settings

logger = logging.getLogger(__name__)


def get_db_setting(key: str, default: str = None):
    """Get setting from database, fallback to default"""
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from models import Settings
        from config import settings as app_settings

        engine = create_engine(app_settings.DATABASE_URL)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()
        setting = db.query(Settings).filter(Settings.setting_key == key).first()
        db.close()
        engine.dispose()
        if setting and setting.setting_value:
            return setting.setting_value
    except Exception as e:
        logger.debug(f"Could not read db setting {key}: {e}")
    return default


class ProxSMSService:
    def __init__(self):
        self.api_url = settings.WHATSAPP_API_URL

    @property
    def account_id(self):
        """Get account ID from database or .env"""
        db_value = get_db_setting('whatsapp_account_id')
        return db_value or settings.WHATSAPP_ACCOUNT_ID

    @property
    def secret(self):
        """Get secret from database or .env"""
        db_value = get_db_setting('whatsapp_send_secret')
        return db_value or settings.WHATSAPP_SEND_SECRET

    async def send_message(
        self,
        phone: str,
        message: str,
        priority: int = 2
    ) -> dict:
        """Send text message to WhatsApp user"""
        data = {
            'secret': self.secret,
            'account': self.account_id,
            'recipient': self._normalize_phone(phone),
            'type': 'text',
            'message': message,
            'priority': priority  # 1 = high priority (immediate), 2 = normal
        }

        return await self._make_request(data)

    async def send_image(
        self,
        phone: str,
        image_url: str,
        caption: Optional[str] = None
    ) -> dict:
        """Send message with image"""
        data = {
            'secret': self.secret,
            'account': self.account_id,
            'recipient': self._normalize_phone(phone),
            'type': 'media',
            'message': caption or 'Image',
            'media_url': image_url,
            'media_type': 'image',
            'priority': 2
        }

        return await self._make_request(data)

    async def send_document(
        self,
        phone: str,
        document_url: str,
        filename: Optional[str] = None
    ) -> dict:
        """Send document/file"""
        # Determine document type from filename or URL
        doc_type = 'pdf'  # default
        if filename:
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'pdf'
            if ext in ['pdf', 'xml', 'xls', 'xlsx', 'doc', 'docx']:
                doc_type = ext

        data = {
            'secret': self.secret,
            'account': self.account_id,
            'recipient': self._normalize_phone(phone),
            'type': 'document',
            'message': 'Document',
            'document_url': document_url,
            'document_name': filename or f'document.{doc_type}',
            'document_type': doc_type,
            'priority': 2
        }

        return await self._make_request(data)

    async def send_location(
        self,
        phone: str,
        latitude: float,
        longitude: float,
        name: Optional[str] = None
    ) -> dict:
        """Send location"""
        data = {
            'secret': self.secret,
            'account': self.account_id,
            'recipient': self._normalize_phone(phone),
            'type': 'location',
            'latitude': latitude,
            'longitude': longitude,
            'name': name
        }

        return await self._make_request(data)

    async def _make_request(self, data: dict) -> dict:
        """Make API request to ProxSMS"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # ProxSMS expects multipart/form-data, not JSON
                response = await client.post(self.api_url, data=data)

                if response.status_code != 200:
                    logger.error(f"ProxSMS HTTP Error {response.status_code}: {response.text}")
                    return {
                        'success': False,
                        'error': f"HTTP {response.status_code}"
                    }

                try:
                    decoded = response.json()
                except Exception:
                    logger.error(f"ProxSMS JSON Error: Invalid JSON response")
                    return {
                        'success': False,
                        'error': 'Invalid JSON response'
                    }

                # Check if request was successful
                is_success = decoded.get('status') in ['success', 200, '200']

                return {
                    'success': is_success,
                    'response': decoded,
                    'error': None if is_success else (
                        decoded.get('message') or decoded.get('error') or 'Unknown error'
                    )
                }

        except Exception as e:
            logger.error(f"ProxSMS Exception: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def _normalize_phone(self, phone: str) -> str:
        """Normalize phone number for ProxSMS"""
        original = phone

        # Trim whitespace
        phone = phone.strip()

        # Remove all non-numeric characters except +
        phone = re.sub(r'[^0-9+]', '', phone)

        # If empty after cleaning, raise error
        if not phone:
            logger.error(f"Phone normalization failed - Original: '{original}', After cleaning: '{phone}'")
            raise ValueError('Invalid phone number!')

        # If starts with 0, replace with +961 (local format like 03080203)
        if phone.startswith('0'):
            phone = '+961' + phone[1:]
        # If starts with 961 (already has country code), add +
        elif phone.startswith('961'):
            phone = '+' + phone
        # If doesn't start with +, add +961 (assume local number without 0)
        elif not phone.startswith('+'):
            phone = '+961' + phone

        logger.debug(f"Phone normalized: '{original}' → '{phone}'")
        return phone

    async def test_connection(self, test_phone: str) -> dict:
        """Test ProxSMS API connection"""
        test_message = "✅ ProxSMS connection test successful!"
        return await self.send_message(test_phone, test_message)

    async def send_store_location(self, phone: str) -> dict:
        """Send store location"""
        # Librarie Memoires coordinates (Tripoli, Lebanon)
        latitude = 34.4369
        longitude = 35.8335

        return await self.send_location(phone, latitude, longitude, settings.STORE_NAME)

    async def send_order_confirmation(self, phone: str, order: dict) -> dict:
        """Send formatted order confirmation"""
        message = "✅ تم استلام طلبك بنجاح!\n\n"
        message += f"📋 رقم الطلب: {order.get('order_number', 'N/A')}\n"
        message += f"💰 المبلغ الإجمالي: {order.get('total', 0):,.0f} {settings.CURRENCY}\n\n"

        if order.get('items'):
            message += "📦 المنتجات:\n"
            for item in order['items']:
                message += f"  • {item.get('product_name', '')} x{item.get('quantity', 1)}\n"

        message += f"\n📅 التاريخ: {order.get('created_at', '')}\n"
        message += f"⏳ الحالة: {order.get('status', 'pending')}\n\n"
        message += "شكراً لتسوقك معنا! 🙏"

        return await self.send_message(phone, message)

    async def send_welcome(self, phone: str, customer_name: Optional[str] = None) -> dict:
        """Send welcome message"""
        greeting = f"مرحباً {customer_name}!" if customer_name else "مرحباً!"

        message = f"{greeting} 👋\n\n"
        message += f"أهلاً بك في *{settings.STORE_NAME}* 📚\n\n"
        message += "كيف يمكنني مساعدتك اليوم؟\n\n"
        message += "يمكنك:\n"
        message += "• البحث عن الكتب 🔍\n"
        message += "• الاستفسار عن الأسعار 💰\n"
        message += "• طلب منتجات 🛒\n"
        message += "• الاستعلام عن رصيدك 💳\n\n"
        message += "أنا هنا لمساعدتك! 😊"

        return await self.send_message(phone, message)

    async def send_error(self, phone: str, error_type: str = 'general') -> dict:
        """Send error message"""
        messages = {
            'general': "⚠️ عذراً، حدث خطأ. الرجاء المحاولة مرة أخرى.",
            'product_not_found': "❌ عذراً، لم أتمكن من إيجاد المنتج المطلوب.",
            'out_of_stock': "📦 عذراً، هذا المنتج غير متوفر حالياً.",
            'credit_limit': "💳 عذراً، تجاوزت الحد الائتماني المسموح.",
            'system_error': "⚙️ خطأ في النظام. الرجاء المحاولة لاحقاً."
        }

        message = messages.get(error_type, messages['general'])
        return await self.send_message(phone, message)
