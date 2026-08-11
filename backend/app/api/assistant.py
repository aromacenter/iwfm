"""AI-asszisztens chat végpont.

A kliens a teljes beszélgetés-előzményt küldi (állapotmentes szerver); a
válasz a szöveg + a végrehajtott műveletek eseménylistája (a kliens ebből
mutat visszaigazoló chipeket, és a navigate eseményre átnavigál).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db import get_db
from app.models import User
from app.services.wfm import assistant

router = APIRouter()


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=6000)


class ChatBody(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)


class ChatEvent(BaseModel):
    kind: str
    label: str
    path: str | None = None


class ChatOut(BaseModel):
    reply: str
    events: list[ChatEvent]


@router.post("/chat", response_model=ChatOut)
async def chat(
    body: ChatBody,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    if body.messages[-1].role != "user":
        raise HTTPException(status_code=422, detail={"code": "assistant.last_not_user"})
    try:
        result = await assistant.run_chat(
            db, actor, [m.model_dump() for m in body.messages]
        )
    except ValueError as exc:
        if str(exc) == "ai_not_configured":
            raise HTTPException(
                status_code=422, detail={"code": "assistant.ai_not_configured"}
            )
        raise
    return ChatOut(
        reply=result["reply"],
        events=[ChatEvent(**e) for e in result["events"]],
    )
