# products/converter/service.py
"""
Currency conversion business logic.
- Fetches live rates from ExchangeRate-API
- Caches rates for 1 hour
- Supports 30+ currencies
"""

import logging
import time
import requests
from typing import Optional

from products.converter.models import ConversionRequest, ConversionResult, RateCache

logger = logging.getLogger("kanyoza.products.converter")

# Free tier: 1,500 requests/month — more than enough
EXCHANGE_API_BASE = "https://open.er-api.com/v6/latest"

# Currencies we support
SUPPORTED_CURRENCIES = {
    "USD", "MWK", "EUR", "GBP", "ZAR", "KES", "TZS", "UGX", "RWF",
    "NGN", "GHS", "AUD", "CAD", "JPY", "CNY", "INR", "BRL", "CHF",
    "SEK", "NOK", "DKK", "AED", "SAR", "QAR", "EGP", "MAD", "XOF",
    "XAF", "BWP", "ZMW", "MZN", "AOA", "SZL", "LSL", "NAD",
}


class CurrencyService:
    """Handles currency conversion with caching."""

    def __init__(self):
        self._cache: dict[str, RateCache] = {}

    def convert(self, request: ConversionRequest) -> Optional[ConversionResult]:
        """
        Convert currency. Returns ConversionResult or None on failure.
        """
        from_curr = request.from_currency
        to_curr = request.to_currency

        # Validate currencies
        if from_curr not in SUPPORTED_CURRENCIES:
            logger.warning(f"Unsupported currency: {from_curr}")
            return None
        if to_curr not in SUPPORTED_CURRENCIES:
            logger.warning(f"Unsupported currency: {to_curr}")
            return None

        # Same currency — no conversion needed
        if from_curr == to_curr:
            return ConversionResult(
                from_currency=from_curr,
                to_currency=to_curr,
                amount=request.amount,
                result=request.amount,
                rate=1.0,
            )

        # Get exchange rate
        rate = self._get_rate(from_curr, to_curr)
        if rate is None:
            return None

        result = round(request.amount * rate, 2)

        return ConversionResult(
            from_currency=from_curr,
            to_currency=to_curr,
            amount=request.amount,
            result=result,
            rate=round(rate, 4),
        )

    def _get_rate(self, from_curr: str, to_curr: str) -> Optional[float]:
        """
        Get exchange rate. Checks cache first, then fetches live.
        All rates are stored relative to USD.
        """
        # Check cache for USD base rates
        cache_key = "USD"
        cached = self._cache.get(cache_key)

        if cached and not cached.is_expired():
            logger.debug("Using cached rates")
            rates = cached.rates
        else:
            logger.info("Fetching live rates from API")
            rates = self._fetch_live_rates()
            if rates:
                self._cache[cache_key] = RateCache(
                    base_currency="USD",
                    rates=rates,
                    cached_at=time.time(),
                )
            else:
                # If API fails, try stale cache
                if cached:
                    logger.warning("API failed, using stale cache")
                    rates = cached.rates
                else:
                    logger.error("No rates available")
                    return None

        # Calculate cross rate via USD
        usd_to_from = rates.get(from_curr)
        usd_to_to = rates.get(to_curr)

        if not usd_to_from or not usd_to_to:
            logger.error(f"Rate not found: {from_curr}={usd_to_from}, {to_curr}={usd_to_to}")
            return None

        # Cross rate: (amount in USD) / (from in USD) * (to in USD)
        return usd_to_to / usd_to_from

    def _fetch_live_rates(self) -> Optional[dict[str, float]]:
        """
        Fetch live rates from ExchangeRate-API (free, no key needed).
        """
        try:
            response = requests.get(f"{EXCHANGE_API_BASE}/USD", timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("result") == "success":
                    rates = data.get("rates", {})
                    # Filter to only supported currencies
                    return {
                        curr: rate
                        for curr, rate in rates.items()
                        if curr in SUPPORTED_CURRENCIES
                    }
            logger.error(f"Exchange API returned {response.status_code}")
        except requests.exceptions.Timeout:
            logger.error("Exchange API timeout")
        except requests.exceptions.ConnectionError:
            logger.error("Exchange API unreachable")
        except Exception as e:
            logger.error(f"Exchange API error: {e}")

        return None

    def get_supported_currencies(self) -> list[str]:
        """Return sorted list of supported currencies."""
        return sorted(SUPPORTED_CURRENCIES)

    def health_check(self) -> bool:
        """Check if the exchange rate service is working."""
        rates = self._fetch_live_rates()
        return rates is not None and len(rates) > 0
