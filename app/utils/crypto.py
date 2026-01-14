"""
Token encryption utilities using Fernet symmetric encryption
"""

import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Cache the Fernet instance
_fernet_instance = None


def get_fernet() -> Fernet:
    """Get or create Fernet encryption instance"""
    global _fernet_instance
    
    if _fernet_instance is None:
        # Derive key from secret_key using PBKDF2
        secret_key = settings.secret_key.encode()
        salt = b'voice_agent_salt'  # Fixed salt for consistency
        
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(secret_key))
        _fernet_instance = Fernet(key)
    
    return _fernet_instance


def encrypt_token(token_data: bytes) -> str:
    """
    Encrypt OAuth token data
    
    Args:
        token_data: Pickled credentials bytes
        
    Returns:
        Base64-encoded encrypted string
    """
    try:
        fernet = get_fernet()
        encrypted = fernet.encrypt(token_data)
        return base64.urlsafe_b64encode(encrypted).decode('utf-8')
    except Exception as e:
        logger.error(f"Error encrypting token: {e}")
        raise


def decrypt_token(encrypted_token: str) -> bytes:
    """
    Decrypt OAuth token data
    
    Args:
        encrypted_token: Base64-encoded encrypted string
        
    Returns:
        Decrypted pickled credentials bytes
    """
    try:
        fernet = get_fernet()
        encrypted_bytes = base64.urlsafe_b64decode(encrypted_token.encode('utf-8'))
        decrypted = fernet.decrypt(encrypted_bytes)
        return decrypted
    except Exception as e:
        logger.error(f"Error decrypting token: {e}")
        raise
