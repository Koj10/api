import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

load_dotenv()

SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
FROM_EMAIL = os.getenv("FROM_EMAIL") or SMTP_USER


def _smtp_configured():
    return all([SMTP_SERVER, SMTP_USER, SMTP_PASSWORD, FROM_EMAIL])


def send_email(to_email, subject, text_body, html_body=None):
    if not to_email:
        logging.error("send_email: пустой адрес получателя")
        return False

    if not _smtp_configured():
        logging.error(
            "SMTP не настроен. Задайте SMTP_SERVER, SMTP_USER, SMTP_PASSWORD и FROM_EMAIL в .env"
        )
        return False

    msg = MIMEMultipart()
    msg["From"] = FROM_EMAIL
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body or "", "plain"))

    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    try:
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(FROM_EMAIL, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(FROM_EMAIL, [to_email], msg.as_string())
        return True
    except Exception:
        logging.exception("Ошибка отправки email на %s", to_email)
        return False
