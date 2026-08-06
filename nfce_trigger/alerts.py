import logging
import os
import smtplib
from email.mime.text import MIMEText


# Configuração

LOGGER = logging.getLogger("nfce_trigger")


# Envio de alertas

def send_alert(hotel: str, message: str) -> None:
    user = os.getenv("NFCE_SMTP_USER")
    password = os.getenv("NFCE_SMTP_PASSWORD")
    recipient = os.getenv("NFCE_ALERT_RECIPIENT")

    if not all((user, password, recipient)):
        LOGGER.warning("Alerta não enviado; SMTP não configurado: %s", message)
        return

    email = MIMEText(
        f"Problema no NFCeTrigger do hotel {hotel}: {message}",
        _charset="utf-8",
    )
    email["From"], email["To"] = user, recipient
    email["Subject"] = f"Problema no NFCeTrigger do Hotel {hotel}"

    try:
        port = int(os.getenv("NFCE_SMTP_PORT", "587"))
        host = os.getenv("NFCE_SMTP_HOST", "smtp.gmail.com")
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(email)
    except (OSError, ValueError, smtplib.SMTPException) as error:
        LOGGER.error("Falha ao enviar alerta: %s", error)
