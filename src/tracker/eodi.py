"""European Observatory of Drone Incidents (EODI) - live source.

The observatory republishes its full register every hour, so each fetch
replaces our copy entirely. Licence: CC BY 4.0.
https://droneobservatory.eu
"""

import json
import urllib.request

import pandas as pd

from tracker.schema import COLUMNS

LOCATED_URL = "https://droneobservatory.eu/datos/incidentes.csv"
UNLOCATED_URL = "https://droneobservatory.eu/datos/incidentes_sin_ubicacion.json"
INCIDENT_PAGE = "https://droneobservatory.eu/{id}"
HEADERS = {"User-Agent": "hybrid-tracker (open-data dashboard, daily refresh)"}

CATEGORY = {
    "sobrevuelo": "drone_overflight",
    "interrupcion_aeroportuaria": "airport_disruption",
    "incursion": "airspace_incursion",
}

# EODI "presencia_dron": was a drone actually established?
EVENT_STATUS = {
    "confirmada": "confirmed",
    "no_confirmada": "unconfirmed",
    "no_acreditada": "unconfirmed",
    "descartada": "dismissed",
}

# EODI gives the attributed actor in the language of the attributing authority.
# True means Russia. Listed explicitly because guessing from the spelling is
# unsafe ("Belarus" contains "rus"). An unknown name stops the ingestion.
ACTOR_IS_RUSSIA = {
    "Rosja": True,
    "Federația Rusă": True,
    "Russland": True,
    "Ryssland": True,
    # Names of Russia in the languages of the countries the register covers.
    "Russia": True,
    "Rusia": True,
    "Rusija": True,
    "Russie": True,
    "Rusland": True,
    "Krievija": True,
    "Venemaa": True,
    "Venäjä": True,
    "Rusko": True,
    "Oroszország": True,
    "Русия": True,
    "Ρωσία": True,
    "Ukraina": False,
    "Ucraina": False,
}


def fetch() -> pd.DataFrame:
    """Download the register and return it in the unified schema."""
    located = pd.read_csv(LOCATED_URL, storage_options=HEADERS)
    return normalize(pd.concat([located, fetch_unlocated()], ignore_index=True))


def fetch_unlocated() -> pd.DataFrame:
    """Incidents without exact coordinates, reshaped like the CSV rows."""
    request = urllib.request.Request(UNLOCATED_URL, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        incidents = json.load(response)["incidentes"]

    rows = []
    for incident in incidents:
        if incident["estado"]["actual"] == "atribuido":
            # The attributed actor is not parsed from this file yet.
            raise ValueError(f"Attributed unlocated incident: {incident['id']}")
        rows.append(
            {
                "id": incident["id"],
                "tipo": incident["tipo"],
                "presencia_dron": incident["presencia_dron"],
                "titulo_en": incident["titulo"]["en"],
                "inicio": incident["tiempo"]["inicio"]["valor"],
                "pais": incident["lugar"]["pais"],
                "lugar_en": incident["nombres"]["lugar"]["en"],
                "enlace": INCIDENT_PAGE.format(id=incident["id"]),
            }
        )
    return pd.DataFrame(rows)


def normalize(raw: pd.DataFrame) -> pd.DataFrame:
    """Map EODI columns to the unified schema."""
    events = pd.DataFrame(
        {
            "event_id": "eodi:" + raw["id"],
            "source": "eodi",
            "date": raw["inicio"].str[:10],
            "date_precision": "day",
            "countries": raw["pais"],
            "place": raw["lugar_en"],
            "lat": raw["latitud"],
            "lon": raw["longitud"],
            "category": raw["tipo"].map(CATEGORY),
            "subcategory": raw["tipo"],
            "title": raw["titulo_en"],
            "event_status": raw["presencia_dron"].map(EVENT_STATUS),
            "russia_link": raw["atribucion_actor"].map(russia_link, na_action=None),
            "url": raw["enlace"],
        }
    )
    return events[COLUMNS]


def russia_link(actor) -> str:
    """Translate EODI's attributed actor into a RUSSIA_LINKS value."""
    if pd.isna(actor):
        return "none"
    if actor not in ACTOR_IS_RUSSIA:
        raise ValueError(f"Unknown actor {actor!r}: add it to ACTOR_IS_RUSSIA")
    return "official" if ACTOR_IS_RUSSIA[actor] else "none"
