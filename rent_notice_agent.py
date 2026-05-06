"""
╔══════════════════════════════════════════════════════════════╗
║   P Ventures — Florida Non-Payment Notice Agent             ║
║   DoorLoop API  →  3-Day Notice PDF  →  Gmail                ║
║   Runs daily at 9am ET via GitHub Actions                    ║
╚══════════════════════════════════════════════════════════════╝

SETUP (one-time):
  1. pip install requests reportlab
  2. Fill in GMAIL_ADDRESS, GMAIL_APP_PASSWORD, NOTIFY_EMAIL below
     (or set as GitHub Secrets — see README)
  3. Test locally:  python rent_notice_agent.py
  4. Push to private GitHub repo → Actions runs it daily for free
"""

import io
import json
import logging
import os
import smtplib
from datetime import date, datetime, timedelta
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

# ─────────────────────────────────────────────────────────────
#  CONFIG
#  DoorLoop API key and company info are pre-filled.
#  Add your Gmail credentials — either here or as GitHub Secrets.
# ─────────────────────────────────────────────────────────────
CONFIG = {
    # ── DoorLoop (your key) ───────────────────────────────────
    "DOORLOOP_API_KEY": os.environ.get(
        "DOORLOOP_API_KEY",
        "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ0eXBlIjoiQVBJIiwiaWQiOiI2OWZiNjNkMzJhNTY5ODA2NWZjYWMwMDIiLCJleHAiOjIwOTM0NDI3NzF9.bAsq3PTzU5Smnbm-D2diFAyJ2jXS6oT0uQL_e2UKFuU"
    ),

    # ── Company info (matches your PDF template) ──────────────
    "COMPANY_NAME":     "P Ventures",
    "LANDLORD_NAME":    "Tyde Piccoli",
    "LANDLORD_TITLE":   "Manager",
    "MAILING_ADDRESS":  "PO Box 401, Cumberland, RI 02864",

    # ── Signature image ───────────────────────────────────────
    # Path to signature.png — include this file in your GitHub repo
    # alongside rent_notice_agent.py
    "SIGNATURE_PATH":   os.path.join(os.path.dirname(__file__), "signature.png"),

    # ── Gmail / Google Workspace ──────────────────────────────
    # The address notices send FROM (must be your Google Workspace email)
    # App Password: myaccount.google.com/apppasswords
    "GMAIL_ADDRESS":      os.environ.get("GMAIL_ADDRESS",      "risingtydemgmt@gmail.com"),
    "GMAIL_APP_PASSWORD": os.environ.get("GMAIL_APP_PASSWORD", "yiqe gqib plyd esom"),

    # ── Internal CC — you receive a copy of every notice sent ─
    "NOTIFY_EMAIL":       os.environ.get("NOTIFY_EMAIL",       "tyde@piccoliventures.com"),

    # ── Notice trigger ────────────────────────────────────────
    # Script fires on EXACTLY this day — won't re-send on day 10, 11, etc.
    "DAYS_OVERDUE_THRESHOLD": 9,

    # Late fees: your lease says $150 after 5 days + $25/day (max $400/mo)
    # Florida law only allows including late fees in the 3-day notice
    # if the lease calls them "additional rent." Set True if yours does.
    "INCLUDE_LATE_FEES_IN_NOTICE": False,

    # Late fee schedule (shown in the Account Summary section of the notice)
    "LATE_FEE_DESCRIPTION": "Per Lease: $150 after 5 days, plus $25/day thereafter, max $400/mo",
}

# ─────────────────────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

DOORLOOP_BASE = "https://api.doorloop.com"


