"""Parsers DATEX II (NAP/MOBI.E), independentes de namespaces.

Escritos a partir da Regra Técnica 1/EADME/2026 (Anexo I). A primeira
recolha real valida a estrutura; o resumo do run mostra o que foi lido.
"""
from lxml import etree

XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"


def _ln(el):
    try:
        return etree.QName(el).localname
    except (ValueError, TypeError):
        return None  # comentários / instruções de processamento


def _child(el, name):
    if el is None:
        return None
    for c in el:
        if _ln(c) == name:
            return c
    return None


def _desc(el, name):
    if el is None:
        return None
    for d in el.iter():
        if d is not el and _ln(d) == name:
            return d
    return None


def _text(el):
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t or None


def _value(el):
    """Texto multilingue (values/value) ou texto simples."""
    if el is None:
        return None
    v = _desc(el, "value")
    return _text(v) if v is not None else _text(el)


def _num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def parse_status(src):
    """Devolve (linhas, excerto, publication_time).

    Linhas: (point_id, status, last_updated). publication_time é o texto do
    primeiro elemento <publicationTime> (hora de geração do ficheiro).
    """
    rows, excerpt, pub, site = [], None, None, None
    for _, el in etree.iterparse(src, events=("end",), huge_tree=True):
        name = _ln(el)
        if pub is None and name == "publicationTime":
            pub = _text(el)
            continue
        if name == "reference":
            par = el.getparent()
            if par is not None and _ln(par) == "energyInfrastructureSiteStatus":
                site = el.get("id")
            continue
        if name != "refillPointStatus":
            continue
        if excerpt is None:
            excerpt = etree.tostring(el, encoding="unicode")[:1500]
        ref = _child(el, "reference")
        pid = ref.get("id") if ref is not None else el.get("id")
        st_el = _child(el, "status")
        if st_el is None:
            st_el = _desc(el, "status")
        lu = _text(_child(el, "lastUpdated"))
        if pid:
            rows.append((pid, _text(st_el) or "", lu, tariff_signature(el), site))
        el.clear(keep_tail=True)
    return rows, excerpt, pub


def tariff_signature(el):
    """Tarifário OPC do ponto como texto estável: "política;valor;início;fim|..." (ordenado).

    Políticas observadas: pricePerDeliveryUnit (€/kWh), pricePerChargingTime (€/min),
    flatRate (€/sessão).
    """
    parts = []
    for mix in el:
        if _ln(mix) != "electricEnergyMixOverride":
            continue
        pol = _text(_desc(mix, "pricingPolicy")) or ""
        fee = _text(_desc(mix, "minimumDeliveryFee")) or ""
        start = _text(_desc(mix, "overallStartTime")) or ""
        end = _text(_desc(mix, "overallEndTime")) or ""
        parts.append(f"{pol};{fee};{start};{end}")
    return "|".join(sorted(parts))


def tariff_components(sig, at_iso):
    """(€/sessão, €/kWh, €/min) em vigor no instante at_iso (texto ISO UTC)."""
    out = {"flatRate": 0.0, "pricePerDeliveryUnit": 0.0, "pricePerChargingTime": 0.0}
    for part in filter(None, (sig or "").split("|")):
        pol, fee, start, end = (part.split(";") + ["", "", "", ""])[:4]
        if start and start[:19] > at_iso[:19]:
            continue
        if end and end[:19] <= at_iso[:19]:
            continue
        try:
            out[pol] = float(fee)
        except (KeyError, ValueError):
            pass
    return out["flatRate"], out["pricePerDeliveryUnit"], out["pricePerChargingTime"]


INFRA_FIELDS = [
    "point_id", "point_id_raw", "point_external_id", "site_id", "site_name",
    "operator_id", "operator_name", "lat", "lon", "postcode", "city",
    "address", "hours_type", "n_connectors", "connector_types",
    "charging_modes", "max_power_raw", "available_power_raw", "hours_raw",
    # v1.9 — atributos adicionais por ponto
    "station_id", "connector_formats", "voltage_max", "current_max", "plug_and_charge",
    "usage_types", "is_green_energy", "energy_sources", "co2_impact", "nuclear_impact",
    "payment_brands", "contract_providers_n",
]

SITE_FIELDS = [
    "site_id", "site_external_id", "site_name", "operator_id", "operator_name", "operator_nif",
    "operator_website", "operator_phone", "owner_id", "owner_name", "suboperator_id", "suboperator_name",
    "country", "nuts1_code", "nuts1_name", "city", "postcode", "address", "lat", "lon", "time_zone",
    "directions", "hours_type", "hours_raw", "n_stations", "n_points", "simultaneous_points", "station_points",
    "parking_spaces", "pmr_spaces", "accessibility", "service_facilities", "equipment", "service_type",
    "auth_methods", "payment_brands", "contract_providers_n", "contract_providers",
    "vehicle_types", "max_weight_t", "max_height_m", "max_length_m", "max_width_m",
    "green_energy_share", "last_updated",
]


