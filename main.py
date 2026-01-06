"""
WhatsApp Bot - Python FastAPI Implementation with Admin Dashboard
Compiled with Nuitka for source code protection
"""

import re
import os
import json
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Depends, Form, Query
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import create_engine, or_, and_, func, desc
from sqlalchemy.orm import sessionmaker, Session

from config import settings
from models import Base, Customer, Product, Order, OrderItem, Message, ConversationState, CustomQA, Settings as BotSettings
from services.claude_ai import ClaudeAI
from services.proxsms import ProxSMSService
from services.brains_api import BrainsAPI
from utils.license import LicenseValidator
from utils.templates import ResponseTemplates

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Database setup
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Initialize services
claude_ai = ClaudeAI()
proxsms = ProxSMSService()
brains_api = BrainsAPI()
license_validator = LicenseValidator()

# Templates directory
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'templates')
if not os.path.exists(TEMPLATES_DIR):
    os.makedirs(TEMPLATES_DIR)

templates = Jinja2Templates(directory=TEMPLATES_DIR)


def get_store_name():
    """Get store name from database or config"""
    try:
        db = SessionLocal()
        setting = db.query(BotSettings).filter(BotSettings.setting_key == 'store_name').first()
        db.close()
        if setting and setting.setting_value:
            return setting.setting_value
    except:
        pass
    return settings.STORE_NAME


# Register function with Jinja2 templates
templates.env.globals['get_store_name'] = get_store_name


def get_db():
    """Database session dependency"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    logger.info("WhatsApp Bot starting...")
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/verified")
    yield
    logger.info("WhatsApp Bot shutting down...")


app = FastAPI(
    title="WhatsApp Bot",
    version=settings.BOT_VERSION,
    lifespan=lifespan
)

# Add session middleware
SECRET_KEY = getattr(settings, 'SECRET_KEY', 'whatsbot-secret-key-default')
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)

# Constants
PRODUCTS_PER_PAGE = 10
ADMIN_USER = getattr(settings, 'ADMIN_USER', 'admin')
ADMIN_PASS = getattr(settings, 'ADMIN_PASS', 'admin123')


# ============== ADMIN AUTHENTICATION ==============

def get_current_user(request: Request):
    """Get current logged in user from session"""
    return request.session.get('user')


def require_login(request: Request):
    """Require login for admin routes"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


# ============== ADMIN DASHBOARD ROUTES ==============

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Redirect to dashboard or login"""
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None):
    """Login page"""
    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": error
    })


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    """Handle login"""
    if username == ADMIN_USER and password == ADMIN_PASS:
        request.session['user'] = username
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/login?error=Invalid+credentials", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    """Logout"""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """Main dashboard"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # Get stats
    stats = {
        'total_customers': db.query(func.count(Customer.id)).scalar() or 0,
        'total_products': db.query(func.count(Product.id)).scalar() or 0,
        'total_orders': db.query(func.count(Order.id)).scalar() or 0,
        'pending_orders': db.query(func.count(Order.id)).filter(Order.status == 'pending').scalar() or 0,
        'total_messages': db.query(func.count(Message.id)).scalar() or 0,
        'today_messages': db.query(func.count(Message.id)).filter(
            func.date(Message.created_at) == datetime.now().date()
        ).scalar() or 0,
    }

    # Recent orders
    recent_orders = db.query(Order).order_by(desc(Order.created_at)).limit(5).all()

    # Recent messages
    recent_messages = db.query(Message).order_by(desc(Message.created_at)).limit(10).all()

    # License info
    license_info = license_validator.get_license_info()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "stats": stats,
        "recent_orders": recent_orders,
        "recent_messages": recent_messages,
        "license_info": license_info,
        "settings": settings
    })


@app.get("/customers", response_class=HTMLResponse)
async def customers_page(request: Request, page: int = 1, search: str = None, db: Session = Depends(get_db)):
    """Customers management page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    per_page = 20
    query = db.query(Customer)

    if search:
        query = query.filter(or_(
            Customer.phone.ilike(f'%{search}%'),
            Customer.name.ilike(f'%{search}%')
        ))

    total = query.count()
    customers = query.order_by(desc(Customer.created_at)).offset((page-1)*per_page).limit(per_page).all()
    total_pages = (total + per_page - 1) // per_page

    return templates.TemplateResponse("customers.html", {
        "request": request,
        "user": user,
        "customers": customers,
        "page": page,
        "total_pages": total_pages,
        "search": search or "",
        "total": total
    })


@app.get("/customers/{customer_id}", response_class=HTMLResponse)
async def customer_detail(request: Request, customer_id: int, db: Session = Depends(get_db)):
    """Customer detail page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    messages = db.query(Message).filter(Message.customer_id == customer_id).order_by(desc(Message.created_at)).limit(50).all()
    orders = db.query(Order).filter(Order.customer_id == customer_id).order_by(desc(Order.created_at)).all()

    return templates.TemplateResponse("customer_detail.html", {
        "request": request,
        "user": user,
        "customer": customer,
        "messages": messages,
        "orders": orders
    })


@app.post("/customers/{customer_id}/block")
async def block_customer(customer_id: int, db: Session = Depends(get_db)):
    """Block/unblock customer"""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if customer:
        customer.is_blocked = not customer.is_blocked
        db.commit()
    return RedirectResponse(url=f"/customers/{customer_id}", status_code=302)


