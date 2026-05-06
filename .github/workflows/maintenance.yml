"""
╔══════════════════════════════════════════════════════════════╗
║   P Ventures — Maintenance Triage Agent                      ║
║   DoorLoop → AI Classification → Vendor Routing → Messaging  ║
║   Runs every 30 minutes via GitHub Actions                   ║
╚══════════════════════════════════════════════════════════════╝

SETUP:
  1. pip install requests reportlab
  2. Fill in CONFIG below or set as GitHub Secrets
  3. Add your vendors to VENDORS dict
  4. python maintenance_agent.py
"""

import os
import json
import sqlite3
import logging
import smtplib
import requests
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ─────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────
CONFIG = {
    # DoorLoop
    "DOORLOOP_API_KEY": os.environ.get("DOORLOOP_API_KEY",
        "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJ0eXBlIjoiQVBJIiwiaWQiOiI2OWZiNjNkMzJhNTY5ODA2NWZjYWMwMDIiLCJleHAiOjIwOTM0NDI3NzF9.bAsq3PTzU5Smnbm-D2diFAyJ2jXS6oT0uQL_e2UKFuU"),

    # Anthropic — AI triage
    "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", "PASTE_YOUR_ANTHROPIC_KEY"),

    # Gmail
    "GMAIL_ADDRESS":      os.environ.get("GMAIL_ADDRESS",      "risingtydemgmt@gmail.com"),
    "GMAIL_APP_PASSWORD": os.environ.get("GMAIL_APP_PASSWORD", "yiqe gqib plyd esom"),

    # Internal notifications
    "MANAGER_EMAIL":      os.environ.get("MANAGER_EMAIL",      "risingtydemgmt@gmail.com"),
    "MANAGER_PHONE":      os.environ.get("MANAGER_PHONE",      ""),

    # Company
    "COMPANY_NAME":       "P Ventures",
    "LANDLORD_NAME":      "Tyde Piccoli",

    # Approval thresholds
    "AUTO_APPROVE_UNDER":   250,    # auto-approve repairs under $250
    "MANAGER_REVIEW_UNDER": 750,    # manager reviews $250-$750
    # over $750 = owner approval required

    # Database
    "DB_PATH": os.environ.get("DB_PATH", "maintenance.db"),
}

