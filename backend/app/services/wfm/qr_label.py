"""Gép QR-címkék (reportlab).

Címkénként: QR-kód (a nyilvános támogatási oldal URL-je), a gép típusa,
vonalkódja (gép kód), gyári száma és a tulajdonos-felirat. A partner neve
SZÁNDÉKOSAN nincs rajta — gépcserénél így nem kell matricát cserélni.

Két formátum:
- A4 ív: 2×4 címke (105 × 74,25 mm), sima papírra / öntapadós ívre
- 51 × 25 mm: címkenyomtatóra (VOID címke), címkénként egy oldal
"""

from __future__ import annotations

import io

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdf_canvas

from app.services.wfm.worksheet_pdf import FONT, FONT_BOLD

LABEL_W = 105 * mm
LABEL_H = 74.25 * mm
COLS = 2
ROWS = 4

OWNER_TEXT = "X-Presso Coffee Kft tulajdona"


def _draw_qr(c: pdf_canvas.Canvas, url: str, x: float, y: float, size: float) -> None:
    widget = QrCodeWidget(url)
    bounds = widget.getBounds()
    w = bounds[2] - bounds[0]
    h = bounds[3] - bounds[1]
    drawing = Drawing(size, size, transform=[size / w, 0, 0, size / h, 0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, c, x, y)


def _draw_label(c: pdf_canvas.Canvas, item: dict, x: float, y: float) -> None:
    """Egy címke rajzolása; (x, y) a címke bal alsó sarka."""
    pad = 5 * mm
    qr_size = 44 * mm
    # keret (halvány, segít a vágásnál)
    c.setStrokeColorRGB(0.85, 0.87, 0.9)
    c.setLineWidth(0.5)
    c.rect(x, y, LABEL_W, LABEL_H)

    _draw_qr(c, item["url"], x + pad, y + LABEL_H - pad - qr_size, qr_size)

    text_x = x + pad + qr_size + 4 * mm
    text_y = y + LABEL_H - pad - 6 * mm
    c.setFillColorRGB(0, 0, 0)
    c.setFont(FONT_BOLD, 11)
    name = str(item.get("name") or "")[:26]
    c.drawString(text_x, text_y, name)
    c.setFont(FONT, 9)
    text_y -= 6 * mm
    c.drawString(text_x, text_y, f"Kód: {item.get('barcode') or '—'}")
    text_y -= 5 * mm
    if item.get("serial_number"):
        c.drawString(text_x, text_y, f"Gyári szám: {str(item['serial_number'])[:20]}")
        text_y -= 5 * mm
    if not item.get("customer_owned"):
        c.setFillColorRGB(0.35, 0.4, 0.5)
        c.drawString(text_x, text_y, OWNER_TEXT)
        c.setFillColorRGB(0, 0, 0)

    # alsó sáv: felszólítás + a QR-oldal funkciói (link nélkül)
    c.setFont(FONT_BOLD, 10.5)
    c.drawString(x + pad, y + 15 * mm, "Olvassa be a QR-kódot a telefonjával!")
    c.setFont(FONT, 8)
    c.setFillColorRGB(0.3, 0.35, 0.45)
    c.drawString(x + pad, y + 10 * mm, "Hibabejelentés fotóval  ·  Azonnali segítség a géphez")
    c.drawString(x + pad, y + 5.5 * mm, "Kávérendelés  ·  Számláló-állás bejelentése")
    c.setFillColorRGB(0, 0, 0)


SMALL_W = 51 * mm
SMALL_H = 25 * mm


def _draw_small_label(c: pdf_canvas.Canvas, item: dict) -> None:
    """51 × 25 mm-es címke (címkenyomtató, VOID címke): QR balra, mellette a
    gép típusa, kódja és a tulajdonos-felirat."""
    pad = 1.5 * mm
    qr_size = SMALL_H - 2 * pad  # ~22 mm
    _draw_qr(c, item["url"], pad, pad, qr_size)

    text_x = pad + qr_size + 2 * mm
    max_w = SMALL_W - text_x - pad
    c.setFillColorRGB(0, 0, 0)
    c.setFont(FONT_BOLD, 8)
    name = str(item.get("name") or "")
    while name and c.stringWidth(name, FONT_BOLD, 8) > max_w:
        name = name[:-1]
    c.drawString(text_x, SMALL_H - pad - 3 * mm, name)
    c.setFont(FONT, 7)
    c.drawString(text_x, SMALL_H - pad - 6.8 * mm, f"Kód: {item.get('barcode') or '—'}")
    if item.get("serial_number"):
        c.drawString(text_x, SMALL_H - pad - 10.2 * mm, f"Gy.sz.: {str(item['serial_number'])[:16]}")
    c.setFont(FONT, 6.5)
    c.setFillColorRGB(0.3, 0.35, 0.45)
    # Ügyfél behozott gépén nincs tulajdon-felirat
    if not item.get("customer_owned"):
        c.drawString(text_x, pad + 4.8 * mm, OWNER_TEXT)
    c.drawString(text_x, pad + 1.5 * mm, "Olvassa be a QR-kódot!")
    c.setFillColorRGB(0, 0, 0)


def build_qr_labels_small_pdf(items: list[dict]) -> bytes:
    """51 × 25 mm-es címkék — címkénként egy PDF-oldal (címkenyomtatóhoz)."""
    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=(SMALL_W, SMALL_H))
    for index, item in enumerate(items):
        if index > 0:
            c.showPage()
            c.setPageSize((SMALL_W, SMALL_H))
        _draw_small_label(c, item)
    c.showPage()
    c.save()
    return buf.getvalue()


