from pydantic import BaseModel, Field

from app.schemas.conversations import ConversationMessageResponse
from app.schemas.modeling import ProviderRequest, SupportedLanguage


class ChatMessageRequest(ProviderRequest):
    text: str = Field(min_length=1, max_length=20000)
    conversation_id: str | None = None
    language: SupportedLanguage = "en"


class ChatMessageResult(BaseModel):
    conversation_id: str
    message: ConversationMessageResponse