# ─────────────────────────────────────────────────────────────
#  VENDORS — add your real vendors here
# ─────────────────────────────────────────────────────────────
VENDORS = {
    "plumber": [
        {"name": "YOUR PLUMBER NAME", "phone": "555-000-0000", "email": "", "avg_cost": 200, "response_hours": 4},
    ],
    "electrician": [
        {"name": "YOUR ELECTRICIAN NAME", "phone": "555-000-0001", "email": "", "avg_cost": 250, "response_hours": 4},
    ],
    "hvac": [
        {"name": "YOUR HVAC VENDOR", "phone": "555-000-0002", "email": "", "avg_cost": 300, "response_hours": 6},
    ],
    "appliance": [
        {"name": "YOUR APPLIANCE REPAIR", "phone": "555-000-0003", "email": "", "avg_cost": 175, "response_hours": 24},
    ],
    "pest": [
        {"name": "YOUR PEST CONTROL", "phone": "555-000-0004", "email": "", "avg_cost": 150, "response_hours": 48},
    ],
    "locksmith": [
        {"name": "YOUR LOCKSMITH", "phone": "555-000-0005", "email": "", "avg_cost": 120, "response_hours": 2},
    ],
    "roofer": [
        {"name": "YOUR ROOFER", "phone": "555-000-0006", "email": "", "avg_cost": 500, "response_hours": 24},
    ],
    "handyman": [
        {"name": "YOUR HANDYMAN", "phone": "555-000-0007", "email": "", "avg_cost": 100, "response_hours": 72},
    ],
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

# ─────────────────────────────────────────────────────────────
#  DATABASE — SQLite for local state tracking
# ─────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(CONFIG["DB_PATH"])
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS maintenance_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doorloop_id TEXT UNIQUE,
            tenant_name TEXT,
            tenant_email TEXT,
            tenant_phone TEXT,
            property_name TEXT,
            property_address TEXT,
            unit TEXT,
            issue_description TEXT,
            urgency TEXT,
            category TEXT,
            vendor_type TEXT,
            vendor_name TEXT,
            vendor_phone TEXT,
            estimated_cost_low REAL,
            estimated_cost_high REAL,
            approval_required TEXT,
            status TEXT DEFAULT 'New Request',
            ai_summary TEXT,
            ai_reasoning TEXT,
            doorloop_work_order_id TEXT,
            invoice_amount REAL,
            invoice_uploaded INTEGER DEFAULT 0,
            owner_notified INTEGER DEFAULT 0,
            tenant_messaged INTEGER DEFAULT 0,
            vendor_contacted INTEGER DEFAULT 0,
            date_submitted TEXT,
            date_updated TEXT,
            resolution_notes TEXT,
            photos_requested INTEGER DEFAULT 0,
            access_permission TEXT,
            pets_in_unit INTEGER DEFAULT 0,
            preferred_time TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS ticket_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER,
            direction TEXT,
            recipient TEXT,
            message_type TEXT,
            subject TEXT,
            body TEXT,
            sent_at TEXT,
            status TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS ticket_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id INTEGER,
            old_status TEXT,
            new_status TEXT,
            changed_at TEXT,
            note TEXT
        )
    """)

    conn.commit()
    conn.close()
    log.info("Database initialized")


def get_db():
    return sqlite3.connect(CONFIG["DB_PATH"])


def ticket_exists(doorloop_id: str) -> bool:
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM maintenance_tickets WHERE doorloop_id=?", (doorloop_id,))
    result = c.fetchone()
    conn.close()
    return result is not None


def save_ticket(ticket: dict) -> int:
    conn = get_db()
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute("""
        INSERT OR REPLACE INTO maintenance_tickets
        (doorloop_id, tenant_name, tenant_email, tenant_phone,
         property_name, property_address, unit, issue_description,
         urgency, category, vendor_type, vendor_name, vendor_phone,
         estimated_cost_low, estimated_cost_high, approval_required,
         status, ai_summary, ai_reasoning, date_submitted, date_updated,
         tenant_messaged, owner_notified)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        ticket.get("doorloop_id"),
        ticket.get("tenant_name"),
        ticket.get("tenant_email"),
        ticket.get("tenant_phone"),
        ticket.get("property_name"),
        ticket.get("property_address"),
        ticket.get("unit"),
        ticket.get("issue_description"),
        ticket.get("urgency"),
        ticket.get("category"),
        ticket.get("vendor_type"),
        ticket.get("vendor_name"),
        ticket.get("vendor_phone"),
        ticket.get("estimated_cost_low"),
        ticket.get("estimated_cost_high"),
        ticket.get("approval_required"),
        ticket.get("status", "New Request"),
        ticket.get("ai_summary"),
        ticket.get("ai_reasoning"),
        ticket.get("date_submitted", now),
        now,
        0, 0
    ))
    ticket_id = c.lastrowid
    conn.commit()
    conn.close()
    return ticket_id


def update_ticket(ticket_id: int, updates: dict):
    conn = get_db()
    c = conn.cursor()
    updates["date_updated"] = datetime.now().isoformat()
    sets = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [ticket_id]
    c.execute(f"UPDATE maintenance_tickets SET {sets} WHERE id=?", vals)
    conn.commit()
    conn.close()