def _ascii(s: str) -> str:
    """Ékezet-mentesítés az EZPL belső fontjaihoz (kódlap-független)."""
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c)
    )


def build_qr_labels_ezpl(items: list[dict]) -> bytes:
    """Godex EZPL parancsfájl (.ezp) az 51×25 mm-es VOID címkéhez — a GoLabel
    megnyitja és a nyomtatóra tölti, vagy raw-nyomtatással közvetlenül
    kiküldhető. 203 dpi (8 dot/mm): 51 mm = 408 dot, 25 mm = 200 dot.
    QR: W parancs (x,y,mode,type,ec,mask,mul,len,rotate + adatsor).

    MINDEN szövegsor pontosan a gép nevének betűjével (AB-font, 1×) megy ki —
    a hosszú tulajdon-felirat ezért két sorba törve fér el. Ügyfél behozott
    gépénél (customer_owned) a tulajdon-felirat lemarad."""
    TEXT_X = 170
    MAX_CHARS = 18  # ennyi AB-karakter fér el a szövegoszlopban

    out: list[str] = []
    for item in items:
        url = str(item["url"])
        name = _ascii(str(item.get("name") or ""))[:MAX_CHARS]
        barcode = str(item.get("barcode") or "")
        serial = _ascii(str(item.get("serial_number") or ""))
        out += [
            "^Q25,3",   # címkemagasság 25 mm, rés 3 mm
            "^W51",     # címkeszélesség 51 mm
            "^H8",      # sötétség
            "^P1",      # példányszám
            "^S3",      # sebesség
            "^AT",
            "^C1",
            "^R0",
            "~Q+0",
            "^O0",
            "^D0",
            "^E12",
            "~R255",
            "^L",
            # a QR lejjebb tolva, hogy ne a lap tetején üljön
            f"W12,40,5,2,M,8,3,{len(url)},0",
            url,
        ]
        texts = [name, f"Kod: {barcode}"[:MAX_CHARS]]
        if serial:
            texts.append(f"Gy.sz: {serial}"[:MAX_CHARS])
        if not item.get("customer_owned"):
            texts += ["X-Presso Coffee", "Kft tulajdona"]
        texts.append("Olvassa be a QR-t!")
        # legfeljebb 6 sor: 8, 38, 68, 98, 128, 158 (egy sor ~20 dot magas)
        y = 8
        for text in texts:
            out.append(f"AB,{TEXT_X},{y},1,1,0,0,{text}")
            y += 30
        out.append("E")
    return ("\n".join(out) + "\n").encode("ascii", errors="replace")


