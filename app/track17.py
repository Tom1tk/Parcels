"""Optional 17TRACK API v2.4: carrier-level events and ETAs for active parcels."""
from datetime import datetime

import httpx

API = "https://api.17track.net/track/v2.4"
_STAGE = {"InfoReceived": "label_created", "InTransit": "in_transit", "OutForDelivery": "out_for_delivery",
          "AvailableForPickup": "ready_for_collection", "Delivered": "delivered", "DeliveryFailure": "failed_attempt"}
_SUB = {"InTransit_PickedUp": "with_carrier", "InTransit_CustomsProcessing": "customs",
        "InTransit_CustomsRequiringInformation": "action_required", "Exception_Returning": "returned",
        "Exception_Returned": "returned", "Exception_Lost": "lost", "Exception_Damage": "damaged",
        "Exception_Delayed": "delayed", "Exception_Cancel": "cancelled", "Exception_NoBody": "action_required"}


def _stage(stage, sub) -> str | None:
    return _SUB.get(sub) or _STAGE.get(stage) or ("delayed" if stage == "Exception" else None)


def _post(key: str, path: str, numbers: list[str]) -> dict:
    r = httpx.post(f"{API}/{path}", headers={"17token": key}, json=[{"number": n} for n in numbers], timeout=30)
    r.raise_for_status()
    return r.json()


def register(key: str, numbers: list[str]) -> None:
    """Costs 1 quota per new number; already-registered numbers are rejected for free."""
    for i in range(0, len(numbers), 40):
        _post(key, "register", numbers[i:i + 40])


def track(key: str, numbers: list[str]) -> dict[str, dict]:
    """number -> {eta, events:[{ts, stage, description, location}]}"""
    out = {}
    for i in range(0, len(numbers), 40):
        for item in _post(key, "gettrackinfo", numbers[i:i + 40]).get("data", {}).get("accepted", []):
            info = item.get("track_info") or {}
            eta = (info.get("time_metrics") or {}).get("estimated_delivery_date")
            if isinstance(eta, dict):  # {source, from, to}
                eta = eta.get("to") or eta.get("from")
            events = []
            for p in (info.get("tracking") or {}).get("providers") or []:
                for e in p.get("events") or []:
                    if e.get("time_iso"):
                        events.append({"ts": int(datetime.fromisoformat(e["time_iso"]).timestamp() * 1000),
                                       "stage": _stage(e.get("stage"), e.get("sub_status")),
                                       "description": e.get("description") or "", "location": e.get("location")})
            out[item["number"]] = {"eta": eta[:10] if eta else None, "events": events}
    return out