def log_message(ticket_id: int, direction: str, recipient: str,
                msg_type: str, subject: str, body: str, status: str):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO ticket_messages
        (ticket_id, direction, recipient, message_type, subject, body, sent_at, status)
        VALUES (?,?,?,?,?,?,?,?)
    """, (ticket_id, direction, recipient, msg_type, subject, body,
          datetime.now().isoformat(), status))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────
#  DOORLOOP API
# ─────────────────────────────────────────────────────────────
def dl_get(endpoint: str, params: dict = None) -> dict:
    headers = {
        "Authorization": f"Bearer {CONFIG['DOORLOOP_API_KEY']}",
        "Content-Type": "application/json",
    }
    resp = requests.get(
        f"https://api.doorloop.com{endpoint}",
        headers=headers, params=params or {}, timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def get_maintenance_requests() -> list:
    """Fetch open maintenance/work orders from DoorLoop."""
    try:
        data = dl_get("/workOrders", {"status": "open", "pageSize": 100})
        requests_list = data.get("data", [])
        log.info(f"Fetched {len(requests_list)} open work orders from DoorLoop")
        return requests_list
    except Exception as e:
        log.error(f"DoorLoop API error: {e}")
        return []


def extract_request_info(work_order: dict) -> dict:
    """Pull all relevant fields from a DoorLoop work order."""
    tenant = work_order.get("tenant", {}) or {}
    prop   = work_order.get("property", {}) or {}
    unit   = work_order.get("unit", {}) or {}
    addr   = prop.get("address", {}) or {}

    street  = addr.get("street1", "")
    city    = addr.get("city", "")
    state   = addr.get("state", "FL")
    zipcode = addr.get("zip", "")
    full_address = f"{street}, {city}, {state} {zipcode}".strip(", ")

    return {
        "doorloop_id":      str(work_order.get("id", "")),
        "tenant_name":      f"{tenant.get('firstName','')} {tenant.get('lastName','')}".strip(),
        "tenant_email":     tenant.get("email", ""),
        "tenant_phone":     tenant.get("phone", ""),
        "property_name":    prop.get("name", ""),
        "property_address": full_address,
        "unit":             unit.get("name", ""),
        "issue_description": work_order.get("description", "") or work_order.get("subject", ""),
        "date_submitted":   work_order.get("createdAt", datetime.now().isoformat())[:10],
        "doorloop_work_order_id": str(work_order.get("id", "")),
    }


# ─────────────────────────────────────────────────────────────
#  AI TRIAGE — Claude classifies the request
# ─────────────────────────────────────────────────────────────
TRIAGE_PROMPT = """You are a property management maintenance triage AI for P Ventures.

Analyze the maintenance request below and respond with ONLY valid JSON.

Maintenance request: "{description}"

Classify it and return this exact JSON structure:
{{
  "urgency": "Emergency|Urgent|Standard",
  "category": "one short category label",
  "vendor_type": "plumber|electrician|hvac|appliance|pest|locksmith|roofer|handyman|general",
  "estimated_cost_low": <number>,
  "estimated_cost_high": <number>,
  "approval_required": "auto|manager|owner",
  "habitability_risk": true|false,
  "legal_risk": true|false,
  "ai_summary": "one sentence summary of the issue",
  "ai_reasoning": "one sentence explaining urgency classification",
  "photos_needed": true|false,
  "response_within_hours": <number>
}}

Urgency rules:
- Emergency: active leak, no AC in extreme heat, electrical sparks, sewage backup, flooding, fire/smoke, broken exterior lock, no heat in cold weather
- Urgent: AC not working, fridge not cooling, water heater issue, minor leak, pest issue, security concern
- Standard: cosmetic repairs, dripping faucet, cabinet repair, paint, minor appliance, general wear

Cost estimation: realistic market rates for South Florida / Port Saint Lucie area.

