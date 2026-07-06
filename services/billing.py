"""
Billing Service v1.0
- Stripe payment integration
- M-Pesa mobile money integration
- Subscription management
- Invoice generation
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from config.settings import settings
from utils.http_client import HttpClient

logger = logging.getLogger("kanyoza.services.billing")

# Shared HTTP client for Stripe and M-Pesa API calls
_http = HttpClient("billing", default_timeout=30)


class BillingService:
    """Payment processing with Stripe and M-Pesa."""

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client
        self.stripe_key = getattr(settings, 'stripe_secret_key', '')
        self.stripe_webhook_secret = getattr(settings, 'stripe_webhook_secret', '')
        self.mpesa_consumer_key = getattr(settings, 'mpesa_consumer_key', '')
        self.mpesa_consumer_secret = getattr(settings, 'mpesa_consumer_secret', '')
        self.mpesa_passkey = getattr(settings, 'mpesa_passkey', '')
        self.mpesa_shortcode = getattr(settings, 'mpesa_shortcode', '')
        self.mpesa_environment = getattr(settings, 'mpesa_environment', 'sandbox')

    # ------------------------------------------------------------------
    # STRIPE
    # ------------------------------------------------------------------
    @property
    def stripe_configured(self) -> bool:
        return bool(self.stripe_key)

    def create_stripe_checkout(self, tenant_id: str, plan: str, success_url: str, cancel_url: str) -> Optional[str]:
        """Create a Stripe checkout session."""
        if not self.stripe_configured:
            return None

        prices = {
            'pro': 'price_pro_monthly',
            'business': 'price_business_monthly',
            'enterprise': 'price_enterprise_monthly',
        }

        try:
            response = _http.post(
                "https://api.stripe.com/v1/checkout/sessions",
                auth=(self.stripe_key, ''),
                data={
                    'payment_method_types[]': 'card',
                    'line_items[0][price]': prices.get(plan, prices['pro']),
                    'line_items[0][quantity]': 1,
                    'mode': 'subscription',
                    'success_url': success_url,
                    'cancel_url': cancel_url,
                    'client_reference_id': tenant_id,
                    'metadata[tenant_id]': tenant_id,
                    'metadata[plan]': plan,
                },
                timeout=15,
            )
            if response.status_code == 200:
                return response.json().get('url')
            logger.error(f"Stripe checkout failed: {response.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"Stripe error: {e}")
            return None

    def handle_stripe_webhook(self, payload: str, signature: str) -> dict:
        """Process Stripe webhook events."""
        # In production, verify signature with stripe_webhook_secret
        try:
            import json
            event = json.loads(payload)
            event_type = event.get('type')

            if event_type == 'checkout.session.completed':
                session = event.get('data', {}).get('object', {})
                tenant_id = session.get('metadata', {}).get('tenant_id')
                plan = session.get('metadata', {}).get('plan')
                if tenant_id and self.supabase:
                    self.supabase.table('subscriptions').upsert({
                        'id': f"sub-{tenant_id}",
                        'tenant_id': tenant_id,
                        'plan': plan,
                        'status': 'active',
                        'stripe_subscription_id': session.get('subscription'),
                        'amount': session.get('amount_total', 0) / 100,
                        'currency': session.get('currency', 'usd').upper(),
                        'current_period_start': datetime.now(timezone.utc).isoformat(),
                        'current_period_end': datetime.now(timezone.utc).isoformat(),
                    }).execute()
                return {'status': 'ok', 'event': event_type}

            return {'status': 'ignored', 'event': event_type}
        except Exception as e:
            logger.error(f"Stripe webhook error: {e}")
            return {'status': 'error', 'message': str(e)}

    # ------------------------------------------------------------------
    # M-PESA
    # ------------------------------------------------------------------
    @property
    def mpesa_configured(self) -> bool:
        return bool(self.mpesa_consumer_key and self.mpesa_consumer_secret)

    def _mpesa_auth(self) -> Optional[str]:
        """Get M-Pesa OAuth token."""
        try:
            response = _http.get(
                f"https://{'sandbox' if self.mpesa_environment == 'sandbox' else 'api'}.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials",
                auth=(self.mpesa_consumer_key, self.mpesa_consumer_secret),
                timeout=10,
            )
            if response.status_code == 200:
                return response.json().get('access_token')
            return None
        except Exception as e:
            logger.error(f"M-Pesa auth error: {e}")
            return None

    def stk_push(self, phone_number: str, amount: int, reference: str, description: str = "Kanyoza Subscription") -> dict:
        """Initiate M-Pesa STK Push payment."""
        if not self.mpesa_configured:
            return {'success': False, 'error': 'M-Pesa not configured'}

        token = self._mpesa_auth()
        if not token:
            return {'success': False, 'error': 'M-Pesa auth failed'}

        try:
            import base64
            from datetime import datetime as dt

            timestamp = dt.now().strftime('%Y%m%d%H%M%S')
            password = base64.b64encode(
                f"{self.mpesa_shortcode}{self.mpesa_passkey}{timestamp}".encode()
            ).decode()

            response = _http.post(
                f"https://{'sandbox' if self.mpesa_environment == 'sandbox' else 'api'}.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "BusinessShortCode": self.mpesa_shortcode,
                    "Password": password,
                    "Timestamp": timestamp,
                    "TransactionType": "CustomerPayBillOnline",
                    "Amount": amount,
                    "PartyA": phone_number.replace('+', ''),
                    "PartyB": self.mpesa_shortcode,
                    "PhoneNumber": phone_number.replace('+', ''),
                    "CallBackURL": f"{getattr(settings, 'base_url', '')}/api/mpesa/callback",
                    "AccountReference": reference[:12],
                    "TransactionDesc": description[:13],
                },
                timeout=15,
            )

            result = response.json()
            if result.get('ResponseCode') == '0':
                return {'success': True, 'checkout_request_id': result.get('CheckoutRequestID')}
            return {'success': False, 'error': result.get('ResponseDescription', 'Unknown error')}
        except Exception as e:
            logger.error(f"M-Pesa STK push error: {e}")
            return {'success': False, 'error': str(e)}

    def verify_transaction(self, checkout_request_id: str) -> dict:
        """Verify M-Pesa transaction status."""
        if not self.mpesa_configured:
            return {'success': False, 'error': 'M-Pesa not configured'}

        token = self._mpesa_auth()
        if not token:
            return {'success': False, 'error': 'M-Pesa auth failed'}

        try:
            response = _http.post(
                f"https://{'sandbox' if self.mpesa_environment == 'sandbox' else 'api'}.safaricom.co.ke/mpesa/stkpushquery/v1/query",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={
                    "BusinessShortCode": self.mpesa_shortcode,
                    "Password": "",  # Will be computed
                    "Timestamp": "",
                    "CheckoutRequestID": checkout_request_id,
                },
                timeout=15,
            )
            return response.json()
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # ------------------------------------------------------------------
    # UNIFIED BILLING
    # ------------------------------------------------------------------
    def create_subscription(self, tenant_id: str, plan: str, payment_method: str = 'stripe', **kwargs) -> dict:
        """Create a subscription for a tenant."""
        if payment_method == 'stripe':
            url = self.create_stripe_checkout(tenant_id, plan, kwargs.get('success_url', ''), kwargs.get('cancel_url', ''))
            return {'success': url is not None, 'checkout_url': url, 'method': 'stripe'}
        elif payment_method == 'mpesa':
            result = self.stk_push(kwargs.get('phone_number', ''), kwargs.get('amount', 0), f"KNY-{tenant_id[:8]}")
            return {**result, 'method': 'mpesa'}
        return {'success': False, 'error': 'Invalid payment method'}

    def get_subscription(self, tenant_id: str) -> Optional[dict]:
        """Get tenant's current subscription."""
        if not self.supabase:
            return None
        try:
            result = self.supabase.table('subscriptions').select('*').eq('tenant_id', tenant_id).maybe_single().execute()
            return result.data
        except Exception:
            return None


billing = BillingService()
