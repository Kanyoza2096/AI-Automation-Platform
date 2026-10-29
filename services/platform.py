"""
Platform detection and routing.
Detects whether an incoming webhook event is from Messenger or WhatsApp
and routes responses to the appropriate client.
"""

import logging
from typing import Any, Dict, Literal, Optional

logger = logging.getLogger("kanyoza.services.platform")

Platform = Literal["messenger", "whatsapp", "unknown"]


def detect_platform(webhook_body: Dict[str, Any]) -> Platform:
    """
    Detect which platform sent the webhook event.
    Facebook Messenger webhooks have {"object": "page"}.
    WhatsApp Cloud API webhooks have {"object": "whatsapp_business_account"}.
    """
    obj = webhook_body.get("object", "")
    if obj == "page":
        return "messenger"
    if obj == "whatsapp_business_account":
        return "whatsapp"
    return "unknown"


def extract_messenger_events(webhook_body: Dict[str, Any]) -> list:
    """Extract all messaging events from a Messenger webhook payload."""
    events = []
    for entry in webhook_body.get("entry", []):
        for messaging in entry.get("messaging", []):
            events.append(messaging)
    return events


def extract_whatsapp_events(webhook_body: Dict[str, Any]) -> list:
    """Extract all message events from a WhatsApp Cloud API webhook payload."""
    events = []
    for entry in webhook_body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                events.append({
                    "message": msg,
                    "contacts": value.get("contacts", []),
                    "metadata": value.get("metadata", {}),
                })
    return events


def get_messenger_sender(event: Dict[str, Any]) -> Optional[str]:
    return event.get("sender", {}).get("id")


def get_messenger_text(event: Dict[str, Any]) -> Optional[str]:
    return event.get("message", {}).get("text")


def get_messenger_postback(event: Dict[str, Any]) -> Optional[str]:
    postback = event.get("postback", {})
    return postback.get("payload")


def get_whatsapp_sender(event: Dict[str, Any]) -> Optional[str]:
    return event.get("message", {}).get("from")


def get_whatsapp_text(event: Dict[str, Any]) -> Optional[str]:
    msg = event.get("message", {})
    if msg.get("type") == "text":
        return msg.get("text", {}).get("body")
    return None


def get_whatsapp_message_id(event: Dict[str, Any]) -> Optional[str]:
    return event.get("message", {}).get("id")