approval_required rules:
- "auto" if estimated_cost_high < 250
- "manager" if estimated_cost_high between 250-750
- "owner" if estimated_cost_high > 750 OR habitability_risk is true"""


def ai_triage(description: str) -> dict:
    """Use Claude API to classify the maintenance request."""
    if not description:
        return _fallback_triage("No description provided")

    api_key = CONFIG["ANTHROPIC_API_KEY"]
    if api_key == "PASTE_YOUR_ANTHROPIC_KEY":
        log.warning("No Anthropic API key — using keyword triage fallback")
        return _fallback_triage(description)

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 500,
                "messages": [{"role": "user", "content": TRIAGE_PROMPT.format(description=description)}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"].strip()

        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]

        result = json.loads(text)
        log.info(f"  AI triage: {result['urgency']} | {result['vendor_type']} | ${result['estimated_cost_low']}-${result['estimated_cost_high']}")
        return result

    except Exception as e:
        log.warning(f"  AI triage failed ({e}), using keyword fallback")
        return _fallback_triage(description)


def _fallback_triage(description: str) -> dict:
    """Keyword-based fallback if AI is unavailable."""
    desc = description.lower()

    # Emergency keywords
    emergency_kw = ["flood", "flooding", "sewage", "backup", "fire", "smoke",
                    "spark", "sparking", "burning smell", "electrical fire", "no lock",
                    "broken lock", "exterior door", "water everywhere", "gushing",
                    "overflowing", "overflow", "clogged and over"]
    # Urgent keywords
    urgent_kw = ["no ac", "ac not", "ac stopped", "air conditioning", "air condition",
                 "no heat", "no hot water", "water heater", "fridge not", "refrigerator not",
                 "fridge stopped", "cooling stopped", "stopped cooling", "stopped working",
                 "not cooling", "not working", "leak", "pest", "roach",
                 "mice", "mouse", "rat", "rodent", "security", "break-in", "insect", "bug"]

    if any(k in desc for k in emergency_kw):
        urgency = "Emergency"
        cost_low, cost_high = 300, 1500
        response_hours = 2
    elif any(k in desc for k in urgent_kw):
        urgency = "Urgent"
        cost_low, cost_high = 150, 600
        response_hours = 24
    else:
        urgency = "Standard"
        cost_low, cost_high = 75, 300
        response_hours = 72

    # Vendor routing
    # ORDER MATTERS — most specific first
    # Use word-boundary matching to avoid "rat" in "refrigerator", "ant" in "want" etc.
    import re as _re
    pest_words = ["pest", "roach", "cockroach", "mice", "mouse", r"\brat\b", r"\bant\b", "termite", "insect", "spider", r"\bbug\b"]
    if any(_re.search(k, desc) for k in pest_words):
        vendor = "pest"
    elif any(k in desc for k in ["sewage", "sewer", "septic"]):
        vendor = "plumber"
    elif any(k in desc for k in ["spark", "sparking", "electrical fire", "outlet", "breaker", "wire", "electric"]):
        vendor = "electrician"
    elif any(k in desc for k in ["not cooling", "not cold", "stopped cooling", "fridge not", "refrigerator not",
                                   "fridge stopped", "refrigerator stopped", "oven", "stove",
                                   "dishwasher", "washer", "dryer", "appliance", "microwave"]):
        vendor = "appliance"
    elif any(k in desc for k in ["fridge", "refrigerator"]):
        vendor = "appliance"
    elif any(k in desc for k in ["ac ", "a/c", "hvac", "air condition", "air-condition", "furnace", "thermostat", "cooling", "no heat", "no cool"]):
        vendor = "hvac"
    elif any(k in desc for k in ["light", "power", "lamp"]):
        vendor = "electrician"
    elif any(k in desc for k in ["roof", "ceiling leak", "water intrusion"]):
        vendor = "roofer"
    elif any(k in desc for k in ["broken lock", "lock broken", "front door lock", "cannot lock", "wont lock", "exterior lock"]):
        vendor = "locksmith"
    elif any(k in desc for k in ["plumb", "pipe", "water", "drain", "toilet", "sink", "faucet", "leak", "flood"]):
        vendor = "plumber"
    elif any(k in desc for k in ["lock", "key", "deadbolt"]):
        vendor = "locksmith"
    else:
        vendor = "handyman"

    approval = "auto" if cost_high < 250 else ("manager" if cost_high < 750 else "owner")

    return {
        "urgency": urgency,
        "category": vendor.title() + " Issue",
        "vendor_type": vendor,
        "estimated_cost_low": cost_low,
        "estimated_cost_high": cost_high,
        "approval_required": approval,
        "habitability_risk": urgency == "Emergency",
        "legal_risk": False,
        "ai_summary": f"{urgency} maintenance request: {description[:100]}",
        "ai_reasoning": "Classified by keyword matching (AI unavailable)",
        "photos_needed": True,
        "response_within_hours": response_hours,
    }


# ─────────────────────────────────────────────────────────────
#  VENDOR ROUTING
# ─────────────────────────────────────────────────────────────
def assign_vendor(vendor_type: str) -> dict:
    """Pick the best available vendor for the type."""
    vendors = VENDORS.get(vendor_type, VENDORS.get("handyman", []))
    if not vendors:
        return {"name": "TBD", "phone": "", "email": ""}
    # Sort by response time (fastest first), then cost
    sorted_vendors = sorted(vendors, key=lambda v: (v.get("response_hours", 99), v.get("avg_cost", 999)))
    return sorted_vendors[0]


# ─────────────────────────────────────────────────────────────
#  EMAIL MESSAGING
# ─────────────────────────────────────────────────────────────
def send_email(to: str, subject: str, html: str) -> bool:
    try:
        msg = MIMEMultipart("alternative")
        msg["From"]    = f"{CONFIG['COMPANY_NAME']} <{CONFIG['GMAIL_ADDRESS']}>"
        msg["To"]      = to
        msg["Subject"] = subject
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(CONFIG["GMAIL_ADDRESS"], CONFIG["GMAIL_APP_PASSWORD"])
            srv.sendmail(CONFIG["GMAIL_ADDRESS"], [to], msg.as_string())
        log.info(f"  ✅ Email sent → {to}")
        return True
    except Exception as e:
        log.error(f"  ❌ Email failed → {to}: {e}")
        return False


def email_tenant_first_response(ticket: dict) -> bool:
    """Send initial acknowledgment + photo/info request to tenant."""
    urgency = ticket.get("urgency", "Standard")

    if urgency == "Emergency":
        urgency_line = """
        <div style="background:#fff0f0;border-left:4px solid #cc1a1a;padding:12px 16px;margin:16px 0;border-radius:0 6px 6px 0">
            <strong style="color:#cc1a1a">⚠ Emergency Response</strong><br>
            <span style="font-size:13px">This has been flagged as an urgent issue. Emergency maintenance has been notified.
            If it is safe to do so, please take steps to prevent further damage (e.g., turn off water at the shutoff valve).</span>
        </div>"""
        response_time = "within the next 2 hours"
    elif urgency == "Urgent":
        urgency_line = """
        <div style="background:#fffbf0;border-left:4px solid #8a5a00;padding:12px 16px;margin:16px 0;border-radius:0 6px 6px 0">
            <strong style="color:#8a5a00">⚡ Urgent Request</strong><br>
            <span style="font-size:13px">We're treating this as a priority and will have someone out within 24–48 hours.</span>
        </div>"""
        response_time = "within 24–48 hours"
    else:
        urgency_line = ""
        response_time = "within 3–7 business days"

    html = f"""
