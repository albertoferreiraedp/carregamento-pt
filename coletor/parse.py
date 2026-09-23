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
    rows, excerpt, pub = [], None, None
    for _, el in etree.iterparse(src, events=("end",), huge_tree=True):
        name = _ln(el)
        if pub is None and name == "publicationTime":
            pub = _text(el)
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
            rows.append((pid, _text(st_el) or "", lu))
        el.clear(keep_tail=True)
    return rows, excerpt, pub


INFRA_FIELDS = [
    "point_id", "point_external_id", "site_id", "site_name",
    "operator_id", "operator_name", "lat", "lon", "postcode", "city",
    "address", "hours_type", "n_connectors", "connector_types",
    "charging_modes", "max_power_raw", "available_power_raw",
]


def parse_infra(src):
    """Uma linha por ponto de carregamento (refillPoint)."""
    rows, excerpt = [], None
    for _, el in etree.iterparse(src, events=("end",), huge_tree=True):
        if _ln(el) != "energyInfrastructureSite":
            continue
        if excerpt is None:
            excerpt = etree.tostring(el, encoding="unicode")[:2500]
        op = _child(el, "operator")
        addr_line = _desc(el, "addressLine")
        oh = _child(el, "operatingHours")
        site = {
            "site_id": el.get("id"),
            "site_name": _value(_child(el, "name")),
            "operator_id": op.get("id") if op is not None else None,
            "operator_name": _value(_child(op, "name")),
            "lat": _num(_text(_desc(el, "latitude"))),
            "lon": _num(_text(_desc(el, "longitude"))),
            "postcode": _text(_desc(el, "postcode")),
            "city": _value(_desc(el, "city")),
            "address": _value(_desc(addr_line, "text")),
            "hours_type": oh.get(XSI_TYPE) if oh is not None else None,
        }
        for rp in el.iter():
            if _ln(rp) != "refillPoint":
                continue
            conns = [c for c in rp if _ln(c) == "connector"]
            types = sorted({t for c in conns if (t := _text(_child(c, "connectorType")))})
            modes = sorted({m for c in conns if (m := _text(_child(c, "chargingMode")))})
            powers = [p for c in conns if (p := _num(_text(_child(c, "maxPowerAtSocket")))) is not None]
            rows.append({
                "point_id": rp.get("id"),
                "point_external_id": _text(_child(rp, "externalIdentifier")),
                **site,
                "n_connectors": len(conns),
                "connector_types": "|".join(types) or None,
                "charging_modes": "|".join(modes) or None,
                "max_power_raw": max(powers) if powers else None,
                "available_power_raw": _num(_text(_child(rp, "availableChargingPower"))),
            })
        el.clear(keep_tail=True)
    return rows, excerpt
