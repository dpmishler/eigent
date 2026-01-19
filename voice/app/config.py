from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    deepgram_api_key: str = ""
    eigent_backend_url: str = "http://localhost:5001"
    voice_service_port: int = 5002

    # Deepgram Voice Agent settings
    deepgram_model: str = "nova-2"
    tts_model: str = "aura-asteria-en"
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"

    class Config:
        env_file = ".env"


settings = Settings()
