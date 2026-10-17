"""The 3PL partner, as the 3PL System API sees it: consignments in the partner's own language
(backend-systems/3pl/README.md), mapped to the platform's contract. The partner's shipment API is
strict: the contract (openapi.yaml, additionalProperties: false) refuses any field it does not know.

A consignment is booked first (BOOKED); the partner reports the pick-up (PICKED_UP, from then on it
is a shipment) and the delivery (DELIVERED). A booking can be cancelled until it is picked up.
"""

import kit
from kit import Fact

PARTNER = "speedy-3pl"
PARTNER_API_VERSION = "2.3"
STATE = {"PICKED_UP": "dispatched", "DELIVERED": "delivered"}
BOOKING_STATE = {"BOOKED": "booked", "PICKED_UP": "pickedUp", "DELIVERED": "pickedUp", "CANCELLED": "cancelled"}


class ShipmentNotFound(Exception):
    pass


class AlreadyPickedUp(Exception):
    pass


class BookingCancelled(Exception):
    pass


def is_shipment(doc: dict) -> bool:
    """A consignment is a shipment once the partner picked it up."""
    return doc["state"] in STATE


def shipment(doc: dict) -> dict:
    out = {"_links": {"self": {"href": f"/shipments/{doc['id']}"}}, "shipmentId": doc["id"], "orderId": doc["customerRef"],
           "customerId": doc.get("consignee"), "partnerId": doc["partner"], "status": STATE[doc["state"]],
           "trackingNumber": doc["trackingNumber"], "dispatchedAt": doc["pickedUpAt"], "deliveredAt": doc.get("deliveredAt"),
           "receivedBy": doc.get("receivedBy")}
    return {k: v for k, v in out.items() if v is not None}


def booking(doc: dict) -> dict:
    out = {"_links": {"self": {"href": f"/shipment-bookings/{doc['id']}"}}, "shipmentId": doc["id"], "orderId": doc["customerRef"],
           "customerId": doc.get("consignee"), "partnerId": doc["partner"], "status": BOOKING_STATE[doc["state"]],
           "trackingNumber": doc["trackingNumber"],
           "deliveryAddress": {"street": doc["shipToStreet"], "postalCode": doc["shipToZip"], "city": doc["shipToCity"],
                               "countryCode": doc["shipToCountry"]},
           "orderLines": [{"productId": item["sku"], "quantity": item["qty"]} for item in doc["items"]],
           "requestedDeliveryDate": doc.get("wantedBy"), "bookedAt": doc.get("bookedAt") or doc["pickedUpAt"],
           "pickedUpAt": doc.get("pickedUpAt"), "cancelledAt": doc.get("cancelledAt"), "cancellationReason": doc.get("cancelReason")}
    return {k: v for k, v in out.items() if v is not None}


def _consignment(request: dict, state: str) -> dict:
    address = request["deliveryAddress"]
    doc = {"id": f"SPD-{int(request['orderId']):06d}", "partner": PARTNER, "customerRef": request["orderId"],
           "consignee": request.get("customerId"), "shipToName": request.get("customerId", ""), "shipToStreet": address["street"],
           "shipToZip": address["postalCode"], "shipToCity": address["city"], "shipToCountry": address["countryCode"],
           "items": [{"sku": line["productId"], "qty": line["quantity"]} for line in request["orderLines"]],
           "wantedBy": request.get("requestedDeliveryDate"), "apiVersion": PARTNER_API_VERSION, "state": state,
           "trackingNumber": f"SPD{request['orderId']}{address['countryCode']}"}
    return {k: v for k, v in doc.items() if v is not None}


def _keys(doc: dict) -> dict:
    return {"order_id": doc["customerRef"], "customer_id": doc.get("consignee"), "partner_id": PARTNER}


def _dispatched(doc: dict) -> Fact:
    return Fact("ShipmentDispatched", {"orderId": doc["customerRef"], "shipmentId": doc["id"], "partnerId": PARTNER,
                                       "trackingNumber": doc["trackingNumber"], "dispatchedAt": doc["pickedUpAt"]}, **_keys(doc))