# ─────────────────────────────────────────────────────────────
#  DOORLOOP API
# ─────────────────────────────────────────────────────────────
def dl_get(endpoint: str, params: dict = None) -> dict:
    headers = {
        "Authorization": f"Bearer {CONFIG['DOORLOOP_API_KEY']}",
        "Content-Type":  "application/json",
    }
    resp = requests.get(
        f"{DOORLOOP_BASE}{endpoint}",
        headers=headers,
        params=params or {},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def get_all_active_leases() -> list:
    leases, page = [], 1
    while True:
        data  = dl_get("/leases", {"status": "active", "page": page, "pageSize": 100})
        batch = data.get("data", [])
        leases.extend(batch)
        log.info(f"  Page {page}: {len(batch)} leases")
        if len(batch) < 100:
            break
        page += 1
    log.info(f"Total active leases: {len(leases)}")
    return leases


def get_lease_transactions(lease_id: str) -> list:
    data = dl_get(f"/leases/{lease_id}/transactions")
    return data.get("data", [])


def parse_balance(transactions: list) -> dict:
    """Sum unpaid rent and late fee charges. Track oldest unpaid date."""
    rent_bal = 0.0
    late_bal = 0.0
    earliest = None

    for txn in transactions:
        if txn.get("type") != "charge":
            continue
        unpaid = float(txn.get("balance", 0))
        if unpaid <= 0:
            continue

        memo    = (txn.get("memo") or "").lower()
        is_late = any(k in memo for k in ("late", "fee", "penalty"))
        if is_late:
            late_bal += unpaid
        else:
            rent_bal += unpaid

        raw = (txn.get("date") or "")[:10]
        try:
            d = datetime.strptime(raw, "%Y-%m-%d").date()
            if earliest is None or d < earliest:
                earliest = d
        except ValueError:
            pass

    return {
        "rent_balance":         round(rent_bal, 2),
        "late_fee_balance":     round(late_bal, 2),
        "total_balance":        round(rent_bal + late_bal, 2),
        "earliest_unpaid_date": earliest,
    }


def extract_tenant_info(lease: dict) -> dict:
    tenants = lease.get("tenants", [])
    primary = tenants[0] if tenants else {}
    first   = primary.get("firstName", "Tenant")
    last    = primary.get("lastName",  "")
    email   = primary.get("email",     "")

    unit_obj = lease.get("unit", {})
    unit     = unit_obj.get("name", "")
    prop     = lease.get("property", {})
    addr     = prop.get("address", {})
    street   = addr.get("street1", "")
    city     = addr.get("city",    "")
    state    = addr.get("state",   "FL")
    zipcode  = addr.get("zip",     "")
    county   = addr.get("county",  "St. Lucie")

    full_address = (
        f"{street}, Unit {unit}, {city}, {state} {zipcode}"
        if unit else
        f"{street}, {city}, {state} {zipcode}"
    )

    # Parse lease start date — used to skip leases that haven't started yet
    raw_start = (lease.get("startDate") or lease.get("start_date") or "")[:10]
    try:
        lease_start = datetime.strptime(raw_start, "%Y-%m-%d").date()
    except ValueError:
        lease_start = None

    return {
        "tenant_name":   f"{first} {last}".strip(),
        "tenant_email":  email,
        "unit":          unit,
        "property_name": prop.get("name", ""),
        "full_address":  full_address,
        "county":        county,
        "lease_id":      lease.get("id", ""),
        "monthly_rent":  float(lease.get("rent", 0)),
        "lease_start":   lease_start,
    }


# ─────────────────────────────────────────────────────────────
#  FLORIDA BUSINESS DAY CALCULATOR
#  §83.56(3): 3-day window excludes Sat, Sun, and legal holidays
# ─────────────────────────────────────────────────────────────
FL_HOLIDAYS = {
    date(2025,  1,  1), date(2025,  1, 20), date(2025,  5, 26),
    date(2025,  7,  4), date(2025,  9,  1), date(2025, 11, 11),
    date(2025, 11, 27), date(2025, 12, 25),
    date(2026,  1,  1), date(2026,  1, 19), date(2026,  5, 25),
    date(2026,  7,  4), date(2026,  9,  7), date(2026, 11, 11),
    date(2026, 11, 26), date(2026, 12, 25),
    date(2027,  1,  1), date(2027,  1, 18), date(2027,  5, 31),
    date(2027,  7,  5), date(2027,  9,  6), date(2027, 11, 11),
    date(2027, 11, 25), date(2027, 12, 24),
}


def add_fl_business_days(start: date, n: int) -> date:
    d, added = start, 0
    while added < n:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in FL_HOLIDAYS:
            added += 1
    return d


def days_since(target: date) -> int:
    return (date.today() - target).days


# ─────────────────────────────────────────────────────────────
#  PDF GENERATOR — matches your DP Ventures template exactly
# ─────────────────────────────────────────────────────────────
def generate_notice_pdf(tenant: dict, balance: dict) -> bytes:
    try:
        from reportlab.lib              import colors
        from reportlab.lib.enums        import TA_JUSTIFY, TA_LEFT
        from reportlab.lib.pagesizes    import letter
        from reportlab.lib.styles       import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units        import inch
        from reportlab.platypus         import (HRFlowable, Paragraph,
                                                 SimpleDocTemplate, Spacer,
                                                 Table, TableStyle)
    except ImportError:
        raise ImportError("Run: pip install reportlab")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        rightMargin=0.9*inch, leftMargin=0.9*inch,
        topMargin=0.75*inch,  bottomMargin=0.75*inch,
    )

    # ── Colours from your template ──
    DARK_BLUE = colors.HexColor("#1a2e44")
    MID_GRAY  = colors.HexColor("#444444")
    LT_GRAY   = colors.HexColor("#cccccc")
    NOTE_GRAY = colors.HexColor("#666666")

    # ── Styles ──
    logo_s  = ParagraphStyle("logo",  fontName="Helvetica-Bold", fontSize=22, textColor=DARK_BLUE)
    title_s = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=16, spaceAfter=2)
    sub_s   = ParagraphStyle("sub",   fontName="Helvetica",      fontSize=11, textColor=MID_GRAY)
    body_s  = ParagraphStyle("body",  fontName="Helvetica",      fontSize=11, leading=17,
                               spaceAfter=6, alignment=TA_JUSTIFY)
    bold_s  = ParagraphStyle("bold",  fontName="Helvetica-Bold", fontSize=11, leading=17)
    lbl_s   = ParagraphStyle("lbl",   fontName="Helvetica-Bold", fontSize=12, spaceBefore=4)
    sm_s    = ParagraphStyle("sm",    fontName="Helvetica",      fontSize=9,  leading=12,
                               textColor=NOTE_GRAY)

    today    = date.today()
    deadline = add_fl_business_days(today, 3)

    # Notice amount = rent only (safe FL default)
    notice_amount = (
        balance["total_balance"]
        if CONFIG["INCLUDE_LATE_FEES_IN_NOTICE"]
        else balance["rent_balance"]
    )

    E = []

    # ── DP Ventures logo/header ──────────────────────────────
    E.append(Paragraph(
        '<font color="#1a2e44"><b>\u25b6&nbsp;P VENTURES</b></font>', logo_s))
    E.append(Spacer(1, 0.22*inch))

    # ── Title block ──────────────────────────────────────────
    E.append(Paragraph("3-DAY NOTICE TO PAY RENT OR DELIVER POSSESSION", title_s))
    E.append(Paragraph("In Accordance with Florida Statute \u00a7 83.56", sub_s))
    E.append(Spacer(1, 0.18*inch))

    # ── TO / PROPERTY / DATE ─────────────────────────────────
    fields = [
        ["TO:", tenant["tenant_name"]],
        ["PROPERTY:", tenant["full_address"]],
        ["DATE OF DELIVERY:", today.strftime("%B %d, %Y")],
    ]
    ft = Table(fields, colWidths=[1.65*inch, 5.05*inch])
    ft.setStyle(TableStyle([
        ("FONTNAME",      (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME",      (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE",      (0, 0), (-1, -1), 11),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW",     (0, -1), (-1, -1), 0.5, LT_GRAY),
    ]))
    E.append(ft)
    E.append(Spacer(1, 0.18*inch))

    # ── Statutory paragraph ──────────────────────────────────
    E.append(Paragraph(
        f"<b>YOU ARE HEREBY NOTIFIED</b> that you are indebted to "
        f"<b>{CONFIG['COMPANY_NAME']} / {CONFIG['LANDLORD_NAME']}, "
        f"{CONFIG['LANDLORD_TITLE']}</b> in the sum of "
        f"<b>${notice_amount:,.2f}</b> for the rent and use of the premises described above.",
        body_s))

    E.append(Spacer(1, 0.14*inch))

    # ── Account Summary ──────────────────────────────────────
    E.append(Paragraph("ACCOUNT SUMMARY", lbl_s))
    E.append(Spacer(1, 0.06*inch))

    summary = [
        ["\u2022  Past Due Rent:",     f"${balance['rent_balance']:,.2f}"],
        ["\u2022  Late Fees:",
         f"${balance['late_fee_balance']:,.2f}   ({CONFIG['LATE_FEE_DESCRIPTION']})"],
        ["\u2022  TOTAL AMOUNT DUE:",  f"${balance['total_balance']:,.2f}"],
    ]
    st = Table(summary, colWidths=[1.95*inch, 4.75*inch])
    st.setStyle(TableStyle([
        ("FONTNAME",      (0, 0),  (1, -2), "Helvetica"),
        ("FONTNAME",      (0, -1), (1, -1), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0),  (-1, -1), 11),
        ("TOPPADDING",    (0, 0),  (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0),  (-1, -1), 4),
        ("VALIGN",        (0, 0),  (-1, -1), "TOP"),
    ]))
    E.append(st)
    E.append(Spacer(1, 0.18*inch))

    # ── Payment method ───────────────────────────────────────
    E.append(Paragraph(
        "<b>PAYMENT METHOD:</b> As per Section 3 of your Lease Agreement, "
        "all payments must be made electronically via <b>Doorloop.com</b>.",
        body_s))

    E.append(Spacer(1, 0.14*inch))

    # ── Legal Demand ─────────────────────────────────────────
    E.append(Paragraph("LEGAL DEMAND", lbl_s))
    E.append(Spacer(1, 0.06*inch))
    E.append(Paragraph(
        "I demand payment of the rent in full or possession of the premises within "
        "<b>three (3) days</b> (excluding Saturdays, Sundays, and legal holidays) "
        "from the date of delivery of this notice.",
        body_s))

    E.append(Spacer(1, 0.1*inch))
    E.append(Paragraph(
        f"<b>YOUR THREE-DAY PERIOD EXPIRES ON: "
        f"{deadline.strftime('%B %d')}, {deadline.year}.</b>",
        body_s))

    E.append(Spacer(1, 0.1*inch))
    E.append(Paragraph(
        "If the total amount due is not paid by the date specified above, your lease will be "
        "terminated and the Landlord will immediately proceed with legal action to evict you "
        "and seek all available damages, including court costs and attorney fees.",
        body_s))

    E.append(Spacer(1, 0.28*inch))

    # ── Signature block ──────────────────────────────────────
    E.append(Paragraph("LANDLORD/AGENT SIGNATURE:", bold_s))
    E.append(Spacer(1, 0.1*inch))

    # Embed actual signature image (black ink, 2.5" wide)
    sig_path = CONFIG.get("SIGNATURE_PATH", "signature.png")
    if os.path.exists(sig_path):
        try:
            from reportlab.platypus import Image as RLImage
            sig_w = 2.5 * inch
            sig_h = sig_w * (522 / 1316)   # preserve original aspect ratio
            E.append(RLImage(sig_path, width=sig_w, height=sig_h))
        except Exception:
            # Fallback to blank signature line if image fails
            E.append(Spacer(1, 0.35*inch))
            E.append(HRFlowable(width=3*inch, thickness=0.5,
                                 color=colors.black, hAlign="LEFT"))
    else:
        E.append(Spacer(1, 0.35*inch))
        E.append(HRFlowable(width=3*inch, thickness=0.5,
                             color=colors.black, hAlign="LEFT"))

    E.append(Spacer(1, 0.08*inch))
    E.append(Paragraph(
        f"<b>Mailing Address:</b> {CONFIG['MAILING_ADDRESS']}", body_s))
    E.append(Paragraph(
        f"<b>By: {CONFIG['LANDLORD_NAME']}, {CONFIG['LANDLORD_TITLE']}</b>", body_s))

    # ── Footer ───────────────────────────────────────────────
    E.append(Spacer(1, 0.3*inch))
    E.append(HRFlowable(width="100%", thickness=0.5, color=LT_GRAY))
    E.append(Spacer(1, 0.07*inch))
    E.append(Paragraph(
        f"Auto-generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}  \u00b7  "
        f"Lease ID: {tenant['lease_id']}  \u00b7  "
        f"Florida Statute \u00a7 83.56(3)",
        sm_s))

    doc.build(E)
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────
#  EMAIL — sends notice to tenant, CC's you
# ─────────────────────────────────────────────────────────────
def send_notice_email(tenant: dict, balance: dict, pdf_bytes: bytes) -> bool:
    notice_amount = (
        balance["total_balance"]
        if CONFIG["INCLUDE_LATE_FEES_IN_NOTICE"]
        else balance["rent_balance"]
    )
    today_str = date.today().strftime("%B %d, %Y")
    deadline  = add_fl_business_days(date.today(), 3).strftime("%B %d, %Y")

    msg            = MIMEMultipart("mixed")
    msg["From"]    = f"{CONFIG['COMPANY_NAME']} <{CONFIG['GMAIL_ADDRESS']}>"
    msg["To"]      = tenant["tenant_email"]
    msg["CC"]      = "risingtydemgmt@gmail.com"
    msg["Subject"] = f"NOTICE: Non-Payment of Rent — {tenant['full_address']}"

    html = f"""
<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#222;max-width:620px;margin:0 auto;">

<!-- Header -->
<div style="border-bottom:3px solid #1a2e44;padding-bottom:12px;margin-bottom:20px;">
  <p style="color:#1a2e44;font-weight:bold;font-size:22px;margin:0;">&#9654;&nbsp;P VENTURES</p>
</div>

<!-- Title block -->
<div style="margin-bottom:20px;">
  <h2 style="font-size:17px;font-weight:bold;margin:0 0 4px;">3-DAY NOTICE TO PAY RENT OR DELIVER POSSESSION</h2>
  <p style="font-size:12px;color:#666;margin:0;">In Accordance with Florida Statute &sect; 83.56</p>
</div>

<!-- To / Property / Date -->
<table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
  <tr>
    <td style="font-weight:bold;padding:5px 12px 5px 0;width:150px;vertical-align:top;">TO:</td>
    <td style="padding:5px 0;">{tenant['tenant_name']}</td>
  </tr>
  <tr>
    <td style="font-weight:bold;padding:5px 12px 5px 0;vertical-align:top;">PROPERTY:</td>
    <td style="padding:5px 0;">{tenant['full_address']}</td>
  </tr>
  <tr>
    <td style="font-weight:bold;padding:5px 12px 5px 0;">DATE OF DELIVERY:</td>
    <td style="padding:5px 0;">{today_str}</td>
  </tr>
</table>

<hr style="border:none;border-top:1px solid #ddd;margin:0 0 20px;">

<!-- Statutory notice -->
<p style="margin:0 0 16px;line-height:1.6;">
<strong>YOU ARE HEREBY NOTIFIED</strong> that you are indebted to
<strong>{CONFIG['LANDLORD_NAME']} / {CONFIG['COMPANY_NAME']}</strong>
in the sum of <strong>${notice_amount:,.2f}</strong> for the rent and use of the
premises described above.
</p>

<!-- Account Summary -->
<div style="margin-bottom:20px;">
  <p style="font-weight:bold;font-size:15px;margin:0 0 10px;">ACCOUNT SUMMARY</p>
  <table style="width:100%;border-collapse:collapse;background:#f8f8f8;border-radius:6px;">
    <tr>
      <td style="padding:8px 16px;border-bottom:1px solid #eee;">&#8226;&nbsp; Past Due Rent:</td>
      <td style="padding:8px 16px;border-bottom:1px solid #eee;font-weight:bold;">${balance['rent_balance']:,.2f}</td>
    </tr>
    <tr>
      <td style="padding:8px 16px;border-bottom:1px solid #eee;">&#8226;&nbsp; Late Fees:</td>
      <td style="padding:8px 16px;border-bottom:1px solid #eee;">${balance['late_fee_balance']:,.2f}&nbsp;&nbsp;<span style="font-size:12px;color:#666;">({CONFIG['LATE_FEE_DESCRIPTION']})</span></td>
    </tr>
    <tr style="background:#fff3f3;">
      <td style="padding:10px 16px;font-weight:bold;">&#8226;&nbsp; TOTAL AMOUNT DUE:</td>
      <td style="padding:10px 16px;font-weight:bold;color:#c00;font-size:16px;">${balance['total_balance']:,.2f}</td>
    </tr>
  </table>
</div>

<!-- Payment method -->
<p style="margin:0 0 16px;line-height:1.6;">
<strong>PAYMENT METHOD:</strong> As per Section 3 of your Lease Agreement, all payments
must be made electronically via <a href="https://doorloop.com" style="color:#1a2e44;">Doorloop.com</a>.
</p>

<!-- Legal Demand -->
<div style="background:#fff8f0;border-left:4px solid #c00;padding:14px 16px;margin-bottom:20px;border-radius:0 6px 6px 0;">
  <p style="font-weight:bold;font-size:15px;margin:0 0 8px;">LEGAL DEMAND</p>
  <p style="margin:0 0 10px;line-height:1.6;">
  I demand payment of the rent in full or possession of the premises within
  <strong>three (3) days</strong> (excluding Saturdays, Sundays, and legal holidays)
  from the date of delivery of this notice.
  </p>
  <p style="margin:0;font-weight:bold;">
  YOUR THREE-DAY PERIOD EXPIRES ON:&nbsp;
  <span style="color:#c00;font-size:15px;">{deadline}</span>
  </p>
</div>

<p style="margin:0 0 20px;line-height:1.6;">
If the total amount due is not paid by the date specified above, your lease will be
terminated and the Landlord will immediately proceed with legal action to evict you
and seek all available damages, including court costs and attorney fees.
</p>

<hr style="border:none;border-top:1px solid #ddd;margin:0 0 16px;">

<!-- Signature -->
<p style="margin:0 0 4px;"><strong>LANDLORD/AGENT:</strong></p>
<p style="margin:0 0 2px;"><strong>{CONFIG['LANDLORD_NAME']}, {CONFIG['LANDLORD_TITLE']}</strong></p>
<p style="margin:0 0 2px;">{CONFIG['COMPANY_NAME']}</p>
<p style="margin:0 0 16px;">Mailing Address: {CONFIG['MAILING_ADDRESS']}</p>

<hr style="border:none;border-top:1px solid #eee;margin:0 0 10px;">
<p style="font-size:11px;color:#999;line-height:1.5;">
This is an automated notice generated by your property management system.
The attached PDF (signed copy) may be used as evidence in legal proceedings
pursuant to Florida Statute &sect;&nbsp;83.56(3).
Lease ID: {tenant['lease_id']}
</p>

</body></html>"""

    msg.attach(MIMEText(html, "html"))

    # Attach PDF
    fname = (f"3Day_Notice_{tenant['tenant_name'].replace(' ','_')}"
             f"_{date.today()}.pdf")
    part = MIMEBase("application", "octet-stream")
    part.set_payload(pdf_bytes)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{fname}"')
    msg.attach(part)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(CONFIG["GMAIL_ADDRESS"], CONFIG["GMAIL_APP_PASSWORD"])
            srv.sendmail(
                CONFIG["GMAIL_ADDRESS"],
                [tenant["tenant_email"], "risingtydemgmt@gmail.com"],
                msg.as_string(),
            )
        log.info(f"  \u2705  Email sent \u2192 {tenant['tenant_email']}")
        return True
    except Exception as e:
        log.error(f"  \u274c  Email failed ({tenant['tenant_name']}): {e}")
        return False