@app.get("/products", response_class=HTMLResponse)
async def products_page(request: Request, page: int = 1, search: str = None, db: Session = Depends(get_db)):
    """Products management page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    per_page = 20
    query = db.query(Product)

    if search:
        query = query.filter(or_(
            Product.item_code.ilike(f'%{search}%'),
            Product.item_name.ilike(f'%{search}%')
        ))

    total = query.count()
    products = query.order_by(desc(Product.updated_at)).offset((page-1)*per_page).limit(per_page).all()
    total_pages = (total + per_page - 1) // per_page

    return templates.TemplateResponse("products.html", {
        "request": request,
        "user": user,
        "products": products,
        "page": page,
        "total_pages": total_pages,
        "search": search or "",
        "total": total
    })


@app.post("/products/sync")
async def sync_products(request: Request, db: Session = Depends(get_db)):
    """Sync products from Brains ERP"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    result = await brains_api.sync_products(db, Product)
    return RedirectResponse(url="/products?synced=1", status_code=302)


@app.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request, page: int = 1, status: str = None, db: Session = Depends(get_db)):
    """Orders management page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    per_page = 20
    query = db.query(Order)

    if status:
        query = query.filter(Order.status == status)

    total = query.count()
    orders = query.order_by(desc(Order.created_at)).offset((page-1)*per_page).limit(per_page).all()
    total_pages = (total + per_page - 1) // per_page

    return templates.TemplateResponse("orders.html", {
        "request": request,
        "user": user,
        "orders": orders,
        "page": page,
        "total_pages": total_pages,
        "status_filter": status or "",
        "total": total
    })


@app.get("/orders/{order_id}", response_class=HTMLResponse)
async def order_detail(request: Request, order_id: int, db: Session = Depends(get_db)):
    """Order detail page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    return templates.TemplateResponse("order_detail.html", {
        "request": request,
        "user": user,
        "order": order
    })


@app.post("/orders/{order_id}/status")
async def update_order_status(request: Request, order_id: int, status: str = Form(...), db: Session = Depends(get_db)):
    """Update order status"""
    order = db.query(Order).filter(Order.id == order_id).first()
    if order:
        order.status = status
        order.updated_at = datetime.now()
        if status == 'delivered':
            order.delivered_at = datetime.now()
        db.commit()

        # Notify customer via WhatsApp
        customer = db.query(Customer).filter(Customer.id == order.customer_id).first()
        if customer:
            status_messages = {
                'confirmed': f"Your order {order.order_number} has been confirmed!",
                'preparing': f"Your order {order.order_number} is being prepared.",
                'on_the_way': f"Your order {order.order_number} is on the way!",
                'delivered': f"Your order {order.order_number} has been delivered. Thank you!",
                'cancelled': f"Your order {order.order_number} has been cancelled."
            }
            if status in status_messages:
                await proxsms.send_message(customer.phone, status_messages[status])

    return RedirectResponse(url=f"/orders/{order_id}", status_code=302)


@app.get("/messages", response_class=HTMLResponse)
async def messages_page(request: Request, page: int = 1, db: Session = Depends(get_db)):
    """Messages log page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    per_page = 50
    query = db.query(Message)

    total = query.count()
    messages = query.order_by(desc(Message.created_at)).offset((page-1)*per_page).limit(per_page).all()
    total_pages = (total + per_page - 1) // per_page

    return templates.TemplateResponse("messages.html", {
        "request": request,
        "user": user,
        "messages": messages,
        "page": page,
        "total_pages": total_pages,
        "total": total
    })


@app.get("/qa", response_class=HTMLResponse)
async def qa_page(request: Request, db: Session = Depends(get_db)):
    """Custom Q&A management page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    qa_items = db.query(CustomQA).order_by(desc(CustomQA.priority)).all()

    return templates.TemplateResponse("qa.html", {
        "request": request,
        "user": user,
        "qa_items": qa_items
    })


@app.post("/qa/add")
async def add_qa(
    request: Request,
    keywords: str = Form(...),
    answer_en: str = Form(...),
    answer_ar: str = Form(None),
    category: str = Form(None),
    priority: int = Form(0),
    db: Session = Depends(get_db)
):
    """Add new Q&A"""
    qa = CustomQA(
        keywords=keywords,
        answer_en=answer_en,
        answer_ar=answer_ar,
        category=category,
        priority=priority,
        is_active=True
    )
    db.add(qa)
    db.commit()
    return RedirectResponse(url="/qa", status_code=302)


@app.post("/qa/{qa_id}/delete")
async def delete_qa(qa_id: int, db: Session = Depends(get_db)):
    """Delete Q&A"""
    qa = db.query(CustomQA).filter(CustomQA.id == qa_id).first()
    if qa:
        db.delete(qa)
        db.commit()
    return RedirectResponse(url="/qa", status_code=302)


