"""
CLIP Visual Search Service
Uses CLIP model for visual product matching
"""

import os
import logging
import json
import httpx
import numpy as np
from typing import Optional, List, Dict, Any
from PIL import Image
from io import BytesIO

logger = logging.getLogger(__name__)

# Will be initialized on first use
_clip_model = None
_clip_processor = None


def get_clip_model():
    """Lazy load CLIP model"""
    global _clip_model, _clip_processor

    if _clip_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading CLIP model (clip-ViT-B-32)...")
            _clip_model = SentenceTransformer('clip-ViT-B-32')
            logger.info("CLIP model loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load CLIP model: {e}")
            return None

    return _clip_model


class CLIPSearchService:
    """Visual search using CLIP embeddings"""

    def __init__(self, index_path: str = None):
        # Auto-detect index path based on where the script is running
        if index_path is None:
            import os
            script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            index_path = os.path.join(script_dir, "data", "clip_index.json")
        self.index_path = index_path
        self.embeddings: Dict[str, List[float]] = {}  # Image embeddings
        self.text_embeddings: Dict[str, List[float]] = {}  # Product name embeddings
        self.product_data: Dict[str, Dict] = {}
        self._load_index()

    def _load_index(self):
        """Load existing index from disk"""
        if os.path.exists(self.index_path):
            try:
                with open(self.index_path, 'r') as f:
                    data = json.load(f)
                    self.embeddings = data.get('embeddings', {})
                    self.text_embeddings = data.get('text_embeddings', {})
                    self.product_data = data.get('product_data', {})
                logger.info(f"Loaded CLIP index: {len(self.embeddings)} images, {len(self.text_embeddings)} texts")
            except Exception as e:
                logger.error(f"Failed to load CLIP index: {e}")

    def _save_index(self):
        """Save index to disk"""
        os.makedirs(os.path.dirname(self.index_path), exist_ok=True)
        with open(self.index_path, 'w') as f:
            json.dump({
                'embeddings': self.embeddings,
                'text_embeddings': self.text_embeddings,
                'product_data': self.product_data
            }, f)
        logger.info(f"Saved CLIP index: {len(self.embeddings)} images, {len(self.text_embeddings)} texts")

    def encode_text(self, text: str) -> Optional[List[float]]:
        """Encode text to CLIP embedding"""
        model = get_clip_model()
        if model is None:
            return None

        try:
            embedding = model.encode(text, convert_to_numpy=True)
            return embedding.tolist()
        except Exception as e:
            logger.error(f"Failed to encode text: {e}")
            return None

    def index_product_texts(self, products: List[Dict], batch_size: int = 100):
        """Index product names as text embeddings"""
        model = get_clip_model()
        if model is None:
            return {'success': False, 'error': 'CLIP model not available'}

        indexed = 0

        for i, product in enumerate(products):
            sku = product.get('item_code', '')
            name = product.get('item_name', '')
            if not sku or not name:
                continue

            # Skip if already indexed
            if sku in self.text_embeddings:
                continue

            # Encode product name
            embedding = self.encode_text(name)
            if embedding:
                self.text_embeddings[sku] = embedding
                self.product_data[sku] = {
                    'sku': sku,
                    'item_name': name,
                    'price': product.get('price', 0),
                    'category': product.get('category', '')
                }
                indexed += 1

                # Save periodically
                if indexed % batch_size == 0:
                    self._save_index()
                    logger.info(f"Indexed {indexed} product texts...")

        # Final save
        self._save_index()

        return {
            'success': True,
            'indexed': indexed,
            'total_in_index': len(self.text_embeddings)
        }

    async def search_image_vs_text(self, image_url: str, top_k: int = 10) -> List[Dict]:
        """Search for products by matching image against product name text embeddings"""
        if not self.text_embeddings:
            logger.warning("No text embeddings in CLIP index")
            return []

        # Encode query image
        query_embedding = await self.encode_image_from_url(image_url)
        if query_embedding is None:
            return []

        # Calculate similarities with text embeddings
        similarities = []
        for sku, text_embedding in self.text_embeddings.items():
            score = self.cosine_similarity(query_embedding, text_embedding)
            product_info = self.product_data.get(sku, {})
            similarities.append({
                'sku': sku,
                'score': score,
                'item_name': product_info.get('item_name', ''),
                'price': product_info.get('price', 0),
                'category': product_info.get('category', '')
            })

        # Sort by similarity
        similarities.sort(key=lambda x: x['score'], reverse=True)

        return similarities[:top_k]

    async def search_image_vs_image(self, image_url: str, top_k: int = 10) -> List[Dict]:
        """Search for products by matching image against indexed product images"""
        if not self.embeddings:
            logger.warning("No image embeddings in CLIP index")
            return []

        # Encode query image
        query_embedding = await self.encode_image_from_url(image_url)
        if query_embedding is None:
            return []

        # Calculate similarities with image embeddings
        similarities = []
        for sku, image_embedding in self.embeddings.items():
            score = self.cosine_similarity(query_embedding, image_embedding)
            product_info = self.product_data.get(sku, {})
            similarities.append({
                'sku': sku,
                'score': score,
                'item_name': product_info.get('item_name', ''),
                'price': product_info.get('price', 0),
                'category': product_info.get('category', '')
            })

        # Sort by similarity
        similarities.sort(key=lambda x: x['score'], reverse=True)

        return similarities[:top_k]

    async def search_by_image(self, image_url: str, top_k: int = 10) -> List[Dict]:
        """Smart search: Use combined image+text matching for better accuracy"""
        # Use combined search if we have both image and text embeddings
        # This helps distinguish visually similar products (mechanical vs graphite pencils)
        if self.embeddings and self.text_embeddings:
            results = await self.search_combined(image_url, top_k)
            if results and results[0].get('score', 0) > 0.4:
                return results

        # Fallback: image-to-image only
        if self.embeddings:
            results = await self.search_image_vs_image(image_url, top_k)
            if results and results[0].get('score', 0) > 0.5:
                logger.info(f"CLIP image-to-image match: {results[0]['score']:.3f}")
                return results

        # Fallback: image-to-text only
        if self.text_embeddings:
            results = await self.search_image_vs_text(image_url, top_k)
            if results:
                logger.info(f"CLIP image-to-text match: {results[0]['score']:.3f}")
                return results

        return []

    async def encode_image_from_url(self, image_url: str) -> Optional[List[float]]:
        """Encode image from URL to CLIP embedding"""
        model = get_clip_model()
        if model is None:
            return None

        try:
            # Handle file:// URLs for local testing
            if image_url.startswith("file://"):
                file_path = image_url[7:]
                with open(file_path, "rb") as f:
                    image_data = f.read()
            else:
                # Download image from URL
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(image_url)
                    if response.status_code != 200:
                        logger.error(f"Failed to download image: {response.status_code}")
                        return None
                    image_data = response.content

            # Load image with PIL
            image = Image.open(BytesIO(image_data))
            if image.mode != 'RGB':
                image = image.convert('RGB')

            # Encode with CLIP
            embedding = model.encode(image, convert_to_numpy=True)
            return embedding.tolist()

        except Exception as e:
            logger.error(f"Failed to encode image: {e}")
            return None

    def encode_image_from_path(self, image_path: str) -> Optional[List[float]]:
        """Encode local image file to CLIP embedding"""
        model = get_clip_model()
        if model is None:
            return None

        try:
            image = Image.open(image_path)
            if image.mode != 'RGB':
                image = image.convert('RGB')

            embedding = model.encode(image, convert_to_numpy=True)
            return embedding.tolist()

        except Exception as e:
            logger.error(f"Failed to encode image from path: {e}")
            return None

    def add_product(self, sku: str, embedding: List[float], product_info: Dict[str, Any]):
        """Add product to index"""
        self.embeddings[sku] = embedding
        self.product_data[sku] = product_info

    def index_products_from_directory(self, image_dir: str, products: List[Dict], batch_size: int = 100):
        """Index all product images from directory"""
        model = get_clip_model()
        if model is None:
            logger.error("CLIP model not available")
            return {'success': False, 'error': 'CLIP model not available'}

        indexed = 0
        skipped = 0

        for product in products:
            sku = product.get('item_code', '')
            if not sku:
                continue

            # Check if already indexed
            if sku in self.embeddings:
                skipped += 1
                continue

            # Find image file
            image_path = None
            for ext in ['.jpg', '.jpeg', '.png', '.webp']:
                path = os.path.join(image_dir, f"{sku}{ext}")
                if os.path.exists(path):
                    image_path = path
                    break

            if not image_path:
                skipped += 1
                continue

            # Encode image
            embedding = self.encode_image_from_path(image_path)
            if embedding:
                self.add_product(sku, embedding, {
                    'sku': sku,
                    'item_name': product.get('item_name', ''),
                    'price': product.get('price', 0),
                    'category': product.get('category', '')
                })
                indexed += 1

                # Save periodically
                if indexed % batch_size == 0:
                    self._save_index()
                    logger.info(f"Indexed {indexed} products...")

        # Final save
        self._save_index()

        return {
            'success': True,
            'indexed': indexed,
            'skipped': skipped,
            'total_in_index': len(self.embeddings)
        }

    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors"""
        a = np.array(vec1)
        b = np.array(vec2)
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

    async def search_combined(self, image_url: str, top_k: int = 10,
                              image_weight: float = 0.6, text_weight: float = 0.4) -> List[Dict]:
        """
        Combined search: Use BOTH image-to-image AND image-to-text matching.
        This helps distinguish between visually similar products (like mechanical vs graphite pencils)
        by also considering how well the image matches product names semantically.
        """
        if not self.embeddings and not self.text_embeddings:
            logger.warning("No embeddings in CLIP index")
            return []

        # Encode query image
        query_embedding = await self.encode_image_from_url(image_url)
        if query_embedding is None:
            return []

        # Get all products with both scores
        combined_scores = {}

        # Calculate image-to-image similarities
        if self.embeddings:
            for sku, img_emb in self.embeddings.items():
                img_score = self.cosine_similarity(query_embedding, img_emb)
                combined_scores[sku] = {'img_score': img_score, 'txt_score': 0}

        # Calculate image-to-text similarities (how well image matches product NAMES)
        if self.text_embeddings:
            for sku, txt_emb in self.text_embeddings.items():
                txt_score = self.cosine_similarity(query_embedding, txt_emb)
                if sku in combined_scores:
                    combined_scores[sku]['txt_score'] = txt_score
                else:
                    combined_scores[sku] = {'img_score': 0, 'txt_score': txt_score}

        # Calculate combined score and build results
        results = []
        for sku, scores in combined_scores.items():
            img_score = scores['img_score']
            txt_score = scores['txt_score']

            # Combined score: weighted average
            # Products that match BOTH visually AND semantically will rank higher
            combined = (image_weight * img_score) + (text_weight * txt_score)

            product_info = self.product_data.get(sku, {})
            results.append({
                'sku': sku,
                'score': combined,
                'img_score': img_score,
                'txt_score': txt_score,
                'item_name': product_info.get('item_name', ''),
                'price': product_info.get('price', 0),
                'category': product_info.get('category', '')
            })

        # Sort by combined score
        results.sort(key=lambda x: x['score'], reverse=True)

        if results:
            # Log top 5 results for debugging
            logger.info("=== CLIP Combined Search Top 5 ===")
            for i, r in enumerate(results[:5]):
                logger.info(f"  #{i+1}: {r['item_name'][:50]} "
                           f"(combined={r['score']:.3f}, img={r['img_score']:.3f}, txt={r['txt_score']:.3f})")

        return results[:top_k]

    def search_by_embedding(self, query_embedding: List[float], top_k: int = 5) -> List[Dict]:
        """Search for similar products by embedding vector (searches text embeddings)"""
        # Search through text embeddings (product names)
        search_dict = self.text_embeddings if self.text_embeddings else self.embeddings

        if not search_dict:
            return []

        similarities = []
        for sku, embedding in search_dict.items():
            score = self.cosine_similarity(query_embedding, embedding)
            product_info = self.product_data.get(sku, {})
            similarities.append({
                'sku': sku,
                'score': score,
                'item_name': product_info.get('item_name', ''),
                'price': product_info.get('price', 0),
                'category': product_info.get('category', '')
            })

        similarities.sort(key=lambda x: x['score'], reverse=True)
        return similarities[:top_k]

    def search_text_by_text(self, query: str, top_k: int = 10) -> List[Dict]:
        """Search for products by text query (text-to-text similarity)"""
        if not self.text_embeddings:
            return []

        query_embedding = self.encode_text(query)
        if query_embedding is None:
            return []

        return self.search_by_embedding(query_embedding, top_k)


# Global instance
_clip_service = None


def get_clip_service() -> Optional[CLIPSearchService]:
    """Get or create CLIP search service"""
    global _clip_service

    if _clip_service is None:
        _clip_service = CLIPSearchService()

    return _clip_service
