"""
Migration script to import existing token.pickle to database
Run this once to migrate from file-based tokens to database storage
"""

import os
import pickle
from datetime import datetime

from app.database import init_db, SessionLocal
from app.models import User
from app.services.calendar import CalendarService
from app.utils.logger import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)


def migrate_token():
    """Migrate existing token.pickle to database"""
    token_file = 'data/token.pickle'
    
    if not os.path.exists(token_file):
        logger.info("No token.pickle file found. Nothing to migrate.")
        return
    
    # Initialize database
    init_db()
    db = SessionLocal()
    
    try:
        # Load existing token
        with open(token_file, 'rb') as f:
            creds = pickle.load(f)
        
        logger.info("Found existing token.pickle file")
        
        # Get user email from credentials
        user_email = None
        try:
            from googleapiclient.discovery import build
            service = build('oauth2', 'v2', credentials=creds)
            user_info = service.userinfo().get().execute()
            user_email = user_info.get('email')
            logger.info(f"Retrieved user email: {user_email}")
        except Exception as e:
            logger.warning(f"Could not retrieve user email: {e}")
            user_email = f"migrated_user_{datetime.now().strftime('%Y%m%d_%H%M%S')}@migrated.local"
        
        # Get or create user
        user = db.query(User).filter(User.email == user_email).first()
        if not user:
            user = User(email=user_email)
            db.add(user)
            db.commit()
            db.refresh(user)
            logger.info(f"Created user: {user_email}")
        else:
            logger.info(f"Using existing user: {user_email}")
        
        # Save credentials to user using CalendarService
        calendar_service = CalendarService(db=db, user_id=user.id)
        success = calendar_service.save_credentials(user.id, creds)
        
        if success:
            logger.info(f"Successfully migrated token to user {user.id} ({user_email})")
            
            # Optionally backup the old file
            backup_file = f"{token_file}.backup"
            if not os.path.exists(backup_file):
                import shutil
                shutil.copy2(token_file, backup_file)
                logger.info(f"Backed up token.pickle to {backup_file}")
            
            # Optionally remove the old file (uncomment if desired)
            # os.remove(token_file)
            # logger.info("Removed old token.pickle file")
        else:
            logger.error("Failed to save credentials to database")
    
    except Exception as e:
        logger.error(f"Migration failed: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        db.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Token Migration Script")
    print("=" * 60)
    print()
    print("This script will migrate your existing token.pickle file")
    print("to the database for multi-user support.")
    print()
    
    response = input("Do you want to proceed? (yes/no): ")
    if response.lower() in ['yes', 'y']:
        migrate_token()
        print()
        print("Migration complete!")
    else:
        print("Migration cancelled.")