def book_shipment(store, request: dict) -> tuple[int, dict, list[Fact]]:
    """The partner books the consignment; it reports the pick-up later."""
    existing = store.get("Consignment", f"SPD-{int(request['orderId']):06d}")
    if existing:  # one booking per order: a retry changes nothing
        return 200, booking(existing), []
    doc = {**_consignment(request, "BOOKED"), "bookedAt": kit.now()}
    store.create("Consignment", doc)
    return 201, booking(doc), [Fact("ShipmentBooked", {"orderId": doc["customerRef"], "shipmentId": doc["id"], "partnerId": PARTNER,
                                                       "trackingNumber": doc["trackingNumber"], "bookedAt": doc["bookedAt"]}, **_keys(doc))]


def request_shipment(store, request: dict) -> tuple[int, dict, list[Fact]]:
    """Deprecated since 1.1.0 (book_shipment): the partner takes the shipment and picks it up at once."""
    existing = store.get("Consignment", f"SPD-{int(request['orderId']):06d}")
    if existing and is_shipment(existing):  # one shipment per order: a retry changes nothing
        return 200, shipment(existing), []
    if existing:
        return 200, *record_pick_up(store, existing["id"], {})
    doc = {**_consignment(request, "PICKED_UP"), "pickedUpAt": kit.now()}
    store.create("Consignment", doc)
    return 201, shipment(doc), [_dispatched(doc)]


def bookings(store, status: str | None) -> list[dict]:
    """The partner's bookings, newest first."""
    states = [state for state, name in BOOKING_STATE.items() if name == status] if status else list(BOOKING_STATE)
    docs = [doc for state in states for doc in store.find("Consignment", state=state)]
    return [booking(doc) for doc in sorted(docs, key=lambda d: d.get("bookedAt") or d["pickedUpAt"], reverse=True)]


def _booked(store, shipment_id: str) -> dict:
    doc = store.get("Consignment", shipment_id)
    if doc is None:
        raise ShipmentNotFound
    return doc


def cancel_booking(store, shipment_id: str, reason: str) -> tuple[dict, list[Fact]]:
    """Cancel a booking before pick-up (soft delete). A repeated cancellation changes nothing."""
    doc = _booked(store, shipment_id)
    if doc["state"] == "CANCELLED":
        return booking(doc), []
    if is_shipment(doc):
        raise AlreadyPickedUp
    doc.update(state="CANCELLED", cancelledAt=kit.now(), cancelReason=reason)
    store.replace("Consignment", doc)
    return booking(doc), [Fact("ShipmentCancelled", {"orderId": doc["customerRef"], "shipmentId": doc["id"], "partnerId": PARTNER,
                                                     "reason": reason, "cancelledAt": doc["cancelledAt"]}, **_keys(doc))]


def record_pick_up(store, shipment_id: str, notice: dict) -> tuple[dict, list[Fact]]:
    """The partner's pick-up notice: the booking becomes a shipment. A repeated notice changes nothing."""
    doc = _booked(store, shipment_id)
    if doc["state"] == "CANCELLED":
        raise BookingCancelled
    if is_shipment(doc):
        return shipment(doc), []
    doc.update(state="PICKED_UP", pickedUpAt=notice.get("pickedUpAt") or kit.now())
    store.replace("Consignment", doc)
    return shipment(doc), [_dispatched(doc)]


def record_delivery(store, shipment_id: str, notice: dict) -> tuple[dict, list[Fact]]:
    """The partner's delivery notice. A repeated notice changes nothing."""
    doc = store.get("Consignment", shipment_id)
    if doc is None or not is_shipment(doc):
        raise ShipmentNotFound
    if doc["state"] == "DELIVERED":
        return shipment(doc), []
    doc.update(state="DELIVERED", deliveredAt=notice.get("deliveredAt") or kit.now())
    if notice.get("receivedBy"):
        doc["receivedBy"] = notice["receivedBy"]
    store.replace("Consignment", doc)
    data = {"orderId": doc["customerRef"], "shipmentId": doc["id"], "partnerId": PARTNER, "deliveredAt": doc["deliveredAt"]}
    if doc.get("receivedBy"):
        data["receivedBy"] = doc["receivedBy"]
    return shipment(doc), [Fact("ShipmentDelivered", data, **_keys(doc))]
