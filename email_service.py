"""
email_service.py - Step 3: send the welcome email after signup.

Two ways to send, chosen automatically from your .env:

1) Brevo HTTPS API  (used when BREVO_API_KEY is set)
   Works on your laptop AND on Render's free tier, because it uses normal
   HTTPS (port 443), not the SMTP ports that Render blocks.

2) Gmail SMTP with an App Password  (fallback when BREVO_API_KEY is not set)
   Works on your laptop only. Render's free tier blocks SMTP ports.
"""

import html
import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage

import certifi
from dotenv import load_dotenv

load_dotenv()

APP_NAME = "TripMate AI"

# --- Gmail SMTP settings (fallback) ---
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # SSL

# --- Brevo API settings ---
BREVO_URL = "https://api.brevo.com/v3/smtp/email"


# ---------------------------------------------------------------------------
# Way 1: Brevo HTTPS API
# ---------------------------------------------------------------------------
def _send_via_brevo(to_email: str, subject: str, text_body: str, html_body: str) -> bool:
    api_key = os.getenv("BREVO_API_KEY")
    sender_email = os.getenv("BREVO_SENDER_EMAIL")
    sender_name = os.getenv("BREVO_SENDER_NAME", APP_NAME)

    if not sender_email:
        print("EMAIL SKIPPED: BREVO_SENDER_EMAIL is missing in .env")
        return False

    payload = {
        "sender": {"name": sender_name, "email": sender_email},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_body,
        "textContent": text_body,
    }

    request = urllib.request.Request(
        BREVO_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "api-key": api_key,
            "content-type": "application/json",
            "accept": "application/json",
        },
        method="POST",
    )

    try:
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(request, timeout=20, context=context) as response:
            print(f"EMAIL SENT via Brevo to {to_email} (status {response.status})")
            return True
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        print(f"EMAIL ERROR (Brevo) status {exc.code}: {details}")
        return False
    except Exception as exc:
        print(f"EMAIL ERROR (Brevo) {type(exc).__name__}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Way 2: Gmail SMTP (laptop only)
# ---------------------------------------------------------------------------
def _send_via_gmail(to_email: str, subject: str, text_body: str, html_body: str) -> bool:
    sender = os.getenv("SMTP_EMAIL")
    app_password = os.getenv("SMTP_APP_PASSWORD")

    if not sender or not app_password:
        print(
            "EMAIL SKIPPED: set BREVO_API_KEY (+ BREVO_SENDER_EMAIL), "
            "or SMTP_EMAIL + SMTP_APP_PASSWORD, in .env"
        )
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{APP_NAME} <{sender}>"
    message["To"] = to_email
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    try:
        context = ssl.create_default_context(cafile=certifi.where())
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context, timeout=20) as server:
            # Gmail shows app passwords with spaces; remove them if present
            server.login(sender, app_password.replace(" ", ""))
            server.send_message(message)
        print(f"EMAIL SENT via Gmail to {to_email}")
        return True
    except Exception as exc:
        print(f"EMAIL ERROR (Gmail) {type(exc).__name__}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Public functions used by app.py
# ---------------------------------------------------------------------------
def send_email(to_email: str, subject: str, text_body: str, html_body: str) -> bool:
    """Send one email. Returns True if sent, False if anything went wrong.
    Never raises, so a failed email can never break signup."""
    if os.getenv("BREVO_API_KEY"):
        return _send_via_brevo(to_email, subject, text_body, html_body)
    return _send_via_gmail(to_email, subject, text_body, html_body)


def send_welcome_email(to_email: str, name: str) -> bool:
    """The 'Successfully registered' email."""
    safe_name = html.escape(name.strip() or "traveller")

    subject = f"Welcome to {APP_NAME} - you're registered!"

    text_body = (
        f"Hi {name.strip() or 'traveller'},\n\n"
        f"You have successfully registered with {APP_NAME}.\n"
        "You can now log in and start planning your trips with AI.\n\n"
        "Happy travels!\n"
        f"The {APP_NAME} team\n"
    )

    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 520px; margin: auto;
                padding: 28px; border-radius: 16px; background: #0b1030; color: #e8ecf8;">
        <h2 style="margin-top: 0;">&#9992;&#65039; Welcome to {APP_NAME}</h2>
        <p>Hi {safe_name},</p>
        <p>You have <strong>successfully registered</strong> with {APP_NAME}.</p>
        <p>You can now log in and start planning your trips with AI:
           flights, hotels, weather, budget and a full day-by-day itinerary.</p>
        <p style="margin-bottom: 0;">Happy travels!<br>The {APP_NAME} team</p>
    </div>
    """

    return send_email(to_email, subject, text_body, html_body)


# ---------------------------------------------------------------------------
# Quick test: run `python email_service.py`  (sends a test mail to yourself)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    test_address = os.getenv("BREVO_SENDER_EMAIL") or os.getenv("SMTP_EMAIL")
    if not test_address:
        print("Add your email settings to .env first (see the notes at the top of this file).")
    else:
        ok = send_welcome_email(test_address, "Test User")
        print("Result:", "sent - check your inbox (and spam folder)" if ok else "FAILED - see the error above")