<html><body style="font-family:-apple-system,'Helvetica Neue',sans-serif;font-size:14px;color:#1a1a1a;max-width:580px;margin:0 auto">
<div style="border-bottom:2px solid #000;padding-bottom:12px;margin-bottom:20px">
  <strong style="font-size:16px">{CONFIG['COMPANY_NAME']}</strong>
  <span style="font-size:12px;color:#666;margin-left:8px">Maintenance Request Received</span>
</div>

<p>Hi {ticket.get('tenant_name','Resident')},</p>

<p>We received your maintenance request regarding <strong>{ticket.get('ai_summary', ticket.get('issue_description','your issue'))}</strong>
at <strong>{ticket.get('property_address')}, Unit {ticket.get('unit')}</strong>.</p>

{urgency_line}

<p>To get a technician scheduled <strong>{response_time}</strong>, please reply with:</p>

<ol style="line-height:2">
  <li>A <strong>clear photo or video</strong> of the issue</li>
  <li>Your <strong>preferred access windows</strong> (days/times we can enter)</li>
  <li>Confirmation that <strong>we have permission to enter</strong> if you're not home</li>
  <li>Whether you have <strong>pets in the unit</strong></li>
</ol>

<p style="font-size:13px;color:#555">Ticket reference: <strong>#{ticket.get('doorloop_id')}</strong></p>

