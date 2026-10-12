"""Saferpay (Worldline) JSON API kliens — kávégépes QR-fizetéshez.

Payment Page folyamat: Initialize → (vevő a telefonján fizet) → a Saferpay
meghívja a notify URL-t → Assert → Capture. Minden hívás szerveroldali,
HTTP Basic auth-tal; a jelszó Fernet-titkosítva pihen az adatbázisban.

A hívások a ``_post`` függvényen mennek át — a tesztek ezt cserélik ki,
éles hálózati hívás tesztben nincs.
"""

from __future__ import annotations

import uuid

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import decrypt_pii
from app.models import SaferpaySettings

SPEC_VERSION = "1.40"
LIVE_BASE = "https://www.saferpay.com/api"
TEST_BASE = "https://test.saferpay.com/api"
# A Payment Page token kb. 20 percig él — mi ennél szigorúbbak vagyunk,
# a gép előtt álló vevőnek pár perc bőven elég.
ORDER_TTL_MINUTES = 10


class SaferpayError(Exception):
    def __init__(self, name: str, message: str):
        super().__init__(f"{name}: {message}")
        self.name = name
        self.message = message


class SaferpayNotConfigured(SaferpayError):
    def __init__(self):
        super().__init__("NOT_CONFIGURED", "Saferpay credentials missing")


async def get_settings_row(db: AsyncSession) -> SaferpaySettings:
    row = (
        await db.execute(select(SaferpaySettings).where(SaferpaySettings.id == 1))
    ).scalar_one_or_none()
    if row is None:
        row = SaferpaySettings(id=1)
        db.add(row)
        await db.flush()
    return row


def _configured(row: SaferpaySettings) -> bool:
    return bool(
        row.customer_id and row.terminal_id
        and row.api_username and row.api_password_encrypted
    )


async def _post(row: SaferpaySettings, path: str, payload: dict) -> dict:
    """Egy Saferpay JSON API hívás. Hibánál SaferpayError (ErrorName+Message)."""
    base = TEST_BASE if row.test_mode else LIVE_BASE
    auth = (row.api_username or "", decrypt_pii(row.api_password_encrypted) or "")
    async with httpx.AsyncClient(timeout=30.0) as cx:
        res = await cx.post(
            f"{base}{path}",
            json=payload,
            auth=auth,
            headers={"Accept": "application/json"},
        )
    if res.status_code >= 400:
        try:
            body = res.json()
            raise SaferpayError(
                str(body.get("ErrorName", res.status_code)),
                str(body.get("ErrorMessage", ""))[:256],
            )
        except ValueError:
            raise SaferpayError(str(res.status_code), res.text[:256])
    return res.json()


def _header(row: SaferpaySettings) -> dict:
    return {
        "SpecVersion": SPEC_VERSION,
        "CustomerId": row.customer_id,
        "RequestId": uuid.uuid4().hex,
        "RetryIndicator": 0,
    }


async def initialize_payment(
    db: AsyncSession, *, order_no: str, amount_huf: float, description: str,
    return_url: str, success_notify_url: str, fail_notify_url: str,
) -> tuple[str, str]:
    """Payment Page Initialize → (token, redirect_url)."""
    row = await get_settings_row(db)
    if not _configured(row):
        raise SaferpayNotConfigured()
    # Minor unit: a HUF ISO-kitevője 2, tehát Ft × 100.
    payload = {
        "RequestHeader": _header(row),
        "TerminalId": row.terminal_id,
        "Payment": {
            "Amount": {
                "Value": str(int(round(amount_huf * 100))),
                "CurrencyCode": row.currency or "HUF",
            },
            "OrderId": order_no,
            "Description": description[:256],
        },
        "ReturnUrl": {"Url": return_url},
        "Notification": {
            "SuccessNotifyUrl": success_notify_url,
            "FailNotifyUrl": fail_notify_url,
        },
    }
    out = await _post(row, "/Payment/v1/PaymentPage/Initialize", payload)
    return out["Token"], out["RedirectUrl"]


async def assert_payment(db: AsyncSession, token: str) -> dict:
    """Payment Page Assert → {status, transaction_id, brand}."""
    row = await get_settings_row(db)
    if not _configured(row):
        raise SaferpayNotConfigured()
    out = await _post(
        row, "/Payment/v1/PaymentPage/Assert",
        {"RequestHeader": _header(row), "Token": token},
    )
    tr = out.get("Transaction", {})
    means = out.get("PaymentMeans", {})
    brand = (means.get("Brand") or {}).get("Name") or means.get("DisplayText")
    return {
        "status": tr.get("Status"),  # AUTHORIZED | CAPTURED | PENDING
        "transaction_id": tr.get("Id"),
        "brand": brand,
    }


async def capture(db: AsyncSession, transaction_id: str) -> str:
    """Transaction Capture → Status (várhatóan CAPTURED)."""
    row = await get_settings_row(db)
    if not _configured(row):
        raise SaferpayNotConfigured()
    out = await _post(
        row, "/Payment/v1/Transaction/Capture",
        {
            "RequestHeader": _header(row),
            "TransactionReference": {"TransactionId": transaction_id},
        },
    )
    return str(out.get("Status", ""))
