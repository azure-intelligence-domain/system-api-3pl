import json

import azure.functions as func
import pytest

import function_app as api
from contracts_test import assert_matches_asyncapi
from fakes import FakeTopic, MemoryStore

SHIP = {"orderId": "4711", "customerId": "ACME", "requestedDeliveryDate": "2026-10-09",
        "deliveryAddress": {"street": "Rue de la Loi 16", "postalCode": "1000", "city": "Brussels", "countryCode": "BE"},
        "orderLines": [{"productId": "SKU-1042", "quantity": 12}]}


def request(method, url, body=None, route=None):
    return func.HttpRequest(method, url, headers={"Correlation-Id": "ord-4711"}, route_params=route or {},
                            body=json.dumps(body).encode() if body is not None else b"")


@pytest.fixture
def tpl():
    return MemoryStore(), FakeTopic()


def ship(tpl, body=SHIP):
    response = api.request_shipment(request("POST", "/shipments", body), *tpl)
    return response, json.loads(response.get_body())


def book(tpl, body=SHIP):
    response = api.book_shipment(request("POST", "/shipment-bookings", body), *tpl)
    return response, json.loads(response.get_body())


def booking_call(handler, tpl, method="POST", body=None, params=None):
    req = func.HttpRequest(method, "/shipment-bookings/SPD-004711", headers={"Correlation-Id": "ord-4711"},
                           route_params={"shipmentId": "SPD-004711"}, params=params or {}, body=json.dumps(body).encode() if body is not None else b"")
    response = handler(req, *tpl)
    return response, json.loads(response.get_body())


def test_booked_then_picked_up_then_delivered(tpl):
    response, booked = book(tpl)
    assert response.status_code == 201 and booked["status"] == "booked" and booked["deliveryAddress"]["city"] == "Brussels"
    assert tpl[1].types == ["com.acme.logistics.shipmentBooked"]
    assert_matches_asyncapi(tpl[1].events[0])
    assert api.retrieve_shipment(request("GET", "/shipments/SPD-004711", route={"shipmentId": "SPD-004711"}), tpl[0]).status_code == 404
    work = json.loads(api.retrieve_list_of_shipment_bookings(
        func.HttpRequest("GET", "/shipment-bookings", headers={"Correlation-Id": "ord-4711"}, params={"status": "booked"}, body=b""), tpl[0]).get_body())
    assert work["totalCount"] == 1 and work["_embedded"]["shipmentBookings"][0]["shipmentId"] == "SPD-004711"
    response, shipped = booking_call(api.record_pick_up, tpl, body={})
    assert response.status_code == 200 and shipped["status"] == "dispatched"
    assert tpl[1].types[-1] == "com.acme.logistics.shipmentDispatched"
    assert_matches_asyncapi(tpl[1].events[-1])
    assert booking_call(api.record_pick_up, tpl, body={})[0].status_code == 200 and len(tpl[1].events) == 2
    assert booking_call(api.cancel_shipment_booking, tpl, "DELETE")[0].status_code == 409  # picked up: too late


def test_a_booking_is_cancelled_before_pick_up(tpl):
    book(tpl)
    response, cancelled = booking_call(api.cancel_shipment_booking, tpl, "DELETE", params={"reason": "Order cancelled."})
    assert response.status_code == 200 and cancelled["status"] == "cancelled" and cancelled["cancellationReason"] == "Order cancelled."
    assert tpl[1].types[-1] == "com.acme.logistics.shipmentCancelled"
    assert_matches_asyncapi(tpl[1].events[-1])
    assert booking_call(api.cancel_shipment_booking, tpl, "DELETE")[0].status_code == 200 and len(tpl[1].events) == 2
    assert booking_call(api.record_pick_up, tpl, body={})[0].status_code == 409


def test_the_partners_booking_api_is_strict(tpl):
    """The v2.4 story: a field the partner's API 2.3 does not know is refused."""
    response, problem = book(tpl, {**SHIP, "shipTo": SHIP["deliveryAddress"]})
    assert response.status_code == 400 and "shipTo" in problem["detail"] and not tpl[1].events


def test_the_partner_picks_up_at_once(tpl):
    """Deprecated since 1.1.0: request a shipment, picked up at once."""
    response, shipped = ship(tpl)
    assert response.status_code == 201 and shipped["shipmentId"] == "SPD-004711" and shipped["status"] == "dispatched"
    assert shipped["trackingNumber"] == "SPD4711BE"
    event = tpl[1].events[0]
    assert event["type"] == "com.acme.logistics.shipmentDispatched" and event["partnerid"] == "speedy-3pl"
    assert_matches_asyncapi(event)
    assert tpl[0].get("Consignment", "SPD-004711")["shipToCity"] == "Brussels"  # the partner's own naming


def test_the_partners_api_is_strict(tpl):
    """The v2.4 story: a field the partner's API 2.3 does not know is refused."""
    response, problem = ship(tpl, {**SHIP, "shipTo": SHIP["deliveryAddress"]})
    assert response.status_code == 400 and "shipTo" in problem["detail"]
    assert not tpl[1].events


def test_a_retry_ships_nothing_more(tpl):
    ship(tpl)
    response, _ = ship(tpl)
    assert response.status_code == 200 and len(tpl[1].events) == 1


def test_delivery_notice(tpl):
    ship(tpl)
    route = {"shipmentId": "SPD-004711"}
    response = api.record_delivery(request("POST", "/shipments/SPD-004711/delivery", {"receivedBy": "J. Peeters"}, route), *tpl)
    delivered = json.loads(response.get_body())
    assert response.status_code == 200 and delivered["status"] == "delivered" and delivered["receivedBy"] == "J. Peeters"
    assert tpl[1].types[-1] == "com.acme.logistics.shipmentDelivered"
    assert_matches_asyncapi(tpl[1].events[-1])
    again = api.record_delivery(request("POST", "/shipments/SPD-004711/delivery", {}, route), *tpl)
    assert again.status_code == 200 and len(tpl[1].events) == 2


def test_unknown_shipment_is_404(tpl):
    assert api.record_delivery(request("POST", "/x", {}, {"shipmentId": "SPD-1"}), *tpl).status_code == 404
