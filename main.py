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
from sqlalchemy import create_engine, or_, and_, func, desc, text
from sqlalchemy.orm import sessionmaker, Session

from config import settings
from models import Base, Customer, Product, Order, OrderItem, Message, ConversationState, CustomQA, Settings as BotSettings, School, ProductFamily, ProductAttribute
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

# Mount static files for product images
IMAGES_DIR = os.path.join(os.path.dirname(__file__), 'images', 'products', 'SfitemPicture')
if os.path.exists(IMAGES_DIR):
    app.mount("/product-images", StaticFiles(directory=IMAGES_DIR), name="product-images")

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
async def customers_page(request: Request, page: int = 1, per_page: int = 50, search: str = None, db: Session = Depends(get_db)):
    """Customers management page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # Validate per_page (allow 50, 100, 200)
    if per_page not in [50, 100, 200]:
        per_page = 50
    query = db.query(Customer)

    if search:
        query = query.filter(or_(
            Customer.phone.ilike(f'%{search}%'),
            Customer.name.ilike(f'%{search}%')
        ))

    total = query.count()
    customers = query.order_by(desc(Customer.last_message_at)).offset((page-1)*per_page).limit(per_page).all()
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
        "orders": orders,
        "now": datetime.now()
    })


@app.post("/customers/{customer_id}/block")
async def block_customer(customer_id: int, db: Session = Depends(get_db)):
    """Block/unblock customer"""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if customer:
        customer.is_blocked = not customer.is_blocked
        db.commit()
    return RedirectResponse(url=f"/customers/{customer_id}", status_code=302)


@app.post("/customers/{customer_id}/reply")
async def reply_to_customer(
    customer_id: int,
    message: str = Form(...),
    db: Session = Depends(get_db)
):
    """Send reply to customer via WhatsApp"""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        return JSONResponse({"success": False, "error": "Customer not found"})

    # Check minimum message length (ProxSMS requires at least 5 chars)
    if len(message.strip()) < 5:
        return JSONResponse({"success": False, "error": "Message must be at least 5 characters"})

    try:
        # Send message via ProxSMS
        result = await proxsms.send_message(customer.phone, message)

        if result.get('success'):
            # Save message to database
            new_message = Message(
                customer_id=customer_id,
                message=message,
                direction='SENT'
            )
            db.add(new_message)
            db.commit()
            return JSONResponse({"success": True})
        else:
            return JSONResponse({"success": False, "error": result.get('error', 'Failed to send')})
    except Exception as e:
        logger.error(f"Reply error: {e}")
        return JSONResponse({"success": False, "error": str(e)})


@app.get("/customers/{customer_id}/messages")
async def get_customer_messages(
    customer_id: int,
    db: Session = Depends(get_db)
):
    """Get messages for live polling"""
    messages = db.query(Message).filter(Message.customer_id == customer_id).order_by(Message.created_at).all()
    return JSONResponse({
        "messages": [
            {
                "id": m.id,
                "message": m.message or "",
                "direction": m.direction,
                "time": m.created_at.strftime("%d/%m %H:%M") if m.created_at else ""
            }
            for m in messages
        ]
    })


@app.post("/customers/{customer_id}/toggle-bot")
async def toggle_bot_for_customer(
    customer_id: int,
    db: Session = Depends(get_db)
):
    """Toggle bot auto-reply for customer (5 hour pause)"""
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        return JSONResponse({"success": False, "error": "Customer not found"})

    now = datetime.now()

    if customer.bot_paused_until and customer.bot_paused_until > now:
        # Bot is paused - resume it
        customer.bot_paused_until = None
        db.commit()
        return JSONResponse({"success": True, "paused": False, "message": "Bot resumed"})
    else:
        # Pause bot for 5 hours
        customer.bot_paused_until = now + timedelta(hours=5)
        db.commit()
        return JSONResponse({"success": True, "paused": True, "message": "Bot paused for 5 hours"})


@app.post("/customers/{customer_id}/delete-chat")
async def delete_customer_chat(
    customer_id: int,
    db: Session = Depends(get_db)
):
    """Delete all messages for a customer"""
    try:
        db.query(Message).filter(Message.customer_id == customer_id).delete()
        db.commit()
        return JSONResponse({"success": True})
    except Exception as e:
        logger.error(f"Delete chat error: {e}")
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/customers/{customer_id}/delete")
async def delete_customer(
    customer_id: int,
    db: Session = Depends(get_db)
):
    """Delete customer and all their data"""
    try:
        # Delete messages
        db.query(Message).filter(Message.customer_id == customer_id).delete()
        # Delete conversation state
        db.query(ConversationState).filter(ConversationState.customer_id == customer_id).delete()
        # Delete order items for customer's orders
        order_ids = [o.id for o in db.query(Order).filter(Order.customer_id == customer_id).all()]
        if order_ids:
            db.query(OrderItem).filter(OrderItem.order_id.in_(order_ids)).delete(synchronize_session=False)
        # Delete orders
        db.query(Order).filter(Order.customer_id == customer_id).delete()
        # Delete customer
        db.query(Customer).filter(Customer.id == customer_id).delete()
        db.commit()
        return JSONResponse({"success": True})
    except Exception as e:
        logger.error(f"Delete customer error: {e}")
        return JSONResponse({"success": False, "error": str(e)})


@app.get("/products", response_class=HTMLResponse)
async def products_page(request: Request, page: int = 1, per_page: int = 50, search: str = None, db: Session = Depends(get_db)):
    """Products management page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # Validate per_page (allow 50, 100, 200)
    if per_page not in [50, 100, 200]:
        per_page = 50
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
        "per_page": per_page,
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


@app.get("/qa/{qa_id}/edit", response_class=HTMLResponse)
async def edit_qa_page(qa_id: int, request: Request, db: Session = Depends(get_db)):
    """Edit Q&A page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    qa = db.query(CustomQA).filter(CustomQA.id == qa_id).first()
    if not qa:
        return RedirectResponse(url="/qa", status_code=302)

    return templates.TemplateResponse("qa_edit.html", {
        "request": request,
        "user": user,
        "qa": qa
    })


@app.post("/qa/{qa_id}/edit")
async def update_qa(
    qa_id: int,
    keywords: str = Form(...),
    answer_en: str = Form(...),
    answer_ar: str = Form(None),
    answer_fr: str = Form(None),
    category: str = Form(None),
    priority: int = Form(0),
    db: Session = Depends(get_db)
):
    """Update Q&A entry"""
    qa = db.query(CustomQA).filter(CustomQA.id == qa_id).first()
    if qa:
        qa.keywords = keywords
        qa.answer_en = answer_en
        qa.answer_ar = answer_ar
        qa.answer_fr = answer_fr
        qa.category = category
        qa.priority = priority
        db.commit()
    return RedirectResponse(url="/qa", status_code=302)


# ==================== PRODUCT FAMILIES ROUTES ====================

@app.get("/families", response_class=HTMLResponse)
async def families_page(request: Request, db: Session = Depends(get_db)):
    """Product families management page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    families = db.query(ProductFamily).order_by(ProductFamily.name).all()

    # Add product count for each family
    for family in families:
        family.product_count = db.query(Product).filter(Product.family_id == family.id).count()

    return templates.TemplateResponse("families.html", {
        "request": request,
        "user": user,
        "families": families,
        "message": request.query_params.get("message"),
        "message_type": request.query_params.get("type", "success")
    })


@app.post("/families/add")
async def add_family(
    request: Request,
    name: str = Form(...),
    name_ar: str = Form(None),
    keywords: str = Form(None),
    has_sizes: bool = Form(False),
    has_colors: bool = Form(False),
    selection_order: str = Form("size_first"),
    db: Session = Depends(get_db)
):
    """Add new product family"""
    family = ProductFamily(
        name=name,
        name_ar=name_ar,
        keywords=keywords,
        has_sizes=has_sizes,
        has_colors=has_colors,
        selection_order=selection_order,
        is_active=True
    )
    db.add(family)
    db.commit()
    return RedirectResponse(url=f"/families?message=Family '{name}' created&type=success", status_code=302)


