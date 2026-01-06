"""
Multilingual Response Templates
Provides predefined responses in Arabic, English, and French
"""

from typing import Optional, Dict, List, Any
from config import settings


class ResponseTemplates:
    """Multilingual response templates"""

    @staticmethod
    def welcome(lang: str, customer_name: Optional[str] = None, is_returning: bool = False) -> str:
        """Get welcome message"""
        if is_returning and customer_name:
            greeting = {
                'ar': f"أهلاً بعودتك {customer_name}!",
                'en': f"Welcome back {customer_name}!",
                'fr': f"Bon retour {customer_name}!"
            }.get(lang, f"Welcome back {customer_name}!")
        elif customer_name:
            greeting = {
                'ar': f"مرحباً {customer_name}!",
                'en': f"Hello {customer_name}!",
                'fr': f"Bonjour {customer_name}!"
            }.get(lang, f"Hello {customer_name}!")
        else:
            greeting = {
                'ar': "مرحباً!",
                'en': "Hello!",
                'fr': "Bonjour!"
            }.get(lang, "Hello!")

        messages = {
            'ar': f"""{greeting} 👋

أهلاً بك في *{settings.STORE_NAME}* 📚

كيف يمكنني مساعدتك اليوم؟

• 🏫 اكتب *كتب مدرسية* لطلب لوائح المدارس
• 📖 اكتب *منتجات* لرؤية الكتب المتاحة
• 📦 اكتب *طلباتي* لرؤية طلباتك
• 💰 اكتب *حساب* للاستعلام عن رصيدك
• ❓ اكتب *مساعدة* لمزيد من المعلومات""",

            'en': f"""{greeting} 👋

Welcome to *{settings.STORE_NAME}* 📚

How can I help you today?

• 🏫 Type *school books* to order school lists
• 📖 Type *products* to see available books
• 📦 Type *my orders* to view your orders
• 💰 Type *account* to check your balance
• ❓ Type *help* for more information""",

            'fr': f"""{greeting} 👋

Bienvenue à *{settings.STORE_NAME}* 📚

Comment puis-je vous aider aujourd'hui?

• 🏫 Tapez *livres scolaires* pour commander les listes scolaires
• 📖 Tapez *produits* pour voir les livres disponibles
• 📦 Tapez *mes commandes* pour voir vos commandes
• 💰 Tapez *compte* pour vérifier votre solde
• ❓ Tapez *aide* pour plus d'informations"""
        }

        return messages.get(lang, messages['en'])

    @staticmethod
    def help_message(lang: str) -> str:
        """Get help message"""
        messages = {
            'ar': f"""📚 *كيف يمكنني مساعدتك؟*

🔍 *للبحث عن كتاب:*
اكتب: "منتجات" لرؤية القائمة

🛒 *لطلب منتج:*
اختر رقم المنتج من القائمة

📦 *لرؤية طلباتك:*
اكتب: "طلباتي" أو "طلبي"

💳 *للاستعلام عن حسابك:*
اكتب: "رصيدي" أو "حساب"

📞 *للتواصل:*
{settings.STORE_LOCATION}""",

            'en': f"""📚 *How can I help you?*

🔍 *To search for a book:*
Type: "products" to see the list

🛒 *To order a product:*
Choose a product number from the list

📦 *To view your orders:*
Type: "my orders" or "orders"

💳 *To check your account:*
Type: "account" or "balance"

📞 *To contact us:*
{settings.STORE_LOCATION}""",

            'fr': f"""📚 *Comment puis-je vous aider?*

🔍 *Pour chercher un livre:*
Tapez: "produits" pour voir la liste

🛒 *Pour commander un produit:*
Choisissez un numéro de produit de la liste

📦 *Pour voir vos commandes:*
Tapez: "mes commandes" ou "commande"

💳 *Pour vérifier votre compte:*
Tapez: "compte" ou "solde"

📞 *Pour nous contacter:*
{settings.STORE_LOCATION}"""
        }

        return messages.get(lang, messages['en'])

    @staticmethod
    def product_list(
        lang: str,
        products: list,
        current_page: int,
        total_pages: int,
        search_suggestion: Optional[dict] = None
    ) -> str:
        """Get product list message with pagination"""
        header = {
            'ar': f"📚 *قائمة المنتجات* (صفحة {current_page} من {total_pages})\n\n",
            'en': f"📚 *Product List* (Page {current_page} of {total_pages})\n\n",
            'fr': f"📚 *Liste des Produits* (Page {current_page} de {total_pages})\n\n"
        }.get(lang, f"📚 *Product List* (Page {current_page} of {total_pages})\n\n")

        message = header

        for index, product in enumerate(products):
            num = index + 1
            name = product.get('item_name', '')
            price = f"{product.get('price', 0):,.0f}"

            # Stock status
            stock_qty = product.get('stock_quantity', 0)
            if stock_qty > 0:
                stock_info = '✅'
            else:
                expected = product.get('expected_arrival')
                if expected:
                    if expected == '1970-01-01':
                        stock_info = {
                            'ar': "❌ (قريباً)",
                            'en': "❌ (coming soon)",
                            'fr': "❌ (bientôt)"
                        }.get(lang, "❌ (coming soon)")
                    else:
                        stock_info = {
                            'ar': f"❌ (متوقع: {expected})",
                            'en': f"❌ (arriving: {expected})",
                            'fr': f"❌ (arrivée: {expected})"
                        }.get(lang, f"❌ (arriving: {expected})")
                else:
                    stock_info = '❌'

            message += f"*{num}.* {name}\n"
            message += f"   💰 {price} {settings.CURRENCY} {stock_info}\n\n"

        footer = {
            'ar': "➡️ اكتب رقم المنتج للطلب (مثال: *1*)\n",
            'en': "➡️ Type product number to order (example: *1*)\n",
            'fr': "➡️ Tapez le numéro du produit pour commander (exemple: *1*)\n"
        }.get(lang, "➡️ Type product number to order (example: *1*)\n")

        if current_page < total_pages:
            footer += {
                'ar': "📄 اكتب *التالي* للصفحة التالية",
                'en': "📄 Type *next* for next page",
                'fr': "📄 Tapez *suivant* pour la page suivante"
            }.get(lang, "📄 Type *next* for next page")

        message += "\n" + footer

        if search_suggestion:
            keyword = search_suggestion.get('keyword', '')
            count = search_suggestion.get('count', 0)
            tip = {
                'ar': f"\n\n💡 *نصيحة:* للمزيد من المنتجات، جرب البحث عن '{keyword}' ({count} منتج)",
                'en': f"\n\n💡 *Tip:* For more products, try searching '{keyword}' ({count} products)",
                'fr': f"\n\n💡 *Astuce:* Pour plus de produits, essayez de rechercher '{keyword}' ({count} produits)"
            }.get(lang, f"\n\n💡 *Tip:* For more products, try searching '{keyword}' ({count} products)")
            message += tip

        return message

    @staticmethod
    def ask_name(lang: str, product_name: str) -> str:
        """Ask for customer name"""
        messages = {
            'ar': f"""✅ اخترت: *{product_name}*

👤 الرجاء إدخال اسمك الكامل:""",
            'en': f"""✅ You selected: *{product_name}*

👤 Please enter your full name:""",
            'fr': f"""✅ Vous avez sélectionné: *{product_name}*

👤 Veuillez entrer votre nom complet:"""
        }
        return messages.get(lang, messages['en'])

    @staticmethod
    def ask_quantity(lang: str, product_name: str) -> str:
        """Ask for quantity"""
        messages = {
            'ar': f"""📦 *{product_name}*

كم قطعة تريد؟

👉 اكتب الكمية (مثال: *5*)""",
            'en': f"""📦 *{product_name}*

How many pieces do you want?

👉 Type the quantity (example: *5*)""",
            'fr': f"""📦 *{product_name}*

Combien de pièces voulez-vous?

👉 Tapez la quantité (exemple: *5*)"""
        }
        return messages.get(lang, messages['en'])

    @staticmethod
    def order_confirmation(lang: str, order_data: dict) -> str:
        """Order confirmation message"""
        product = order_data.get('product_name', '')
        name = order_data.get('customer_name', '')
        email = order_data.get('customer_email', '')
        address = order_data.get('customer_address', '')
        quantity = order_data.get('quantity', 1)
        unit_price = f"{order_data.get('price', 0):,.0f}"
        total_price = f"{order_data.get('price', 0) * quantity:,.0f}"

        quantity_text = f" (x{quantity})" if quantity > 1 else ""

        email_line_ar = f"📧 *البريد:* {email}\n" if email else ""
        email_line_en = f"📧 *Email:* {email}\n" if email else ""
        email_line_fr = f"📧 *Email:* {email}\n" if email else ""

        unit_line_ar = f"💰 *السعر للقطعة:* {unit_price} {settings.CURRENCY}\n" if quantity > 1 else ""
        unit_line_en = f"💰 *Unit Price:* {unit_price} {settings.CURRENCY}\n" if quantity > 1 else ""
        unit_line_fr = f"💰 *Prix unitaire:* {unit_price} {settings.CURRENCY}\n" if quantity > 1 else ""

        messages = {
            'ar': f"""✅ *تم إنشاء طلبك بنجاح!*

📦 *المنتج:* {product}{quantity_text}
👤 *الاسم:* {name}
{email_line_ar}📍 *العنوان:* {address}
{unit_line_ar}💰 *المبلغ الإجمالي:* {total_price} {settings.CURRENCY}

سنتواصل معك قريباً لتأكيد التوصيل! 🙏""",

            'en': f"""✅ *Your order has been created successfully!*

📦 *Product:* {product}{quantity_text}
👤 *Name:* {name}
{email_line_en}📍 *Address:* {address}
{unit_line_en}💰 *Total:* {total_price} {settings.CURRENCY}

We will contact you soon to confirm delivery! 🙏""",

            'fr': f"""✅ *Votre commande a été créée avec succès!*

📦 *Produit:* {product}{quantity_text}
👤 *Nom:* {name}
{email_line_fr}📍 *Adresse:* {address}
{unit_line_fr}💰 *Total:* {total_price} {settings.CURRENCY}

Nous vous contacterons bientôt pour confirmer la livraison! 🙏"""
        }
        return messages.get(lang, messages['en'])

    @staticmethod
    def balance_info(lang: str, customer: dict) -> str:
        """Balance inquiry response"""
        name = customer.get('name', 'N/A')
        balance = f"{customer.get('balance', 0):,.0f}"
        credit_limit = f"{customer.get('credit_limit', 0):,.0f}"
        available = f"{(customer.get('credit_limit', 0) - abs(customer.get('balance', 0))):,.0f}"

        messages = {
            'ar': f"""💳 *معلومات حسابك:*

👤 الاسم: {name}
💰 الرصيد: {balance} {settings.CURRENCY}
📊 الحد الائتماني: {credit_limit} {settings.CURRENCY}
✅ المتاح: {available} {settings.CURRENCY}""",

            'en': f"""💳 *Your Account Information:*

👤 Name: {name}
💰 Balance: {balance} {settings.CURRENCY}
📊 Credit Limit: {credit_limit} {settings.CURRENCY}
✅ Available: {available} {settings.CURRENCY}""",

            'fr': f"""💳 *Informations sur votre compte:*

👤 Nom: {name}
💰 Solde: {balance} {settings.CURRENCY}
📊 Limite de crédit: {credit_limit} {settings.CURRENCY}
✅ Disponible: {available} {settings.CURRENCY}"""
        }
        return messages.get(lang, messages['en'])

    @staticmethod
    def invalid_input(lang: str) -> str:
        """Invalid input message"""
        messages = {
            'ar': "❌ عذراً، لم أفهم طلبك.\n\nاكتب *مساعدة* لرؤية الخيارات المتاحة.",
            'en': "❌ Sorry, I didn't understand your request.\n\nType *help* to see available options.",
            'fr': "❌ Désolé, je n'ai pas compris votre demande.\n\nTapez *aide* pour voir les options disponibles."
        }
        return messages.get(lang, messages['en'])

    @staticmethod
    def product_not_available(lang: str) -> str:
        """Product not available message"""
        messages = {
            'ar': "❌ عذراً، هذا المنتج غير متوفر حالياً.",
            'en': "❌ Sorry, this product is currently unavailable.",
            'fr': "❌ Désolé, ce produit est actuellement indisponible."
        }
        return messages.get(lang, messages['en'])

    @staticmethod
    def account_not_linked(lang: str) -> str:
        """Account not linked message"""
        messages = {
            'ar': "💳 عذراً، حسابك غير مرتبط بنظامنا بعد.\n\nالرجاء التواصل معنا لربط حسابك.",
            'en': "💳 Sorry, your account is not linked to our system yet.\n\nPlease contact us to link your account.",
            'fr': "💳 Désolé, votre compte n'est pas encore lié à notre système.\n\nVeuillez nous contacter pour lier votre compte."
        }
        return messages.get(lang, messages['en'])

    @staticmethod
    def order_status_notification(lang: str, order_data: dict, new_status: str) -> str:
        """Order status change notification"""
        order_number = order_data.get('order_number', '')
        total_amount = f"{order_data.get('total_amount', 0):,.0f}"

        status_emojis = {
            'pending': '⏳',
            'confirmed': '✅',
            'preparing': '📦',
            'on_the_way': '🚚',
            'delivered': '✅',
            'out_of_stock': '❌',
            'cancelled': '🚫'
        }
        emoji = status_emojis.get(new_status, '📋')

        status_names = {
            'ar': {
                'pending': 'قيد الانتظار', 'confirmed': 'تم التأكيد',
                'preparing': 'قيد التحضير', 'on_the_way': 'في الطريق',
                'delivered': 'تم التوصيل', 'out_of_stock': 'غير متوفر',
                'cancelled': 'تم الإلغاء'
            },
            'en': {
                'pending': 'Pending', 'confirmed': 'Confirmed',
                'preparing': 'Preparing', 'on_the_way': 'On the Way',
                'delivered': 'Delivered', 'out_of_stock': 'Out of Stock',
                'cancelled': 'Cancelled'
            },
            'fr': {
                'pending': 'En attente', 'confirmed': 'Confirmé',
                'preparing': 'En préparation', 'on_the_way': 'En route',
                'delivered': 'Livré', 'out_of_stock': 'Rupture de stock',
                'cancelled': 'Annulé'
            }
        }

        status_name = status_names.get(lang, status_names['en']).get(new_status, new_status.replace('_', ' ').title())

        # Build items list
        items_list = ''
        items = order_data.get('items', [])
        for item in items:
            items_list += f"   • {item.get('product_name', '')}"
            if item.get('quantity', 1) > 1:
                items_list += f" (x{item['quantity']})"
            items_list += "\n"

        status_msg = ResponseTemplates._get_status_message(new_status, lang)

        messages = {
            'ar': f"""{emoji} *تحديث طلبك*

رقم الطلب: *{order_number}*
الحالة الجديدة: *{status_name}*

📦 *المنتجات:*
{items_list}
💰 المبلغ الإجمالي: {total_amount} {settings.CURRENCY}

{status_msg}""",

            'en': f"""{emoji} *Order Update*

Order Number: *{order_number}*
New Status: *{status_name}*

📦 *Products:*
{items_list}
💰 Total Amount: {total_amount} {settings.CURRENCY}

{status_msg}""",

            'fr': f"""{emoji} *Mise à jour de commande*

Numéro de commande: *{order_number}*
Nouveau statut: *{status_name}*

📦 *Produits:*
{items_list}
💰 Montant total: {total_amount} {settings.CURRENCY}

{status_msg}"""
        }
        return messages.get(lang, messages['en'])

    @staticmethod
    def _get_status_message(status: str, lang: str) -> str:
        """Get specific message for each status"""
        messages = {
            'confirmed': {
                'ar': '✅ تم تأكيد طلبك! سنبدأ بتحضيره قريباً.',
                'en': '✅ Your order has been confirmed! We will start preparing it soon.',
                'fr': '✅ Votre commande a été confirmée! Nous allons bientôt la préparer.'
            },
            'preparing': {
                'ar': '📦 جاري تحضير طلبك الآن!',
                'en': '📦 Your order is being prepared now!',
                'fr': '📦 Votre commande est en cours de préparation!'
            },
            'on_the_way': {
                'ar': '🚚 طلبك في الطريق إليك! سيصل قريباً.',
                'en': '🚚 Your order is on the way! It will arrive soon.',
                'fr': '🚚 Votre commande est en route! Elle arrivera bientôt.'
            },
            'delivered': {
                'ar': '✅ تم توصيل طلبك! نتمنى أن تستمتع بمشترياتك. شكراً لتسوقك معنا! 🙏',
                'en': '✅ Your order has been delivered! We hope you enjoy your purchase. Thank you for shopping with us! 🙏',
                'fr': '✅ Votre commande a été livrée! Nous espérons que vous apprécierez votre achat. Merci de faire vos achats avec nous! 🙏'
            },
            'out_of_stock': {
                'ar': '❌ عذراً، المنتج غير متوفر حالياً. سنتواصل معك قريباً.',
                'en': '❌ Sorry, the product is currently unavailable. We will contact you soon.',
                'fr': '❌ Désolé, le produit est actuellement indisponible. Nous vous contacterons bientôt.'
            },
            'cancelled': {
                'ar': '🚫 تم إلغاء طلبك.',
                'en': '🚫 Your order has been cancelled.',
                'fr': '🚫 Votre commande a été annulée.'
            }
        }
        return messages.get(status, {}).get(lang, '')
