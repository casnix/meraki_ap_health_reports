"""
send_email.py
-------------
Send an email with an optional attachment over SMTP.

Usage:
  python send_email.py \\
    --server smtp.example.com \\
    --from sender@example.com \\
    --to recipient@example.com \\
    --title "Monthly AP Report" \\
    --message "Please find the report attached." \\
    --attachment ap_report.pdf

  # Multiple recipients:
  python send_email.py --to alice@example.com --to bob@example.com ...

  # Credentials via env vars (recommended):
  export SMTP_USER=sender@example.com
  export SMTP_PASSWORD=secret
  python send_email.py --server smtp.example.com ...

  # With explicit port and TLS options:
  python send_email.py --server smtp.office365.com --port 587 ...
  python send_email.py --server smtp.gmail.com --port 465 --ssl ...
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path


# ---------------------------------------------------------------------------
# Core send function
# ---------------------------------------------------------------------------

def send_email(
    server:     str,
    port:       int,
    use_ssl:    bool,
    sender:     str,
    recipients: list[str],
    title:      str,
    message:    str,
    attachment: Path | None,
    username:   str | None,
    password:   str | None,
) -> None:
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"]    = sender
    msg["To"]      = ", ".join(recipients)
    msg.set_content(message)

    if attachment:
        if not attachment.exists():
            raise FileNotFoundError(f"Attachment not found: {attachment}")

        mime_type, encoding = mimetypes.guess_type(str(attachment))
        if mime_type is None or encoding is not None:
            mime_type = "application/octet-stream"
        maintype, subtype = mime_type.split("/", 1)

        with attachment.open("rb") as fh:
            msg.add_attachment(
                fh.read(),
                maintype=maintype,
                subtype=subtype,
                filename=attachment.name,
            )
        print(f"  Attachment : {attachment} ({mime_type})")

    print(f"  Server     : {server}:{port} ({'SSL' if use_ssl else 'STARTTLS/plain'})")
    print(f"  From       : {sender}")
    print(f"  To         : {', '.join(recipients)}")
    print(f"  Subject    : {title}")

    smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_cls(server, port, timeout=30) as smtp:
        if not use_ssl:
            smtp.ehlo()
            try:
                smtp.starttls()
                smtp.ehlo()
            except smtplib.SMTPException:
                # Server doesn't support STARTTLS — continue unencrypted
                pass

        if username and password:
            smtp.login(username, password)

        smtp.send_message(msg)

    print("  Status     : Sent successfully.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Send an email with an optional attachment over SMTP.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage:")[1] if "Usage:" in __doc__ else "",
    )

    p.add_argument(
        "--server", required=True, metavar="HOST",
        help="SMTP server hostname (e.g. smtp.gmail.com).",
    )
    p.add_argument(
        "--port", type=int, default=None, metavar="PORT",
        help="SMTP port. Defaults to 465 with --ssl, 587 otherwise.",
    )
    p.add_argument(
        "--ssl", action="store_true",
        help="Use SMTP_SSL (port 465) instead of STARTTLS (port 587).",
    )
    p.add_argument(
        "--from", dest="sender", metavar="ADDRESS",
        default=os.environ.get("SMTP_USER", ""),
        help="Sender address (or set SMTP_USER env var).",
    )
    p.add_argument(
        "--to", dest="recipients", metavar="ADDRESS",
        action="append", required=True,
        help="Recipient address. Repeat for multiple recipients.",
    )
    p.add_argument(
        "--title", required=True, metavar="SUBJECT",
        help="Email subject line.",
    )
    p.add_argument(
        "--message", required=True, metavar="BODY",
        help="Plain-text email body.",
    )
    p.add_argument(
        "--attachment", metavar="FILE", default=None,
        help="Path to a file to attach (optional).",
    )
    p.add_argument(
        "--username", metavar="USER",
        default=os.environ.get("SMTP_USER", ""),
        help="SMTP login username (or set SMTP_USER env var).",
    )
    p.add_argument(
        "--password", metavar="PASS",
        default=os.environ.get("SMTP_PASSWORD", ""),
        help="SMTP login password (or set SMTP_PASSWORD env var).",
    )
    return p


def main() -> None:
    parser = build_parser()
    args   = parser.parse_args()

    # Resolve default port
    port = args.port or (465 if args.ssl else 587)

    # Sender defaults to username if not set separately
    sender = args.sender or args.username
    if not sender:
        parser.error(
            "Sender address required. Use --from, --username, or set SMTP_USER."
        )

    attachment = Path(args.attachment) if args.attachment else None

    try:
        send_email(
            server=args.server,
            port=port,
            use_ssl=args.ssl,
            sender=sender,
            recipients=args.recipients,
            title=args.title,
            message=args.message,
            attachment=attachment,
            username=args.username or None,
            password=args.password or None,
        )
    except FileNotFoundError as exc:
        sys.exit(f"[ERROR] {exc}")
    except smtplib.SMTPAuthenticationError:
        sys.exit("[ERROR] SMTP authentication failed. Check --username / SMTP_PASSWORD.")
    except smtplib.SMTPException as exc:
        sys.exit(f"[ERROR] SMTP error: {exc}")
    except OSError as exc:
        sys.exit(f"[ERROR] Could not connect to {args.server}:{port} — {exc}")


if __name__ == "__main__":
    main()