@app.get("/families/{family_id}/edit", response_class=HTMLResponse)
async def edit_family_page(family_id: int, request: Request, db: Session = Depends(get_db)):
    """Edit family page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    family = db.query(ProductFamily).filter(ProductFamily.id == family_id).first()
    if not family:
        return RedirectResponse(url="/families", status_code=302)

    return templates.TemplateResponse("family_edit.html", {
        "request": request,
        "user": user,
        "family": family
    })


@app.post("/families/{family_id}/edit")
async def update_family(
    family_id: int,
    name: str = Form(...),
    name_ar: str = Form(None),
    keywords: str = Form(None),
    has_sizes: bool = Form(False),
    has_colors: bool = Form(False),
    selection_order: str = Form("size_first"),
    db: Session = Depends(get_db)
):
    """Update product family"""
    family = db.query(ProductFamily).filter(ProductFamily.id == family_id).first()
    if family:
        family.name = name
        family.name_ar = name_ar
        family.keywords = keywords
        family.has_sizes = has_sizes
        family.has_colors = has_colors
        family.selection_order = selection_order
        db.commit()
    return RedirectResponse(url="/families", status_code=302)


@app.post("/families/{family_id}/toggle")
async def toggle_family(family_id: int, db: Session = Depends(get_db)):
    """Toggle family active status"""
    family = db.query(ProductFamily).filter(ProductFamily.id == family_id).first()
    if family:
        family.is_active = not family.is_active
        db.commit()
    return RedirectResponse(url="/families", status_code=302)


@app.post("/families/{family_id}/delete")
async def delete_family(family_id: int, db: Session = Depends(get_db)):
    """Delete family and unlink products"""
    family = db.query(ProductFamily).filter(ProductFamily.id == family_id).first()
    if family:
        # Unlink products
        db.query(Product).filter(Product.family_id == family_id).update({
            Product.family_id: None,
            Product.variant_size: None,
            Product.variant_color: None
        })
        db.delete(family)
        db.commit()
    return RedirectResponse(url="/families", status_code=302)


@app.get("/families/{family_id}/products", response_class=HTMLResponse)
async def family_products_page(family_id: int, request: Request, db: Session = Depends(get_db)):
    """Manage products in a family"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    family = db.query(ProductFamily).filter(ProductFamily.id == family_id).first()
    if not family:
        return RedirectResponse(url="/families", status_code=302)

    products = db.query(Product).filter(Product.family_id == family_id).order_by(Product.item_name).all()

    # Calculate unique sizes and colors
    sizes = {}
    colors = {}
    for p in products:
        if p.variant_size:
            sizes[p.variant_size] = sizes.get(p.variant_size, 0) + 1
        if p.variant_color:
            colors[p.variant_color] = colors.get(p.variant_color, 0) + 1

    return templates.TemplateResponse("family_products.html", {
        "request": request,
        "user": user,
        "family": family,
        "products": products,
        "sizes": sizes,
        "colors": colors,
        "message": request.query_params.get("message"),
        "message_type": request.query_params.get("type", "success")
    })


@app.get("/families/{family_id}/search-products")
async def search_products_for_family(family_id: int, q: str = Query(...), db: Session = Depends(get_db)):
    """Search products to add to family"""
    products = db.query(Product).filter(
        or_(
            Product.item_name.ilike(f"%{q}%"),
            Product.item_code.ilike(f"%{q}%")
        )
    ).limit(20).all()

    return {"products": [
        {
            "id": p.id,
            "item_name": p.item_name,
            "item_code": p.item_code,
            "stock_quantity": p.stock_quantity,
            "family_id": p.family_id
        } for p in products
    ]}


@app.post("/families/{family_id}/products/add")
async def add_product_to_family(
    family_id: int,
    product_id: int = Form(...),
    db: Session = Depends(get_db)
):
    """Add product to family"""
    product = db.query(Product).filter(Product.id == product_id).first()
    if product:
        product.family_id = family_id
        db.commit()
    return RedirectResponse(url=f"/families/{family_id}/products", status_code=302)


@app.post("/families/{family_id}/products/{product_id}/remove")
async def remove_product_from_family(family_id: int, product_id: int, db: Session = Depends(get_db)):
    """Remove product from family"""
    product = db.query(Product).filter(Product.id == product_id).first()
    if product:
        product.family_id = None
        product.variant_size = None
        product.variant_color = None
        db.commit()
    return RedirectResponse(url=f"/families/{family_id}/products", status_code=302)


@app.post("/families/{family_id}/products/update-attributes")
async def update_product_attributes(
    family_id: int,
    product_id: int = Form(...),
    variant_size: str = Form(None),
    variant_color: str = Form(None),
    db: Session = Depends(get_db)
):
    """Update product size/color attributes"""
    product = db.query(Product).filter(Product.id == product_id).first()
    if product:
        product.variant_size = variant_size.strip() if variant_size else None
        product.variant_color = variant_color.strip() if variant_color else None
        db.commit()
    return RedirectResponse(url=f"/families/{family_id}/products", status_code=302)


