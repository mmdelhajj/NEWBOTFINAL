"""
Claude AI Integration
Handles AI-powered conversations using Anthropic Claude
"""

import httpx
import base64
import re
import logging
from typing import Optional, Dict, List, Any
from config import settings

logger = logging.getLogger(__name__)


class ClaudeAI:
    def __init__(self):
        self.api_key = settings.ANTHROPIC_API_KEY
        self.api_url = settings.ANTHROPIC_API_URL
        self.model = settings.ANTHROPIC_MODEL
        self.max_tokens = settings.ANTHROPIC_MAX_TOKENS

    async def process_message(
        self,
        customer_id: int,
        customer_message: str,
        customer_data: dict = None,
        recent_messages: list = None
    ) -> dict:
        """Process customer message and generate AI response"""
        try:
            customer_data = customer_data or {}
            recent_messages = recent_messages or []

            # Build system prompt
            system_prompt = self._build_system_prompt(customer_data)

            # Build conversation history
            messages = self._build_conversation_history(recent_messages, customer_message)

            # Make API call to Claude
            response = await self._call_claude_api(system_prompt, messages)

            if response['success']:
                return {
                    'success': True,
                    'message': response['message'],
                    'intent': self._detect_intent(response['message'])
                }
            else:
                return {
                    'success': False,
                    'error': response['error']
                }

        except Exception as e:
            logger.error(f"Claude AI Error: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    async def smart_product_search(
        self,
        customer_id: int,
        customer_message: str,
        product_search_func
    ) -> dict:
        """Smart product search with AI interpretation"""
        try:
            # Build AI prompt to extract search keywords
            system_prompt = """You are a smart search keyword extractor for a bookstore/stationery store.

Your job: Extract search keywords from the customer's message.

CRITICAL RULES:
1. KEEP Arabic product names IN ARABIC (e.g., 'لغتي فرحي' stays 'لغتي فرحي')
2. KEEP brand names and codes as-is (e.g., 'EB1', 'Pointier', 'CNRDP')
3. Only translate GENERIC Arabic words like 'أريد' (I want), 'هل يوجد' (is there)
4. Return ONLY the keywords, no explanations
5. Keep multi-word product names together
6. NO COMMAS - separate keywords with spaces only

Examples:
Customer: 'أريد لغتي فرحي EB1' → لغتي فرحي EB1
Customer: 'هل يوجد السراج السنة الثانية' → السراج السنة الثانية
Customer: 'لغتي فرحي EB1 2024 Pointier' → لغتي فرحي EB1 Pointier
Customer: 'قلم أزرق' → قلم أزرق
Customer: 'Life Vision C1' → Life Vision C1
Customer: 'Espaces Litteraires 1ER' → Espaces Litteraires 1ER
Customer: 'coloring book for kids' → coloring book
Customer: 'I want a math book' → math book
Customer: 'دفتر' → دفتر

Return ONLY the keywords, nothing else!"""

            messages = [{'role': 'user', 'content': customer_message}]

            response = await self._call_claude_api(system_prompt, messages)

            if not response['success']:
                return {'success': False, 'error': response['error']}

            keywords = response['message'].strip()
            # Clean up: remove commas and extra whitespace
            keywords = ' '.join(keywords.replace(',', ' ').split())
            logger.debug(f"AI extracted keywords: '{keywords}' from query: '{customer_message}'")

            # Search database with AI-extracted keywords
            found_products = await product_search_func(keywords, limit=5)

            if found_products:
                return {
                    'success': True,
                    'type': 'products',
                    'products': found_products
                }

            # Fallback: Try CLIP text-to-text semantic search
            try:
                from services.clip_search import get_clip_service
                clip_service = get_clip_service()
                if clip_service and clip_service.text_embeddings:
                    logger.info(f"Trying CLIP text search for: {customer_message}")
                    clip_results = clip_service.search_text_by_text(customer_message, top_k=5)
                    if clip_results and clip_results[0].get('score', 0) > 0.3:
                        logger.info(f"CLIP text search found match: {clip_results[0]['score']:.3f}")
                        # Convert CLIP results to product format
                        return {
                            'success': True,
                            'type': 'products',
                            'products': [
                                {
                                    'id': None,  # Will need to look up
                                    'item_code': r['sku'],
                                    'item_name': r['item_name'],
                                    'price': r['price'],
                                    'stock_quantity': 0
                                }
                                for r in clip_results[:5]
                            ]
                        }
            except Exception as e:
                logger.warning(f"CLIP text search failed: {e}")

            return {
                'success': True,
                'type': 'message',
                'message': 'NO_MATCH'
            }

        except Exception as e:
            logger.error(f"Smart search error: {e}")
            return {'success': False, 'error': str(e)}

    def _build_system_prompt(self, customer_data: dict) -> str:
        """Build system prompt for Claude"""
        prompt = """⚠️ CRITICAL INSTRUCTION - READ THIS FIRST ⚠️

You are NOT Claude. You are NOT an AI assistant created by Anthropic.
You are a customer service representative working for {store_name}.
NEVER EVER mention Anthropic, Claude, or being an AI assistant.
NEVER say you don't have products or services - you DO sell real products!

=== YOUR ACTUAL IDENTITY ===
You are: The WhatsApp assistant for {store_name}
You work at: {store_location}
Your job: Help customers buy books, stationery, and educational materials

=== STORE INFORMATION (MEMORIZE THIS) ===
Store Name: {store_name}
Location: {store_location}
Phone: {store_phone}
Website: {store_website}
Hours: {store_hours}
Products: Books, stationery, educational materials, toys, office supplies
Contact: WhatsApp (this chat), Phone: {store_phone}

=== HOW TO ANSWER COMMON QUESTIONS ===
Q: 'What's your website?' → A: 'Our website is {store_website} 🌐'
Q: 'Who are you?' → A: 'I'm the WhatsApp assistant for {store_name} 😊'
Q: 'Do you have a store?' → A: 'Yes! We're located in {store_location} 📍'
Q: 'What do you sell?' → A: 'We sell books, stationery, educational materials, and more! 📚'

=== RESPONSE RULES ===
- Respond in customer's language (Arabic/English/French)
- Be VERY brief (1-2 sentences max)
- Use emojis 😊
- Prices: XX,XXX {currency}

""".format(
            store_name=settings.STORE_NAME,
            store_location=settings.STORE_LOCATION,
            store_phone=settings.STORE_PHONE,
            store_website=settings.STORE_WEBSITE,
            store_hours=settings.STORE_HOURS,
            currency=settings.CURRENCY
        )

        # Add customer context if available
        if customer_data.get('name'):
            prompt += "**Customer Information:**\n"
            prompt += f"- Name: {customer_data['name']}\n"

            if 'balance' in customer_data:
                prompt += f"- Account Balance: {customer_data['balance']:,.0f} {settings.CURRENCY}\n"

            if 'credit_limit' in customer_data:
                prompt += f"- Credit Limit: {customer_data['credit_limit']:,.0f} {settings.CURRENCY}\n"

        return prompt

    def _build_conversation_history(
        self,
        recent_messages: list,
        current_message: str
    ) -> list:
        """Build conversation history for Claude"""
        messages = []

        # Add recent messages
        for msg in recent_messages:
            role = 'user' if msg.get('direction') == 'RECEIVED' else 'assistant'
            messages.append({
                'role': role,
                'content': msg.get('message', '')
            })

        # Add current message
        messages.append({
            'role': 'user',
            'content': current_message
        })

        return messages

    async def _call_claude_api(self, system_prompt: str, messages: list) -> dict:
        """Call Claude API"""
        data = {
            'model': self.model,
            'max_tokens': self.max_tokens,
            'system': system_prompt,
            'messages': messages
        }

        headers = {
            'Content-Type': 'application/json',
            'x-api-key': self.api_key,
            'anthropic-version': '2023-06-01'
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.api_url,
                    json=data,
                    headers=headers
                )

                if response.status_code != 200:
                    logger.error(f"Claude API HTTP {response.status_code}: {response.text}")
                    error_data = response.json() if response.text else {}
                    error_msg = f"HTTP Error {response.status_code}"
                    if error_data.get('error', {}).get('message'):
                        error_msg += f": {error_data['error']['message']}"
                    return {
                        'success': False,
                        'error': error_msg,
                        'raw_response': response.text
                    }

                decoded = response.json()

                if decoded.get('content') and decoded['content'][0].get('text'):
                    return {
                        'success': True,
                        'message': decoded['content'][0]['text']
                    }

                return {
                    'success': False,
                    'error': 'Invalid response format'
                }

        except Exception as e:
            return {
                'success': False,
                'error': f"CURL Error: {str(e)}"
            }

    def _detect_intent(self, message: str) -> str:
        """Detect intent from AI response or user message"""
        message_lower = message.lower()

        # Product search intent
        if re.search(r'(بحث|كتاب|search|book|find|product)', message_lower):
            return 'product_search'

        # Order intent
        if re.search(r'(طلب|order|buy|purchase|اطلب|بدي)', message_lower):
            return 'order'

        # Balance inquiry intent
        if re.search(r'(رصيد|balance|حساب|account|credit)', message_lower):
            return 'balance_inquiry'

        # Greeting intent
        if re.search(r'(مرحبا|hello|hi|السلام|صباح|مساء)', message_lower):
            return 'greeting'

        # Help intent
        if re.search(r'(مساعدة|help|ساعد)', message_lower):
            return 'help'

        return 'general'

    def format_product_results(self, products: list) -> str:
        """Generate product search results message"""
        if not products:
            return "❌ عذراً، لم أجد أي منتجات مطابقة لبحثك.\n\nيمكنك المحاولة بكلمات مختلفة أو الاتصال بنا مباشرة."

        message = "🔍 نتائج البحث:\n\n"

        for index, product in enumerate(products):
            num = index + 1
            message += f"{num}. **{product['item_name']}**\n"
            message += f"   📦 الكود: {product['item_code']}\n"
            message += f"   💰 السعر: {product['price']:,.0f} {settings.CURRENCY}\n"

            stock_status = "✅ متوفر" if product.get('stock_quantity', 0) > 0 else "❌ غير متوفر"
            message += f"   {stock_status}\n\n"

        message += "لطلب أي منتج، اكتب رقمه أو اسمه."

        return message

    async def analyze_image_and_search(
        self,
        image_url: str,
        product_search_func,
        lang: str = 'en'
    ) -> dict:
        """Analyze image and search for matching products using CLIP + Claude"""
        clip_candidates = None  # Will store CLIP results for hybrid approach

        try:
            logger.info(f"Starting image analysis for URL: {image_url}")

            # ===== CLIP VISUAL SEARCH (Primary Method) =====
            try:
                from services.clip_search import get_clip_service
                clip_service = get_clip_service()

                if clip_service and (clip_service.embeddings or clip_service.text_embeddings):
                    logger.info("Using CLIP visual search...")
                    clip_results = await clip_service.search_by_image(image_url, top_k=5)

                    # SIMPLIFIED: Trust CLIP if image match is strong (>0.8)
                    # This works for 20k+ products without constant tweaking
                    top_result = clip_results[0] if clip_results else {}
                    top_score = top_result.get('score', 0)
                    top_img_score = top_result.get('img_score', 0)
                    logger.info(f"CLIP found match with score {top_score:.3f} (img={top_img_score:.3f})")

                    # Trust CLIP if image score > 0.72 (strong visual match)
                    if top_img_score > 0.72:
                        # High confidence - trust CLIP results directly
                        # Show more results so user has options
                        max_results = 3 if top_img_score > 0.85 else 5
                        logger.info(f"CLIP high confidence - returning top {max_results} results")

                        products = []
                        for r in clip_results[:5]:
                            if r.get('item_name') and r.get('sku'):
                                products.append({
                                    'id': 0,
                                    'item_code': r['sku'],
                                    'item_name': r['item_name'],
                                    'price': r.get('price', 0),
                                    'stock_quantity': 1,
                                    'clip_score': r['score']
                                })
                            if len(products) >= max_results:
                                break

                        return {
                            'success': True,
                            'description': f"CLIP visual match (score: {top_score:.2f})",
                            'products': products,
                            'search_method': 'clip_visual'
                        }
                    elif top_score > 0.5:
                        # Medium confidence - save CLIP results but continue to Claude for verification
                        logger.info(f"CLIP score {top_score:.3f} is medium confidence, will verify with Claude")
                        # Store clip results for potential use later
                        clip_candidates = clip_results[:5]
                    else:
                        logger.info("CLIP score too low, falling back to Claude analysis")
                        clip_candidates = None
            except Exception as clip_error:
                logger.warning(f"CLIP search failed: {clip_error}")
                clip_candidates = None

            # ===== CLAUDE VISION ANALYSIS (Fallback) =====

            # Download image and convert to base64
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(image_url)

                if response.status_code != 200:
                    logger.error(f"Failed to download image from URL: {image_url}")
                    return {
                        'success': False,
                        'error': 'Failed to download image'
                    }

                image_data = response.content

            # Get content type from response
            content_type = response.headers.get('content-type', 'image/jpeg')

            # Convert to base64
            base64_image = base64.b64encode(image_data).decode('utf-8')
            logger.info(f"Image downloaded and encoded. MIME type: {content_type}, Size: {len(image_data)} bytes")

            # Map MIME type
            media_type_map = {
                'image/jpeg': 'image/jpeg',
                'image/jpg': 'image/jpeg',
                'image/png': 'image/png',
                'image/gif': 'image/gif',
                'image/webp': 'image/webp'
            }
            media_type = media_type_map.get(content_type, 'image/jpeg')

            # Use Claude's vision API to analyze the image
            system_prompt = "You are a product recognition assistant for a bookstore and stationery shop in Lebanon. When you see text in the image (especially Arabic, French, or English), extract it EXACTLY as written. Then identify what product it is."

            messages = [
                {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'image',
                            'source': {
                                'type': 'base64',
                                'media_type': media_type,
                                'data': base64_image
                            }
                        },
                        {
                            'type': 'text',
                            'text': '''IMPORTANT: Extract ALL text you see on the product in its ORIGINAL language (Arabic عربي, French, or English).

For BOOKS specifically:
- TITLE: The main book title (e.g., "سر المتسول" or "The Secret")
- AUTHOR: Author name (e.g., "روبير البيطار" or "Robert Bitar")
- PUBLISHER: Publisher or series name if visible (e.g., "Mithra", "دار الساقي")

For OTHER products:
- BRAND: Brand name (e.g., "DELI", "Faber-Castell")
- MODEL: Model name or number

Format your response EXACTLY like this:
TEXT ON PRODUCT: [ALL text you see, each line separately]
PRODUCT TYPE: [book, pen, pencil, notebook, etc.]
DESCRIPTION: [brief description of what you see]'''
                        }
                    ]
                }
            ]

            response = await self._call_claude_api(system_prompt, messages)

            if not response['success']:
                logger.error(f"Claude API error: {response.get('error', 'Unknown')}")
                return {
                    'success': False,
                    'error': response['error']
                }

            description = response['message']
            logger.info(f"Image analyzed: {description}")

            # Extract TEXT ON PRODUCT (brand/title) and PRODUCT TYPE (category)
            text_on_product = ""
            product_type = ""

            if "TEXT ON PRODUCT:" in description:
                parts = description.split("TEXT ON PRODUCT:")
                if len(parts) > 1:
                    text_section = parts[1].split("\n\n")[0].split("PRODUCT TYPE:")[0]
                    text_on_product = text_section.strip()
                    logger.info(f"Text on product: {text_on_product}")

            if "PRODUCT TYPE:" in description:
                parts = description.split("PRODUCT TYPE:")
                if len(parts) > 1:
                    type_section = parts[1].split("\n")[0].split("DESCRIPTION:")[0]
                    product_type = type_section.strip()
                    logger.info(f"Product type: {product_type}")

            # Build search queries - prioritize Arabic text for books
            products = []
            search_queries = []

            # Check if text contains Arabic (for Arabic books)
            has_arabic = any('\u0600' <= c <= '\u06FF' for c in text_on_product) if text_on_product else False

            if has_arabic:
                # For Arabic products (especially books), search TITLE first, then author
                import re
                lines = []
                for line in text_on_product.split('\n'):
                    line = line.strip()
                    # Remove punctuation that might prevent matching
                    line = re.sub(r'[!?.,;:()"\'\[\]{}]', '', line).strip()
                    if len(line) > 2:
                        lines.append(line)
                        logger.info(f"Arabic text found: '{line}'")

                # For books: title is usually the longer/second line, search it FIRST
                if product_type and 'book' in product_type.lower() and len(lines) >= 2:
                    # Reverse order: title first, then author
                    search_queries = list(reversed(lines))
                else:
                    search_queries = lines
                # Then try combined with product type
                if product_type:
                    search_queries.append(f"{text_on_product} {product_type}")
            else:
                # For non-Arabic, try "brand + product type" first (e.g., "DELI mechanical pencil")
                if text_on_product and product_type:
                    search_queries.append(f"{text_on_product} {product_type}")
                # Then try just brand/text
                if text_on_product:
                    for line in text_on_product.split('\n'):
                        line = line.strip()
                        if len(line) > 2:
                            search_queries.append(line)

            # Always try product type as fallback (e.g., "mechanical pencil", "Book")
            if product_type and product_type.lower() != 'book':  # Skip generic "Book"
                search_queries.append(product_type)

            # Execute searches and collect ALL results (for Arabic, try all lines)
            all_results = {}  # Use dict to dedupe by item_code

            logger.info(f"Search queries to try: {search_queries}")

            for i, query in enumerate(search_queries):
                found = await product_search_func(query, limit=50)
                if found:
                    logger.info(f"Found {len(found)} products using query: '{query}'")
                    for product in found:
                        item_code = product.get('item_code', '')
                        if item_code and item_code not in all_results:
                            all_results[item_code] = product

                    # For books: if FIRST query (title) finds 1-3 exact matches, stop
                    if has_arabic and i == 0 and 1 <= len(found) <= 3:
                        logger.info(f"Exact match found with title, stopping search")
                        break
                else:
                    logger.info(f"No products found for query: '{query}'")

                # For non-Arabic queries, stop at first match (brand search)
                if not has_arabic and found:
                    break

            products = list(all_results.values())
            logger.info(f"Total unique products after all searches: {len(products)}")

            # Fallback to English keywords if no products found
            if not products:
                keywords = self._extract_keywords(description)
                logger.info(f"Fallback to keywords: {', '.join(keywords)}")
                for keyword in keywords:
                    found = await product_search_func(keyword, limit=10)
                    if found:
                        products = found
                        break

            # If we have CLIP candidates, check if any match the text search results
            # This helps verify/boost correct matches
            if clip_candidates and products:
                clip_skus = {c.get('sku') for c in clip_candidates}
                product_skus = {p.get('item_code') for p in products}
                matching_skus = clip_skus & product_skus

                if matching_skus:
                    # Found products that match BOTH CLIP and text search - prioritize these
                    logger.info(f"Found {len(matching_skus)} products matching both CLIP and text search")
                    matched = [p for p in products if p.get('item_code') in matching_skus]
                    others = [p for p in products if p.get('item_code') not in matching_skus]
                    products = matched + others
                else:
                    # No overlap - check if CLIP has high confidence match
                    # If CLIP's top result has good image score, use it instead of generic text results
                    top_clip = clip_candidates[0] if clip_candidates else {}
                    top_clip_img = top_clip.get('img_score', 0)
                    if top_clip_img > 0.65:
                        logger.info(f"CLIP top match (img={top_clip_img:.3f}) doesn't match text results - using CLIP results")
                        # Convert CLIP results to product format
                        products = []
                        for r in clip_candidates[:5]:
                            if r.get('item_name') and r.get('sku'):
                                products.append({
                                    'id': 0,
                                    'item_code': r['sku'],
                                    'item_name': r['item_name'],
                                    'price': r.get('price', 0),
                                    'stock_quantity': 1,
                                    'clip_score': r.get('score', 0)
                                })

            # Limit to 5 most relevant products
            products = products[:5]
            logger.info(f"Final result: {len(products)} products found")

            return {
                'success': True,
                'description': description,
                'products': products
            }

        except Exception as e:
            logger.error(f"Image analysis error: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def _extract_keywords(self, text: str) -> list:
        """Extract keywords from description"""
        stop_words = [
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with',
            'is', 'are', 'was', 'were', 'this', 'that', 'these', 'those', 'from', 'by',
            'image', 'shows', 'depicts', 'features', 'picture', 'photo', 'photograph',
            'displayed', 'shown', 'includes', 'contains', 'has', 'have', 'also', 'comes'
        ]

        product_types = [
            'barbie', 'doll', 'book', 'pen', 'pencil', 'notebook', 'ruler', 'eraser',
            'backpack', 'bag', 'toy', 'game', 'puzzle', 'sticker', 'coloring',
            'crayon', 'marker', 'paint', 'brush', 'glue', 'scissors', 'calculator'
        ]

        priority_keywords = []

        # Extract capitalized words (brand names)
        brands = re.findall(r'\b([A-Z][a-z]{2,})\b', text)
        for brand in brands:
            brand_lower = brand.lower()
            if brand_lower in product_types or brand_lower not in stop_words:
                priority_keywords.append(brand_lower)

        # Extract all words
        words = text.lower().split()
        keywords = []

        for word in words:
            word = re.sub(r'[^a-z]', '', word)
            if word in product_types:
                priority_keywords.insert(0, word)
            elif len(word) > 3 and word not in stop_words:
                keywords.append(word)

        all_keywords = list(dict.fromkeys(priority_keywords + keywords))
        return all_keywords[:5]

    async def test_connection(self) -> dict:
        """Test Claude API connection"""
        test_prompt = "You are a test assistant."
        test_messages = [{'role': 'user', 'content': 'Say hello'}]

        result = await self._call_claude_api(test_prompt, test_messages)

        return {
            'success': result['success'],
            'message': 'Claude API connection successful' if result['success'] else 'Connection failed',
            'error': result.get('error')
        }
