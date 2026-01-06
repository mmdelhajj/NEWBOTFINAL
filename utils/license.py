"""
License Validator with Auto-Registration & Trial Support
Automatically registers new installations for 3-day trial
"""

import httpx
import hashlib
import json
import os
import socket
import platform
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
from config import settings

logger = logging.getLogger(__name__)


class LicenseValidator:
    def __init__(self):
        self.license_server = settings.LICENSE_SERVER_URL or 'https://lic.proxpanel.com'
        self.domain = settings.SITE_DOMAIN or socket.gethostname() or 'unknown'
        self.version = settings.BOT_VERSION or '1.0.0'
        self.cache_expiry = 7200  # Cache validation for 2 hours

        # Storage paths
        self.storage_dir = Path(__file__).parent.parent / 'storage'
        self.cache_file = self.storage_dir / 'license_cache.json'
        self.license_key_file = self.storage_dir / 'license_key.txt'

        # Ensure storage directory exists
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        # Cached server IP
        self._cached_server_ip = None

    async def validate(self) -> dict:
        """Validate license - auto-registers if needed"""
        # Get or generate license key
        license_key = self._get_license_key()

        if not license_key:
            # First run - auto-register for trial
            registration = await self._auto_register()

            if not registration['success']:
                logger.error(f"LICENSE: Auto-registration failed - {registration['message']}")
                return {
                    'valid': False,
                    'message': registration['message'],
                    'is_trial': False,
                    'days_left': 0
                }

            license_key = registration['license_key']
            self._save_license_key(license_key)

            logger.info(f"LICENSE: Auto-registered successfully - {license_key} (Trial: {registration['days_left']} days)")

            return {
                'valid': True,
                'message': 'Trial license activated',
                'is_trial': registration.get('installation_type') == 'trial',
                'is_paid': registration.get('installation_type') == 'paid',
                'days_left': registration.get('days_left', 0),
                'expires_at': registration.get('expires_at'),
                'data': registration
            }

        # Check cache first
        cached = self._get_cached_validation()
        if cached is not None:
            # Send heartbeat in background (non-blocking)
            await self._send_heartbeat(license_key)
            return cached

        # Validate with remote server
        result = await self._validate_remote(license_key)

        # Cache successful validation
        if result['valid']:
            self._cache_validation(result)

        # Send heartbeat
        await self._send_heartbeat(license_key)

        return result

    async def _auto_register(self) -> dict:
        """Auto-register new installation for trial"""
        try:
            fingerprint = self._get_server_fingerprint()
            ip = self._get_server_public_ip()

            url = f"{self.license_server}/api/register.php"

            data = {
                'domain': self.domain,
                'fingerprint': fingerprint,
                'ip': ip,
                'version': self.version,
                'product': 'whatsbot'  # Different product than OLT Manager
            }

            async with httpx.AsyncClient(timeout=15.0, verify=False) as client:
                response = await client.post(url, data=data)

                if response.status_code != 200:
                    return {
                        'success': False,
                        'message': f"Cannot connect to license server (HTTP {response.status_code})"
                    }

                result = response.json()

                if not result or not result.get('success'):
                    return {
                        'success': False,
                        'message': result.get('message', 'Registration failed') if result else 'Registration failed'
                    }

                return {
                    'success': True,
                    'license_key': result['data']['license_key'],
                    'installation_type': result['data'].get('installation_type', 'trial'),
                    'days_left': result['data'].get('days_left', 3),
                    'expires_at': result['data'].get('expires_at'),
                    'is_trial': result['data'].get('installation_type') == 'trial'
                }

        except Exception as e:
            return {
                'success': False,
                'message': f'Registration error: {str(e)}'
            }

    async def _validate_remote(self, license_key: str) -> dict:
        """Validate license with remote server"""
        try:
            fingerprint = self._get_server_fingerprint()
            server_ip = self._get_server_public_ip()

            url = f"{self.license_server}/api/validate.php"
            params = {
                'key': license_key,
                'domain': self.domain,
                'fingerprint': fingerprint,
                'server_ip': server_ip,
                'product': 'whatsbot'
            }

            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                response = await client.get(url, params=params)

                if response.status_code != 200:
                    logger.error(f"LICENSE ERROR: Failed to connect to license server (HTTP {response.status_code})")

                    # If we can't reach server, check if we have a recent cache
                    cached = self._get_cached_validation(max_age=7200)  # 2-hour grace period
                    if cached is not None:
                        logger.warning("LICENSE: Using cached validation due to server connectivity issue")
                        return cached

                    return {
                        'valid': False,
                        'message': 'Cannot connect to license server',
                        'is_trial': False,
                        'days_left': 0
                    }

                data = response.json()

                if not data:
                    logger.error("LICENSE ERROR: Invalid response from license server")
                    return {
                        'valid': False,
                        'message': 'Invalid server response',
                        'is_trial': False,
                        'days_left': 0
                    }

                if data.get('success'):
                    is_trial = data.get('data', {}).get('installation_type') == 'trial'
                    days_left = int(data.get('data', {}).get('days_left', 0))

                    logger.info(f"LICENSE: Valid - Customer: {data['data'].get('customer')}, Expires: {data['data'].get('expires_at')}, Days Left: {days_left}")

                    return {
                        'valid': True,
                        'message': 'License valid',
                        'is_trial': is_trial,
                        'is_paid': not is_trial,
                        'days_left': days_left,
                        'expires_at': data['data'].get('expires_at'),
                        'data': data['data']
                    }
                else:
                    logger.error(f"LICENSE: Invalid - {data.get('message')}")
                    return {
                        'valid': False,
                        'message': data.get('message', 'License invalid'),
                        'is_trial': False,
                        'days_left': 0
                    }

        except Exception as e:
            logger.error(f"LICENSE EXCEPTION: {e}")
            return {
                'valid': False,
                'message': 'License validation error',
                'is_trial': False,
                'days_left': 0
            }

    def _get_server_public_ip(self) -> str:
        """Get server public IP"""
        if self._cached_server_ip is not None:
            return self._cached_server_ip

        try:
            # Try to get public IP
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            self._cached_server_ip = ip
            return ip
        except Exception:
            self._cached_server_ip = 'unknown'
            return 'unknown'

    async def _send_heartbeat(self, license_key: str):
        """Send heartbeat to license server (non-blocking)"""
        try:
            ip = self._get_server_public_ip()
            url = f"{self.license_server}/api/heartbeat.php"

            data = {
                'license_key': license_key,
                'ip': ip,
                'version': self.version,
                'product': 'whatsbot'
            }

            # Very short timeout - don't wait for response
            async with httpx.AsyncClient(timeout=1.0, verify=False) as client:
                await client.post(url, data=data)

        except Exception:
            # Ignore heartbeat errors
            pass

    def _get_server_fingerprint(self) -> str:
        """Get server fingerprint for hardware binding"""
        factors = [
            platform.node(),  # Hostname
            platform.machine(),  # Machine type
        ]

        # Linux machine ID
        for path in ['/etc/machine-id', '/var/lib/dbus/machine-id']:
            try:
                with open(path, 'r') as f:
                    factors.append(f.read().strip())
            except Exception:
                pass

        fingerprint = hashlib.md5('|'.join(filter(None, factors)).encode()).hexdigest()
        return fingerprint

    def _get_license_key(self) -> Optional[str]:
        """Get license key from storage or settings"""
        # Check storage file first
        if self.license_key_file.exists():
            key = self.license_key_file.read_text().strip()
            if key:
                return key

        # Check settings as fallback (for manually configured licenses)
        if hasattr(settings, 'LICENSE_KEY') and settings.LICENSE_KEY:
            return settings.LICENSE_KEY

        return None

    def _save_license_key(self, license_key: str):
        """Save license key to storage"""
        try:
            self.license_key_file.write_text(license_key)
            os.chmod(self.license_key_file, 0o600)  # Secure permissions
        except Exception as e:
            logger.error(f"Failed to save license key: {e}")

    def _get_cached_validation(self, max_age: Optional[int] = None) -> Optional[dict]:
        """Get cached validation result"""
        if not self.cache_file.exists():
            return None

        try:
            cache = json.loads(self.cache_file.read_text())
            if not cache or 'timestamp' not in cache or 'result' not in cache:
                return None

            age = datetime.now().timestamp() - cache['timestamp']
            max_age = max_age or self.cache_expiry

            if age > max_age:
                return None

            return cache['result']
        except Exception:
            return None

    def _cache_validation(self, result: dict):
        """Cache validation result"""
        try:
            cache = {
                'timestamp': datetime.now().timestamp(),
                'result': result
            }
            self.cache_file.write_text(json.dumps(cache))
        except Exception as e:
            logger.error(f"Failed to cache validation: {e}")

    def clear_cache(self):
        """Clear validation cache"""
        if self.cache_file.exists():
            try:
                self.cache_file.unlink()
            except Exception:
                pass

    def get_license_info(self) -> dict:
        """Get license info without validation"""
        license_key = self._get_license_key()

        return {
            'license_key': license_key[:12] + '...' if license_key else 'Not registered yet',
            'domain': self.domain,
            'fingerprint': self._get_server_fingerprint(),
            'server': self.license_server
        }
