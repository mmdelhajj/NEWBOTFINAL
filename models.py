"""
SQLAlchemy Database Models
Matches the existing MySQL database schema from the PHP bot
"""

from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, DateTime,
    ForeignKey, JSON, Enum, create_engine
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


class Customer(Base):
    """Customer/User model - stores WhatsApp users"""
    __tablename__ = 'customers'

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=True)
    email = Column(String(100), nullable=True)
    address = Column(Text, nullable=True)
    preferred_language = Column(String(5), default='en')

    # Brains ERP Integration
    brains_account_code = Column(String(50), nullable=True)
    balance = Column(Float, default=0.0)
    credit_limit = Column(Float, default=0.0)

    # Status
    is_active = Column(Boolean, default=True)
    is_blocked = Column(Boolean, default=False)
    bot_paused_until = Column(DateTime, nullable=True)  # Pause bot auto-reply until this time

    # Timestamps
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    last_message_at = Column(DateTime, nullable=True)

    # Relationships
    messages = relationship("Message", back_populates="customer")
    orders = relationship("Order", back_populates="customer")
    conversation_state = relationship("ConversationState", back_populates="customer", uselist=False)


class Product(Base):
    """Product model - synced from Brains ERP"""
    __tablename__ = 'products'

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_code = Column(String(50), unique=True, nullable=False, index=True)
    item_name = Column(String(255), nullable=False)
    item_name_ar = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    description_ar = Column(Text, nullable=True)

    # Pricing
    price = Column(Float, default=0.0)
    cost_price = Column(Float, default=0.0)

    # Stock
    stock_quantity = Column(Integer, default=0)
    expected_arrival = Column(DateTime, nullable=True)

    # Category
    category = Column(String(100), nullable=True)
    category_ar = Column(String(100), nullable=True)
    subcategory = Column(String(100), nullable=True)

    # Image
    image_url = Column(String(500), nullable=True)

    # Brains integration
    brains_item_id = Column(String(50), nullable=True)

    # Status
    is_active = Column(Boolean, default=True)
    is_featured = Column(Boolean, default=False)

    # School-related fields
    is_school = Column(Boolean, default=False)
    grade_level = Column(String(20), nullable=True)
    arrival_status = Column(String(20), default="in_stock")  # in_stock, coming_soon, arriving

    # Timestamps
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # Relationships
    order_items = relationship("OrderItem", back_populates="product")


class Order(Base):
    """Order model"""
    __tablename__ = 'orders'

    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=False)
    order_number = Column(String(50), unique=True, nullable=False)

    # Status
    status = Column(
        Enum('pending', 'confirmed', 'preparing', 'on_the_way', 'delivered', 'cancelled', 'out_of_stock'),
        default='pending'
    )

    # Pricing
    subtotal = Column(Float, default=0.0)
    discount = Column(Float, default=0.0)
    total_amount = Column(Float, default=0.0)

    # Delivery
    delivery_address = Column(Text, nullable=True)
    delivery_notes = Column(Text, nullable=True)

    # Brains integration
    brains_invoice_id = Column(String(50), nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    delivered_at = Column(DateTime, nullable=True)

    # Relationships
    customer = relationship("Customer", back_populates="orders")
    items = relationship("OrderItem", back_populates="order")


class OrderItem(Base):
    """Order items model"""
    __tablename__ = 'order_items'

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(Integer, ForeignKey('orders.id'), nullable=False)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)

    quantity = Column(Integer, default=1)
    unit_price = Column(Float, nullable=False)
    total_price = Column(Float, nullable=False)

    # Relationships
    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")


class Message(Base):
    """Message log model"""
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), nullable=False)

    # Message content
    message = Column(Text, nullable=False)
    direction = Column(Enum('RECEIVED', 'SENT'), nullable=False)
    attachment = Column(String(500), nullable=True)

    # Metadata
    message_type = Column(String(20), default='text')  # text, image, document, location
    status = Column(String(20), default='delivered')  # sent, delivered, read, failed

    # Timestamps
    created_at = Column(DateTime, default=datetime.now)

    # Relationships
    customer = relationship("Customer", back_populates="messages")


class ConversationState(Base):
    """Conversation state for multi-step flows"""
    __tablename__ = 'conversation_states'

    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), unique=True, nullable=False)

    # State
    state = Column(String(50), default='idle')
    data = Column(JSON, default={})  # Stores state-specific data

    # Timestamps
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # Relationships
    customer = relationship("Customer", back_populates="conversation_state")


class CustomQA(Base):
    """Custom Q&A from admin panel"""
    __tablename__ = 'custom_qa'

    id = Column(Integer, primary_key=True, autoincrement=True)
    keywords = Column(Text, nullable=False)  # Comma-separated keywords

    # Answers in different languages
    answer_en = Column(Text, nullable=False)
    answer_ar = Column(Text, nullable=True)
    answer_fr = Column(Text, nullable=True)

    # Metadata
    category = Column(String(50), nullable=True)
    priority = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class Settings(Base):
    """Bot settings"""
    __tablename__ = 'bot_settings'

    id = Column(Integer, primary_key=True, autoincrement=True)
    setting_key = Column(String(100), unique=True, nullable=False)
    setting_value = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class BrainsSyncLog(Base):
    """Brains ERP sync log"""
    __tablename__ = 'brains_sync_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    sync_type = Column(String(50), nullable=False)  # products, accounts
    records_count = Column(Integer, default=0)
    records_added = Column(Integer, default=0)
    records_updated = Column(Integer, default=0)
    status = Column(String(20), default='success')  # success, failed
    error_message = Column(Text, nullable=True)
    duration_seconds = Column(Float, default=0.0)

    # Timestamps
    created_at = Column(DateTime, default=datetime.now)


class School(Base):
    """School model for school book management"""
    __tablename__ = 'schools'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)
    display_name = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