@app.get("/families/{family_id}/auto-extract")
async def auto_extract_attributes(family_id: int, request: Request, db: Session = Depends(get_db)):
    """Auto-extract size and color from product names"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    family = db.query(ProductFamily).filter(ProductFamily.id == family_id).first()
    if not family:
        return RedirectResponse(url="/families", status_code=302)

    products = db.query(Product).filter(Product.family_id == family_id).all()

    # Common size patterns
    size_patterns = [
        r'(\d+[xX]\d+(?:cm|mm|in)?)',  # 50x70cm, 50X70
        r'(\d+(?:cm|mm|in))',  # 50cm
        r'(A\d)',  # A4, A3
        r'(\d+x\d+)',  # 50x70
    ]

    # Common colors (expandable)
    known_colors = [
        'Red', 'Blue', 'Green', 'Yellow', 'Orange', 'Purple', 'Pink', 'Black', 'White',
        'Gold', 'Silver', 'Bronze', 'Brown', 'Grey', 'Gray', 'Beige', 'Navy', 'Indigo',
        'Violet', 'Cyan', 'Magenta', 'Turquoise', 'Cream', 'Ivory', 'Coral', 'Salmon',
        'Maroon', 'Burgundy', 'Olive', 'Teal', 'Aqua', 'Lime', 'Sky Blue', 'Dark Red',
        'Light Blue', 'Dark Blue', 'Light Green', 'Dark Green', 'Coffee', 'Gold Yellow',
        'Metallic', 'Kraft', 'Brillant', 'Neon', 'Pastel', 'Matte', 'Glossy', 'Transparent'
    ]

    updated = 0
    for product in products:
        name = product.item_name

        # Extract size
        if family.has_sizes:
            for pattern in size_patterns:
                match = re.search(pattern, name, re.IGNORECASE)
                if match:
                    product.variant_size = match.group(1)
                    break

        # Extract color
        if family.has_colors:
            name_lower = name.lower()
            for color in known_colors:
                if color.lower() in name_lower:
                    product.variant_color = color
                    break

        updated += 1

    db.commit()

    return RedirectResponse(
        url=f"/families/{family_id}/products?message=Extracted attributes for {updated} products&type=success",
        status_code=302
    )


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
        license_result = await license_validator.validate()
        license_basic = license_validator.get_license_info()
        license_info = {
            "license_key": license_basic.get("license_key", "Not registered yet"),
            "license_status": "Active" if license_result.get("valid") else "Invalid",
            "license_type": "Paid" if license_result.get("is_paid") else ("Trial" if license_result.get("is_trial") else "Unknown"),
            "license_expiry": license_result.get("expires_at", ""),
            "days_left": license_result.get("days_left", 0),
            "registered_domain": license_basic.get("domain", ""),
            "customer": license_result.get("data", {}).get("customer", "")
        }
    except Exception as e:
        logger.error(f"License error: {e}")
        license_info = {
            "license_key": "Error loading license",
            "license_status": "Unknown",
            "license_type": "Unknown",
            "license_expiry": "",
            "days_left": 0,
            "registered_domain": "",
            "customer": ""
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

    # Class-level cache for settings (shared across instances)
    _settings_cache = {}
    _settings_cache_time = 0
    _settings_cache_ttl = 300  # 5 minutes

    def __init__(self, db: Session):
        self.db = db

    def _get_cached_settings(self) -> dict:
        """Get all bot settings with caching"""
        import time
        now = time.time()
        if now - MessageProcessor._settings_cache_time < MessageProcessor._settings_cache_ttl:
            return MessageProcessor._settings_cache

        # Refresh cache
        all_settings = self.db.query(BotSettings).all()
        MessageProcessor._settings_cache = {s.setting_key: s.setting_value or '' for s in all_settings}
        MessageProcessor._settings_cache_time = now
        return MessageProcessor._settings_cache

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

            # Check if bot is paused for this customer (agent takeover)
            if customer.bot_paused_until and customer.bot_paused_until > datetime.now():
                logger.info(f"Bot paused for customer {phone} until {customer.bot_paused_until}")
                # Still save the message but don't auto-reply
                self._save_message(customer.id, message, 'RECEIVED', attachment)
                customer.last_message_at = datetime.now()
                self.db.commit()
                return {'success': True, 'customer_id': customer.id, 'bot_paused': True}

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
        # Normalize phone: remove non-digits, handle Lebanon country code
        digits = re.sub(r'[^0-9]', '', phone)

        # Handle Lebanon format: +961XXXXXXX -> 0XXXXXXX
        if digits.startswith('961') and len(digits) >= 10:
            # +9613080203 -> 03080203 (add leading 0)
            local_number = '0' + digits[3:]
        elif digits.startswith('0'):
            local_number = digits
        else:
            local_number = digits

        # Search by last 7 digits (core number without prefix)
        search_digits = re.sub(r'[^0-9]', '', local_number)[-7:]

        customer = self.db.query(Customer).filter(
            Customer.phone.contains(search_digits)
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

        # Check for order inquiry FIRST (even before greeting)
        # This handles "hello my order", "hi cancel order", etc.
        if self._is_order_inquiry(message_lower):
            # Check if it's a cancel request
            cancel_keywords = ['cancel', 'الغاء', 'elghe', 'annuler']
            is_cancel = any(k in message_lower for k in cancel_keywords)
            return self._handle_order_inquiry(customer, lang, cancel_mode=is_cancel)

        # Check for store info questions (hours, location, etc.) before greeting
        store_response = self._check_store_info_questions(message, lang)
        if store_response:
            return store_response

        # Check for Q&A before greeting (handles "hi do you have delivery?" etc.)
        qa_response = self._check_custom_qa(message, lang)
        if qa_response:
            return qa_response

        # Now check for pure greetings (only if no question was detected)
        if self._is_greeting(message_lower):
            self._set_conversation_state(customer.id, 'idle')
            # Build personalized greeting for returning customers
            greeting = ""
            if customer.name:
                # Personalized greeting for known customers
                greeting = self._get_personalized_greeting(customer.name, lang)

            # Check for custom welcome message from database settings
            custom_welcome = self._get_custom_welcome_message(lang)
            if custom_welcome:
                if greeting:
                    return f"{greeting}\n\n{custom_welcome}"
                return custom_welcome

            # Fall back to template
            return ResponseTemplates.welcome(lang, customer.name)

        if self._is_help_request(message_lower):
            return ResponseTemplates.help_message(lang)

        # Check for website category redirect (books, toys, pens, schools, etc.)
        website_redirect = self._check_website_redirect(message, lang)
        if website_redirect:
            self._set_conversation_state(customer.id, 'idle')
            return website_redirect

        if self._is_product_list_request(message_lower):
            return await self._show_product_list(customer.id, lang, 1)

        if self._is_balance_inquiry(message_lower):
            return self._handle_balance_inquiry(customer, lang)

        if state in ['browsing_products', 'awaiting_product_selection']:
            # If message is a number, handle product selection
            # Otherwise, treat it as a new search
            if message.strip().isdigit():
                return await self._handle_product_selection(customer.id, message, lang)
            # Not a number - fall through to search

        if state == 'selecting_family_size':
            if message.strip().isdigit():
                return await self._handle_family_size_selection(customer.id, message, lang)
            # Not a number - fall through to search

        if state == 'selecting_family_color':
            if message.strip().isdigit():
                return await self._handle_family_color_selection(customer.id, message, lang)
            # Not a number - fall through to search

        if state == 'awaiting_product_confirm':
            return await self._handle_product_confirm(customer.id, message, lang)

        if state == 'awaiting_address_choice':
            return await self._handle_address_choice(customer.id, message, lang)

        if state == 'awaiting_quantity':
            return await self._handle_quantity_input(customer.id, message, lang)

        if state == 'awaiting_name':
            return await self._handle_name_input(customer.id, message, lang)

        if state == 'awaiting_address':
            return await self._handle_address_input(customer.id, message, lang)

        if state == 'awaiting_order_confirm':
            return await self._handle_order_confirm(customer.id, message, lang)

        if state == 'awaiting_order_cancel':
            return self._handle_order_cancel_input(customer, message, lang)

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
        # Only treat SHORT messages as greetings
        # If message contains a question or is long, don't treat as greeting
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

        # If message has more than 3 words, it's probably a question, not just a greeting
        if len(words) > 3:
            return False

        # If message contains question indicators, it's a question not a greeting
        question_indicators = ['?', 'what', 'when', 'where', 'how', 'time', 'open', 'close', 'price', 'cost', 'have', 'do you']
        message_lower = message.lower()
        if any(q in message_lower for q in question_indicators):
            return False

        # Check for whole words only (not substrings like "hi" in "chimie")
        # Also check the whole message for Arabic greetings (might not split well)
        return any(g in words for g in greetings) or any(g in message for g in ['مرحبا', 'سلام', 'اهلا', 'هلا'])

    def _get_custom_welcome_message(self, lang: str) -> Optional[str]:
        """Get custom welcome message from database settings (cached)"""
        lang_key_map = {
            'en': 'welcome_message_en',
            'ar': 'welcome_message_ar',
            'fr': 'welcome_message_fr'
        }
        key = lang_key_map.get(lang, 'welcome_message_en')
        cached_settings = self._get_cached_settings()
        value = cached_settings.get(key, '')
        if value and value.strip():
            return value.strip()
        return None

    def _get_personalized_greeting(self, customer_name: str, lang: str) -> str:
        """Get personalized greeting for returning customers"""
        greetings = {
            'ar': f"مرحباً بعودتك {customer_name}! 👋",
            'en': f"Welcome back {customer_name}! 👋",
            'fr': f"Bon retour {customer_name}! 👋"
        }
        return greetings.get(lang, greetings['en'])

    def _is_help_request(self, message: str) -> bool:
        # Check for whole words only
        help_words = ['help', 'aide', 'mosaada']
        words = message.lower().split()
        return any(w in words for w in help_words)

    def _check_website_redirect(self, message: str, lang: str) -> Optional[str]:
        """Check if message matches a category and return website link"""
        message_lower = message.lower().strip()
        base_url = "https://store.libmemoires.com"

        # School-specific keywords (check FIRST - more specific)
        school_mappings = {
            'sscc': '/college-saint-coeur-kfarhbab',
            'saint coeur': '/college-saint-coeur-kfarhbab',
            'kfarhbab': '/college-saint-coeur-kfarhbab',
            'كفرحباب': '/college-saint-coeur-kfarhbab',
            'franco libanais': '/lycee-franco-libanais-nahr-ibrahim',
            'nahr ibrahim': '/lycee-franco-libanais-nahr-ibrahim',
            'central college': '/central-college-jounieh',
            'libano allemand': '/lycee-libano-allemand-jounieh',
            'allemand jounieh': '/lycee-libano-allemand-jounieh',
            'antonine': '/antonine-sisters-school-ghazir',
            'ghazir': '/antonine-sisters-school-ghazir',
            'sja': '/ecole-sja-besan%C3%A7on-kfour',
            'besancon': '/ecole-sja-besan%C3%A7on-kfour',
            'kfour': '/ecole-sja-besan%C3%A7on-kfour',
            'saint francois': '/ecole-saint-francois',
        }

        # Check for school keywords first
        for keyword, path in school_mappings.items():
            if keyword in message_lower:
                link = f"{base_url}{path}"
                return self._format_redirect_response(lang, 'school_books', link)

        # Category mappings (order matters - more specific first)
        category_mappings = [
            # School books (general)
            (['school book', 'school books', 'livre scolaire', 'livres scolaires', 'كتب مدرسية', 'كتاب مدرسي'], '/books', 'school_books'),

            # General books
            (['book', 'books', 'livre', 'livres', 'كتاب', 'كتب', 'reading', 'novel', 'story'], '/books-2', 'books'),

            # Toys
            (['toy', 'toys', 'jouet', 'jouets', 'لعبة', 'العاب', 'game', 'games', 'jeu', 'jeux', 'puzzle', 'plush', 'squishmallow', 'lego', 'barbie', 'doll', 'car', 'truck', 'robot', 'dinosaur', 'action figure'], '/toys-gadgets', 'toys'),

            # Writing instruments / Pens
            (['pen', 'pens', 'stylo', 'stylos', 'قلم', 'اقلام', 'pencil', 'pencils', 'crayon', 'crayons', 'marker', 'markers', 'highlighter'], '/writing-instruments', 'pens'),

            # Stationery
            (['stationery', 'stationary', 'fourniture', 'fournitures', 'قرطاسية', 'office', 'bureau'], '/stationary-2', 'stationery'),

            # Notebooks / Paper
            (['notebook', 'notebooks', 'cahier', 'cahiers', 'دفتر', 'دفاتر', 'paper', 'papier', 'ورق'], '/notebook', 'notebooks'),

            # Coloring / Art
            (['color', 'coloring', 'coloriage', 'تلوين', 'paint', 'painting', 'peinture', 'art', 'craft', 'bricolage'], '/coloring', 'coloring'),

            # Bags / Backpacks
            (['bag', 'bags', 'backpack', 'sac', 'شنطة', 'حقيبة', 'kipling', 'eastpak', 'polo'], '/stationary-2', 'bags'),

            # Gadgets
            (['gadget', 'gadgets', 'electronic', 'tech', 'اكسسوارات'], '/gadgets', 'gadgets'),

            # Frames & Albums
            (['frame', 'frames', 'album', 'albums', 'cadre', 'photo', 'صورة', 'اطار'], '/frames-albums', 'frames'),

            # Gift Items
            (['gift', 'gifts', 'cadeau', 'cadeaux', 'هدية', 'هدايا', 'present'], '/gift-items', 'gifts'),

            # Board Games
            (['board game', 'board games', 'jeu de société', 'العاب طاولة'], '/board-games-2', 'board_games'),

            # Puzzles
            (['puzzle', 'puzzles', 'بازل'], '/puzzle', 'puzzles'),

            # Educational
            (['educational', 'educatif', 'تعليمي', 'learning'], '/educational-games', 'educational'),

            # Calculator
            (['calculator', 'calculatrice', 'آلة حاسبة'], '/calculator-computer-accessories', 'calculator'),

            # Eraser / Sharpener
            (['eraser', 'gomme', 'ممحاة', 'sharpener', 'taille', 'براية'], '/eraser', 'eraser'),

            # Scissors
            (['scissors', 'ciseaux', 'مقص'], '/scissors-2', 'scissors'),

            # Glue / Tape
            (['glue', 'colle', 'صمغ', 'tape', 'scotch', 'لاصق', 'adhesive'], '/glue-2', 'glue'),

            # Ruler / Geometry
            (['ruler', 'règle', 'مسطرة', 'compass', 'geometry', 'هندسة'], '/ruler', 'ruler'),

            # Agenda / Diary
            (['agenda', 'diary', 'planner', 'مفكرة'], '/agenda-2', 'agenda'),
        ]

        # Check category mappings
        for keywords, path, category in category_mappings:
            for keyword in keywords:
                if keyword in message_lower:
                    link = f"{base_url}{path}"
                    return self._format_redirect_response(lang, category, link)

        # Fallback: redirect ALL product-related queries to website homepage
        # If message looks like a product query (not a greeting, not a command), redirect to browse
        if len(message_lower) > 2 and not self._is_greeting(message) and not self._is_help_request(message_lower):
            link = f"{base_url}"
            return self._format_redirect_response(lang, 'browse', link)

    def _format_redirect_response(self, lang: str, category: str, link: str) -> str:
        """Format the redirect response message"""
        category_names = {
            'school_books': {'en': 'School Books', 'ar': 'الكتب المدرسية', 'fr': 'Livres Scolaires'},
            'books': {'en': 'Books', 'ar': 'الكتب', 'fr': 'Livres'},
            'toys': {'en': 'Toys & Games', 'ar': 'الألعاب', 'fr': 'Jouets'},
            'pens': {'en': 'Writing Instruments', 'ar': 'أدوات الكتابة', 'fr': 'Instruments d\'écriture'},
            'stationery': {'en': 'Stationery', 'ar': 'القرطاسية', 'fr': 'Papeterie'},
            'notebooks': {'en': 'Notebooks & Paper', 'ar': 'الدفاتر والورق', 'fr': 'Cahiers et Papier'},
            'coloring': {'en': 'Coloring & Art', 'ar': 'التلوين والفن', 'fr': 'Coloriage et Art'},
            'bags': {'en': 'Bags & Backpacks', 'ar': 'الحقائب', 'fr': 'Sacs'},
            'gadgets': {'en': 'Gadgets', 'ar': 'الأجهزة', 'fr': 'Gadgets'},
            'frames': {'en': 'Frames & Albums', 'ar': 'الإطارات والألبومات', 'fr': 'Cadres et Albums'},
            'gifts': {'en': 'Gift Items', 'ar': 'الهدايا', 'fr': 'Cadeaux'},
            'board_games': {'en': 'Board Games', 'ar': 'ألعاب الطاولة', 'fr': 'Jeux de Société'},
            'puzzles': {'en': 'Puzzles', 'ar': 'البازل', 'fr': 'Puzzles'},
            'educational': {'en': 'Educational Games', 'ar': 'الألعاب التعليمية', 'fr': 'Jeux Éducatifs'},
            'calculator': {'en': 'Calculators', 'ar': 'الآلات الحاسبة', 'fr': 'Calculatrices'},
            'eraser': {'en': 'Erasers & Sharpeners', 'ar': 'الممحاة والبراية', 'fr': 'Gommes et Taille-crayons'},
            'scissors': {'en': 'Scissors', 'ar': 'المقصات', 'fr': 'Ciseaux'},
            'glue': {'en': 'Glue & Tape', 'ar': 'الصمغ واللاصق', 'fr': 'Colle et Ruban'},
            'ruler': {'en': 'Rulers & Geometry', 'ar': 'المساطر والهندسة', 'fr': 'Règles et Géométrie'},
            'agenda': {'en': 'Agenda & Diaries', 'ar': 'المفكرات', 'fr': 'Agendas'},
            'browse': {'en': 'Our Products', 'ar': 'منتجاتنا', 'fr': 'Nos Produits'},
        }

        cat_name = category_names.get(category, {}).get(lang, category_names.get(category, {}).get('en', category))

        messages = {
            'en': f"📦 *{cat_name}*\n\nYou can browse and order from our website:\n\n🔗 {link}\n\n✨ Free delivery on orders above $60!",
            'ar': f"📦 *{cat_name}*\n\nيمكنك التصفح والطلب من موقعنا:\n\n🔗 {link}\n\n✨ توصيل مجاني للطلبات فوق 60$!",
            'fr': f"📦 *{cat_name}*\n\nVous pouvez parcourir et commander sur notre site:\n\n🔗 {link}\n\n✨ Livraison gratuite pour les commandes de plus de 60$!"
        }

        return messages.get(lang, messages['en'])

    def _is_product_list_request(self, message: str) -> bool:
        # Check for whole words only
        keywords = ['products', 'produits', 'list', 'catalogue', 'catalog']
        words = message.lower().split()
        return any(k in words for k in keywords)

    def _is_balance_inquiry(self, message: str) -> bool:
        # Check for whole words only
        keywords = ['balance', 'account', 'solde', 'compte']
        words = message.lower().split()
        return any(k in words for k in keywords)

    def _is_order_inquiry(self, message: str) -> bool:
        """Check if message is asking about order status or actions"""
        message_lower = message.lower()
        # Keywords that indicate order inquiry or action
        order_keywords = [
            'my order', 'order status', 'where is my order', 'track order',
            'order tracking', 'check order', 'my orders', 'pending order',
            'cancel order', 'cancel my order', 'change order', 'change my order',
            'modify order', 'modify my order', 'update order', 'update my order',
            'order cancel', 'order change', 'order modify', 'order update',
            'طلبي', 'طلبيتي', 'وين طلبي', 'تتبع الطلب', 'الغاء الطلب', 'تعديل الطلب',
            'talabi', 'talabiteh', 'talabeh', 'talabiti', 'wein talabi',
            'cancel talabi', 'change talabi', 'badde elghe talabi',
            'ma commande', 'mes commandes', 'suivi commande', 'annuler commande'
        ]
        for keyword in order_keywords:
            if keyword in message_lower:
                return True
        # Also check if "order" with action/possessive words
        words = message_lower.split()
        if 'order' in words and any(w in words for w in ['my', 'check', 'status', 'where', 'track', 'cancel', 'change', 'modify', 'update']):
            return True
        return False

    def _handle_order_inquiry(self, customer: Customer, lang: str, cancel_mode: bool = False) -> str:
        """Show customer's orders with status, or handle cancellation"""
        orders = self.db.query(Order).filter(
            Order.customer_id == customer.id
        ).order_by(Order.created_at.desc()).limit(5).all()

        if not orders:
            messages = {
                'en': "📦 You don't have any orders yet.\n\nWould you like to browse our products? Just type what you're looking for!",
                'ar': "📦 ليس لديك أي طلبات بعد.\n\nهل تريد تصفح منتجاتنا؟ اكتب ما تبحث عنه!",
                'fr': "📦 Vous n'avez pas encore de commandes.\n\nVoulez-vous parcourir nos produits? Tapez ce que vous cherchez!"
            }
            return messages.get(lang, messages['en'])

        # Status translations and emojis
        status_display = {
            'pending': {'en': '⏳ Pending', 'ar': '⏳ قيد الانتظار', 'fr': '⏳ En attente'},
            'confirmed': {'en': '✅ Confirmed', 'ar': '✅ مؤكد', 'fr': '✅ Confirmé'},
            'preparing': {'en': '📦 Preparing', 'ar': '📦 جاري التحضير', 'fr': '📦 En préparation'},
            'on_the_way': {'en': '🚚 On the way', 'ar': '🚚 في الطريق', 'fr': '🚚 En route'},
            'delivered': {'en': '✅ Delivered', 'ar': '✅ تم التوصيل', 'fr': '✅ Livré'},
            'cancelled': {'en': '❌ Cancelled', 'ar': '❌ ملغي', 'fr': '❌ Annulé'},
            'out_of_stock': {'en': '⚠️ Out of stock', 'ar': '⚠️ نفذ من المخزون', 'fr': '⚠️ Rupture de stock'}
        }

        # Cancellable statuses
        cancellable_statuses = ['pending', 'confirmed', 'preparing']

        # Check if this is a cancel request
        if cancel_mode:
            cancellable_orders = [o for o in orders if o.status in cancellable_statuses]

            if not cancellable_orders:
                messages = {
                    'en': "❌ You don't have any orders that can be cancelled.\n\nOrders that are already on the way or delivered cannot be cancelled.",
                    'ar': "❌ ليس لديك طلبات يمكن إلغاؤها.\n\nالطلبات التي في الطريق أو تم توصيلها لا يمكن إلغاؤها.",
                    'fr': "❌ Vous n'avez aucune commande pouvant être annulée.\n\nLes commandes en route ou livrées ne peuvent pas être annulées."
                }
                return messages.get(lang, messages['en'])

            headers = {
                'en': "🗑️ *Select order to cancel:*\n",
                'ar': "🗑️ *اختر الطلب للإلغاء:*\n",
                'fr': "🗑️ *Sélectionnez la commande à annuler:*\n"
            }
            response = headers.get(lang, headers['en'])

            # Store cancellable order IDs for state
            order_ids = []
            for i, order in enumerate(cancellable_orders, 1):
                status_info = status_display.get(order.status, {'en': order.status})
                status_text = status_info.get(lang, status_info['en'])
                items = self.db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
                item_count = sum(item.quantity for item in items)

                response += f"\n*{i}.* #{order.order_number}\n"
                response += f"   Status: {status_text}\n"
                response += f"   Items: {item_count} | Total: {order.total_amount:,.0f} LBP\n"
                order_ids.append(order.id)

            # Set state for cancellation
            self._set_conversation_state(customer.id, 'awaiting_order_cancel', {'order_ids': order_ids})

            footer = {
                'en': "\n➡️ Type number to cancel (e.g., 1)\n❌ Type 'no' to go back",
                'ar': "\n➡️ اكتب الرقم للإلغاء (مثال: 1)\n❌ اكتب 'لا' للعودة",
                'fr': "\n➡️ Tapez le numéro pour annuler (ex: 1)\n❌ Tapez 'non' pour revenir"
            }
            response += footer.get(lang, footer['en'])
            return response

        # Normal order display
        headers = {
            'en': "📦 *Your Orders:*\n",
            'ar': "📦 *طلباتك:*\n",
            'fr': "📦 *Vos commandes:*\n"
        }

        response = headers.get(lang, headers['en'])

        for order in orders:
            status_info = status_display.get(order.status, {'en': order.status, 'ar': order.status, 'fr': order.status})
            status_text = status_info.get(lang, status_info['en'])

            # Get order items
            items = self.db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
            item_count = sum(item.quantity for item in items)

            response += f"\n*#{order.order_number}*\n"
            response += f"   Status: {status_text}\n"
            response += f"   Items: {item_count}\n"
            response += f"   Total: {order.total_amount:,.0f} LBP\n"
            if order.created_at:
                response += f"   Date: {order.created_at.strftime('%d/%m/%Y')}\n"

        response += "\n💬 To cancel an order, type 'cancel order'"
        return response

    def _handle_order_cancel_input(self, customer: Customer, message: str, lang: str) -> str:
        """Handle order cancellation selection"""
        message_lower = message.lower().strip()

        # Check if user wants to go back
        if message_lower in ['no', 'non', 'لا', 'la', 'back', 'cancel']:
            self._set_conversation_state(customer.id, 'idle')
            messages = {
                'en': "✅ Cancelled. Your orders remain unchanged.",
                'ar': "✅ تم الإلغاء. طلباتك لم تتغير.",
                'fr': "✅ Annulé. Vos commandes restent inchangées."
            }
            return messages.get(lang, messages['en'])

        # Get the state data
        state_obj = self.db.query(ConversationState).filter(
            ConversationState.customer_id == customer.id
        ).first()

        if not state_obj or not state_obj.data or 'order_ids' not in state_obj.data:
            self._set_conversation_state(customer.id, 'idle')
            return self._handle_order_inquiry(customer, lang, cancel_mode=True)

        order_ids = state_obj.data['order_ids']

        # Check if input is a valid number
        if not message.strip().isdigit():
            messages = {
                'en': "❌ Please enter a number (e.g., 1) or type 'no' to go back.",
                'ar': "❌ الرجاء إدخال رقم (مثال: 1) أو اكتب 'لا' للعودة.",
                'fr': "❌ Veuillez entrer un numéro (ex: 1) ou tapez 'non' pour revenir."
            }
            return messages.get(lang, messages['en'])

        selection = int(message.strip())

        if selection < 1 or selection > len(order_ids):
            messages = {
                'en': f"❌ Please enter a number between 1 and {len(order_ids)}.",
                'ar': f"❌ الرجاء إدخال رقم بين 1 و {len(order_ids)}.",
                'fr': f"❌ Veuillez entrer un numéro entre 1 et {len(order_ids)}."
            }
            return messages.get(lang, messages['en'])

        # Get the order and cancel it
        order_id = order_ids[selection - 1]
        order = self.db.query(Order).filter(Order.id == order_id).first()

        if not order:
            self._set_conversation_state(customer.id, 'idle')
            messages = {
                'en': "❌ Order not found. Please try again.",
                'ar': "❌ الطلب غير موجود. حاول مرة أخرى.",
                'fr': "❌ Commande non trouvée. Veuillez réessayer."
            }
            return messages.get(lang, messages['en'])

        # Cancel the order
        order.status = 'cancelled'
        order.updated_at = datetime.now()
        self.db.commit()

        self._set_conversation_state(customer.id, 'idle')

        messages = {
            'en': f"✅ Order #{order.order_number} has been cancelled successfully.\n\nTotal refund: {order.total_amount:,.0f} LBP\n\nThank you for shopping with us!",
            'ar': f"✅ تم إلغاء الطلب #{order.order_number} بنجاح.\n\nالمبلغ المسترد: {order.total_amount:,.0f} ل.ل\n\nشكراً لتسوقك معنا!",
            'fr': f"✅ La commande #{order.order_number} a été annulée avec succès.\n\nRemboursement total: {order.total_amount:,.0f} LBP\n\nMerci de votre visite!"
        }
        return messages.get(lang, messages['en'])

    def _check_store_info_questions(self, message: str, lang: str) -> Optional[str]:
        """Check for store info questions and return database settings"""
        message_lower = message.lower()

        # Get all settings from cache (much faster)
        db_settings = self._get_cached_settings()

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
                hours = db_settings.get(f'hours_{day}', '')
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
        message_lower = message.lower().strip()
        # Clean message words (remove punctuation)
        message_words = set(re.sub(r'[^\w\s]', '', message_lower).split())

        qa_items = self.db.query(CustomQA).filter(
            CustomQA.is_active == True
        ).order_by(desc(CustomQA.priority)).all()

        best_match = None
        best_score = 0
        best_keyword = ""

        for qa in qa_items:
            keywords = [k.strip().lower() for k in qa.keywords.split(',')]

            for keyword in keywords:
                score = 0
                keyword_clean = re.sub(r'[^\w\s]', '', keyword).strip()

                # Exact phrase match (highest priority)
                if keyword_clean == re.sub(r'[^\w\s]', '', message_lower).strip():
                    score = 100
                # Single keyword word that appears as whole word in message
                elif len(keyword_clean.split()) == 1 and keyword_clean in message_words:
                    # Important single word match - give good score
                    score = max(15, len(keyword_clean) * 2)
                # Full keyword phrase is contained in message
                elif keyword_clean in message_lower:
                    # Score based on keyword length (longer = more specific = better)
                    score = len(keyword_clean)
                else:
                    # Word-based matching - count matching words
                    keyword_words = set(keyword_clean.split())
                    matching_words = message_words & keyword_words
                    # Need at least 2 matching words or 50% of keyword words
                    if len(matching_words) >= 2 or (len(keyword_words) > 0 and len(matching_words) / len(keyword_words) >= 0.5):
                        score = len(matching_words) * 5

                if score > best_score:
                    best_score = score
                    best_match = qa
                    best_keyword = keyword

        logger.info(f"Q&A MATCH: message='{message_lower}', best_score={best_score}, keyword='{best_keyword}'")

        # Only return if we have a decent match
        if best_match and best_score >= 10:
            if lang == 'ar' and best_match.answer_ar:
                return best_match.answer_ar
            elif lang == 'fr' and best_match.answer_fr:
                return best_match.answer_fr
            else:
                return best_match.answer_en

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
            # Greetings (IMPORTANT: filter these out so "hello math book" searches "math book")
            'hello', 'hi', 'hey', 'hola', 'bonjour', 'bonsoir', 'salut',
            'marhaba', 'salam', 'ahla', 'hala', 'hey',
            # English conversational
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

    def _check_product_family(self, query: str) -> Optional[ProductFamily]:
        """Check if query matches a product family by keywords"""
        query_lower = query.lower().strip()

        families = self.db.query(ProductFamily).filter(ProductFamily.is_active == True).all()

        for family in families:
            if not family.keywords:
                continue

            keywords = [k.strip().lower() for k in family.keywords.split(',')]
            for keyword in keywords:
                if keyword and keyword in query_lower:
                    return family

        return None

    async def _handle_family_search(self, customer_id: int, family: ProductFamily, lang: str) -> str:
        """Handle search for a product family - show sizes or colors first"""
        # Get products in this family with stock
        products = self.db.query(Product).filter(
            Product.family_id == family.id,
            Product.stock_quantity > 0
        ).all()

        if not products:
            return {
                'ar': f"عذراً، لا توجد منتجات متوفرة من {family.name} حالياً",
                'en': f"Sorry, no {family.name} products available at the moment",
                'fr': f"Désolé, aucun produit {family.name} disponible pour le moment"
            }.get(lang, f"Sorry, no {family.name} products available at the moment")

        # Determine first attribute to show based on selection_order
        if family.selection_order == 'size_first' and family.has_sizes:
            return self._show_family_sizes(customer_id, family, products, lang)
        elif family.selection_order == 'color_first' and family.has_colors:
            return self._show_family_colors(customer_id, family, products, lang)
        elif family.has_sizes:
            return self._show_family_sizes(customer_id, family, products, lang)
        elif family.has_colors:
            return self._show_family_colors(customer_id, family, products, lang)
        else:
            # No size/color, show products directly
            return None

    def _show_family_sizes(self, customer_id: int, family: ProductFamily, products: list, lang: str) -> str:
        """Show available sizes for a family"""
        # Get unique sizes with counts
        sizes = {}
        for p in products:
            if p.variant_size:
                if p.variant_size not in sizes:
                    sizes[p.variant_size] = {'count': 0, 'colors': set()}
                sizes[p.variant_size]['count'] += 1
                # Expand multi-color values and count each color
                if p.variant_color:
                    color_values = [c.strip() for c in p.variant_color.split(',')]
                    for color_val in color_values:
                        if color_val:
                            sizes[p.variant_size]['colors'].add(color_val)

        if not sizes:
            # No sizes extracted, fallback to normal search
            return None

        # Sort sizes
        size_list = sorted(sizes.keys())

        # Build message
        msg_header = {
            'ar': f"📦 *{family.name}*\n\n📐 اختر المقاس:\n",
            'en': f"📦 *{family.name}*\n\n📐 Select size:\n",
            'fr': f"📦 *{family.name}*\n\n📐 Choisissez la taille:\n"
        }.get(lang, f"📦 *{family.name}*\n\n📐 Select size:\n")

        msg_items = ""
        for i, size in enumerate(size_list, 1):
            color_count = len(sizes[size]['colors'])
            color_text = {
                'ar': f"({color_count} لون)" if color_count > 0 else "",
                'en': f"({color_count} colors)" if color_count > 0 else "",
                'fr': f"({color_count} couleurs)" if color_count > 0 else ""
            }.get(lang, f"({color_count} colors)" if color_count > 0 else "")
            msg_items += f"{i}. {size} {color_text}\n"

        # Set conversation state
        self._set_conversation_state(customer_id, 'selecting_family_size', {
            'family_id': family.id,
            'family_name': family.name,
            'sizes': size_list,
            'has_colors': family.has_colors
        })

        return msg_header + msg_items

    def _show_family_colors(self, customer_id: int, family: ProductFamily, products: list, lang: str, size_filter: str = None) -> str:
        """Show available colors for a family (optionally filtered by size)"""
        # Get unique colors with stock counts
        colors = {}
        for p in products:
            if size_filter and p.variant_size != size_filter:
                continue
            if p.variant_color:
                # Expand multi-color values (like "Red,Blue,Green") into individual colors
                color_values = [c.strip() for c in p.variant_color.split(',')]
                for color_val in color_values:
                    if not color_val:
                        continue
                    if color_val not in colors:
                        colors[color_val] = {'count': 0, 'stock': 0, 'product_ids': []}
                    colors[color_val]['count'] += 1
                    colors[color_val]['stock'] += p.stock_quantity
                    colors[color_val]['product_ids'].append(p.id)

        if not colors:
            # No colors extracted
            return None

        # Sort colors
        color_list = sorted(colors.keys())

        # Build message
        size_text = f" ({size_filter})" if size_filter else ""
        msg_header = {
            'ar': f"📦 *{family.name}*{size_text}\n\n🎨 اختر اللون:\n",
            'en': f"📦 *{family.name}*{size_text}\n\n🎨 Select color:\n",
            'fr': f"📦 *{family.name}*{size_text}\n\n🎨 Choisissez la couleur:\n"
        }.get(lang, f"📦 *{family.name}*{size_text}\n\n🎨 Select color:\n")

        msg_items = ""
        for i, color in enumerate(color_list, 1):
            stock = colors[color]['stock']
            stock_badge = "✅" if stock > 0 else "❌"
            msg_items += f"{i}. {color} {stock_badge}\n"

        # Set conversation state
        self._set_conversation_state(customer_id, 'selecting_family_color', {
            'family_id': family.id,
            'family_name': family.name,
            'colors': color_list,
            'size_filter': size_filter
        })

        return msg_header + msg_items

    async def _handle_family_size_selection(self, customer_id: int, message: str, lang: str) -> str:
        """Handle size selection for a product family"""
        state = self.db.query(ConversationState).filter(
            ConversationState.customer_id == customer_id
        ).first()

        if not state or not state.data.get('sizes'):
            return ResponseTemplates.invalid_input(lang)

        try:
            selection = int(message.strip()) - 1
            sizes = state.data['sizes']

            if 0 <= selection < len(sizes):
                selected_size = sizes[selection]
                family_id = state.data['family_id']
                family = self.db.query(ProductFamily).get(family_id)

                if family and family.has_colors:
                    # Show colors for this size
                    products = self.db.query(Product).filter(
                        Product.family_id == family_id,
                        Product.variant_size == selected_size,
                        Product.stock_quantity > 0
                    ).all()

                    result = self._show_family_colors(customer_id, family, products, lang, selected_size)
                    if result:
                        return result

                # No colors, show products directly
                products = self.db.query(Product).filter(
                    Product.family_id == family_id,
                    Product.variant_size == selected_size,
                    Product.stock_quantity > 0
                ).limit(10).all()

                if products:
                    product_list = [
                        {
                            'id': p.id,
                            'item_name': p.item_name,
                            'item_code': p.item_code,
                            'price': p.price,
                            'stock_quantity': p.stock_quantity
                        }
                        for p in products
                    ]
                    self._set_conversation_state(customer_id, 'browsing_products', {
                        'products': [p.id for p in products]
                    })
                    return ResponseTemplates.product_list(lang, product_list, 1, 1)

        except ValueError:
            pass

        return {
            'ar': "الرجاء اختيار رقم صحيح من القائمة",
            'en': "Please select a valid number from the list",
            'fr': "Veuillez sélectionner un numéro valide de la liste"
        }.get(lang, "Please select a valid number from the list")

    async def _handle_family_color_selection(self, customer_id: int, message: str, lang: str) -> str:
        """Handle color selection for a product family"""
        state = self.db.query(ConversationState).filter(
            ConversationState.customer_id == customer_id
        ).first()

        if not state or not state.data.get('colors'):
            return ResponseTemplates.invalid_input(lang)

        try:
            selection = int(message.strip()) - 1
            colors = state.data['colors']

            if 0 <= selection < len(colors):
                selected_color = colors[selection]
                family_id = state.data['family_id']
                size_filter = state.data.get('size_filter')

                # Find the product matching size and color
                # First try exact match
                query = self.db.query(Product).filter(
                    Product.family_id == family_id,
                    Product.variant_color == selected_color,
                    Product.stock_quantity > 0
                )
                if size_filter:
                    query = query.filter(Product.variant_size == size_filter)
                product = query.first()

                # If no exact match, search for color within multi-color products
                if not product:
                    from sqlalchemy import or_
                    query = self.db.query(Product).filter(
                        Product.family_id == family_id,
                        Product.stock_quantity > 0,
                        or_(
                            Product.variant_color.like(f"{selected_color},%"),  # starts with color
                            Product.variant_color.like(f"%,{selected_color},%"),  # middle
                            Product.variant_color.like(f"%,{selected_color}"),  # ends with color
                            Product.variant_color == selected_color  # exact match
                        )
                    )
                    if size_filter:
                        query = query.filter(Product.variant_size == size_filter)
                    product = query.first()

                if product:
                    customer = self.db.query(Customer).get(customer_id)

                    # Send product image if available
                    if product.image_url and customer:
                        try:
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

                    # Ask if customer wants to order
                    self._set_conversation_state(customer_id, 'awaiting_product_confirm', {
                        'selected_product_id': product.id,
                        'product_name': product.item_name,
                        'price': product.price
                    })

                    confirm_msg = {
                        'ar': f"📦 *{product.item_name}*\n💰 {product.price:,.0f} LBP\n\n✅ اكتب *1* للطلب\n🔙 اكتب *2* للعودة والبحث",
                        'en': f"📦 *{product.item_name}*\n💰 {product.price:,.0f} LBP\n\n✅ Type *1* to order\n🔙 Type *2* to go back and search",
                        'fr': f"📦 *{product.item_name}*\n💰 {product.price:,.0f} LBP\n\n✅ Tapez *1* pour commander\n🔙 Tapez *2* pour revenir et chercher"
                    }
                    return confirm_msg.get(lang, confirm_msg['en'])

        except ValueError:
            pass

        return {
            'ar': "الرجاء اختيار رقم صحيح من القائمة",
            'en': "Please select a valid number from the list",
            'fr': "Veuillez sélectionner un numéro valide de la liste"
        }.get(lang, "Please select a valid number from the list")

    async def _quick_product_search(self, customer_id: int, query: str, lang: str) -> Optional[str]:
        # Check for product family match first
        family = self._check_product_family(query)
        if family:
            logger.info(f"FAMILY SEARCH: matched family '{family.name}'")
            result = await self._handle_family_search(customer_id, family, lang)
            if result:
                return result

        # Try SQL ILIKE search
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

                    # Ask if customer wants to order this product
                    self._set_conversation_state(customer_id, 'awaiting_product_confirm', {
                        'selected_product_id': product.id,
                        'product_name': product.item_name,
                        'price': product.price
                    })

                    confirm_msg = {
                        'ar': f"📦 *{product.item_name}*\n💰 {product.price:,.0f} LBP\n\n✅ اكتب *1* للطلب\n🔙 اكتب *2* للعودة والبحث",
                        'en': f"📦 *{product.item_name}*\n💰 {product.price:,.0f} LBP\n\n✅ Type *1* to order\n🔙 Type *2* to go back and search",
                        'fr': f"📦 *{product.item_name}*\n💰 {product.price:,.0f} LBP\n\n✅ Tapez *1* pour commander\n🔙 Tapez *2* pour revenir et chercher"
                    }
                    return confirm_msg.get(lang, confirm_msg['en'])

        except ValueError:
            pass

        return ResponseTemplates.invalid_input(lang)

    async def _handle_product_confirm(self, customer_id: int, message: str, lang: str) -> str:
        """Handle product confirmation (1 to order, 2 to go back)"""
        state = self.db.query(ConversationState).filter(
            ConversationState.customer_id == customer_id
        ).first()

        if not state or not state.data.get('selected_product_id'):
            self._set_conversation_state(customer_id, 'idle')
            return ResponseTemplates.invalid_input(lang)

        choice = message.strip()

        if choice == '2' or choice.lower() in ['back', 'no', 'cancel', 'لا', 'non']:
            # Go back - reset state
            self._set_conversation_state(customer_id, 'idle')
            return {
                'ar': "🔙 تم. اكتب ما تبحث عنه للبحث عن منتج آخر.",
                'en': "🔙 OK. Type what you're looking for to search for another product.",
                'fr': "🔙 OK. Tapez ce que vous cherchez pour rechercher un autre produit."
            }.get(lang, "🔙 OK. Type what you're looking for to search for another product.")

        if choice != '1':
            return {
                'ar': "الرجاء اكتب *1* للطلب أو *2* للعودة",
                'en': "Please type *1* to order or *2* to go back",
                'fr': "Veuillez taper *1* pour commander ou *2* pour revenir"
            }.get(lang, "Please type *1* to order or *2* to go back")

        # Customer wants to order - proceed with order flow
        product = self.db.query(Product).get(state.data['selected_product_id'])
        customer = self.db.query(Customer).get(customer_id)

        if not product:
            self._set_conversation_state(customer_id, 'idle')
            return ResponseTemplates.invalid_input(lang)

        # Check what customer info we have
        has_name = customer and customer.name
        has_address = customer and customer.address

        if has_name and has_address:
            # Customer has saved address - ask if they want to use it or enter new one
            self._set_conversation_state(customer_id, 'awaiting_address_choice', {
                'selected_product_id': product.id,
                'product_name': product.item_name,
                'price': product.price,
                'quantity': 1,
                'saved_address': customer.address
            })
            choice_msg = {
                'ar': f"📦 *{product.item_name}*\n💰 {product.price:,.0f} LBP\n\n📍 عنوانك المحفوظ:\n{customer.address}\n\n✅ اكتب *1* للإرسال لهذا العنوان\n📝 اكتب *2* لإدخال عنوان جديد",
                'en': f"📦 *{product.item_name}*\n💰 {product.price:,.0f} LBP\n\n📍 Your saved address:\n{customer.address}\n\n✅ Type *1* to send to this address\n📝 Type *2* to enter a new address",
                'fr': f"📦 *{product.item_name}*\n💰 {product.price:,.0f} LBP\n\n📍 Votre adresse enregistrée:\n{customer.address}\n\n✅ Tapez *1* pour envoyer à cette adresse\n📝 Tapez *2* pour entrer une nouvelle adresse"
            }
            return choice_msg.get(lang, choice_msg['en'])
        elif has_name and not has_address:
            # Has name but no address - skip to address
            self._set_conversation_state(customer_id, 'awaiting_address', {
                'selected_product_id': product.id,
                'product_name': product.item_name,
                'price': product.price,
                'quantity': 1,
                'customer_name': customer.name
            })
            return {
                'ar': f"مرحبا {customer.name}!\n\n📦 *{product.item_name}*\n💰 {product.price:,.0f} LBP\n\nالرجاء إدخال عنوانك للتوصيل:",
                'en': f"Hi {customer.name}!\n\n📦 *{product.item_name}*\n💰 {product.price:,.0f} LBP\n\nPlease enter your delivery address:",
                'fr': f"Bonjour {customer.name}!\n\n📦 *{product.item_name}*\n💰 {product.price:,.0f} LBP\n\nVeuillez entrer votre adresse de livraison:"
            }.get(lang, f"Hi {customer.name}!\n\nPlease enter your delivery address:")
        else:
            # Need to collect name first
            self._set_conversation_state(customer_id, 'awaiting_name', {
                'selected_product_id': product.id,
                'product_name': product.item_name,
                'price': product.price,
                'quantity': 1
            })
            return ResponseTemplates.ask_name(lang, product.item_name)

    async def _handle_address_choice(self, customer_id: int, message: str, lang: str) -> str:
        """Handle address choice - use saved address (1) or enter new (2)"""
        msg = message.strip()

        state = self.db.query(ConversationState).filter(
            ConversationState.customer_id == customer_id
        ).first()

        if not state or not state.data.get('selected_product_id'):
            self._set_conversation_state(customer_id, 'idle')
            return ResponseTemplates.invalid_input(lang)

        customer = self.db.query(Customer).get(customer_id)
        product = self.db.query(Product).get(state.data['selected_product_id'])
        quantity = state.data.get('quantity', 1)

        if msg == '1':
            # Use saved address - create order directly
            saved_address = state.data.get('saved_address') or customer.address

            order = Order(
                customer_id=customer_id,
                order_number=f"ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{customer_id}",
                total_amount=product.price * quantity,
                status='pending',
                delivery_address=saved_address,
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
                'customer_address': saved_address,
                'quantity': quantity,
                'price': product.price
            })

        elif msg == '2':
            # Enter new address
            state.state = 'awaiting_address'
            state.data['customer_name'] = customer.name
            self.db.commit()

            return {
                'ar': "📍 الرجاء إدخال عنوان التوصيل الجديد:",
                'en': "📍 Please enter your new delivery address:",
                'fr': "📍 Veuillez entrer votre nouvelle adresse de livraison:"
            }.get(lang, "📍 Please enter your new delivery address:")

        else:
            # Invalid input - remind options
            return {
                'ar': "الرجاء اكتب *1* للإرسال للعنوان المحفوظ أو *2* لإدخال عنوان جديد",
                'en': "Please type *1* to send to saved address or *2* to enter a new address",
                'fr': "Veuillez taper *1* pour envoyer à l'adresse enregistrée ou *2* pour entrer une nouvelle adresse"
            }.get(lang, "Please type *1* to send to saved address or *2* to enter a new address")

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

                # Check if customer already has name
                customer = self.db.query(Customer).get(customer_id)
                if customer and customer.name:
                    # Skip name, go to address
                    state.data['customer_name'] = customer.name
                    state.state = 'awaiting_address'
                    self.db.commit()
                    return {
                        'ar': f"شكرا {customer.name}!\n\nالرجاء إدخال عنوانك للتوصيل:",
                        'en': f"Thank you {customer.name}!\n\nPlease enter your delivery address:",
                        'fr': f"Merci {customer.name}!\n\nVeuillez entrer votre adresse de livraison:"
                    }.get(lang, f"Thank you {customer.name}!\n\nPlease enter your delivery address:")
                else:
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

        # Save address to customer profile for future orders
        customer = self.db.query(Customer).get(customer_id)
        if customer:
            customer.address = address
            self.db.commit()

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
    message_fields = ['welcome_message_en', 'welcome_message_ar', 'welcome_message_fr', 'out_of_stock_en', 'out_of_stock_ar']

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


