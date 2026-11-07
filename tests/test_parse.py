from datetime import datetime

import pytest

from app.parse import classify, clean, find_eta, find_service, find_items, find_window, find_orders, find_tracking, item_title, parse

SENT = datetime(2026, 10, 1, 9, 0)  # a Thursday


@pytest.mark.parametrize("subject,body,stage", [
    ('Ordered: "Anker USB-C Cable 2m..."', "Thanks for your order", "ordered"),
    ('Shipped: "Anker USB-C Cable 2m..."', "Your package was shipped!", "dispatched"),
    ('Out for delivery: "Anker USB-C Cable"', "Ordered Shipped Out for delivery Delivered", "out_for_delivery"),
    ('Delivered: "Anker USB-C Cable"', "Ordered Shipped Out for delivery Delivered", "delivered"),
    ("Your Royal Mail item is on its way", "", "dispatched"),
    ("Note added to your order", "Your order has been despatched via Royal Mail Tracked 48", "dispatched"),
    ("Your parcel is coming today", "", "out_for_delivery"),
    ("Your Evri parcel has been delivered", "", "delivered"),
    ("We've got your parcel", "Evri has your parcel from ASOS", "with_carrier"),
    ("Sorry we missed you", "We tried to deliver your item today", "failed_attempt"),
    ("Your parcel is delayed", "", "delayed"),
    ("Your item has been returned to sender", "", "returned"),
    ("Your order has been cancelled", "", "cancelled"),
    ("A fee to pay on your parcel", "", "action_required"),
    ("Your DPD parcel will be delivered today", "1-hour window 10:15 - 11:15", "out_for_delivery"),
    ("Your parcel is ready to collect", "at your chosen locker", "ready_for_collection"),
    ("Order confirmation #123456", "", "ordered"),
    ("We're preparing your order", "", "processing"),
    ("Update on your parcel", "Your parcel has arrived at our hub in Leeds", "in_transit"),
    ("Your newsletter", "Big savings this weekend", None),
    ("We're expecting your Northwind parcel", "a photo of it as proof it has been delivered", "label_created"),
    ("Order 12-34567-89012 has been cancelled", "the item was damaged", "cancelled"),
    ("Parcel loading...", "As soon as we have it we'll let you know when it's ready to collect", "label_created"),
    ("Parcels over & out", "We're happy your parcel is now in your hands", "delivered"),
    ("Our courier has received your order!", "it will be delivered today", "with_carrier"),
    ("Package AB123: in your country/region", "Your package has arrived in your country/region.", "in_transit"),
    ("We're expecting your Northwind parcel", "Choosing to have your parcel delivered to a Pickup Point saves CO2", "label_created"),
    ("Your request to collect from Corner Shop", "We're expecting your parcel. We'll be in touch when it's ready for collection", "label_created"),
    ("Your Northwind parcel is ready for you to collect from Corner Shop", "", "ready_for_collection"),
    ("Your package is now with its courier.", "Let the tracking begin!", "with_carrier"),
    ("📦PACKAGE DELIVERED: Desk lamp", "Your package is here.", "delivered"),
])
def test_classify(subject, body, stage):
    assert classify(subject, body) == stage


def test_progress_bar_in_body_tail_ignored():
    body = "Your order is being prepared. " + "x" * 700 + " Delivered"
    assert classify("Order update", body) == "processing"


def test_postage_service():
    assert find_service("Shipping: £ 3.10 via Tracked 48") == 2
    assert find_service("Royal Mail 1st Class") == 1
    assert find_service("Standard delivery") is None


def test_tracking_numbers():
    t = find_tracking("Track AB123456785GB and 1Z999AA10123456784, also AB123456789GB (bad check)")
    assert t == {"AB123456785GB": "royal_mail", "1Z999AA10123456784": "ups"}
    assert find_tracking("Tracking number: 15501234567890") == {"15501234567890": "dpd"}
    assert find_tracking("Call us on 08001234567") == {}


def test_orders():
    assert find_orders("Order # 205-1234567-7654321 placed") == {"205-1234567-7654321"}
    assert find_orders("Order number: ABC12345") == {"ABC12345"}
    assert find_orders("View order details") == set()


@pytest.mark.parametrize("text,eta", [
    ("Arriving Saturday", "2026-10-03"),
    ("Arriving tomorrow", "2026-10-02"),
    ("Expected delivery: 6 October", "2026-10-06"),
    ("Estimated delivery date: 07/10/2026", "2026-10-07"),
    ("Arriving October 9", "2026-10-09"),
    ("Estimated Delivery: Thursday the 8th of October", "2026-10-08"),
    ("No date here", None),
])
def test_eta(text, eta):
    assert find_eta(text, "", SENT) == eta


