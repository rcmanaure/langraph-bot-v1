from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from fastapi import Request


@dataclass
class ChannelEvent:
    """Normalized inbound message after crossing the channel boundary into the graph."""
    tenant_slug: str
    channel: str       # discriminator: "telegram" | "whatsapp" | ...
    user_id: str
    chat_id: str       # delivery target (may differ from user_id on some channels)
    text: str
    thread_id: str     # graph checkpoint key: tenant:{slug}:user:{id}:channel:{channel}


class ChannelAdapter(Protocol):
    """Contract every channel adapter must satisfy.

    Concrete implementations: TelegramAdapter (channels/telegram.py),
    WhatsAppAdapter (channels/whatsapp.py).

    Adding a new channel requires exactly these three methods — nothing else.
    """
    channel: str

    async def verify(self, request: "Request") -> bool:
        """Return True if the request's authentication credential is valid."""
        ...

    async def normalize(self, body: dict) -> ChannelEvent | None:
        """Parse raw webhook payload → ChannelEvent; None if no user text message.

        ponytail: returns first text message only; multi-message payloads (rare on WA)
        are not iterated — extend when a channel routinely batches messages.
        """
        ...

    async def send(self, event: ChannelEvent, text: str) -> None:
        """Deliver text response to the originating user."""
        ...