# ============ SCHOOL MANAGEMENT ROUTES ============

@app.get("/schools", response_class=HTMLResponse)
async def schools_page(request: Request, school: str = None, grade: str = None, db: Session = Depends(get_db)):
    """Schools management page - 3 level hierarchy"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    # Get school mode setting
    mode_setting = db.query(BotSettings).filter(BotSettings.setting_key == "school_books_mode").first()
    school_mode = mode_setting.setting_value if mode_setting else "off"

    # Get all schools with counts
    schools_query = db.execute(text("""
        SELECT subcategory as school_name,
               COUNT(*) as total_books,
               SUM(CASE WHEN stock_quantity > 0 THEN 1 ELSE 0 END) as available_books
        FROM products
        WHERE is_school = 1
          AND subcategory IS NOT NULL
          AND subcategory != ''
          AND subcategory != 'N / A'
        GROUP BY subcategory
        ORDER BY subcategory
    """))
    schools = [{"name": r[0], "total": r[1], "available": r[2]} for r in schools_query.fetchall()]

    # Get grades for selected school
    grades = []
    if school:
        grades_query = db.execute(text("""
            SELECT COALESCE(grade_level, 'Other') as grade_name,
                   COUNT(*) as total_books,
                   SUM(CASE WHEN stock_quantity > 0 THEN 1 ELSE 0 END) as available_books
            FROM products
            WHERE is_school = 1 AND subcategory = :school
            GROUP BY grade_level
            ORDER BY
                CASE
                    WHEN grade_level = 'PS' THEN 1
                    WHEN grade_level = 'MS' THEN 2
                    WHEN grade_level = 'GS' THEN 3
                    WHEN grade_level = 'KG1' THEN 4
                    WHEN grade_level = 'KG2' THEN 5
                    WHEN grade_level = 'KG3' THEN 6
                    WHEN grade_level = 'CP' THEN 7
                    WHEN grade_level = 'CE1' THEN 8
                    WHEN grade_level = 'CE2' THEN 9
                    WHEN grade_level = 'CM1' THEN 10
                    WHEN grade_level = 'CM2' THEN 11
                    WHEN grade_level = 'EB1' THEN 12
                    WHEN grade_level = 'EB2' THEN 13
                    WHEN grade_level = 'EB3' THEN 14
                    WHEN grade_level = 'EB4' THEN 15
                    WHEN grade_level = 'EB5' THEN 16
                    WHEN grade_level = 'EB6' THEN 17
                    WHEN grade_level = 'EB7' THEN 18
                    WHEN grade_level = 'EB8' THEN 19
                    WHEN grade_level = 'EB9' THEN 20
                    ELSE 100
                END
        """), {"school": school})
        grades = [{"name": r[0], "total": r[1], "available": r[2]} for r in grades_query.fetchall()]

    # Get books for selected school and grade
    books = []
    if school and grade:
        if grade == 'Other':
            books = db.query(Product).filter(
                Product.subcategory == school,
                Product.is_school == True,
                Product.grade_level.is_(None)
            ).order_by(Product.item_name).all()
        else:
            books = db.query(Product).filter(
                Product.subcategory == school,
                Product.is_school == True,
                Product.grade_level == grade
            ).order_by(Product.item_name).all()

    return templates.TemplateResponse("schools.html", {
        "request": request,
        "user": user,
        "schools": schools,
        "grades": grades,
        "books": books,
        "selected_school": school,
        "selected_grade": grade,
        "school_mode": school_mode,
        "currency": settings.CURRENCY
    })


@app.post("/schools/toggle-mode")
async def toggle_school_mode(request: Request, db: Session = Depends(get_db)):
    """Toggle school books mode"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    mode_setting = db.query(BotSettings).filter(BotSettings.setting_key == "school_books_mode").first()
    if mode_setting:
        mode_setting.setting_value = "off" if mode_setting.setting_value == "on" else "on"
    else:
        mode_setting = BotSettings(setting_key="school_books_mode", setting_value="on")
        db.add(mode_setting)
    db.commit()

    return RedirectResponse(url="/schools", status_code=303)


