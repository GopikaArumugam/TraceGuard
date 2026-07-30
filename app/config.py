import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # LLM Settings
    OPENAI_API_KEY: str = "mock-key"
    ANTHROPIC_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    
    # Database Settings
    DATABASE_URL: str = "sqlite:///./audit_logs.db"
    
    # FastAPI Server Settings
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    
    # Enable reading configurations from .env files
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
