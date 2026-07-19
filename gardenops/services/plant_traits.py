from __future__ import annotations

from collections.abc import Mapping

_BERRY_CATEGORIES = {"baerbusker", "bærbusker"}
_EVIDENCE_REQUIRED_HARVEST_CATEGORIES = {"fro", "frø", "traer", "trær"}
_HARVEST_EVIDENCE = (
    "fruit",
    "frukt",
    "harvest",
    "høstes",
    "høsting",
    "innhøsting",
    "crop",
    "avling",
    "edible",
    "spiselig",
    "walnut",
    "hazelnut",
    "chestnut",
    "valnøtt",
    "hasselnøtt",
    "kastanje",
)
_HARVEST_NAME_EVIDENCE = (
    "apple",
    "eple",
    "pear",
    "pære",
    "plum",
    "plomme",
    "cherry",
    "kirsebær",
    "morell",
    "quince",
    "kvede",
    "walnut",
    "hazelnut",
    "chestnut",
    "valnøtt",
    "hasselnøtt",
    "kastanje",
)
_NON_HARVEST_EVIDENCE = ("fruitless", "ornamental", "sterile", "pryd")


def harvest_offset_months(plant: Mapping[str, object]) -> int | None:
    """Return a bloom-to-harvest estimate only when the plant is plausibly edible."""
    category = str(plant.get("category") or "").strip().lower()
    if category in _BERRY_CATEGORIES:
        return 2
    if category not in _EVIDENCE_REQUIRED_HARVEST_CATEGORIES:
        return None

    care_text = " ".join(
        str(plant.get(field) or "")
        for field in (
            "care_watering",
            "care_soil",
            "care_planting",
            "care_maintenance",
            "care_notes",
        )
    ).lower()
    if any(term in care_text for term in _NON_HARVEST_EVIDENCE):
        return None
    if any(term in care_text for term in _HARVEST_EVIDENCE):
        return 3

    name = str(plant.get("name") or "").lower()
    if any(term in name for term in _NON_HARVEST_EVIDENCE):
        return None
    if any(term in name for term in _HARVEST_NAME_EVIDENCE):
        return 3
    return None