<p>Sincerely,<br><strong>{CONFIG['LANDLORD_NAME']}</strong><br>
{CONFIG['COMPANY_NAME']}<br>
{CONFIG['GMAIL_ADDRESS']}</p>

<hr style="border:none;border-top:1px solid #eee;margin-top:24px">
<p style="font-size:11px;color:#999">This is an automated notification from your property management system.</p>
</body></html>"""

    subject = f"Maintenance Request Received — {ticket.get('property_address')}, Unit {ticket.get('unit')}"
    ok = send_email(ticket["tenant_email"], subject, html)
    return ok


def email_manager_alert(ticket: dict, ticket_id: int) -> bool:
    """Notify manager of new ticket, especially Emergency/Urgent."""
    urgency = ticket.get("urgency", "Standard")
    color   = {"Emergency": "#cc1a1a", "Urgent": "#8a5a00", "Standard": "#1a8a3a"}.get(urgency, "#000")

    html = f"""
<html><body style="font-family:-apple-system,'Helvetica Neue',sans-serif;font-size:14px;color:#1a1a1a;max-width:580px">
<div style="border-bottom:2px solid #000;padding-bottom:12px;margin-bottom:20px">
  <strong style="font-size:16px">{CONFIG['COMPANY_NAME']}</strong>
  <span style="font-size:12px;color:#666;margin-left:8px">New Maintenance Ticket #{ticket_id}</span>
</div>

<div style="background:#f4f4f4;border-radius:8px;padding:16px;margin-bottom:16px">
  <table style="width:100%;font-size:13px;border-collapse:collapse">
    <tr><td style="padding:5px 0;font-weight:700;width:140px">Urgency</td>
        <td style="padding:5px 0;font-weight:700;color:{color}">{urgency.upper()}</td></tr>
    <tr><td style="padding:5px 0;font-weight:700">Tenant</td>
        <td style="padding:5px 0">{ticket.get('tenant_name')}</td></tr>
    <tr><td style="padding:5px 0;font-weight:700">Property</td>
        <td style="padding:5px 0">{ticket.get('property_address')}, Unit {ticket.get('unit')}</td></tr>
    <tr><td style="padding:5px 0;font-weight:700">Issue</td>
        <td style="padding:5px 0">{ticket.get('ai_summary','')}</td></tr>
    <tr><td style="padding:5px 0;font-weight:700">Category</td>
        <td style="padding:5px 0">{ticket.get('category','')}</td></tr>
    <tr><td style="padding:5px 0;font-weight:700">Vendor needed</td>
        <td style="padding:5px 0">{ticket.get('vendor_type','').title()}</td></tr>
    <tr><td style="padding:5px 0;font-weight:700">Assigned vendor</td>
        <td style="padding:5px 0">{ticket.get('vendor_name','TBD')} · {ticket.get('vendor_phone','')}</td></tr>
    <tr><td style="padding:5px 0;font-weight:700">Est. cost</td>
        <td style="padding:5px 0">${ticket.get('estimated_cost_low',0):.0f} – ${ticket.get('estimated_cost_high',0):.0f}</td></tr>
    <tr><td style="padding:5px 0;font-weight:700">Approval</td>
        <td style="padding:5px 0">{ticket.get('approval_required','').upper()}</td></tr>
    <tr><td style="padding:5px 0;font-weight:700">Tenant email</td>
        <td style="padding:5px 0">{ticket.get('tenant_email','')}</td></tr>
    <tr><td style="padding:5px 0;font-weight:700">Tenant phone</td>
        <td style="padding:5px 0">{ticket.get('tenant_phone','')}</td></tr>
  </table>