@app.get("/schools/manage", response_class=HTMLResponse)
async def schools_manage_page(request: Request, db: Session = Depends(get_db)):
    """School management dashboard"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    # Get schools with book counts
    schools_raw = db.query(School).order_by(School.name).all()
    schools = []
    for s in schools_raw:
        book_count = db.query(Product).filter(Product.subcategory == s.name, Product.is_school == True).count()
        schools.append({
            'id': s.id,
            'name': s.name,
            'display_name': s.display_name,
            'is_active': s.is_active,
            'book_count': book_count
        })

    # Get books with arrival status
    arriving_books = db.query(Product).filter(
        Product.is_school == True,
        Product.arrival_status.in_(['coming_soon', 'arriving'])
    ).order_by(Product.expected_arrival).all()

    return templates.TemplateResponse("schools_manage.html", {
        "request": request,
        "user": user,
        "schools": schools,
        "arriving_books": arriving_books
    })


@app.get("/schools/manage/{school_id}/books", response_class=HTMLResponse)
async def school_books_page(request: Request, school_id: int, db: Session = Depends(get_db)):
    """Manage books in a specific school"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    school = db.query(School).filter(School.id == school_id).first()
    if not school:
        return RedirectResponse("/schools/manage")

    books = db.query(Product).filter(
        Product.subcategory == school.name,
        Product.is_school == True
    ).order_by(Product.grade_level, Product.item_name).all()

    # Get unique grades for filter
    grades = db.query(Product.grade_level).filter(
        Product.subcategory == school.name,
        Product.is_school == True,
        Product.grade_level.isnot(None)
    ).distinct().all()
    grade_order = ['KG1','KG2','KG3','EB1','EB2','EB3','EB4','EB5','EB6','EB7','EB8','EB9','SEC1','SEC2','SEC3']
    grades = sorted([g[0] for g in grades], key=lambda x: grade_order.index(x) if x in grade_order else 99)

    all_schools = db.query(School).filter(School.is_active == True).order_by(School.name).all()

    return templates.TemplateResponse("school_books.html", {
        "request": request,
        "user": user,
        "school": school,
        "books": books,
        "grades": grades,
        "all_schools": all_schools
    })


