"""The 3PL partner, as the 3PL System API sees it: consignments in the partner's own language
(backend-systems/3pl/README.md), mapped to the platform's contract. The partner's shipment API is
strict: the contract (openapi.yaml, additionalProperties: false) refuses any field it does not know.
"""

import kit
from kit import Fact

PARTNER = "speedy-3pl"
PARTNER_API_VERSION = "2.3"
STATE = {"PICKED_UP": "dispatched", "DELIVERED": "delivered"}


def shipment(doc: dict) -> dict:
    out = {"_links": {"self": {"href": f"/shipments/{doc['id']}"}}, "shipmentId": doc["id"], "orderId": doc["customerRef"],
           "customerId": doc.get("consignee"), "partnerId": doc["partner"], "status": STATE[doc["state"]],
           "trackingNumber": doc["trackingNumber"], "dispatchedAt": doc["pickedUpAt"], "deliveredAt": doc.get("deliveredAt"),
           "receivedBy": doc.get("receivedBy")}
    return {k: v for k, v in out.items() if v is not None}


def request_shipment(store, request: dict) -> tuple[int, dict, list[Fact]]:
    """The partner takes the shipment and picks it up at once."""
    shipment_id = f"SPD-{int(request['orderId']):06d}"
    existing = store.get("Consignment", shipment_id)
    if existing:  # one shipment per order: a retry changes nothing
        return 200, shipment(existing), []
    address = request["deliveryAddress"]
    doc = {"id": shipment_id, "partner": PARTNER, "customerRef": request["orderId"], "consignee": request.get("customerId"),
           "shipToName": request.get("customerId", ""), "shipToStreet": address["street"], "shipToZip": address["postalCode"],
           "shipToCity": address["city"], "shipToCountry": address["countryCode"],
           "items": [{"sku": line["productId"], "qty": line["quantity"]} for line in request["orderLines"]],
           "apiVersion": PARTNER_API_VERSION, "state": "PICKED_UP",
           "trackingNumber": f"SPD{request['orderId']}{address['countryCode']}", "pickedUpAt": kit.now()}
    store.create("Consignment", {k: v for k, v in doc.items() if v is not None})
    shipped = shipment(doc)
    return 201, shipped, [Fact("ShipmentDispatched", {"orderId": request["orderId"], "shipmentId": shipment_id, "partnerId": PARTNER,
                                                      "trackingNumber": doc["trackingNumber"], "dispatchedAt": doc["pickedUpAt"]},
                               order_id=request["orderId"], customer_id=request.get("customerId"), partner_id=PARTNER)]


class ShipmentNotFound(Exception):
    pass


def record_delivery(store, shipment_id: str, notice: dict) -> tuple[dict, list[Fact]]:
    """The partner's delivery notice. A repeated notice changes nothing."""
    doc = store.get("Consignment", shipment_id)
    if doc is None:
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
    return shipment(doc), [Fact("ShipmentDelivered", data, order_id=doc["customerRef"], customer_id=doc.get("consignee"), partner_id=PARTNER)]