def test_parse_skips_marketing_and_keeps_carrier_mail():
    assert parse("Autumn sale: free delivery", "deals@shop.com", "Free delivery on everything", SENT) is None
    assert parse("Your order has been dispatched", "a@shop.com", "", SENT)["weak"]
    f = parse("Your item is on its way", "no-reply@royalmail.com", "Tracking number AB123456785GB", SENT)
    assert f["carrier"] == "royal_mail" and f["stage"] == "dispatched" and "AB123456785GB" in f["tracking"]
    f = parse("Something new happened", "noreply@evri.com", "Parcel H01HYA0012345678", SENT)
    assert f["stage"] is None and f["carrier"] == "evri"


def test_bare_parcel_reference():
    assert find_tracking("Parcel 15501234567890, 1-hour window") == {"15501234567890": "dpd"}


def test_dpd_spaced_parcel_number_and_merchant_hint():
    f = parse("Your Northwind order will be delivered today between 11:05 - 12:05", "Northwind <yourdelivery@dpd.co.uk>",
              "We will deliver your Northwind parcel TODAY Your parcel: 1550 9876 543 210", SENT)
    assert f["tracking"] == {"15509876543210": "dpd"} and f["hint"] == "Northwind"
    rm = parse("Your parcel from 1. RECORDBARN is on its way", "Royal Mail <no-reply@royalmail.com>", "", SENT)
    assert rm["hint"] == "RECORDBARN"
    assert parse("Your Vinted delivery location has been updated", "Evri <do-not-reply@evri.com>",
                 "Parcel H01HYA0012345678", SENT)["hint"] == "Vinted"
    assert parse("Your Evri parcel is on its way", "Evri <do-not-reply@evri.com>", "Parcel H01HYA0012345678", SENT)["hint"] is None
    assert parse("Your parcel is ready to collect", "InPost via Vinted <shipping@relay.vinted.com>",
                 "Parcel number JJD0001234567890123", SENT)["tracking"] == {"JJD0001234567890123": None}


def test_payment_food_and_digital_senders_ignored():
    assert parse("Receipt for your payment to Inkwell Cards Limited", "PayPal <service@paypal.co.uk>",
                 "Order ID 55512345 Thanks for your order", SENT) is None
    assert parse("Your order has been cancelled", "Deliveroo <noreply@t.deliveroo.co.uk>", "Order #5219", SENT) is None


def test_css_colours_are_not_order_numbers():
    f = parse("Your parcel is ready to collect", "InPost via Vinted <shipping@relay.vinted.com>",
              "html{color: #000000 !important} .x{background:#282828} Parcel number JJD0001234567890123", SENT)
    assert f["orders"] == []


@pytest.mark.parametrize("subject,body,items", [
    ("Ordered: ‘Bamboo & Walnut...’", "Order # 202-5550123-4567890 View or edit order "
     "* Bamboo & Walnut Chopping Board Large – Reversible, Juice Groove & Non-Slip Feet Quantity: 1 14.50 GBP "
     "* Kitchen Sponge Scourers 6x 40 g Quantity: 2 3.49 GBP", ["Bamboo & Walnut Chopping Board Large", "Kitchen Sponge Scourers 6x 40 g"]),
    ("A shipment from order #418273 is on the way", "Items in this shipment ---------------------- "
     "Night Owls / Live At The Lantern - White Tee × 1 L", ["Night Owls / Live At The Lantern - White Tee"]),
    ("Your Fernway Outdoor UK order has been received", "Product Quantity Price Wool Beanie - Grey 1 £ 12.00", ["Wool Beanie - Grey"]),
    ("Your order 301-5550142-7781234", "Order details Featherlite&trade; Packable Quilted Gilet Product Code: 01234567<br>Colour: NAVY",
     ["Featherlite™ Packable Quilted Gilet"]),
    ("Thank you for your Northwind order 1234509876", "Items purchased Quantity Subtotal [LUMAVO] LUMAVO LED bulb E27 600 lumen £2.25",
     ["LUMAVO LED bulb E27 600 lumen"]),
    ("Your Byteforge order has shipped", "Items Qty Arctic P12 120mm PWM Case Fan 5 Pack Black- FAN-ARC-P12-BK-5 1 Thank you",
     ["Arctic P12 120mm PWM Case Fan 5 Pack Black"]),
])
def test_items_from_body(subject, body, items):
    assert find_items(clean(body)) == items