# GoLabel (.ezpx) sablon — a felhasználó saját "gyáriszám-void" címkéjéből
# visszafejtve (QLabelSDK PrintJob XML): Code128 vonalkód (gyári szám / gép
# kód) + "X-Presso Coffee Kft." + "TULAJDONA" felirat, 51×25 mm, Godex G300
# hálózati nyomtatóra. A {sorszám}-helyek .format-tal töltődnek.
_EZPX_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<PrintJob xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <QLabelSDKVersion>1.2.5.2</QLabelSDKVersion>
  <Label>
    <SerialFormat>
{nil10}    </SerialFormat>
    <VariableFormat>
{nil30}    </VariableFormat>
    <qlabel>
      <GraphicShape xsi:type="WindowText" Style="Cross" IsPrint="true" Locked="false" bStroke="true" bFill="true" Direction="Angle0" X="108.370361" Y="166" Alignment="Left" AlignPointX="108.370361" AlignPointY="166" FontCmd="BankGothic Md BT,9.75,B&#xD;&#xA;" FontWidth="14.25" TextSpace="0">
        <qHitOnCircumferance>false</qHitOnCircumferance>
        <Selected>false</Selected>
        <iBackground_color>4294967295</iBackground_color>
        <Id>25</Id>
        <Name>W</Name>
        <DataField>None</DataField>
        <Prompt>None</Prompt>
        <DispData>TULAJDONA</DispData>
        <Data>TULAJDONA</Data>
        <BoundRectWidth>196.212234</BoundRectWidth>
        <BoundRectHeight>28.7781525</BoundRectHeight>
        <BoundRect>
          <Location>
            <X>108.370361</X>
            <Y>166</Y>
          </Location>
          <Size>
            <Width>196.212234</Width>
            <Height>28.7781525</Height>
          </Size>
          <X>108.370361</X>
          <Y>166</Y>
          <Width>196.212234</Width>
          <Height>28.7781525</Height>
        </BoundRect>
      </GraphicShape>
      <GraphicShape xsi:type="WindowText" Style="Cross" IsPrint="true" Locked="false" bStroke="true" bFill="true" Direction="Angle0" X="14.32251" Y="130" Alignment="Left" AlignPointX="14.32251" AlignPointY="130" FontCmd="BankGothic Md BT,11.25,B&#xD;&#xA;" FontWidth="9" TextSpace="0">
        <qHitOnCircumferance>false</qHitOnCircumferance>
        <Selected>false</Selected>
        <iBackground_color>4294967295</iBackground_color>
        <Id>3</Id>
        <Name>W</Name>
        <DataField>None</DataField>
        <Prompt>None</Prompt>
        <DispData>X-Presso Coffee Kft.</DispData>
        <Data>X-Presso Coffee Kft.</Data>
        <BoundRectWidth>390.784882</BoundRectWidth>
        <BoundRectHeight>33.2055626</BoundRectHeight>
        <BoundRect>
          <Location>
            <X>14.32251</X>
            <Y>130</Y>
          </Location>
          <Size>
            <Width>390.784882</Width>
            <Height>33.2055626</Height>
          </Size>
          <X>14.32251</X>
          <Y>130</Y>
          <Width>390.784882</Width>
          <Height>33.2055626</Height>
        </BoundRect>
      </GraphicShape>
      <GraphicShape xsi:type="BarCode" Style="Cross" IsPrint="true" Locked="false" bStroke="true" bFill="true" Direction="Angle0" X="38" Y="28" Alignment="Left" AlignPointX="38" AlignPointY="28" FontCmd="Arial,36,B&#xD;&#xA;" Symbology="Code128Auto" CaptionAlignment="BottomAndCenter" Height="50" Width="7" Narrow="3" Offset="1" bDisplayChecksum="false" bDisplayStartStopChar="false" bBuiltinFont="false" Code128Subset="Auto">
        <qHitOnCircumferance>false</qHitOnCircumferance>
        <Selected>false</Selected>
        <iBackground_color>4294967295</iBackground_color>
        <Id>0</Id>
        <Name>B</Name>
        <DataField>None</DataField>
        <Prompt>None</Prompt>
        <DispData>{code}</DispData>
        <Data>{code}</Data>
        <BoundRectWidth>336</BoundRectWidth>
        <BoundRectHeight>105</BoundRectHeight>
        <BoundRect>
          <Location>
            <X>38</X>
            <Y>28</Y>
          </Location>
          <Size>
            <Width>336</Width>
            <Height>105</Height>
          </Size>
          <X>38</X>
          <Y>28</Y>
          <Width>336</Width>
          <Height>105</Height>
        </BoundRect>
      </GraphicShape>
    </qlabel>
    <VariableOpFormat />
    <DateFormat>y2-me-dd</DateFormat>
    <TimeFormat>h:m:s</TimeFormat>
    <DataBaseFormat>None</DataBaseFormat>
    <DataBaseFilePath />
    <DataBaseSelection />
    <UserID />
    <Password />
    <IntegratedSecurity>false</IntegratedSecurity>
    <RowIndex>0</RowIndex>
  </Label>
  <Setup LabelLength="25" LabelWidth="51" LeftMargin="0" TopMargin="0" LabelType="0" GapLength="3" FeedLength="0" ZSign="45" BlackMark="0" Position="0" Speed="3" Copy="1" bCopyDataBase="false" CopyField="None" Stripper="0" LabelsPerCut="0" Rotate180="200" Stop="12" Darkness="13" Number="1" bNumberDataBase="false" NumberField="None" PageDirection="Portrait" PrintMode="0">
    <Layout Shape="2" AcrossType="Copied" PageDirection="Portrait" HorAcross="1" VerAcross="1" HorGap="0" VerGap="0" HorAcrossMode1="1" VerAcrossMode1="1" LabelMode="0" HorGapMode1="0" VerGapMode1="0" />
    <Description>{desc}</Description>
    <UnitType>Mm</UnitType>
    <Dpi>203</Dpi>
  </Setup>
  <DriverName>Godex G300 (Hálózat)</DriverName>
  <PrinterModel>G300</PrinterModel>
  <PrinterLanguage>EZPL</PrinterLanguage>
  <USBName>00000000</USBName>
  <COMName />
  <CommunicationType>Network</CommunicationType>
  <NetworkIPAddress>3232235806</NetworkIPAddress>
  <NetworkPort>9100</NetworkPort>
  <BaudRate>9600</BaudRate>
  <ComSettings>N81</ComSettings>
  <StandaloneDbSearchKey />
  <StandaloneDbEnable>false</StandaloneDbEnable>
