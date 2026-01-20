from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    deepgram_api_key: str = ""
    eigent_backend_url: str = "http://localhost:5001"
    voice_service_port: int = 5002

    # Deepgram Voice Agent settings
    deepgram_model: str = "nova-2"
    tts_model: str = "aura-asteria-en"
    llm_provider: str = "anthropic"
    llm_model: str = "claude-3-5-haiku-latest"

    @field_validator("deepgram_api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "DEEPGRAM_API_KEY is required. "
                "Please set it in your environment or .env file."
            )
        return v.strip()

    class Config:
        env_file = ".env"


settings = Settings()
