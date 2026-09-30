"""Commerce sector completion-domain models."""
from datetime import datetime
from typing import Optional
from pydantic import Field
from models.base import KanyozaModel


class Product(KanyozaModel):
    id: str
    workspace_id: str
    name: str
    sku: str
    price: float = Field(default=0, ge=0)
    currency: str = Field(default="MWK", min_length=3, max_length=3)
    inventory_quantity: int = Field(default=0, ge=0)
    is_active: bool = True
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class Customer(KanyozaModel):
    id: str
    workspace_id: str
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)


class OrderItem(KanyozaModel):
    product_id: str
    sku: str
    name: str
    quantity: int = Field(gt=0)
    unit_price: float = Field(ge=0)
    line_total: float = Field(ge=0)


class Order(KanyozaModel):
    id: str
    workspace_id: str
    customer_id: str
    items: list[OrderItem] = Field(default_factory=list)
    total_amount: float = Field(ge=0)
    currency: str = Field(default="MWK", min_length=3, max_length=3)
    status: str = Field(default="pending")
    payment_status: str = Field(default="unpaid")
    fulfillment_status: str = Field(default="unfulfilled")
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class InventoryReservation(KanyozaModel):
    id: str
    workspace_id: str
    order_id: str
    product_id: str
    quantity: int = Field(gt=0)
    status: str = Field(default="reserved")
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class PaymentRecord(KanyozaModel):
    id: str
    workspace_id: str
    order_id: str
    amount: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    status: str = Field(default="pending")
    provider_reference: Optional[str] = None
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class FulfillmentRecord(KanyozaModel):
    id: str
    workspace_id: str
    order_id: str
    status: str = Field(default="requested")
    carrier: Optional[str] = None
    tracking_reference: Optional[str] = None
    created_at: datetime = Field(default_factory=KanyozaModel.utcnow)
    updated_at: Optional[datetime] = None


class CommerceOutcome(KanyozaModel):
    id: str
    workspace_id: str
    order_id: Optional[str] = None
    metric: str
    value: float
    source: str = "commerce"
    evidence: dict = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=KanyozaModel.utcnow)
