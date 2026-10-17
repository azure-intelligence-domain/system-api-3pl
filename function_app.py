"""[Logistics] Logistics Partner System API (openapi.yaml): bookings and shipments with the 3PL
partner speedy-3pl, in the platform's contract. Only the gateways reach it (Easy Auth): building
blocks on the platform's allow-list through the system-apis product (ADR 0014), and the partner
itself through the Partner Experience API (ADR 0017). Every change is a logistics fact on
evgt-intplat-logistics (asyncapi.yaml).
"""

import azure.functions as func

import kit
import tpl

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)  # Easy Auth guards the door

NOT_FOUND = ("Booking Not Found", "The partner has no booking with this id.")


def reason_of(req: func.HttpRequest) -> str:
    reason = req.params.get("reason") or "Order cancelled."
    if len(reason) > 200:
        raise kit.invalid("Query parameter reason is at most 200 characters.", req)
    return reason


@kit.refusals
def book_shipment(req: func.HttpRequest, store, topic) -> func.HttpResponse:
    journey = kit.journey(req)
    status, booked, facts = tpl.book_shipment(store, kit.body_of(req, "shipmentRequest"))
    topic.publish(facts, journey)
    return kit.hal(status, booked, {"Location": booked["_links"]["self"]["href"]})


@kit.refusals
def retrieve_list_of_shipment_bookings(req: func.HttpRequest, store) -> func.HttpResponse:
    kit.journey(req)
    status = req.params.get("status")
    if status and status not in ("booked", "pickedUp", "cancelled"):
        raise kit.invalid("Query parameter status is booked, pickedUp or cancelled.", req)
    found = tpl.bookings(store, status)
    self_href = "/shipment-bookings" + (f"?status={status}" if status else "")
    return kit.hal(200, {"_links": {"self": {"href": self_href}}, "totalCount": len(found), "_embedded": {"shipmentBookings": found}})


@kit.refusals
def retrieve_shipment_booking(req: func.HttpRequest, store) -> func.HttpResponse:
    kit.journey(req)
    doc = store.get("Consignment", req.route_params["shipmentId"])
    if doc is None:
        raise kit.refused(404, *NOT_FOUND, "not-found", req)
    return kit.hal(200, tpl.booking(doc))


@kit.refusals
def cancel_shipment_booking(req: func.HttpRequest, store, topic) -> func.HttpResponse:
    journey = kit.journey(req)
    try:
        cancelled, facts = tpl.cancel_booking(store, req.route_params["shipmentId"], reason_of(req))
    except tpl.ShipmentNotFound:
        raise kit.refused(404, *NOT_FOUND, "not-found", req) from None
    except tpl.AlreadyPickedUp:
        raise kit.refused(409, "Already Picked Up", "The partner picked the consignment up already; it can no longer be cancelled.",
                          "already-picked-up", req) from None
    topic.publish(facts, journey)
    return kit.hal(200, cancelled)


@kit.refusals
def record_pick_up(req: func.HttpRequest, store, topic) -> func.HttpResponse:
    journey = kit.journey(req)
    try:
        picked_up, facts = tpl.record_pick_up(store, req.route_params["shipmentId"], kit.body_of(req, "pickUpNotice"))
    except tpl.ShipmentNotFound:
        raise kit.refused(404, *NOT_FOUND, "not-found", req) from None
    except tpl.BookingCancelled:
        raise kit.refused(409, "Booking Cancelled", "The booking was cancelled; there is nothing to pick up.", "booking-cancelled", req) from None
    topic.publish(facts, journey)
    return kit.hal(200, picked_up)


@kit.refusals
def request_shipment(req: func.HttpRequest, store, topic) -> func.HttpResponse:
    journey = kit.journey(req)
    status, shipped, facts = tpl.request_shipment(store, kit.body_of(req, "shipmentRequest"))
    topic.publish(facts, journey)
    return kit.hal(status, shipped, {"Location": shipped["_links"]["self"]["href"]})


@kit.refusals
def retrieve_shipment(req: func.HttpRequest, store) -> func.HttpResponse:
    kit.journey(req)
    doc = store.get("Consignment", req.route_params["shipmentId"])
    if doc is None or not tpl.is_shipment(doc):
        raise kit.refused(404, "Shipment Not Found", "The partner has no shipment with this id.", "not-found", req)
    return kit.hal(200, tpl.shipment(doc))


@kit.refusals
def record_delivery(req: func.HttpRequest, store, topic) -> func.HttpResponse:
    journey = kit.journey(req)
    try:
        delivered, facts = tpl.record_delivery(store, req.route_params["shipmentId"], kit.body_of(req, "deliveryNotice"))
    except tpl.ShipmentNotFound:
        raise kit.refused(404, "Shipment Not Found", "The partner has no shipment with this id.", "not-found", req) from None
    topic.publish(facts, journey)
    return kit.hal(200, delivered)


# --- Routes (host.json: no route prefix, so the paths are the contract's) -----------------------


@app.route(route="shipment-bookings", methods=["POST"])
def bookShipment(req: func.HttpRequest) -> func.HttpResponse:
    return book_shipment(req, kit.system_store(), kit.domain_topic())


@app.route(route="shipment-bookings", methods=["GET"])
def retrieveListOfShipmentBookings(req: func.HttpRequest) -> func.HttpResponse:
    return retrieve_list_of_shipment_bookings(req, kit.system_store())


@app.route(route="shipment-bookings/{shipmentId}", methods=["GET"])
def retrieveShipmentBooking(req: func.HttpRequest) -> func.HttpResponse:
    return retrieve_shipment_booking(req, kit.system_store())


@app.route(route="shipment-bookings/{shipmentId}", methods=["DELETE"])
def cancelShipmentBooking(req: func.HttpRequest) -> func.HttpResponse:
    return cancel_shipment_booking(req, kit.system_store(), kit.domain_topic())


@app.route(route="shipment-bookings/{shipmentId}/pick-up", methods=["POST"])
def recordPickUp(req: func.HttpRequest) -> func.HttpResponse:
    return record_pick_up(req, kit.system_store(), kit.domain_topic())


@app.route(route="shipments", methods=["POST"])
def requestShipment(req: func.HttpRequest) -> func.HttpResponse:
    return request_shipment(req, kit.system_store(), kit.domain_topic())


@app.route(route="shipments/{shipmentId}", methods=["GET"])
def retrieveShipment(req: func.HttpRequest) -> func.HttpResponse:
    return retrieve_shipment(req, kit.system_store())


@app.route(route="shipments/{shipmentId}/delivery", methods=["POST"])
def recordDelivery(req: func.HttpRequest) -> func.HttpResponse:
    return record_delivery(req, kit.system_store(), kit.domain_topic())
