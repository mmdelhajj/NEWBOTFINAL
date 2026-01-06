"""
Brains ERP API Integration
Handles all communication with Brains ERP system
"""

import httpx
import asyncio
import re
import logging
from typing import Optional, Dict, List, Any
from datetime import datetime
from config import settings

logger = logging.getLogger(__name__)


class BrainsAPI:
    def __init__(self):
        self.base_url = settings.BRAINS_API_BASE
        self.timeout = settings.API_TIMEOUT_SECONDS
        self.retry_attempts = settings.API_RETRY_ATTEMPTS

    async def fetch_items(self) -> List[dict]:
        """Fetch all items/products from Brains"""
        url = f"{self.base_url}/items"
        response = await self._make_request(url)

        # Extract Content array from response
        if response and response.get('Success') and response.get('Content'):
            return response['Content']

        return []

    async def fetch_accounts(self) -> List[dict]:
        """Fetch customer accounts from Brains"""
        url = f"{self.base_url}/accounts?type=1&accocode=41110"
        response = await self._make_request(url)

        # Extract Content array from response
        if response and response.get('Success') and response.get('Content'):
            return response['Content']

        return []

    async def fetch_account_by_code(self, account_code: str) -> Optional[dict]:
        """Fetch a specific account by code"""
        accounts = await self.fetch_accounts()

        if not accounts:
            return None

        for account in accounts:
            if account.get('AccountNumber') == account_code:
                return account

        return None

    async def find_account_by_phone(self, phone: str) -> Optional[dict]:
        """Find account by phone number"""
        accounts = await self.fetch_accounts()

        if not accounts:
            return None

        # Normalize phone for comparison
        normalized_phone = re.sub(r'[^0-9]', '', phone)

        for account in accounts:
            if account.get('Telephone'):
                account_phone = re.sub(r'[^0-9]', '', account['Telephone'])

                # Compare last 8 digits (Lebanese mobile numbers)
                if normalized_phone[-8:] == account_phone[-8:]:
                    return account

        return None

    async def fetch_sales(self) -> dict:
        """Fetch sales/invoices from Brains"""
        url = f"{self.base_url}/sales?type=1&accocode=41110"
        return await self._make_request(url)

    async def create_sale(self, sale_data: dict) -> dict:
        """Create new sale/invoice in Brains"""
        url = f"{self.base_url}/sales"

        data = {
            'CustomerCode': sale_data['customer_code'],
            'InvoiceDate': sale_data.get('invoice_date', datetime.now().strftime('%Y-%m-%d')),
            'Items': sale_data['items'],
            'Notes': sale_data.get('notes', 'Created from WhatsApp Bot')
        }

        return await self._make_request(url, method='POST', data=data)

    async def _make_request(
        self,
        url: str,
        method: str = 'GET',
        data: Optional[dict] = None
    ) -> Optional[dict]:
        """Make HTTP request with retry logic"""
        attempt = 0
        last_error = None

        while attempt < self.retry_attempts:
            try:
                result = await self._do_request(url, method, data)
                if result is not None:
                    return result
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Brains API request failed (attempt {attempt + 1}): {last_error}")

            attempt += 1

            if attempt < self.retry_attempts:
                # Exponential backoff: 1s, 2s, 4s
                await asyncio.sleep(2 ** (attempt - 1))

        logger.error(f"Brains API request failed after {self.retry_attempts} attempts: {last_error}")
        return None

    async def _do_request(
        self,
        url: str,
        method: str = 'GET',
        data: Optional[dict] = None
    ) -> dict:
        """Perform actual HTTP request"""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            if method == 'POST':
                response = await client.post(
                    url,
                    json=data,
                    headers={
                        'Content-Type': 'application/json',
                        'Accept': 'application/json'
                    }
                )
            else:
                response = await client.get(url)

            if response.status_code != 200:
                raise Exception(f"HTTP Error {response.status_code}")

            return response.json()

    async def test_connection(self) -> dict:
        """Test connection to Brains API"""
        try:
            items = await self.fetch_items()
            return {
                'success': isinstance(items, list),
                'item_count': len(items) if isinstance(items, list) else 0,
                'message': 'Connection successful' if isinstance(items, list) else 'Invalid response'
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }

    async def sync_products(self, db_session, product_model) -> dict:
        """Sync products to local database"""
        start_time = datetime.now()

        try:
            items = await self.fetch_items()

            if not items:
                return {
                    'success': False,
                    'error': 'No items received from Brains API'
                }

            added = 0
            updated = 0

            for item in items:
                # API returns: SKU, Name, ShortDescription, Price, StockQuantity, Group
                sku = item.get('SKU', '').strip()
                if not sku:
                    continue  # Skip items without SKU

                # Check if product exists
                existing = db_session.query(product_model).filter(
                    product_model.item_code == sku
                ).first()

                if existing:
                    # Update existing
                    existing.item_name = item.get('Name', '')
                    existing.description = item.get('ShortDescription', '')
                    existing.price = float(item.get('Price', 0))
                    existing.stock_quantity = int(item.get('StockQuantity', 0))
                    existing.category = item.get('Group', '')
                    existing.subcategory = item.get('SubGroup', '')
                    existing.updated_at = datetime.now()
                    updated += 1
                else:
                    # Create new
                    new_product = product_model(
                        item_code=sku,
                        item_name=item.get('Name', ''),
                        description=item.get('ShortDescription', ''),
                        price=float(item.get('Price', 0)),
                        stock_quantity=int(item.get('StockQuantity', 0)),
                        category=item.get('Group', ''),
                        subcategory=item.get('SubGroup', '')
                    )
                    db_session.add(new_product)
                    added += 1

            db_session.commit()

            duration = (datetime.now() - start_time).total_seconds()

            return {
                'success': True,
                'total': len(items),
                'added': added,
                'updated': updated,
                'duration': round(duration, 2)
            }

        except Exception as e:
            db_session.rollback()
            duration = (datetime.now() - start_time).total_seconds()

            logger.error(f"Product sync failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'duration': round(duration, 2)
            }

    async def sync_accounts(self, db_session, customer_model) -> dict:
        """Sync customer accounts"""
        start_time = datetime.now()

        try:
            accounts = await self.fetch_accounts()

            if not accounts:
                return {
                    'success': False,
                    'error': 'No accounts received from Brains API'
                }

            updated = 0

            for account in accounts:
                phone = account.get('Phone') or account.get('Telephone')
                if not phone:
                    continue

                # Find customer by phone
                normalized_phone = re.sub(r'[^0-9]', '', phone)
                customer = db_session.query(customer_model).filter(
                    customer_model.phone.contains(normalized_phone[-8:])
                ).first()

                if customer:
                    # Link Brains account
                    customer.brains_account_code = account.get('AccountNumber')
                    customer.balance = float(account.get('Balance', 0))
                    customer.credit_limit = float(account.get('CreditLimit', 0))
                    customer.updated_at = datetime.now()
                    updated += 1

            db_session.commit()

            duration = (datetime.now() - start_time).total_seconds()

            return {
                'success': True,
                'total': len(accounts),
                'updated': updated,
                'duration': round(duration, 2)
            }

        except Exception as e:
            db_session.rollback()
            duration = (datetime.now() - start_time).total_seconds()

            logger.error(f"Account sync failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'duration': round(duration, 2)
            }
