from __future__ import annotations
import logging
from datetime import UTC, datetime
from typing import Any
import httpx
from .config import Settings
from .exceptions import MagiclineApiError
from .models import MagiclineBooking, MagiclineCustomer

logger = logging.getLogger(__name__)

class MagiclineClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.Client(
            base_url=settings.magicline_base_url, timeout=30,
            headers={"X-API-KEY": settings.magicline_api_key, "Accept": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None) -> Any:
        try:
            response = self._client.request(method, path, json=json_body)
        except httpx.HTTPError as exc:
            raise MagiclineApiError(f"Magicline request failed: {exc}") from exc
        if response.status_code >= 400:
            raise MagiclineApiError(f"Magicline API {response.status_code}: {response.text[:500]}")
        return response.json()

    def list_customers(self) -> list[MagiclineCustomer]:
        data = self._request("GET", "/v1/customers")
        return [MagiclineCustomer.model_validate(i) for i in data.get("result", data)]

    def search_customer_by_email(self, email: str) -> MagiclineCustomer | None:
        data = self._request("POST", "/v1/customers/search", json_body={"email": email})
        return MagiclineCustomer.model_validate(data[0]) if data else None

    def list_customer_bookings(self, customer_id: int) -> list[MagiclineBooking]:
        data = self._request("GET", f"/v1/appointments/booking?customerId={customer_id}")
        bookings = []
        for item in data:
            try:
                bookings.append(MagiclineBooking.model_validate(item))
            except Exception:
                logger.exception("Skipping invalid booking for customer_id=%s", customer_id)
        return bookings

    def list_customer_contracts(self, customer_id: int) -> list[dict[str, Any]]:
        data = self._request("GET", f"/v1/customers/{customer_id}/contracts")
        if not isinstance(data, list):
            raise MagiclineApiError("Unexpected contracts format.")
        return data

    def list_bookable_appointments(self) -> list[dict[str, Any]]:
        data = self._request("GET", f"/v1/appointments/bookable?studioId={self._settings.magicline_studio_id}")
        result = data.get("result", data)
        if not isinstance(result, list):
            raise MagiclineApiError("Unexpected bookable format.")
        return result

    def sync_candidates(self) -> list[tuple[MagiclineCustomer, list[MagiclineBooking], list[dict[str, Any]]]]:
        customers = self.list_customers()
        relevant = []
        for customer in customers:
            bookings = [b for b in self.list_customer_bookings(customer.id) if is_relevant_booking(b, self._settings)]
            if bookings:
                relevant.append((customer, bookings, self.list_customer_contracts(customer.id)))
        logger.info("Magicline sync candidates=%s", len(relevant))
        return relevant


def derive_entitlements(contracts: list[dict[str, Any]], settings: Settings) -> dict[str, bool]:
    # XXLARGE is obsolete. We now only care about the bookings.
    # We still return a dict for compatibility with the database schema.
    return {"has_xxlarge": False, "has_free_training_product": True}


def is_relevant_booking(booking: MagiclineBooking, settings: Settings) -> bool:
    """Title match only — used for sync to DB (includes past bookings)."""
    target = settings.magicline_relevant_appointment_title.strip().lower()
    actual = (booking.title or "").strip().lower()
    return actual == target


def is_access_booking(booking: MagiclineBooking, settings: Settings) -> bool:
    """Title match AND not yet ended — used for inspect/debug views."""
    return is_relevant_booking(booking, settings) and booking.end_date_time >= datetime.now(UTC)


def booking_effective_received_at() -> datetime:
    return datetime.now(UTC)
