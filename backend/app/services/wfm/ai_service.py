"""AI szolgáltatók (Anthropic Claude / Google Gemini) kliensrétege.

A kulcsok az ai_settings táblában élnek Fernet-titkosítva. Az aktív szolgáltató
adja majd az AI-alapú funkciókat (pl. feladat-kiosztási javaslat skillek
alapján); most a kapcsolat-teszt és egy egységes ``generate`` belépési pont él.

Anthropic: hivatalos ``anthropic`` SDK (AsyncAnthropic).
Gemini: REST ``generateContent`` httpx-szel.
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_pii
from app.models import AISettings

logger = logging.getLogger(__name__)

PROVIDERS = ("anthropic", "gemini")
DEFAULT_MODELS = {"anthropic": "claude-opus-4-8", "gemini": "gemini-3.5-flash"}


async def get_or_create_settings(db: AsyncSession) -> AISettings:
    row = (await db.execute(select(AISettings).where(AISettings.id == 1))).scalar_one_or_none()
    if row is None:
        row = AISettings(id=1)
        db.add(row)
        await db.flush()
    return row


def _provider_key(row: AISettings, provider: str) -> str | None:
    if provider == "anthropic":
        return decrypt_pii(row.anthropic_key_encrypted)
    if provider == "gemini":
        return decrypt_pii(row.gemini_key_encrypted)
    return None


def _provider_model(row: AISettings, provider: str) -> str:
    if provider == "anthropic":
        return row.anthropic_model or DEFAULT_MODELS["anthropic"]
    return row.gemini_model or DEFAULT_MODELS["gemini"]


async def _anthropic_generate(api_key: str, model: str, prompt: str, max_tokens: int) -> str:
    from anthropic import AsyncAnthropic

    async with AsyncAnthropic(api_key=api_key) as client:
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
    return "".join(block.text for block in response.content if block.type == "text")


async def _gemini_generate(api_key: str, model: str, prompt: str, max_tokens: int) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.post(
            url,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": max_tokens},
            },
        )
        res.raise_for_status()
        data = res.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        return ""


async def generate(
    db: AsyncSession,
    prompt: str,
    *,
    provider: str | None = None,
    max_tokens: int = 1024,
) -> str:
    """Szöveg generálása a kiválasztott (vagy az aktív) szolgáltatóval.

    ValueError, ha nincs konfigurálva; a szolgáltató hibái kivételként jönnek.
    """
    row = await get_or_create_settings(db)
    chosen = provider or row.active_provider
    if chosen not in PROVIDERS:
        raise ValueError("ai_not_configured")
    api_key = _provider_key(row, chosen)
    if not api_key:
        raise ValueError("ai_not_configured")
    model = _provider_model(row, chosen)

    if chosen == "anthropic":
        return await _anthropic_generate(api_key, model, prompt, max_tokens)
    return await _gemini_generate(api_key, model, prompt, max_tokens)


async def test_provider(db: AsyncSession, provider: str) -> str:
    """Kapcsolat-teszt: apró kérés a szolgáltató felé. Visszaadja a választ."""
    return await generate(
        db,
        "Válaszolj pontosan ennyit: OK",
        provider=provider,
        max_tokens=16,
    )
