"""[Logistics] Logistics Partner System API (openapi.yaml): shipments with the 3PL partner
speedy-3pl, in the platform's contract. Only the people gateway reaches it (Easy Auth), and only
building blocks on the platform's allow-list reach the gateway's system-apis product (ADR 0014).
Every change is a logistics fact on evgt-intplat-logistics (asyncapi.yaml).
"""

import azure.functions as func

import kit
import tpl

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)  # Easy Auth guards the door


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
    if doc is None:
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


@app.route(route="shipments", methods=["POST"])
def requestShipment(req: func.HttpRequest) -> func.HttpResponse:
    return request_shipment(req, kit.system_store(), kit.domain_topic())


@app.route(route="shipments/{shipmentId}", methods=["GET"])
def retrieveShipment(req: func.HttpRequest) -> func.HttpResponse:
    return retrieve_shipment(req, kit.system_store())


@app.route(route="shipments/{shipmentId}/delivery", methods=["POST"])
def recordDelivery(req: func.HttpRequest) -> func.HttpResponse:
    return record_delivery(req, kit.system_store(), kit.domain_topic())
