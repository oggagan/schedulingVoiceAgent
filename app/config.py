"""
Configuration management using Pydantic Settings
All configuration is loaded from environment variables
"""

import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )
    
    # Application
    app_name: str = "Voice Scheduling Agent"
    app_version: str = "1.0.0"
    debug: bool = False
    
    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    
    # OpenAI
    openai_api_key: str
    openai_realtime_url: str = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17"
    
    # Google OAuth2
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str = "http://localhost:8000/auth/callback"
    
    # Database
    database_url: str = "sqlite:///./data/voice_agent.db"
    
    # Logging
    log_level: str = "INFO"
    log_file: str = "./logs/app.log"
    log_max_bytes: int = 10 * 1024 * 1024  # 10MB
    log_backup_count: int = 5
    
    # Security
    # SECRET_KEY must be set via environment variable for production
    # Generate with: openssl rand -hex 32
    secret_key: str = os.environ.get(
        "SECRET_KEY",
        "change-this-in-production-use-openssl-rand-hex-32"
    )
    
    # CORS (comma-separated origins for production)
    cors_origins: str = "*"
    
    # Timezone (defaults to system timezone, can be overridden with env var like "Asia/Kolkata")
    timezone: str = "Asia/Kolkata"  # Default to India Kolkata (IST), can be changed via env var
    
    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS origins from comma-separated string"""
        if self.cors_origins == "*":
            return ["*"]
        return [origin.strip() for origin in self.cors_origins.split(",")]


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()


# Convenience access
settings = get_settings()