@pytest.mark.parametrize("subject,title", [
    ("Ordered: ‘Bamboo & Walnut...’", "Bamboo & Walnut"),
    ("Order confirmed: Samsung 990 1TB M.2 N...", "Samsung 990 1TB M.2 N"),
    ('Your receipt for "Women’s Medium navy striped linen shirt“', "Women’s Medium navy striped linen shirt"),
])
def test_item_title_from_subject(subject, title):
    assert item_title(subject) == title


def test_order_with_warehouse_items_and_shipment_plan():
    body = ("\u200c " * 50 + "Thank You Your order details are below, including estimated delivery date(s). "
            "Order Number: #104455667 Delivery Method: Evri - Evri Item(s) in our EU warehouse: "
            "Acme - Comfort Saddle (Black) Qty: 1 Estimated Delivery: 14/10/2026 - 16/10/2026")
    f = parse("TrailHub: New Order # 104455667", "TrailHub Sales <no-reply@send.trailhub.example>", body, SENT)
    assert f["stage"] == "ordered" and f["orders"] == ["104455667"] and f["carrier"] == "evri"
    assert f["items"] == ["Acme - Comfort Saddle (Black)"] and f["eta"] == "2026-10-14"
    assert classify("Your Shipment Plan: Order # 104455667", "") == "processing"


WINDOW_SENT = datetime(2026, 10, 3, 9, 0)


@pytest.mark.parametrize("subject,text,window", [
    ("Out for delivery: 'Chopping board'", "Ordered Dispatched Out for delivery Delivered Arriving today 2:30 pm - 6:30 pm Alex", ("2026-10-03", "14:30", "18:30")),
    ("Your GB parcel is out for delivery", "Your courier is delivering your GB parcel today. Estimated delivery time 17:00 - 19:00 Track", ("2026-10-03", "17:00", "19:00")),
    ("Your Vinted delivery time has been updated", "Your estimated delivery time has changed Updated delivery time 09:45 - 10:45 Your courier", ("2026-10-03", "09:45", "10:45")),
    ("Your Northwind order will be delivered today between 11:05 - 12:05", "", ("2026-10-03", "11:05", "12:05")),
    ("Out for delivery today", "Sam will deliver your item from Oakwood TODAY between 10:40-12:40 You can", ("2026-10-03", "10:40", "12:40")),
    ("Your parcel is on its way", "Delivery is due: Monday, 5 October 2026* Delivery is estimated by: 5:00pm* We won't", ("2026-10-05", None, "17:00")),
    ("Your parcel is out for delivery", "Your parcel will be delivered between 5:30 - 7:30pm today", ("2026-10-03", "17:30", "19:30")),
    # not delivery windows
    ("Your order", "On your delivery day, your order should be with you between 7am and 9pm .", None),
    ("Thank you for your Northwind order", "Parcel delivery. Planned delivery date: Saturday 3 October 2026 08:00 - 20:00 Items", None),
    ("Your parcel is ready to collect", "Opening times Mon ▪ 08:00am - 06:00pm Tue ▪ 08:00am - 06:00pm", None),
    ("We're expecting your Northwind parcel", "We'll send your time slot on the day of delivery once your parcel has been loaded on the van, usually by 12:00pm.", None),
    ("New Order", "call us between the hours of 9am to 5pm GMT, Monday to Friday", None),
    ("Postage Confirmation", "Collection date: Tuesday, 4 March 2025 estimated by 17:00", None),
    ("Dispatched", "Arriving in 2 - 3 days", None),
])
def test_delivery_window(subject, text, window):
    w = find_window(subject, text, WINDOW_SENT)
    assert (w and (w["day"], w["from"], w["to"])) == window or (w is None and window is None)


def test_ready_to_collect_boilerplate_is_not_the_stage():
    body = "Your parcel is due to be delivered today. We'll let you know when your parcel is ready to collect. If we are delivering"
    assert classify("Your parcel from 1. RECORDBARN is due to be delivered today", body) != "ready_for_collection"


def test_plain_part_that_is_mostly_css_falls_back_to_html():
    import base64
    from app.gmail import to_text
    part = lambda mime, body: {"mimeType": mime, "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}}
    css = " ".join(f".c{i}{{ color: #000; margin: 0; }}" for i in range(50))
    payload = {"mimeType": "multipart/alternative", "parts": [
        part("text/plain", css + " Parcel number JJD0001234567890123"),
        part("text/html", f"<style>{css}</style><p>Parcel number JJD0001234567890123</p>")]}
    assert to_text(payload) == "Parcel number JJD0001234567890123"