</PrintJob>
"""


def _xml_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_qr_label_ezpx(item: dict) -> bytes:
    """GoLabel projekt (.ezpx) EGY géphez — a felhasználó saját VOID-címke
    sablonjának pontos mása, a vonalkódban a gép gyári számával (ennek
    híján a gép kódjával)."""
    code = str(item.get("serial_number") or item.get("barcode") or "")
    xml = _EZPX_TEMPLATE.format(
        nil10='      <string xsi:nil="true" />\n' * 10,
        nil30='      <string xsi:nil="true" />\n' * 30,
        code=_xml_escape(code),
        desc=_xml_escape(str(item.get("name") or "Cimke")[:60]),
    )
    return xml.encode("utf-8")


def build_qr_labels_ezpx_zip(items: list[dict]) -> bytes:
    """Több gép: gépenként egy .ezpx, ZIP-be csomagolva."""
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in items:
            barcode = str(item.get("barcode") or "cimke")
            zf.writestr(f"{barcode}.ezpx", build_qr_label_ezpx(item))
    return buf.getvalue()


def build_qr_labels_pdf(items: list[dict]) -> bytes:
    """Címkeív PDF (A4). ``items`` elemei: url, name, barcode, serial_number."""
    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    _width, height = A4

    for index, item in enumerate(items):
        slot = index % (COLS * ROWS)
        if index > 0 and slot == 0:
            c.showPage()
        col = slot % COLS
        row = slot // COLS
        x = col * LABEL_W
        y = height - (row + 1) * LABEL_H
        _draw_label(c, item, x, y)

    c.showPage()
    c.save()
    return buf.getvalue()
