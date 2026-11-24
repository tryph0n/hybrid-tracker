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

# EODI "inicio_precision", as its incident pages label it: "dia" day only,
# "hora" approximate time, "minuto" exact time, "aproximada" approximate date.
DATE_PRECISION = {
    "dia": "day",
    "hora": "day",
    "minuto": "day",
    "aproximada": "approximate",
}

# EODI gives the attributed actor in the language of the attributing authority.
# True means Russia. Listed explicitly because guessing from the spelling is
# unsafe ("Belarus" contains "rus"). An unknown name stops the ingestion.
ACTOR_IS_RUSSIA = {
    "Rosja": True,
    "Federacja Rosyjska": True,
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
    """Download the register and return it in the unified schema.

    Returns:
        Located incidents (CSV) and unlocated ones (JSON), mapped by `normalize`.

    Raises:
        OSError: A download failed.
        KeyError: A column is missing (see `normalize`) or a JSON field is
            missing (see `fetch_unlocated`).
        ValueError: An unknown date precision, an unknown actor or an attributed
            unlocated incident (see `normalize` and `fetch_unlocated`).
    """
    located = pd.read_csv(LOCATED_URL, storage_options=HEADERS)
    return normalize(pd.concat([located, fetch_unlocated()], ignore_index=True))


def fetch_unlocated() -> pd.DataFrame:
    """Incidents without exact coordinates, reshaped like the CSV rows.

    Downloads the JSON file (60 s timeout). The frame has no `latitud`,
    `longitud` or `atribucion_actor` column: after the concat in `fetch` those
    cells are empty.

    Returns:
        One row per incident, with the CSV column names the other rows use.

    Raises:
        OSError: The download failed.
        KeyError: A JSON field is missing.
        ValueError: An incident has the state "atribuido": its actor is not
            parsed yet, so the ingestion stops.
    """
    request = urllib.request.Request(UNLOCATED_URL, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        incidents = json.load(response)["incidentes"]

    rows = []
    for incident in incidents:
        if incident["estado"]["actual"] == "atribuido":
            raise ValueError(f"Attributed unlocated incident: {incident['id']}")
        rows.append(
            {
                "id": incident["id"],
                "tipo": incident["tipo"],
                "presencia_dron": incident["presencia_dron"],
                "titulo_en": incident["titulo"]["en"],
                "inicio": incident["tiempo"]["inicio"]["valor"],
                "inicio_precision": incident["tiempo"]["inicio"]["precision"],
                "pais": incident["lugar"]["pais"],
                "lugar_en": incident["nombres"]["lugar"]["en"],
                "enlace": INCIDENT_PAGE.format(id=incident["id"]),
            }
        )
    return pd.DataFrame(rows)


def normalize(raw: pd.DataFrame) -> pd.DataFrame:
    """Map EODI columns to the unified schema.

    Args:
        raw: EODI rows with the columns `id`, `inicio`, `inicio_precision`,
            `pais`, `lugar_en`, `latitud`, `longitud`, `tipo`, `titulo_en`,
            `presencia_dron`, `atribucion_actor` and `enlace`.

    Returns:
        The rows with exactly the columns of `tracker.schema.COLUMNS`. An
        unknown `tipo` or `presencia_dron` leaves `category` or `event_status`
        empty, which `validate` rejects.

    Raises:
        KeyError: A column of `raw` is missing.
        ValueError: An unknown date precision or actor.
    """
    events = pd.DataFrame(
        {
            "event_id": "eodi:" + raw["id"],
            "source": "eodi",
            "date": raw["inicio"].str[:10],
            "date_precision": raw["inicio_precision"].map(date_precision),
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


def date_precision(value) -> str:
    """Translate EODI's date precision into a DATE_PRECISIONS value.

    Args:
        value: The `inicio_precision` text, a key of `DATE_PRECISION`.

    Raises:
        ValueError: `value` is not a key of `DATE_PRECISION`.
    """
    if value not in DATE_PRECISION:
        raise ValueError(f"Unknown date precision {value!r}: add it to DATE_PRECISION")
    return DATE_PRECISION[value]


def russia_link(actor) -> str:
    """Translate EODI's attributed actor into a RUSSIA_LINKS value.

    Args:
        actor: The `atribucion_actor` name, or NaN when nobody is attributed.

    Returns:
        "official" for a name of Russia, "none" for NaN or for an actor mapped
        to False.

    Raises:
        ValueError: `actor` is not a key of `ACTOR_IS_RUSSIA`.
    """
    if pd.isna(actor):
        return "none"
    if actor not in ACTOR_IS_RUSSIA:
        raise ValueError(f"Unknown actor {actor!r}: add it to ACTOR_IS_RUSSIA")
    return "official" if ACTOR_IS_RUSSIA[actor] else "none"
