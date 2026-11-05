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


class ChatContext(BaseModel):
    """A kliens-oldal aktuális kontextusa — pl. a Gépek oldalon pipával
    kijelölt gépek, hogy a „a kijelölt gépre" utalás is érthető legyen."""

    page: str | None = Field(default=None, max_length=128)
    selected_machines: list[str] = Field(default_factory=list, max_length=50)


class ChatBody(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=40)
    context: ChatContext | None = None


class ChatEvent(BaseModel):
    kind: str
    label: str
    path: str | None = None


class PendingArg(BaseModel):
    key: str
    value: str


class PendingAction(BaseModel):
    """Megerősítésre váró írási művelet (151. kör): a token aláírt és rövid
    életű; a kártya az itt adott eszköz-nevet és paramétereket mutatja."""

    token: str
    tool: str
    args: list[PendingArg]


class ChatOut(BaseModel):
    reply: str
    events: list[ChatEvent]
    pending: list[PendingAction] = Field(default_factory=list)


class ConfirmBody(BaseModel):
    token: str = Field(min_length=20, max_length=8000)


class ConfirmOut(BaseModel):
    ok: bool
    tool: str
    message: str
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
            db, actor, [m.model_dump() for m in body.messages],
            context=body.context.model_dump() if body.context else None,
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
        pending=[PendingAction(**p) for p in result.get("pending") or []],
    )


@router.post("/confirm", response_model=ConfirmOut)
async def confirm(
    body: ConfirmBody,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    """Az asszisztens által javasolt írási művelet végrehajtása — csak a
    felhasználó megerősítésére, a SAJÁT jogosultságaival, egyszer."""
    try:
        result = await assistant.confirm_action(db, actor, body.token)
    except ValueError as exc:
        code = str(exc)
        status = 410 if code in ("action_expired", "action_used") else 422
        raise HTTPException(status_code=status, detail={"code": f"assistant.{code}"})
    return ConfirmOut(
        ok=result["ok"], tool=result["tool"], message=result["message"],
        events=[ChatEvent(**e) for e in result["events"]],
    )


# ─── Rendszer-kézikönyv (a /kezikonyv oldal + onboarding) ────────────────────


@router.get("/manual")
async def manual_index(
    q: str | None = None,
    _: User = Depends(get_current_user),
):
    """Tartalomjegyzék, vagy ``q``-val keresés (a how_to eszköz ugyanezt használja)."""
    from app.services.wfm import system_manual

    if q and q.strip():
        return {"query": q, "sections": system_manual.search(q, limit=5)}
    return {"sections": [dict(s) for s in system_manual.SECTIONS]}


@router.get("/manual/{key}")
async def manual_section(key: str, _: User = Depends(get_current_user)):
    from app.services.wfm import system_manual

    sec = system_manual.get(key)
    if sec is None:
        raise HTTPException(status_code=404, detail={"code": "manual.not_found"})
    return sec
