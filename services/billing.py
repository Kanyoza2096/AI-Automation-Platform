"""
Billing Service v2.0
- Stripe payment integration (with webhook signature verification)
- M-Pesa mobile money integration (with proper password computation)
- Subscription management
- Invoice generation
- EventBus integration for subscription events
"""

import base64
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from config.settings import settings
from utils.http_client import HttpClient

logger = logging.getLogger("kanyoza.services.billing")

_http = HttpClient("billing", default_timeout=30)


class BillingService:
    """Payment processing with Stripe and M-Pesa."""

    def __init__(
        self, supabase_client=None, event_bus=None
    ):
        self.supabase = supabase_client
        self._event_bus = event_bus
        self.stripe_key = getattr(
            settings, "stripe_secret_key", ""
        )
        self.stripe_webhook_secret = getattr(
            settings, "stripe_webhook_secret", ""
        )
        self.mpesa_consumer_key = getattr(
            settings, "mpesa_consumer_key", ""
        )
        self.mpesa_consumer_secret = getattr(
            settings, "mpesa_consumer_secret", ""
        )
        self.mpesa_passkey = getattr(
            settings, "mpesa_passkey", ""
        )
        self.mpesa_shortcode = getattr(
            settings, "mpesa_shortcode", ""
        )
        self.mpesa_environment = getattr(
            settings, "mpesa_environment", "sandbox"
        )

    # ------------------------------------------------------------------
    # EVENT BUS
    # ------------------------------------------------------------------

    def _emit(
        self, event_name: str, payload: dict
    ) -> None:
        if not self._event_bus:
            return
        try:
            if hasattr(self._event_bus, "publish"):
                self._event_bus.publish(
                    event_name, payload
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # STRIPE
    # ------------------------------------------------------------------

    @property
    def stripe_configured(self) -> bool:
        return bool(self.stripe_key)

    def create_stripe_checkout(
        self,
        tenant_id: str,
        plan: str,
        success_url: str,
        cancel_url: str,
    ) -> Optional[str]:
        """Create a Stripe checkout session."""
        if not self.stripe_configured:
            return None

        prices = {
            "pro": "price_pro_monthly",
            "business": "price_business_monthly",
            "enterprise": "price_enterprise_monthly",
        }

        try:
            response = _http.post(
                "https://api.stripe.com/v1/checkout/sessions",
                auth=(self.stripe_key, ""),
                data={
                    "payment_method_types[]": "card",
                    "line_items[0][price]": prices.get(
                        plan, prices["pro"]
                    ),
                    "line_items[0][quantity]": 1,
                    "mode": "subscription",
                    "success_url": success_url,
                    "cancel_url": cancel_url,
                    "client_reference_id": tenant_id,
                    "metadata[tenant_id]": tenant_id,
                    "metadata[plan]": plan,
                },
                timeout=15,
            )
            if response.status_code == 200:
                return response.json().get("url")
            logger.error(
                "Stripe checkout failed: %s",
                response.text[:200],
            )
            return None
        except Exception as e:
            logger.error("Stripe error: %s", e)
            return None

    def handle_stripe_webhook(
        self, payload: str, signature: str
    ) -> dict:
        """Process Stripe webhook events with signature verification."""
        # v2.0 — verify webhook signature
        if (
            self.stripe_webhook_secret
            and signature
        ):
            try:
                import stripe

                event = stripe.Webhook.construct_event(
                    payload,
                    signature,
                    self.stripe_webhook_secret,
                )
            except stripe.error.SignatureVerificationError:
                logger.error(
                    "Stripe webhook: invalid signature"
                )
                return {
                    "status": "error",
                    "message": "Invalid signature",
                }
            except Exception as e:
                logger.error(
                    "Stripe webhook verification error: %s",
                    e,
                )
                return {
                    "status": "error",
                    "message": str(e),
                }
        else:
            # Fallback: parse without verification
            # (development or missing secret)
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                return {
                    "status": "error",
                    "message": "Invalid JSON",
                }

        event_type = event.get("type")

        if event_type == "checkout.session.completed":
            session = (
                event.get("data", {})
                .get("object", {})
            )
            tenant_id = (
                session.get("metadata", {})
                .get("tenant_id")
            )
            plan = (
                session.get("metadata", {})
                .get("plan")
            )

            if tenant_id and self.supabase:
                self.supabase.table(
                    "subscriptions"
                ).upsert({
                    "id": f"sub-{tenant_id}",
                    "tenant_id": tenant_id,
                    "plan": plan,
                    "status": "active",
                    "stripe_subscription_id": session.get(
                        "subscription"
                    ),
                    "amount": session.get(
                        "amount_total", 0
                    )
                    / 100,
                    "currency": session.get(
                        "currency", "usd"
                    ).upper(),
                    "current_period_start": datetime.now(
                        timezone.utc
                    ).isoformat(),
                    "current_period_end": datetime.now(
                        timezone.utc
                    ).isoformat(),
                }).execute()

                self._emit(
                    "billing.subscription_created",
                    {
                        "tenant_id": tenant_id,
                        "plan": plan,
                        "method": "stripe",
                    },
                )

            return {
                "status": "ok",
                "event": event_type,
            }

        elif event_type == "customer.subscription.deleted":
            subscription = (
                event.get("data", {})
                .get("object", {})
            )
            metadata = subscription.get(
                "metadata", {}
            )
            tenant_id = metadata.get("tenant_id")

            if tenant_id and self.supabase:
                self.supabase.table(
                    "subscriptions"
                ).update({
                    "status": "cancelled",
                }).eq(
                    "tenant_id", tenant_id
                ).execute()

                self._emit(
                    "billing.subscription_cancelled",
                    {
                        "tenant_id": tenant_id,
                        "method": "stripe",
                    },
                )

            return {
                "status": "ok",
                "event": event_type,
            }

        return {
            "status": "ignored",
            "event": event_type,
        }

    # ------------------------------------------------------------------
    # M-PESA
    # ------------------------------------------------------------------

    @property
    def mpesa_configured(self) -> bool:
        return bool(
            self.mpesa_consumer_key
            and self.mpesa_consumer_secret
        )

    def _mpesa_auth(self) -> Optional[str]:
        """Get M-Pesa OAuth token."""
        try:
            base_url = (
                "sandbox"
                if self.mpesa_environment == "sandbox"
                else "api"
            )
            response = _http.get(
                f"https://{base_url}.safaricom.co.ke/"
                f"oauth/v1/generate"
                f"?grant_type=client_credentials",
                auth=(
                    self.mpesa_consumer_key,
                    self.mpesa_consumer_secret,
                ),
                timeout=10,
            )
            if response.status_code == 200:
                return response.json().get(
                    "access_token"
                )
            return None
        except Exception as e:
            logger.error(
                "M-Pesa auth error: %s", e
            )
            return None

    def _mpesa_password(
        self, timestamp: str
    ) -> str:
        """Generate M-Pesa API password."""
        raw = (
            f"{self.mpesa_shortcode}"
            f"{self.mpesa_passkey}"
            f"{timestamp}"
        )
        return base64.b64encode(raw.encode()).decode()

    def stk_push(
        self,
        phone_number: str,
        amount: int,
        reference: str,
        description: str = "Kanyoza Subscription",
    ) -> dict:
        """Initiate M-Pesa STK Push payment."""
        if not self.mpesa_configured:
            return {
                "success": False,
                "error": "M-Pesa not configured",
            }

        token = self._mpesa_auth()
        if not token:
            return {
                "success": False,
                "error": "M-Pesa auth failed",
            }

        try:
            timestamp = (
                datetime.now().strftime(
                    "%Y%m%d%H%M%S"
                )
            )
            password = self._mpesa_password(
                timestamp
            )

            base_url = (
                "sandbox"
                if self.mpesa_environment == "sandbox"
                else "api"
            )

            response = _http.post(
                f"https://{base_url}.safaricom.co.ke/"
                f"mpesa/stkpush/v1/processrequest",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "BusinessShortCode": (
                        self.mpesa_shortcode
                    ),
                    "Password": password,
                    "Timestamp": timestamp,
                    "TransactionType": "CustomerPayBillOnline",
                    "Amount": amount,
                    "PartyA": phone_number.replace(
                        "+", ""
                    ),
                    "PartyB": self.mpesa_shortcode,
                    "PhoneNumber": phone_number.replace(
                        "+", ""
                    ),
                    "CallBackURL": (
                        f"{getattr(settings, 'base_url', '')}"
                        f"/api/mpesa/callback"
                    ),
                    "AccountReference": reference[
                        :12
                    ],
                    "TransactionDesc": description[
                        :13
                    ],
                },
                timeout=15,
            )

            result = response.json()
            if (
                result.get("ResponseCode")
                == "0"
            ):
                return {
                    "success": True,
                    "checkout_request_id": result.get(
                        "CheckoutRequestID"
                    ),
                }
            return {
                "success": False,
                "error": result.get(
                    "ResponseDescription",
                    "Unknown error",
                ),
            }
        except Exception as e:
            logger.error(
                "M-Pesa STK push error: %s", e
            )
            return {
                "success": False,
                "error": str(e),
            }

    def verify_transaction(
        self, checkout_request_id: str
    ) -> dict:
        """Verify M-Pesa transaction status."""
        if not self.mpesa_configured:
            return {
                "success": False,
                "error": "M-Pesa not configured",
            }

        token = self._mpesa_auth()
        if not token:
            return {
                "success": False,
                "error": "M-Pesa auth failed",
            }

        try:
            timestamp = (
                datetime.now().strftime(
                    "%Y%m%d%H%M%S"
                )
            )
            password = self._mpesa_password(
                timestamp
            )

            base_url = (
                "sandbox"
                if self.mpesa_environment == "sandbox"
                else "api"
            )

            response = _http.post(
                f"https://{base_url}.safaricom.co.ke/"
                f"mpesa/stkpushquery/v1/query",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "BusinessShortCode": (
                        self.mpesa_shortcode
                    ),
                    "Password": password,
                    "Timestamp": timestamp,
                    "CheckoutRequestID": (
                        checkout_request_id
                    ),
                },
                timeout=15,
            )
            return response.json()
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
            }

    # ------------------------------------------------------------------
    # UNIFIED BILLING
    # ------------------------------------------------------------------

    def create_subscription(
        self,
        tenant_id: str,
        plan: str,
        payment_method: str = "stripe",
        **kwargs,
    ) -> dict:
        """Create a subscription for a tenant."""
        if payment_method == "stripe":
            url = self.create_stripe_checkout(
                tenant_id,
                plan,
                kwargs.get("success_url", ""),
                kwargs.get("cancel_url", ""),
            )
            return {
                "success": url is not None,
                "checkout_url": url,
                "method": "stripe",
            }
        elif payment_method == "mpesa":
            result = self.stk_push(
                kwargs.get("phone_number", ""),
                kwargs.get("amount", 0),
                f"KNY-{tenant_id[:8]}",
            )
            return {**result, "method": "mpesa"}
        return {
            "success": False,
            "error": "Invalid payment method",
        }

    def get_subscription(
        self, tenant_id: str
    ) -> Optional[dict]:
        """Get tenant's current subscription."""
        if not self.supabase:
            return None
        try:
            result = (
                self.supabase.table(
                    "subscriptions"
                )
                .select("*")
                .eq("tenant_id", tenant_id)
                .maybe_single()
                .execute()
            )
            return result.data
        except Exception:
            return None

    # ------------------------------------------------------------------
    # HEALTH
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        return {
            "stripe_configured": self.stripe_configured,
            "mpesa_configured": self.mpesa_configured,
        }


billing = BillingService()
