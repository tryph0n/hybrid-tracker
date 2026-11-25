"""European Observatory of Drone Incidents (EODI) - live source.

The observatory republishes its full register every hour, so each fetch
replaces our copy entirely. Licence: CC BY 4.0.
https://droneobservatory.eu
"""

import json
import urllib.request

import pandas as pd

from tracker.schema import COLUMNS, make_event_id, read_csv

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
# Each name maps to an ISO 3166-1 alpha-2 code. Listed explicitly because
# guessing from the spelling is unsafe ("Belarus" contains "rus"). An unknown
# name stops the ingestion. Ukraine is outside the five tracked states: the row
# keeps its code, as record 0002 fixes.
ACTOR_CODES = {
    "Rosja": "RU",
    "Federacja Rosyjska": "RU",
    "Federația Rusă": "RU",
    "Russland": "RU",
    "Ryssland": "RU",
    "Russia": "RU",
    "Rusia": "RU",
    "Rusija": "RU",
    "Russie": "RU",
    "Rusland": "RU",
    "Krievija": "RU",
    "Venemaa": "RU",
    "Venäjä": "RU",
    "Rusko": "RU",
    "Oroszország": "RU",
    "Русия": "RU",
    "Ρωσία": "RU",
    "Belarus": "BY",
    "Białoruś": "BY",
    "Weißrussland": "BY",
    "Vitryssland": "BY",
    "Baltkrievija": "BY",
    "Valgevene": "BY",
    "Bělorusko": "BY",
    "Belarusz": "BY",
    "Беларус": "BY",
    "Λευκορωσία": "BY",
    "Ukraina": "UA",
    "Ucraina": "UA",
}

# EODI "lugar.nivel" of an unlocated incident: how precisely the place is known.
# "region" is a named region without coordinates, for example "Romanian coast".
GEO_PRECISION = {
    "localidad": "place",
    "region": "place",
    "pais": "country",
}


def fetch() -> pd.DataFrame:
    """Download the register and return it in the unified schema.

    Returns:
        Located incidents (CSV) and unlocated ones (JSON), mapped by `normalize`.

    Raises:
        OSError: A download failed.
        KeyError: A column is missing (see `normalize`) or a JSON field is
            missing (see `fetch_unlocated`).
        ValueError: An unknown date precision, actor or `nivel`, or an
            attributed unlocated incident (see `normalize` and
            `fetch_unlocated`).
    """
    located = read_csv(LOCATED_URL, storage_options=HEADERS)
    return normalize(pd.concat([located, fetch_unlocated()], ignore_index=True))


def fetch_unlocated() -> pd.DataFrame:
    """Incidents without exact coordinates, reshaped like the CSV rows.

    Downloads the JSON file (60 s timeout). The frame has no `latitud`,
    `longitud`, `atribucion_actor` or `atribucion_autoridad` column: after the
    concat in `fetch` those cells are empty. `fin` and `nivel` come from
    `tiempo.fin.valor` and `lugar.nivel`; an absent `fin` stays empty.

    Returns:
        One row per incident, with the CSV column names the other rows use.

    Raises:
        OSError: The download failed.
        KeyError: A JSON field other than `tiempo.fin` is missing.
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
                "fin": incident["tiempo"].get("fin", {}).get("valor"),
                "nivel": incident["lugar"]["nivel"],
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
            `fin`, `pais`, `lugar_en`, `latitud`, `longitud`, `tipo`,
            `titulo_en`, `presencia_dron`, `atribucion_actor`,
            `atribucion_autoridad` and `enlace`. The column `nivel` is needed
            only for a row without coordinates.

    Returns:
        The rows with exactly the columns of `tracker.schema.COLUMNS`. An
        unknown `tipo` or `presencia_dron` leaves `category` or `event_status`
        empty, which `validate` rejects.

    Raises:
        KeyError: A column of `raw` is missing.
        ValueError: An unknown date precision, actor or `nivel`.
    """
    nivel = raw["nivel"] if "nivel" in raw else pd.Series(pd.NA, index=raw.index)
    actors = raw["atribucion_actor"].map(actor_code, na_action=None)
    attributed = actors.notna()
    events = pd.DataFrame(
        {
            "event_id": raw["id"].map(lambda i: make_event_id("eodi", i)),
            "source": "eodi",
            "date": raw["inicio"].str[:10],
            "end_date": raw["fin"].str[:10],
            "date_precision": raw["inicio_precision"].map(date_precision),
            "countries": raw["pais"],
            "place": raw["lugar_en"],
            "lat": raw["latitud"],
            "lon": raw["longitud"],
            "geo_precision": [
                geo_precision(located, level)
                for located, level in zip(raw["latitud"].notna(), nivel)
            ],
            "domain": "air",
            "category": raw["tipo"].map(CATEGORY),
            "subcategory": raw["tipo"],
            "title": raw["titulo_en"],
            "event_status": raw["presencia_dron"].map(EVENT_STATUS),
            "actors": actors,
            "actor_name": pd.NA,
            "attribution": attributed.map({True: "official", False: "none"}),
            "attributed_by": raw["atribucion_autoridad"].where(attributed),
            "url": raw["enlace"],
        }
    )
    return events[COLUMNS]


def date_precision(value: str) -> str:
    """Translate EODI's date precision into a DATE_PRECISIONS value.

    Args:
        value: The `inicio_precision` text, a key of `DATE_PRECISION`.

    Raises:
        ValueError: `value` is not a key of `DATE_PRECISION`.
    """
    if value not in DATE_PRECISION:
        raise ValueError(f"Unknown date precision {value!r}: add it to DATE_PRECISION")
    return DATE_PRECISION[value]


def geo_precision(located: bool, level: str | float) -> str:
    """Translate the position of a row into a GEO_PRECISIONS value.

    Args:
        located: True when the row has coordinates.
        level: The `nivel` text of an unlocated row, a key of `GEO_PRECISION`.

    Returns:
        "exact" for a located row, otherwise the value of `GEO_PRECISION`.

    Raises:
        ValueError: The row has no coordinates and `level` is not a key of
            `GEO_PRECISION`.
    """
    if located:
        return "exact"
    if level not in GEO_PRECISION:
        raise ValueError(f"Unknown nivel {level!r}: add it to GEO_PRECISION")
    return GEO_PRECISION[level]


def actor_code(actor: str | float) -> str | float:
    """Translate EODI's attributed actor into an ISO 3166-1 alpha-2 code.

    Args:
        actor: The `atribucion_actor` name, or NaN when nobody is attributed.

    Returns:
        The code of the state, or NaN when nobody is attributed.

    Raises:
        ValueError: `actor` is not a key of `ACTOR_CODES`.
    """
    if pd.isna(actor):
        return pd.NA
    if actor not in ACTOR_CODES:
        raise ValueError(f"Unknown actor {actor!r}: add it to ACTOR_CODES")
    return ACTOR_CODES[actor]