</div>

<p style="font-size:13px;color:#444"><strong>AI Reasoning:</strong> {ticket.get('ai_reasoning','')}</p>

{'<div style="background:#fff0f0;border:1px solid #cc1a1a;border-radius:6px;padding:12px;margin:12px 0"><strong style="color:#cc1a1a">⚠ HABITABILITY RISK</strong> — This request may affect tenant habitability. Address immediately.</div>' if ticket.get('habitability_risk') else ''}
{'<div style="background:#fff0f0;border:1px solid #cc1a1a;border-radius:6px;padding:12px;margin:12px 0"><strong style="color:#cc1a1a">⚖ LEGAL RISK</strong> — This request may have legal implications. Review promptly.</div>' if ticket.get('legal_risk') else ''}

<p style="font-size:12px;color:#999;margin-top:20px">Ticket ID: #{ticket_id} · DoorLoop ID: {ticket.get('doorloop_id')}</p>
</body></html>"""

    urgency_prefix = "🚨 EMERGENCY" if urgency == "Emergency" else ("⚡ URGENT" if urgency == "Urgent" else "📋 NEW")
    subject = f"{urgency_prefix} Maintenance — {ticket.get('property_address')}, Unit {ticket.get('unit')}"
    return send_email(CONFIG["MANAGER_EMAIL"], subject, html)


def email_vendor(ticket: dict, vendor: dict) -> bool:
    """Send dispatch email to assigned vendor."""
    if not vendor.get("email"):
        log.info(f"  No vendor email — skipping vendor dispatch (call manually: {vendor.get('phone')})")
        return False

    html = f"""
<html><body style="font-family:-apple-system,'Helvetica Neue',sans-serif;font-size:14px;color:#1a1a1a;max-width:580px">
<div style="border-bottom:2px solid #000;padding-bottom:12px;margin-bottom:20px">
  <strong style="font-size:16px">{CONFIG['COMPANY_NAME']}</strong>
  <span style="font-size:12px;color:#666;margin-left:8px">Maintenance Dispatch</span>
</div>

<p>Hi {vendor['name']},</p>
<p>We have a new maintenance request that needs your attention.</p>

<div style="background:#f4f4f4;border-radius:8px;padding:16px;margin:16px 0">
  <table style="width:100%;font-size:13px;border-collapse:collapse">
    <tr><td style="padding:5px 0;font-weight:700;width:130px">Property</td><td>{ticket.get('property_address')}</td></tr>
    <tr><td style="padding:5px 0;font-weight:700">Unit</td><td>{ticket.get('unit')}</td></tr>
    <tr><td style="padding:5px 0;font-weight:700">Issue</td><td>{ticket.get('ai_summary','')}</td></tr>
    <tr><td style="padding:5px 0;font-weight:700">Urgency</td><td><strong>{ticket.get('urgency')}</strong></td></tr>
    <tr><td style="padding:5px 0;font-weight:700">Tenant</td><td>{ticket.get('tenant_name')} · {ticket.get('tenant_phone','')}</td></tr>
  </table>
</div>

