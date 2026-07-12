import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from paths import load_app_env

load_app_env()


def _smtp_config():
    port_raw = os.getenv("SMTP_PORT", "587") or "587"
    try:
        port = int(port_raw)
    except ValueError:
        logging.error("SMTP_PORT имеет неверное значение: %r", port_raw)
        port = 587

    smtp_user = os.getenv("SMTP_USER")
    from_email = os.getenv("FROM_EMAIL") or smtp_user

    return {
        "server": os.getenv("SMTP_SERVER"),
        "port": port,
        "user": smtp_user,
        "password": os.getenv("SMTP_PASSWORD"),
        "from_email": from_email,
    }


def _smtp_configured(cfg):
    return all([cfg["server"], cfg["user"], cfg["password"], cfg["from_email"]])


def send_email(to_email, subject, text_body, html_body=None):
    if not to_email:
        logging.error("send_email: пустой адрес получателя")
        return False

    cfg = _smtp_config()
    if not _smtp_configured(cfg):
        missing = [
            name
            for name, key in (
                ("SMTP_SERVER", "server"),
                ("SMTP_USER", "user"),
                ("SMTP_PASSWORD", "password"),
                ("FROM_EMAIL", "from_email"),
            )
            if not cfg[key]
        ]
        logging.error("SMTP не настроен. Не заданы: %s", ", ".join(missing))
        return False

    msg = MIMEMultipart()
    msg["From"] = cfg["from_email"]
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body or "", "plain"))

    if html_body:
        msg.attach(MIMEText(html_body, "html"))

    try:
        if cfg["port"] == 465:
            with smtplib.SMTP_SSL(cfg["server"], cfg["port"], timeout=30) as server:
                server.login(cfg["user"], cfg["password"])
                server.sendmail(cfg["from_email"], [to_email], msg.as_string())
        else:
            with smtplib.SMTP(cfg["server"], cfg["port"], timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(cfg["user"], cfg["password"])
                server.sendmail(cfg["from_email"], [to_email], msg.as_string())
        logging.info("Письмо отправлено на %s (тема: %s)", to_email, subject)
        return True
    except smtplib.SMTPAuthenticationError as exc:
        logging.error(
            "SMTP: ошибка авторизации для %s — проверьте SMTP_USER и пароль приложения Gmail: %s",
            cfg["user"],
            exc,
        )
        return False
    except (smtplib.SMTPException, OSError, TimeoutError) as exc:
        logging.error(
            "SMTP: не удалось отправить письмо на %s через %s:%s — %s: %s",
            to_email,
            cfg["server"],
            cfg["port"],
            type(exc).__name__,
            exc,
        )
        return False
    except Exception:
        logging.exception("Неожиданная ошибка отправки email на %s", to_email)
        return False