@app.post("/qa/{qa_id}/toggle")
async def toggle_qa(qa_id: int, db: Session = Depends(get_db)):
    """Toggle Q&A active status"""
    qa = db.query(CustomQA).filter(CustomQA.id == qa_id).first()
    if qa:
        qa.is_active = not qa.is_active
        db.commit()
    return RedirectResponse(url="/qa", status_code=302)


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db)):
    """Settings page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # Get all settings
    db_settings = {s.setting_key: s.setting_value for s in db.query(BotSettings).all()}

    # Get actual license info from validator
    try:
        import asyncio
        license_result = asyncio.get_event_loop().run_until_complete(license_validator.validate())
        license_basic = license_validator.get_license_info()
        license_info = {
            "license_key": license_basic.get("license_key", "Not registered yet"),
            "license_status": "Active" if license_result.get("valid") else "Invalid",
            "license_type": "Paid" if license_result.get("is_paid") else ("Trial" if license_result.get("is_trial") else "Unknown"),
            "license_expiry": license_result.get("expires_at", ""),
            "days_left": license_result.get("days_left", 0),
            "registered_domain": license_basic.get("domain", "")
        }
    except Exception as e:
        license_info = {
            "license_key": "Error loading license",
            "license_status": "Unknown",
            "license_type": "Unknown",
            "license_expiry": "",
            "days_left": 0,
            "registered_domain": ""
        }

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "user": user,
        "settings": settings,
        "db_settings": db_settings,
        "license_info": license_info
    })


@app.post("/settings/save")
async def save_settings(request: Request, db: Session = Depends(get_db)):
    """Save settings"""
    form = await request.form()

    for key, value in form.items():
        setting = db.query(BotSettings).filter(BotSettings.setting_key == key).first()
        if setting:
            setting.setting_value = value
            setting.updated_at = datetime.now()
        else:
            setting = BotSettings(setting_key=key, setting_value=value)
            db.add(setting)

    db.commit()
    return RedirectResponse(url="/settings?saved=1", status_code=302)


# ============== MESSAGE PROCESSOR ==============

class MessageProcessor:
    """Handles message processing logic"""

    def __init__(self, db: Session):
        self.db = db

    async def process_incoming_message(
        self,
        phone: str,
        message: str,
        attachment: Optional[str] = None
    ) -> dict:
        """Main message processing entry point"""
        try:
            # Check license first
            if settings.LICENSE_CHECK_ENABLED:
                license_result = await license_validator.validate()
                if not license_result['valid']:
                    logger.error(f"LICENSE BLOCKED: {license_result.get('message')}")
                    return {'success': False, 'error': 'License invalid'}

            start_time = datetime.now()

            log_msg = f"Incoming message from {phone}: {message}"
            if attachment:
                log_msg += f" [Attachment: {attachment}]"
            logger.info(log_msg)

            customer = self._find_or_create_customer(phone)

            # Check if blocked
            if customer.is_blocked:
                logger.info(f"Blocked customer {phone} tried to message")
                return {'success': True, 'customer_id': customer.id, 'blocked': True}

            self._save_message(customer.id, message, 'RECEIVED', attachment)
            state = self._get_conversation_state(customer.id)
            lang = self._detect_language(message, customer.preferred_language)

            customer.preferred_language = lang
            customer.last_message_at = datetime.now()
            self.db.commit()

            if attachment:
                response = await self._handle_image_message(customer.id, attachment, lang)
            else:
                response = await self._route_message(customer, message, lang, state)

            if response:
                send_result = await proxsms.send_message(phone, response)
                if send_result['success']:
                    self._save_message(customer.id, response, 'SENT')
                    logger.info(f"Response sent to {phone}")
                else:
                    logger.error(f"Failed to send response: {send_result.get('error')}")

            duration = (datetime.now() - start_time).total_seconds() * 1000
            logger.info(f"TOTAL processing time: {duration:.2f}ms")

            return {'success': True, 'customer_id': customer.id}

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return {'success': False, 'error': str(e)}

    def _find_or_create_customer(self, phone: str) -> Customer:
        normalized = re.sub(r'[^0-9]', '', phone)[-8:]

        customer = self.db.query(Customer).filter(
            Customer.phone.contains(normalized)
        ).first()

        if not customer:
            customer = Customer(
                phone=phone,
                created_at=datetime.now()
            )
            self.db.add(customer)
            self.db.commit()
            self.db.refresh(customer)
            logger.info(f"Created new customer: {customer.id}")

        return customer

    def _save_message(self, customer_id: int, content: str, direction: str, attachment: Optional[str] = None):
        msg = Message(
            customer_id=customer_id,
            message=content,
            direction=direction,
            attachment=attachment,
            created_at=datetime.now()
        )
        self.db.add(msg)
        self.db.commit()

    def _get_conversation_state(self, customer_id: int) -> Optional[str]:
        state = self.db.query(ConversationState).filter(
            ConversationState.customer_id == customer_id
        ).first()
        return state.state if state else 'idle'

    def _set_conversation_state(self, customer_id: int, state: str, data: dict = None):
        existing = self.db.query(ConversationState).filter(
            ConversationState.customer_id == customer_id
        ).first()

        if existing:
            existing.state = state
            existing.data = data or {}
            existing.updated_at = datetime.now()
        else:
            new_state = ConversationState(
                customer_id=customer_id,
                state=state,
                data=data or {}
            )
            self.db.add(new_state)

        self.db.commit()

    def _detect_language(self, message: str, saved_lang: Optional[str] = None) -> str:
        if not any(c.isalpha() for c in message):
            return saved_lang or 'en'

        if re.search(r'[\u0600-\u06FF]', message):
            return 'ar'

        french_words = ['bonjour', 'merci', 'oui', 'non', 'je', 'vous', 'nous', 'avec', 'pour']
        message_lower = message.lower()
        for word in french_words:
            if word in message_lower:
                return 'fr'

        return 'en'

    async def _route_message(self, customer: Customer, message: str, lang: str, state: str) -> str:
        message_lower = message.lower().strip()

        if self._is_greeting(message_lower):
            self._set_conversation_state(customer.id, 'idle')
            return ResponseTemplates.welcome(lang, customer.name)

        if self._is_help_request(message_lower):
            return ResponseTemplates.help_message(lang)

        if self._is_product_list_request(message_lower):
            return await self._show_product_list(customer.id, lang, 1)

        if self._is_balance_inquiry(message_lower):
            return self._handle_balance_inquiry(customer, lang)

        store_response = self._check_store_info_questions(message, lang)
        if store_response:
            return store_response

        qa_response = self._check_custom_qa(message, lang)
        if qa_response:
            return qa_response

        if state in ['browsing_products', 'awaiting_product_selection']:
            # If message is a number, handle product selection
            # Otherwise, treat it as a new search
            if message.strip().isdigit():
                return await self._handle_product_selection(customer.id, message, lang)
            # Not a number - fall through to search

        if state == 'awaiting_quantity':
            return await self._handle_quantity_input(customer.id, message, lang)

        if state == 'awaiting_name':
            return await self._handle_name_input(customer.id, message, lang)

        if state == 'awaiting_address':
            return await self._handle_address_input(customer.id, message, lang)

        if state == 'awaiting_order_confirm':
            return await self._handle_order_confirm(customer.id, message, lang)

        search_result = await self._quick_product_search(customer.id, message, lang)
        if search_result:
            return search_result

        ai_search = await claude_ai.smart_product_search(
            customer.id,
            message,
            self._search_products
        )

        if ai_search['success']:
            if ai_search['type'] == 'products' and ai_search.get('products'):
                return await self._display_products(customer.id, ai_search['products'], lang)
            elif ai_search.get('message') == 'NO_MATCH':
                messages = {
                    'ar': "عذرا، هذا المنتج غير متوفر في المخزون حاليا.\n\nاكتب *منتجات* لرؤية المنتجات المتاحة.",
                    'en': "Sorry, this product is not currently in stock.\n\nType *products* to see available items.",
                    'fr': "Desole, ce produit n'est pas en stock actuellement.\n\nTapez *produits* pour voir les articles disponibles."
                }
                return messages.get(lang, messages['en'])

        return await self._handle_with_ai(customer, message, lang)

    def _is_greeting(self, message: str) -> bool:
        # Check for whole words only (not substrings like "hi" in "chimie")
        greetings = [
            # English
            'hi', 'hello', 'hey', 'start',
            # French
            'bonjour', 'salut', 'bonsoir',
            # Arabic (Latin)
            'marhaba', 'salam', 'ahla',
            # Arabic (Script)
            'مرحبا', 'سلام', 'اهلا', 'هلا', 'صباح', 'مساء'
        ]
        words = message.lower().split()
        # Also check the whole message for Arabic greetings (might not split well)
        return any(g in words for g in greetings) or any(g in message for g in ['مرحبا', 'سلام', 'اهلا', 'هلا'])

    def _is_help_request(self, message: str) -> bool:
        # Check for whole words only
        help_words = ['help', 'aide', 'mosaada']
        words = message.lower().split()
        return any(w in words for w in help_words)

    def _is_product_list_request(self, message: str) -> bool:
        # Check for whole words only
        keywords = ['products', 'produits', 'books', 'livres', 'list']
        words = message.lower().split()
        return any(k in words for k in keywords)

    def _is_balance_inquiry(self, message: str) -> bool:
        # Check for whole words only
        keywords = ['balance', 'account', 'solde', 'compte']
        words = message.lower().split()
        return any(k in words for k in keywords)

    def _check_store_info_questions(self, message: str, lang: str) -> Optional[str]:
        """Check for store info questions and return database settings"""
        message_lower = message.lower()

        # Get all settings from database
        db_settings = {}
        all_settings = self.db.query(BotSettings).all()
        for s in all_settings:
            db_settings[s.setting_key] = s.setting_value or ''

        # Phone number question
        if re.search(r'(phone|call|رقم|هاتف|téléphone|numéro)', message_lower):
            phone = db_settings.get('store_phone', '')
            if phone:
                return f"📞 Phone: {phone}"
            return None

        # WhatsApp number question
        if re.search(r'(whatsapp|واتساب|واتس)', message_lower):
            whatsapp = db_settings.get('store_whatsapp', db_settings.get('store_phone', ''))
            if whatsapp:
                return f"📱 WhatsApp: {whatsapp}"
            return None

        # Address/Location question
        if re.search(r'(where|location|address|أين|عنوان|موقع|adresse|où)', message_lower):
            address = db_settings.get('store_address', '')
            location_url = db_settings.get('store_location_url', '')
            if address:
                response = f"📍 Address: {address}"
                if location_url:
                    response += f"\n🗺️ Map: {location_url}"
                return response
            return None

        # Website question
        if re.search(r'(website|site|موقع الويب|site web)', message_lower):
            website = db_settings.get('store_website', '')
            if website:
                return f"🌐 Website: {website}"
            return None

        # Working hours question
        if re.search(r'(hours|open|time|when|ساعات|مفتوح|وقت|horaires|ouvert)', message_lower):
            days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
            day_names = {
                'en': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'],
                'ar': ['الإثنين', 'الثلاثاء', 'الأربعاء', 'الخميس', 'الجمعة', 'السبت', 'الأحد'],
                'fr': ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche']
            }
            names = day_names.get(lang, day_names['en'])
            hours_lines = []
            for i, day in enumerate(days):
                hours = db_settings.get(f'store_hours_{day}', '')
                if hours:
                    hours_lines.append(f"{names[i]}: {hours}")
            if hours_lines:
                return "🕐 Working Hours:\n" + "\n".join(hours_lines)
            return None

        # Instagram question
        if re.search(r'(instagram|insta|انستغرام)', message_lower):
            instagram = db_settings.get('store_instagram', '')
            if instagram:
                return f"📸 Instagram: {instagram}"
            return None

        # General contact question
        if re.search(r'(contact|reach|اتصال|تواصل|contacter)', message_lower):
            store_name = db_settings.get('store_name', 'Our Store')
            phone = db_settings.get('store_phone', '')
            whatsapp = db_settings.get('store_whatsapp', '')
            address = db_settings.get('store_address', '')

            response = f"📞 Contact {store_name}:\n"
            if phone:
                response += f"Phone: {phone}\n"
            if whatsapp and whatsapp != phone:
                response += f"WhatsApp: {whatsapp}\n"
            if address:
                response += f"📍 {address}"
            return response if len(response) > 30 else None

        return None

    def _check_custom_qa(self, message: str, lang: str) -> Optional[str]:
        message_lower = message.lower()

        qa_items = self.db.query(CustomQA).filter(
            CustomQA.is_active == True
        ).all()

        for qa in qa_items:
            keywords = [k.strip().lower() for k in qa.keywords.split(',')]
            if any(k in message_lower for k in keywords):
                if lang == 'ar' and qa.answer_ar:
                    return qa.answer_ar
                elif lang == 'fr' and qa.answer_fr:
                    return qa.answer_fr
                else:
                    return qa.answer_en

        return None

    def _handle_balance_inquiry(self, customer: Customer, lang: str) -> str:
        if not customer.brains_account_code:
            return ResponseTemplates.account_not_linked(lang)

        return ResponseTemplates.balance_info(lang, {
            'name': customer.name,
            'balance': customer.balance or 0,
            'credit_limit': customer.credit_limit or 0
        })

    async def _search_products(self, query: str, limit: int = 10) -> list:
        # Only show IN STOCK products (stock_quantity > 0)
        base_filter = Product.stock_quantity > 0

        # Filter out common conversational words (English, French, Arabic transliterated)
        stop_words = {
            # English
            'do', 'you', 'have', 'is', 'there', 'any', 'want', 'need', 'looking',
            'for', 'the', 'a', 'an', 'i', 'can', 'get', 'find', 'search', 'show',
            'me', 'please', 'would', 'like', 'are', 'does', 'it', 'of', 'to', 'in',
            # French articles/prepositions
            'la', 'le', 'les', 'un', 'une', 'des', 'du', 'de', 'et', 'en', 'au', 'aux',
            'ce', 'ces', 'son', 'sa', 'ses', 'mon', 'ma', 'mes', 'ton', 'ta', 'tes',
            'je', 'tu', 'il', 'elle', 'nous', 'vous', 'ils', 'elles', 'on',
            'qui', 'que', 'quoi', 'ou', 'avec', 'pour', 'par', 'sur', 'dans',
            # Arabic transliterated
            'hal', 'hel', 'ana', 'ayna', 'min', 'ila', 'fee', 'wa', 'aw'
        }
        words = [w.strip().lower() for w in query.split() if len(w.strip()) >= 2 and w.strip().lower() not in stop_words]
        logger.info(f"SEARCH: query='{query}', words={words}")

        # First try exact phrase match
        products = self.db.query(Product).filter(
            and_(
                base_filter,
                or_(
                    Product.item_name.ilike(f'%{query}%'),
                    Product.item_code.ilike(f'%{query}%')
                )
            )
        ).limit(limit).all()
        logger.info(f"SEARCH: exact phrase found {len(products)} products")

        # If no results or few results, try multi-word search
        if len(products) < limit and words:
            # Build filter for each word
            filters = []
            for word in words:
                filters.append(or_(
                    Product.item_name.ilike(f'{word}%'),
                    Product.item_name.ilike(f'% {word}%'),
                    Product.item_code.ilike(f'%{word}%')
                ))

            # Get products matching ANY word (fetch more to score properly)
            # With 30k+ items, need high limit to ensure best matches aren't missed
            all_matches = self.db.query(Product).filter(
                and_(base_filter, or_(*filters))
            ).limit(500).all()

            # Score products by how many keywords they match
            # First keyword gets MUCH higher weight (most important search term)
            scored = []
            for p in all_matches:
                name_lower = p.item_name.lower()
                score = 0
                for i, word in enumerate(words):
                    # First keyword: 100 pts, second: 20 pts, third: 10 pts
                    if i == 0:
                        weight = 100  # First keyword is MOST important
                    elif i == 1:
                        weight = 20
                    else:
                        weight = 10
                    if word in name_lower:
                        score += weight  # Word found in name
                        if name_lower.startswith(word) or f' {word}' in name_lower:
                            score += weight // 4  # Small bonus for word boundary
                scored.append((p, score))

            # Sort by score (highest first) and take only HIGH confidence matches
            scored.sort(key=lambda x: x[1], reverse=True)
            logger.info(f"SEARCH: top scored: {[(p.item_name[:30], s) for p, s in scored[:5]]}")

            # Only show products with score >= 100 (must match first keyword)
            # And limit to max 5 results for cleaner output
            high_confidence = [(p, s) for p, s in scored if s >= 100]
            if high_confidence:
                products = [p for p, s in high_confidence[:5]]
            else:
                # Fallback: show top 3 if no high confidence matches
                products = [p for p, s in scored[:3]]

        return [
            {
                'id': p.id,
                'item_code': p.item_code,
                'item_name': p.item_name,
                'price': p.price,
                'stock_quantity': p.stock_quantity
            }
            for p in products
        ]

    async def _show_product_list(self, customer_id: int, lang: str, page: int) -> str:
        offset = (page - 1) * PRODUCTS_PER_PAGE
        products = self.db.query(Product).filter(
            Product.stock_quantity > 0
        ).offset(offset).limit(PRODUCTS_PER_PAGE).all()

        total = self.db.query(func.count(Product.id)).filter(
            Product.stock_quantity > 0
        ).scalar()
        total_pages = (total + PRODUCTS_PER_PAGE - 1) // PRODUCTS_PER_PAGE

        product_list = [
            {
                'item_name': p.item_name,
                'price': p.price,
                'stock_quantity': p.stock_quantity
            }
            for p in products
        ]

        self._set_conversation_state(customer_id, 'browsing_products', {
            'page': page,
            'products': [p.id for p in products]
        })

        return ResponseTemplates.product_list(lang, product_list, page, total_pages)

    async def _quick_product_search(self, customer_id: int, query: str, lang: str) -> Optional[str]:
        # Try SQL ILIKE search first
        logger.info(f"QUICK SEARCH: query='{query}'")
        products = await self._search_products(query, 10)
        logger.info(f"QUICK SEARCH: found {len(products)} products")

        if products:
            self._set_conversation_state(customer_id, 'browsing_products', {
                'products': [p['id'] for p in products]
            })
            return ResponseTemplates.product_list(lang, products, 1, 1)

        # Fallback: Try CLIP text-to-text semantic search
        try:
            from services.clip_search import get_clip_service
            clip_service = get_clip_service()
            if clip_service and clip_service.text_embeddings:
                clip_results = clip_service.search_text_by_text(query, top_k=10)
                if clip_results and clip_results[0].get('score', 0) > 0.35:
                    logger.info(f"CLIP text search found: {clip_results[0]['score']:.3f}")
                    # Look up full product info from database
                    products = []
                    for r in clip_results[:10]:
                        product = self.db.query(Product).filter(
                            Product.item_code == r['sku']
                        ).first()
                        if product:
                            products.append({
                                'id': product.id,
                                'item_code': product.item_code,
                                'item_name': product.item_name,
                                'price': product.price,
                                'stock_quantity': product.stock_quantity
                            })
                    if products:
                        self._set_conversation_state(customer_id, 'browsing_products', {
                            'products': [p['id'] for p in products]
                        })
                        return ResponseTemplates.product_list(lang, products, 1, 1)
        except Exception as e:
            logger.warning(f"CLIP text search in quick_product_search failed: {e}")

        return None

    async def _display_products(self, customer_id: int, products: list, lang: str) -> str:
        self._set_conversation_state(customer_id, 'browsing_products', {
            'products': [p.get('id') for p in products[:10]]
        })
        return ResponseTemplates.product_list(lang, products[:10], 1, 1)

    async def _handle_product_selection(self, customer_id: int, message: str, lang: str) -> str:
        state = self.db.query(ConversationState).filter(
            ConversationState.customer_id == customer_id
        ).first()

        if not state or not state.data.get('products'):
            return ResponseTemplates.invalid_input(lang)

        try:
            selection = int(message.strip()) - 1
            product_ids = state.data['products']

            if 0 <= selection < len(product_ids):
                product = self.db.query(Product).get(product_ids[selection])
                if product:
                    customer = self.db.query(Customer).get(customer_id)

                    # Send product image if available
                    if product.image_url and customer:
                        try:
                            # Convert relative URL to absolute
                            image_url = product.image_url
                            if image_url.startswith('/'):
                                image_url = f"http://109.110.185.104{image_url}"
                            await proxsms.send_image(
                                customer.phone,
                                image_url,
                                f"📦 {product.item_name}\n💰 {product.price:,.0f} LBP"
                            )
                        except Exception as e:
                            logger.warning(f"Failed to send product image: {e}")

                    # Check if customer already has name and address
                    if customer and customer.name and customer.address:
                        # Customer has profile - ask for confirmation
                        self._set_conversation_state(customer_id, 'awaiting_order_confirm', {
                            'selected_product_id': product.id,
                            'product_name': product.item_name,
                            'price': product.price,
                            'quantity': 1
                        })
                        # Ask for confirmation
                        confirm_msg = {
                            'ar': f"📦 *{product.item_name}*\n💰 {product.price:,.0f} LBP\n\n👤 {customer.name}\n📍 {customer.address}\n\n✅ اكتب *1* للتأكيد\n❌ اكتب *0* للإلغاء",
                            'en': f"📦 *{product.item_name}*\n💰 {product.price:,.0f} LBP\n\n👤 {customer.name}\n📍 {customer.address}\n\n✅ Type *1* to confirm\n❌ Type *0* to cancel",
                            'fr': f"📦 *{product.item_name}*\n💰 {product.price:,.0f} LBP\n\n👤 {customer.name}\n📍 {customer.address}\n\n✅ Tapez *1* pour confirmer\n❌ Tapez *0* pour annuler"
                        }
                        return confirm_msg.get(lang, confirm_msg['en'])
                    else:
                        # Need to collect customer info
                        self._set_conversation_state(customer_id, 'awaiting_name', {
                            'selected_product_id': product.id,
                            'product_name': product.item_name,
                            'price': product.price,
                            'quantity': 1
                        })
                        return ResponseTemplates.ask_name(lang, product.item_name)

        except ValueError:
            pass

        return ResponseTemplates.invalid_input(lang)

    async def _handle_quantity_input(self, customer_id: int, message: str, lang: str) -> str:
        try:
            quantity = int(message.strip())
            if quantity <= 0:
                raise ValueError()

            state = self.db.query(ConversationState).filter(
                ConversationState.customer_id == customer_id
            ).first()

            if state and state.data.get('selected_product_id'):
                state.data['quantity'] = quantity
                state.state = 'awaiting_name'
                self.db.commit()

                return ResponseTemplates.ask_name(lang, state.data['product_name'])

        except ValueError:
            pass

        return {
            'ar': "الرجاء إدخال رقم صحيح للكمية",
            'en': "Please enter a valid number for quantity",
            'fr': "Veuillez entrer un nombre valide pour la quantite"
        }.get(lang, "Please enter a valid number for quantity")

    async def _handle_name_input(self, customer_id: int, message: str, lang: str) -> str:
        name = message.strip()
        if len(name) < 2:
            return {
                'ar': "الرجاء إدخال اسمك الكامل",
                'en': "Please enter your full name",
                'fr': "Veuillez entrer votre nom complet"
            }.get(lang, "Please enter your full name")

        customer = self.db.query(Customer).filter(
            Customer.id == customer_id
        ).first()
        if customer:
            customer.name = name
            self.db.commit()

        state = self.db.query(ConversationState).filter(
            ConversationState.customer_id == customer_id
        ).first()
        if state:
            state.data['customer_name'] = name
            state.state = 'awaiting_address'
            self.db.commit()

        return {
            'ar': f"شكرا {name}!\n\nالرجاء إدخال عنوانك للتوصيل:",
            'en': f"Thank you {name}!\n\nPlease enter your delivery address:",
            'fr': f"Merci {name}!\n\nVeuillez entrer votre adresse de livraison:"
        }.get(lang, f"Thank you {name}!\n\nPlease enter your delivery address:")

    async def _handle_address_input(self, customer_id: int, message: str, lang: str) -> str:
        address = message.strip()
        if len(address) < 5:
            return {
                'ar': "الرجاء إدخال عنوان صحيح",
                'en': "Please enter a valid address",
                'fr': "Veuillez entrer une adresse valide"
            }.get(lang, "Please enter a valid address")

        state = self.db.query(ConversationState).filter(
            ConversationState.customer_id == customer_id
        ).first()

        if not state or not state.data.get('selected_product_id'):
            return ResponseTemplates.invalid_input(lang)

        product = self.db.query(Product).get(state.data['selected_product_id'])
        quantity = state.data.get('quantity', 1)

        order = Order(
            customer_id=customer_id,
            order_number=f"ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{customer_id}",
            total_amount=product.price * quantity,
            status='pending',
            delivery_address=address,
            created_at=datetime.now()
        )
        self.db.add(order)
        self.db.commit()
        self.db.refresh(order)

        order_item = OrderItem(
            order_id=order.id,
            product_id=product.id,
            quantity=quantity,
            unit_price=product.price,
            total_price=product.price * quantity
        )
        self.db.add(order_item)
        self.db.commit()

        self._set_conversation_state(customer_id, 'idle')

        return ResponseTemplates.order_confirmation(lang, {
            'product_name': product.item_name,
            'customer_name': state.data.get('customer_name', ''),
            'customer_email': '',
            'customer_address': address,
            'quantity': quantity,
            'price': product.price
        })

    async def _handle_order_confirm(self, customer_id: int, message: str, lang: str) -> str:
        """Handle order confirmation (1 = confirm, 0 = cancel)"""
        msg = message.strip()

        state = self.db.query(ConversationState).filter(
            ConversationState.customer_id == customer_id
        ).first()

        if not state or not state.data.get('selected_product_id'):
            self._set_conversation_state(customer_id, 'idle')
            return ResponseTemplates.invalid_input(lang)

        if msg == '1':
            # Confirm order
            product = self.db.query(Product).get(state.data['selected_product_id'])
            customer = self.db.query(Customer).get(customer_id)
            quantity = state.data.get('quantity', 1)

            order = Order(
                customer_id=customer_id,
                order_number=f"ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{customer_id}",
                total_amount=product.price * quantity,
                status='pending',
                delivery_address=customer.address,
                created_at=datetime.now()
            )
            self.db.add(order)
            self.db.commit()
            self.db.refresh(order)

            order_item = OrderItem(
                order_id=order.id,
                product_id=product.id,
                quantity=quantity,
                unit_price=product.price,
                total_price=product.price * quantity
            )
            self.db.add(order_item)
            self.db.commit()

            self._set_conversation_state(customer_id, 'idle')

            return ResponseTemplates.order_confirmation(lang, {
                'product_name': product.item_name,
                'customer_name': customer.name,
                'customer_email': customer.email or '',
                'customer_address': customer.address,
                'quantity': quantity,
                'price': product.price
            })

        elif msg == '0':
            # Cancel
            self._set_conversation_state(customer_id, 'idle')
            cancel_msg = {
                'ar': "❌ تم إلغاء الطلب.\n\nيمكنك البحث عن منتج آخر.",
                'en': "❌ Order cancelled.\n\nYou can search for another product.",
                'fr': "❌ Commande annulée.\n\nVous pouvez rechercher un autre produit."
            }
            return cancel_msg.get(lang, cancel_msg['en'])

        else:
            # Invalid input
            invalid_msg = {
                'ar': "الرجاء كتابة *1* للتأكيد أو *0* للإلغاء",
                'en': "Please type *1* to confirm or *0* to cancel",
                'fr': "Veuillez taper *1* pour confirmer ou *0* pour annuler"
            }
            return invalid_msg.get(lang, invalid_msg['en'])

    async def _handle_with_ai(self, customer: Customer, message: str, lang: str) -> str:
        recent = self.db.query(Message).filter(
            Message.customer_id == customer.id
        ).order_by(Message.created_at.desc()).limit(5).all()

        recent_messages = [
            {'direction': m.direction, 'message': m.message}
            for m in reversed(recent)
        ]

        result = await claude_ai.process_message(
            customer.id,
            message,
            {'name': customer.name, 'balance': customer.balance},
            recent_messages
        )

        if result['success']:
            return result['message']

        return ResponseTemplates.invalid_input(lang)

    async def _handle_image_message(self, customer_id: int, attachment: str, lang: str) -> str:
        result = await claude_ai.analyze_image_and_search(
            attachment,
            self._search_products,
            lang
        )

        if result['success'] and result.get('products'):
            return await self._display_products(customer_id, result['products'], lang)

        return {
            'ar': "لم أتمكن من التعرف على المنتج في الصورة. جرب البحث بالاسم!",
            'en': "Couldn't identify the product in the image. Try searching by name!",
            'fr': "Je n'ai pas pu identifier le produit dans l'image. Essayez de chercher par nom!"
        }.get(lang, "Couldn't identify the product in the image. Try searching by name!")



# ============== SETTINGS ROUTES ==============

@app.post("/settings/change-password")
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...)
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    if current_password != ADMIN_PASS:
        return RedirectResponse(url="/settings?error=Current+password+is+incorrect", status_code=302)

    if new_password != confirm_password:
        return RedirectResponse(url="/settings?error=Passwords+do+not+match", status_code=302)

    if len(new_password) < 6:
        return RedirectResponse(url="/settings?error=Password+must+be+at+least+6+characters", status_code=302)

    # Update .env file
    env_file = '/opt/whatsbot/.env'
    with open(env_file, 'r') as f:
        content = f.read()

    import re
    content = re.sub(r'ADMIN_PASS=.*', f'ADMIN_PASS={new_password}', content)

    with open(env_file, 'w') as f:
        f.write(content)

    return RedirectResponse(url="/settings?password_changed=1", status_code=302)


@app.post("/settings/store-info")
async def save_store_info(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    form = await request.form()
    store_fields = [
        'store_name', 'store_address', 'store_phone', 'store_whatsapp',
        'store_instagram', 'store_website', 'store_location_url',
        'hours_monday', 'hours_tuesday', 'hours_wednesday', 'hours_thursday',
        'hours_friday', 'hours_saturday', 'hours_sunday'
    ]

    for key in store_fields:
        value = form.get(key, '')
        setting = db.query(BotSettings).filter(BotSettings.setting_key == key).first()
        if setting:
            setting.setting_value = value
            setting.updated_at = datetime.now()
        else:
            setting = BotSettings(setting_key=key, setting_value=value)
            db.add(setting)

    db.commit()
    return RedirectResponse(url="/settings?saved=1", status_code=302)


@app.post("/settings/api")
async def save_api_settings(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    form = await request.form()
    api_fields = [
        'brains_api_base', 'whatsapp_account_id', 'whatsapp_send_secret',
        'webhook_secret', 'anthropic_api_key', 'anthropic_model', 'anthropic_max_tokens'
    ]

    for key in api_fields:
        value = form.get(key, '')
        setting = db.query(BotSettings).filter(BotSettings.setting_key == key).first()
        if setting:
            setting.setting_value = value
            setting.updated_at = datetime.now()
        else:
            setting = BotSettings(setting_key=key, setting_value=value)
            db.add(setting)

    db.commit()
    return RedirectResponse(url="/settings?saved=1", status_code=302)


@app.post("/settings/system")
async def save_system_settings(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    form = await request.form()
    system_fields = ['timezone', 'currency', 'sync_interval']

    for key in system_fields:
        value = form.get(key, '')
        setting = db.query(BotSettings).filter(BotSettings.setting_key == key).first()
        if setting:
            setting.setting_value = value
            setting.updated_at = datetime.now()
        else:
            setting = BotSettings(setting_key=key, setting_value=value)
            db.add(setting)

    db.commit()
    return RedirectResponse(url="/settings?saved=1", status_code=302)


@app.post("/settings/messages")
async def save_message_settings(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    form = await request.form()
    message_fields = ['welcome_message_en', 'welcome_message_ar', 'out_of_stock_en', 'out_of_stock_ar']

    for key in message_fields:
        value = form.get(key, '')
        setting = db.query(BotSettings).filter(BotSettings.setting_key == key).first()
        if setting:
            setting.setting_value = value
            setting.updated_at = datetime.now()
        else:
            setting = BotSettings(setting_key=key, setting_value=value)
            db.add(setting)

    db.commit()
    return RedirectResponse(url="/settings?saved=1", status_code=302)

# ============== API ENDPOINTS ==============

@app.post("/webhook-whatsapp")
async def webhook_whatsapp(request: Request, db: Session = Depends(get_db)):
    """WhatsApp webhook endpoint"""
    try:
        form_data = await request.form()
        input_data = dict(form_data)

        # Always log webhook data to see what ProxSMS sends
        logger.info(f"WEBHOOK RAW DATA: {input_data}")

        if settings.WHATSAPP_WEBHOOK_SECRET:
            provided_secret = input_data.get('secret', '')
            if provided_secret != settings.WHATSAPP_WEBHOOK_SECRET:
                logger.warning("Invalid webhook secret provided")
                raise HTTPException(status_code=403, detail="Invalid secret")

        payload_type = input_data.get('type')
        if payload_type != 'whatsapp':
            logger.info(f"Ignoring non-WhatsApp payload type: {payload_type}")
            return JSONResponse({'status': 'ignored', 'reason': 'Not a WhatsApp message'})

        # Handle both nested 'data' object and 'data[key]' format (form-encoded)
        data = input_data.get('data', {})
        if isinstance(data, str):
            data = json.loads(data)

        # If data is empty, try data[key] format
        if not data or not isinstance(data, dict) or not data.get('phone'):
            data = {
                'phone': input_data.get('data[phone]'),
                'message': input_data.get('data[message]'),
                'attachment': input_data.get('data[attachment]'),
            }

        phone = data.get('phone')
        message = data.get('message')
        attachment = data.get('attachment')

        # ProxSMS sends "0" for no attachment - normalize to None
        if attachment in ('0', '', None):
            attachment = None

        # Log extracted values for debugging
        logger.info(f"WEBHOOK PARSED: phone={phone}, message={message[:50] if message else None}..., attachment={attachment}")

        if not phone:
            raise HTTPException(status_code=400, detail="Missing phone")

        if not message and not attachment:
            raise HTTPException(status_code=400, detail="Missing message")

        if not message and attachment:
            message = '[Image received]'

        processor = MessageProcessor(db)
        result = await processor.process_incoming_message(phone, message, attachment)

        if result['success']:
            return JSONResponse({
                'status': 'success',
                'customer_id': result['customer_id']
            })
        else:
            raise HTTPException(status_code=500, detail=result.get('error', 'Processing failed'))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Webhook exception: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "version": settings.BOT_VERSION}


@app.get("/api/license")
async def license_info():
    """License info endpoint"""
    return license_validator.get_license_info()


@app.post("/api/sync/products")
async def api_sync_products(db: Session = Depends(get_db)):
    """Sync products from Brains ERP"""
    result = await brains_api.sync_products(db, Product)
    return result


@app.post("/api/sync/accounts")
async def api_sync_accounts(db: Session = Depends(get_db)):
    """Sync accounts from Brains ERP"""
    result = await brains_api.sync_accounts(db, Customer)
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
