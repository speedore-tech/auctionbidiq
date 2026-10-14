"""Sale-location geocoding (static table) and transport distance (spec §8.2)."""
from __future__ import annotations

import math
import re

# Common US truck-auction cities. Extend as needed; sales can also carry explicit lat/lon.
LOCATIONS: dict[str, tuple[float, float, str]] = {
    "SOUTH SIOUX CITY": (42.4739, -96.4131, "NE"),
    "SIOUX CITY": (42.4999, -96.4003, "IA"),
    "DALLAS": (32.7767, -96.7970, "TX"),
    "FORT WORTH": (32.7555, -97.3308, "TX"),
    "DFW": (32.8998, -97.0403, "TX"),
    "HOUSTON": (29.7604, -95.3698, "TX"),
    "SAN ANTONIO": (29.4241, -98.4936, "TX"),
    "AUSTIN": (30.2672, -97.7431, "TX"),
    "OKLAHOMA CITY": (35.4676, -97.5164, "OK"),
    "TULSA": (36.1540, -95.9928, "OK"),
    "KANSAS CITY": (39.0997, -94.5786, "MO"),
    "ST LOUIS": (38.6270, -90.1994, "MO"),
    "MEMPHIS": (35.1495, -90.0490, "TN"),
    "NASHVILLE": (36.1627, -86.7816, "TN"),
    "CHATTANOOGA": (35.0456, -85.3097, "TN"),
    "KNOXVILLE": (35.9606, -83.9207, "TN"),
    "ATLANTA": (33.7490, -84.3880, "GA"),
    "MONTGOMERY": (32.3792, -86.3077, "AL"),
    "BIRMINGHAM": (33.5186, -86.8104, "AL"),
    "JACKSON": (32.2988, -90.1848, "MS"),
    "LITTLE ROCK": (34.7465, -92.2896, "AR"),
    "SHREVEPORT": (32.5252, -93.7502, "LA"),
    "NEW ORLEANS": (29.9511, -90.0715, "LA"),
    "ORLANDO": (28.5383, -81.3792, "FL"),
    "TAMPA": (27.9506, -82.4572, "FL"),
    "JACKSONVILLE": (30.3322, -81.6557, "FL"),
    "CHARLOTTE": (35.2271, -80.8431, "NC"),
    "COLUMBUS": (39.9612, -82.9988, "OH"),
    "CINCINNATI": (39.1031, -84.5120, "OH"),
    "CLEVELAND": (41.4993, -81.6944, "OH"),
    "INDIANAPOLIS": (39.7684, -86.1581, "IN"),
    "CHICAGO": (41.8781, -87.6298, "IL"),
    "DETROIT": (42.3314, -83.0458, "MI"),
    "MINNEAPOLIS": (44.9778, -93.2650, "MN"),
    "OMAHA": (41.2565, -95.9345, "NE"),
    "DES MOINES": (41.5868, -93.6250, "IA"),
    "DENVER": (39.7392, -104.9903, "CO"),
    "SALT LAKE CITY": (40.7608, -111.8910, "UT"),
    "PHOENIX": (33.4484, -112.0740, "AZ"),
    "ALBUQUERQUE": (35.0844, -106.6504, "NM"),
    "EL PASO": (31.7619, -106.4850, "TX"),
    "LAS VEGAS": (36.1699, -115.1398, "NV"),
    "LOS ANGELES": (34.0522, -118.2437, "CA"),
    "FONTANA": (34.0922, -117.4350, "CA"),
    "SACRAMENTO": (38.5816, -121.4944, "CA"),
    "BAKERSFIELD": (35.3733, -119.0187, "CA"),
    "PORTLAND": (45.5152, -122.6784, "OR"),
    "SEATTLE": (47.6062, -122.3321, "WA"),
    "HARRISBURG": (40.2732, -76.8867, "PA"),
    "PHILADELPHIA": (39.9526, -75.1652, "PA"),
    "NEWARK": (40.7357, -74.1724, "NJ"),
    "BOSTON": (42.3601, -71.0589, "MA"),
    "RICHMOND": (37.5407, -77.4360, "VA"),
    "LEBANON": (36.2081, -86.2911, "TN"),
}


def lookup_location(name: str | None) -> tuple[float, float, str] | None:
    if not name:
        return None
    s = re.sub(r"[^A-Z ]", " ", name.upper())
    s = re.sub(r"\s+", " ", s).strip()
    if s in LOCATIONS:
        return LOCATIONS[s]
    # longest key contained in the string ("SOUTH SIOUX CITY NE" -> SOUTH SIOUX CITY)
    for key in sorted(LOCATIONS, key=len, reverse=True):
        if key in s:
            return LOCATIONS[key]
    return None


def haversine_miles(lat1, lon1, lat2, lon2) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def road_miles(origin: tuple[float, float] | None, home: tuple[float, float], road_factor: float) -> float | None:
    if not origin:
        return None
    return round(haversine_miles(origin[0], origin[1], home[0], home[1]) * road_factor, 0)