# ─────────────────────────────────────────────────────────────
#  AUDIT LOG — one JSON line per notice attempt
# ─────────────────────────────────────────────────────────────
def audit(tenant: dict, balance: dict, action: str, ok: bool):
    entry = {
        "timestamp":    datetime.now().isoformat(),
        "action":       action,
        "success":      ok,
        "lease_id":     tenant["lease_id"],
        "tenant_name":  tenant["tenant_name"],
        "email":        tenant["tenant_email"],
        "address":      tenant["full_address"],
        "rent_balance": balance["rent_balance"],
        "late_balance": balance["late_fee_balance"],
        "total":        balance["total_balance"],
        "days_overdue": (
            days_since(balance["earliest_unpaid_date"])
            if balance["earliest_unpaid_date"] else None
        ),
    }
    with open("notice_audit_log.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")


# ─────────────────────────────────────────────────────────────
#  MAIN AGENT LOOP
# ─────────────────────────────────────────────────────────────
def run():
    log.info("=" * 62)
    log.info(f"  P Ventures — Rent Notice Agent  "
             f"{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log.info("=" * 62)

    threshold = CONFIG["DAYS_OVERDUE_THRESHOLD"]
    sent = failed = skipped = 0

    # 1. Fetch all active leases
    log.info("\n[1] Fetching active leases from DoorLoop...")
    try:
        leases = get_all_active_leases()
    except requests.HTTPError as e:
        log.error(f"DoorLoop API error: {e}")
        log.error("Check DOORLOOP_API_KEY and confirm Premium plan is active.")
        return

    # 2. Check each lease for overdue balance
    log.info(f"\n[2] Scanning {len(leases)} leases for day-{threshold} non-payment...")

    for lease in leases:
        tenant = extract_tenant_info(lease)
        log.info(f"\n  \u25ba {tenant['tenant_name']}  \u2014  {tenant['full_address']}")

        try:
            txns = get_lease_transactions(tenant["lease_id"])
        except requests.HTTPError as e:
            log.warning(f"  API error: {e}. Skipping.")
            skipped += 1
            continue

        balance = parse_balance(txns)

        # ── Guard 1: Lease must have started ──────────────────
        lease_start = tenant.get("lease_start")
        if lease_start and lease_start > date.today():
            log.info(f"  Lease starts {lease_start} — not yet active. Skipping.")
            skipped += 1
            continue

        # ── Guard 2: No unpaid rent → skip ────────────────────
        if balance["rent_balance"] <= 0:
            log.info("  Balance clear. \u2713")
            skipped += 1
            continue

        if not balance["earliest_unpaid_date"]:
            log.warning("  Cannot determine unpaid date. Skipping.")
            skipped += 1
            continue

        # ── Guard 3: Unpaid charge must be on/after lease start ──
        # Prevents phantom charges from before the lease began
        if lease_start and balance["earliest_unpaid_date"] < lease_start:
            log.warning(
                f"  Earliest unpaid charge ({balance['earliest_unpaid_date']}) "
                f"is before lease start ({lease_start}). Data issue — skipping."
            )
            skipped += 1
            continue

        overdue = days_since(balance["earliest_unpaid_date"])
        log.info(f"  Rent owed: ${balance['rent_balance']:.2f}  |  "
                 f"Late fees: ${balance['late_fee_balance']:.2f}  |  "
                 f"Day {overdue}")

        # Only fire on exactly day 9 (won't re-send on days 10, 11...)
        if overdue != threshold:
            log.info(f"  Not at day {threshold}. No action.")
            skipped += 1
            continue

        if not tenant["tenant_email"]:
            log.warning("  No email on file — update tenant profile in DoorLoop.")
            audit(tenant, balance, "skipped_no_email", False)
            skipped += 1
            continue

        # 3. Generate PDF
        log.info("  \u2192 Generating 3-day notice PDF...")
        try:
            pdf = generate_notice_pdf(tenant, balance)
            log.info(f"  PDF ready ({len(pdf):,} bytes)")
        except Exception as e:
            log.error(f"  PDF failed: {e}")
            audit(tenant, balance, "pdf_failed", False)
            failed += 1
            continue

        # 4. Send email
        log.info(f"  \u2192 Emailing notice to {tenant['tenant_email']}...")
        ok = send_notice_email(tenant, balance, pdf)
        audit(tenant, balance, "notice_sent", ok)

        if ok:
            sent += 1
        else:
            failed += 1

    # Summary
    log.info("\n" + "=" * 62)
    log.info(f"  \u2705  Notices sent:   {sent}")
    log.info(f"  \u274c  Failures:       {failed}")
    log.info(f"  \u23ed  Skipped:        {skipped}")
    log.info("=" * 62)


if __name__ == "__main__":
    run()
