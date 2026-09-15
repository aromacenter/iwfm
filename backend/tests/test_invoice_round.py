"""Hibajegy-kör: számla-dátumok, adagáras sor-név, átadási tételek ajánlatnál."""

from datetime import date, timedelta

from app.api.tasks import _handover_items
from app.models import Partner, Settlement, Worksheet
from app.services.wfm.billingo_service import PORTION_LINE_NAME, settlement_due_date


def _settlement(payment_method: str, due_days=None) -> Settlement:
    s = Settlement(payment_method=payment_method)
    s.due_days = due_days
    return s


def _partner(contract_days=None, partner_days=None) -> Partner:
    p = Partner(name="Teszt")
    p.contract_payment_terms_days = contract_days
    p.payment_terms_days = partner_days
    return p


def test_cash_and_card_due_today():
    today = date.today()
    assert settlement_due_date(_settlement("cash"), _partner(30)) == today
    assert settlement_due_date(_settlement("card", due_days=15), _partner(30)) == today


def test_transfer_due_priority():
    today = date.today()
    # eseti napok > szerződés > partner > 8 nap
    assert settlement_due_date(_settlement("transfer", due_days=3), _partner(30, 20)) == today + timedelta(days=3)
    assert settlement_due_date(_settlement("transfer"), _partner(30, 20)) == today + timedelta(days=30)
    assert settlement_due_date(_settlement("transfer"), _partner(None, 20)) == today + timedelta(days=20)
    assert settlement_due_date(_settlement("transfer"), _partner()) == today + timedelta(days=8)


def test_portion_line_name_mentions_rental():
    assert PORTION_LINE_NAME.startswith("Bérleti díj")


def _ws(**kw) -> Worksheet:
    ws = Worksheet(serial="KSZ-2026-0001")
    ws.works = kw.get("works", [])
    ws.repair_options = kw.get("repair_options", [])
    ws.materials = kw.get("materials", [])
    ws.maintenance_fee = kw.get("maintenance_fee")
    ws.fee_discount = kw.get("fee_discount", False)
    ws.quote_status = kw.get("quote_status", "none")
    return ws


def test_handover_items_accepted_quote_is_all_inclusive():
    """Elfogadott ajánlatnál CSAK a kiválasztott konstrukció + karbantartási
    díj fizetendő — az alkatrészek ára nem duplázódik rá."""
    ws = _ws(
        quote_status="accepted",
        repair_options=[{"name": "Nagyjavítás", "price_net": 40000}],
        works=[{"name": "Munkadíj", "cost_net": 30000, "price_net": 35000}],
        materials=[
            {"name": "vízpumpa", "qty": "1", "price_net": 6000},
            {"name": "Darálóbetét", "qty": "1", "price_net": 10000},
        ],
        maintenance_fee=5000,
    )
    items = _handover_items(ws)
    assert [i["name"] for i in items] == ["Nagyjavítás", "Karbantartási díj"]
    assert sum(i["amount_net"] for i in items) == 45000


def test_handover_items_without_quote_keeps_itemized_prices():
    ws = _ws(
        quote_status="none",
        works=[{"name": "Munkadíj", "price_net": 12000}],
        materials=[{"name": "tömítés", "qty": "2", "price_net": 500}],
    )
    items = _handover_items(ws)
    assert sum(i["amount_net"] for i in items) == 13000


def test_handover_items_declined_survey_fee():
    """Elutasított ajánlat: a works-be került felmérési díj fizetendő."""
    ws = _ws(
        quote_status="declined",
        repair_options=[],
        works=[{"name": "Felmérési díj (a javítást az ügyfél nem kérte)", "price_net": 5000}],
        materials=[{"name": "vízpumpa", "qty": "1", "price_net": 6000}],
    )
    items = _handover_items(ws)
    assert sum(i["amount_net"] for i in items) == 11000