<p>Please confirm your availability and estimated arrival time by replying to this email or calling {CONFIG['MANAGER_EMAIL']}.</p>
<p>Once complete, please send your invoice to {CONFIG['GMAIL_ADDRESS']}.</p>

<p>Thank you,<br><strong>{CONFIG['LANDLORD_NAME']}</strong><br>{CONFIG['COMPANY_NAME']}</p>
</body></html>"""

    subject = f"Maintenance Dispatch — {ticket.get('property_address')}, Unit {ticket.get('unit')} ({ticket.get('urgency')})"
    return send_email(vendor["email"], subject, html)


# ─────────────────────────────────────────────────────────────
#  MAIN AGENT LOOP
# ─────────────────────────────────────────────────────────────
def run():
    log.info("=" * 62)
    log.info(f"  P Ventures — Maintenance Triage Agent")
    log.info(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    log.info("=" * 62)

    init_db()

    new_count = processed = skipped = 0

    # 1. Fetch open work orders from DoorLoop
    log.info("\n[1] Fetching maintenance requests from DoorLoop...")
    work_orders = get_maintenance_requests()

    if not work_orders:
        log.info("  No open work orders found.")
        log.info("=" * 62)
        return

    log.info(f"\n[2] Processing {len(work_orders)} work orders...")

    for wo in work_orders:
        info = extract_request_info(wo)
        dl_id = info["doorloop_id"]

        log.info(f"\n  ► {info['tenant_name']} — {info['property_address']} Unit {info['unit']}")
        log.info(f"    {info['issue_description'][:80]}...")

        # Skip if already processed
        if ticket_exists(dl_id):
            log.info("  Already processed. Skipping.")
            skipped += 1
            continue

        if not info["issue_description"]:
            log.warning("  No description. Skipping.")
            skipped += 1
            continue

        # 3. AI Triage
        log.info("  → AI triaging...")
        triage = ai_triage(info["issue_description"])

        # 4. Assign vendor
        vendor = assign_vendor(triage["vendor_type"])
        log.info(f"  → Vendor: {vendor['name']} ({triage['vendor_type']})")

        # 5. Build full ticket
        ticket = {**info, **triage,
                  "vendor_name":  vendor["name"],
                  "vendor_phone": vendor.get("phone", ""),
                  "status": "AI Reviewing"}

        # 6. Save to DB
        ticket_id = save_ticket(ticket)
        log.info(f"  → Ticket #{ticket_id} saved | {triage['urgency']} | ${triage['estimated_cost_low']}-${triage['estimated_cost_high']}")

        # 7. Send tenant first response (if email on file)
        if info.get("tenant_email"):
            log.info("  → Emailing tenant...")
            ok = email_tenant_first_response(ticket)
            if ok:
                update_ticket(ticket_id, {"tenant_messaged": 1, "status": "Waiting on Tenant Photos"})
                log_message(ticket_id, "outbound", info["tenant_email"],
                            "email", "Maintenance Request Received", "First response sent", "sent")

        # 8. Alert manager
        log.info("  → Alerting manager...")
        email_manager_alert(ticket, ticket_id)
        update_ticket(ticket_id, {"owner_notified": 1})

        # 9. Emergency — also dispatch vendor immediately
        if triage["urgency"] == "Emergency":
            log.info("  → EMERGENCY: dispatching vendor immediately...")
            dispatched = email_vendor(ticket, vendor)
            if dispatched:
                update_ticket(ticket_id, {"vendor_contacted": 1, "status": "Vendor Contacted"})
            else:
                log.info(f"  → Call vendor manually: {vendor['name']} {vendor.get('phone','')}")
                update_ticket(ticket_id, {"status": "Vendor Needed"})

        new_count += 1
        processed += 1

    # Summary
    log.info("\n" + "=" * 62)
    log.info(f"  ✅  New tickets created:  {new_count}")
    log.info(f"  ⏭   Already processed:    {skipped}")
    log.info("=" * 62)


if __name__ == "__main__":
    run()