@app.post("/schools/manage/{school_id}/toggle")
async def toggle_school(school_id: int, request: Request, db: Session = Depends(get_db)):
    """Toggle school active status"""
    try:
        data = await request.json()
        school = db.query(School).filter(School.id == school_id).first()
        if school:
            school.is_active = data.get('is_active', True)
            db.commit()
            return JSONResponse({"success": True})
        return JSONResponse({"success": False, "error": "School not found"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/schools/manage/{school_id}/rename")
async def rename_school(school_id: int, request: Request, db: Session = Depends(get_db)):
    """Rename school display name"""
    try:
        data = await request.json()
        school = db.query(School).filter(School.id == school_id).first()
        if school:
            school.display_name = data.get('display_name', school.name)
            db.commit()
            return JSONResponse({"success": True})
        return JSONResponse({"success": False, "error": "School not found"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/schools/manage/add-book")
async def add_book_to_school(request: Request, db: Session = Depends(get_db)):
    """Add existing product to a school"""
    try:
        data = await request.json()
        product_id = data.get('product_id')
        school = data.get('school')
        grade_level = data.get('grade_level')

        product = db.query(Product).filter(Product.id == product_id).first()
        if product:
            product.subcategory = school
            product.is_school = True
            if grade_level:
                product.grade_level = grade_level
            db.commit()
            return JSONResponse({"success": True})
        return JSONResponse({"success": False, "error": "Product not found"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/schools/manage/book/{book_id}/move")
async def move_book(book_id: int, request: Request, db: Session = Depends(get_db)):
    """Move book to another school"""
    try:
        data = await request.json()
        product = db.query(Product).filter(Product.id == book_id).first()
        if product:
            product.subcategory = data.get('school')
            if data.get('grade_level'):
                product.grade_level = data.get('grade_level')
            db.commit()
            return JSONResponse({"success": True})
        return JSONResponse({"success": False, "error": "Book not found"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/schools/manage/book/{book_id}/arrival")
async def set_arrival_status(book_id: int, request: Request, db: Session = Depends(get_db)):
    """Set book arrival status and date"""
    try:
        data = await request.json()
        product = db.query(Product).filter(Product.id == book_id).first()
        if product:
            product.arrival_status = data.get('status', 'in_stock')
            if data.get('date'):
                product.expected_arrival = datetime.strptime(data['date'], '%Y-%m-%d')
            else:
                product.expected_arrival = None
            db.commit()
            return JSONResponse({"success": True})
        return JSONResponse({"success": False, "error": "Book not found"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/schools/manage/book/{book_id}/arrived")
async def mark_book_arrived(book_id: int, db: Session = Depends(get_db)):
    """Mark book as arrived (back in stock)"""
    try:
        product = db.query(Product).filter(Product.id == book_id).first()
        if product:
            product.arrival_status = 'in_stock'
            product.expected_arrival = None
            db.commit()
            return JSONResponse({"success": True})
        return JSONResponse({"success": False, "error": "Book not found"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.post("/schools/manage/book/{book_id}/remove")
async def remove_book_from_school(book_id: int, db: Session = Depends(get_db)):
    """Remove book from school (keep as regular product)"""
    try:
        product = db.query(Product).filter(Product.id == book_id).first()
        if product:
            product.is_school = False
            product.subcategory = None
            product.grade_level = None
            db.commit()
            return JSONResponse({"success": True})
        return JSONResponse({"success": False, "error": "Book not found"})
    except Exception as e:
        return JSONResponse({"success": False, "error": str(e)})


@app.get("/api/products/search")
async def api_products_search(q: str = Query(None, min_length=2), db: Session = Depends(get_db)):
    """Search products API for school management"""
    if not q:
        return {"products": []}

    products = db.query(Product).filter(
        or_(
            Product.item_name.ilike(f"%{q}%"),
            Product.item_code.ilike(f"%{q}%")
        )
    ).limit(20).all()

    return {"products": [{"id": p.id, "item_name": p.item_name, "subcategory": p.subcategory} for p in products]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