def unique_keys(pairs):
    """Chave estável por ponto. Alguns operadores usam IDs simples (ex.: "202") que se repetem
    noutros locais; nesses casos a chave passa a ser "local|ID". pairs = [(site_id, point_id)]."""
    from collections import Counter
    n = Counter(pid for _, pid in pairs)
    return [pid if n[pid] == 1 else f"{site}|{pid}" for site, pid in pairs]


def hours_text(oh):
    """Horário de funcionamento em texto compacto (folhas do XML: "nome=valor;...")."""
    if oh is None:
        return None
    parts = [f"{_ln(d)}={_text(d)}" for d in oh.iter() if d is not oh and len(d) == 0 and _text(d)]
    return ";".join(parts) or None


def _texts(el, name):
    """Todos os textos (únicos, ordenados) de elementos com este nome local."""
    if el is None:
        return []
    return sorted({t for d in el.iter() if d is not el and _ln(d) == name and (t := _text(d))})


def _join(vals):
    return "|".join(vals) if vals else None


def parse_infra(src):
    """Inventário completo. Devolve (pontos, excerto, locais).

    pontos: uma linha por ponto (refillPoint); locais: uma linha por local (energyInfrastructureSite).
    Campos segundo a Regra Técnica 1/EADME/2026 (Anexo I); campos opcionais podem vir vazios.
    """
    rows, sites, excerpt = [], [], None
    for _, el in etree.iterparse(src, events=("end",), huge_tree=True):
        if _ln(el) != "energyInfrastructureSite":
            continue
        if excerpt is None:
            excerpt = etree.tostring(el, encoding="unicode")[:2500]
        op = _child(el, "operator")
        owner = _child(el, "owner")
        sub = _desc(op, "subOrganisation")
        addr_line = _desc(el, "addressLine")
        oh = _child(el, "operatingHours")
        oh_type = oh.get(XSI_TYPE) if oh is not None else None
        named = _desc(el, "namedArea")
        phone = _texts(op, "telephoneNumber") or _texts(op, "telphoneNumber")
        # estacionamento: associatedFacility (carPark = lugares; other = lugares para pessoas com deficiência)
        parking = pmr = None
        for af in (d for d in el.iter() if _ln(d) == "associatedFacility"):
            cap = _num(_text(_desc(af, "carParkingCapacity")))
            kind = (_text(_child(af, "type")) or "").lower()
            desc = (_value(_child(af, "description")) or "").lower()
            if cap is None:
                continue
            if kind == "carpark" and "defici" not in desc and "disabil" not in desc:
                parking = (parking or 0) + cap
            else:
                pmr = (pmr or 0) + cap
        veh = _desc(el, "applicableForVehicles")
        stations = [d for d in el.iter() if _ln(d) == "energyInfrastructureStation"]
        st_pts = [(_text(_child(st, "numberOfRefillPoints"))) for st in stations]
        simult = sum(int(x) for x in st_pts if x and x.isdigit()) if any(st_pts) else None
        site = {
            "site_id": el.get("id"),
            "site_external_id": _text(_child(el, "externalIdentifier")),
            "site_name": _value(_child(el, "name")),
            "operator_id": op.get("id") if op is not None else None,
            "operator_name": _value(_child(op, "name")),
            "operator_nif": _text(_desc(op, "nationalOrganisationNumber")),
            "operator_website": _value(_desc(op, "linkToGeneralInformation")),
            "operator_phone": _join(phone),
            "owner_id": owner.get("id") if owner is not None else None,
            "owner_name": _value(_child(owner, "name")),
            "suboperator_id": sub.get("id") if sub is not None else None,
            "suboperator_name": _value(_child(sub, "name")),
            "country": _text(_desc(el, "countryCode")),
            "nuts1_code": _text(_desc(named, "nutsCode")),
            "nuts1_name": _value(_desc(named, "areaName")),
            "lat": _num(_text(_desc(el, "latitude"))),
            "lon": _num(_text(_desc(el, "longitude"))),
            "postcode": _text(_desc(el, "postcode")),
            "city": _value(_desc(el, "city")),
            "address": _value(_desc(addr_line, "text")),
            "time_zone": _text(_desc(el, "timeZone")),
            "directions": _join(_texts(_desc(el, "locationDescription"), "value")),
            "hours_type": oh_type,
            "hours_raw": hours_text(oh) if oh_type and "OpenAllHours" not in oh_type else None,
            "n_stations": len(stations),
            "simultaneous_points": simult,
            "station_points": _join([x or "" for x in st_pts]) if stations else None,
            "parking_spaces": parking,
            "pmr_spaces": pmr,
            "accessibility": _join(_texts(el, "accessibility")),
            "service_facilities": _join(_texts(el, "serviceFacilityType")),
            "equipment": _join(_texts(el, "equipmentType")),
            "service_type": _join(_texts(el, "serviceType")),
            "auth_methods": _join(_texts(el, "authenticationAndIdentificationMethods")),
            "payment_brands": _join(_texts(el, "brandsAcceptedList")),
            "contract_providers": _join(_texts(el, "brandsAccepted")),
            "vehicle_types": _join(_texts(veh, "vehicleType")),
            "max_weight_t": _num(_text(_desc(veh, "grossVehicleWeight"))),
            "max_height_m": _num(_text(_desc(veh, "vehicleHeight"))),
            "max_length_m": _num(_text(_desc(veh, "vehicleLength"))),
            "max_width_m": _num(_text(_desc(veh, "vehicleWidth"))),
            "last_updated": _text(_child(el, "lastUpdated")),
        }
        site["contract_providers_n"] = len(site["contract_providers"].split("|")) if site["contract_providers"] else None
        base = {k: site[k] for k in ("site_id", "site_name", "operator_id", "operator_name", "lat", "lon",
                                      "postcode", "city", "address", "hours_type", "hours_raw")}
        n_pts, n_green = 0, 0
        for st in stations or [el]:
            st_id = st.get("id") if st is not el else None
            for rp in st.iter():
                if _ln(rp) != "refillPoint":
                    continue
                conns = [c for c in rp if _ln(c) == "connector"]
                types = sorted({t for c in conns if (t := _text(_child(c, "connectorType")))})
                modes = sorted({m for c in conns if (m := _text(_child(c, "chargingMode")))})
                fmts = sorted({f for c in conns if (f := _text(_child(c, "connectorFormat")))})
                powers = [p for c in conns if (p := _num(_text(_child(c, "maxPowerAtSocket")))) is not None]
                volts = [v for c in conns if (v := _num(_text(_child(c, "voltage")))) is not None]
                amps = [a for c in conns if (a := _num(_text(_child(c, "maximumCurrent")))) is not None]
                mix = _desc(rp, "electricEnergyMix")
                green = _text(_desc(mix, "isGreenEnergy"))
                sources = []
                for ratio in (d for d in (mix.iter() if mix is not None else []) if _ln(d) == "electricEnergySourceRatio"):
                    src_ = _text(_desc(ratio, "energySource")); pct = _text(_desc(ratio, "percentage"))
                    if src_:
                        sources.append(f"{src_}:{pct or ''}")
                v2g = _texts(rp, "vehicleToGridCommunicationType")
                provs = _texts(rp, "brandsAccepted")
                n_pts += 1; n_green += 1 if (green or "").lower() == "true" else 0
                rows.append({
                    "point_id": rp.get("id"),
                    "point_id_raw": rp.get("id"),
                    "point_external_id": _text(_child(rp, "externalIdentifier")),
                    **base,
                    "n_connectors": len(conns),
                    "connector_types": _join(types),
                    "charging_modes": _join(modes),
                    "max_power_raw": max(powers) if powers else None,
                    "available_power_raw": _num(_text(_child(rp, "availableChargingPower"))),
                    "station_id": st_id,
                    "connector_formats": _join(fmts),
                    "voltage_max": max(volts) if volts else None,
                    "current_max": max(amps) if amps else None,
                    "plug_and_charge": ("iso15118" in "|".join(v2g).lower()) if v2g else None,
                    "usage_types": _join(_texts(rp, "usageType")),
                    "is_green_energy": green,
                    "energy_sources": _join(sorted(sources)),
                    "co2_impact": _num(_text(_desc(mix, "carbonDioxideImpact"))),
                    "nuclear_impact": _num(_text(_desc(mix, "nuclearWasteImpact"))),
                    "payment_brands": _join(_texts(rp, "brandsAcceptedList")),
                    "contract_providers_n": len(provs) or None,
                })
        site["n_points"] = n_pts
        site["green_energy_share"] = round(n_green / n_pts, 3) if n_pts else None
        sites.append(site)
        el.clear(keep_tail=True)
    for r, key in zip(rows, unique_keys([(r["site_id"], r["point_id_raw"]) for r in rows])):
        r["point_id"] = key
    return rows, excerpt, sites


def fill_rates(rows, fields):
    """% de registos com valor em cada campo."""
    n = len(rows) or 1
    return {f: round(100 * sum(1 for r in rows if r.get(f) not in (None, "", False)) / n, 1) for f in fields}
