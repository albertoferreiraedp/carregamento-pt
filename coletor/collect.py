"""Recolha: descarrega o estado das tomadas, regista transições e amostras.

Guarda apenas MUDANÇAS de estado (eventos) + um registo de cada amostra
(para calcular cobertura e aplicar o teto de 10 min no cálculo).
"""
import csv
import gzip
import io
import json
import os
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone

import requests

from . import config as C
from .parse import INFRA_FIELDS, parse_infra, parse_status

SUMMARY = []


def say(line=""):
    print(line)
    SUMMARY.append(line)


def write_summary():
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(SUMMARY) + "\n")


def md(x):
    return str(x).replace("|", "\\|")


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def lisbon_date(dt):
    return dt.astimezone(C.TZ).date().isoformat()


def fetch(url):
    last = None
    for attempt in range(C.HTTP_RETRIES):
        try:
            r = requests.get(url, timeout=C.HTTP_TIMEOUT,
                             headers={"User-Agent": C.USER_AGENT, "Accept-Encoding": "gzip"})
            r.raise_for_status()
            return r.content, r.status_code
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(5 * (attempt + 1) ** 2)
    raise RuntimeError(f"{url}: {last}")


class FeedError(Exception):
    pass


def save_debug(raw, err):
    """Guarda início e fim de uma resposta inválida, para diagnóstico."""
    d = C.STATE_DIR / "debug"
    d.mkdir(parents=True, exist_ok=True)
    head = raw[:1500].decode("utf-8", "replace")
    tail = raw[-1500:].decode("utf-8", "replace")
    (d / "ultima_resposta_invalida.txt").write_text(
        f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}\n{err}\nbytes={len(raw)}\n"
        f"--- INÍCIO ---\n{head}\n--- FIM ---\n{tail}\n", encoding="utf-8")


def fetch_parse_status(attempts=2, wait_s=20):
    """Descarrega e lê o feed; repete se a resposta vier inválida (ex.: truncada)."""
    last = None
    for i in range(attempts):
        try:
            raw, http = fetch(C.STATUS_URL)
        except Exception as e:  # noqa: BLE001
            raise FeedError(f"download falhou: {e}") from e
        try:
            rows, excerpt = parse_status(io.BytesIO(raw))
            return raw, http, rows, excerpt, i
        except Exception as e:  # noqa: BLE001 — XML inválido ou truncado
            last = f"XML inválido ({len(raw)/1e6:.1f} MB): {type(e).__name__}: {e}"
            save_debug(raw, last)
            if i < attempts - 1:
                time.sleep(wait_s)
    raise FeedError(last)


def load_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def append_gz_csv(path, header, rows):
    """Acrescenta a um CSV gzip (membros gzip concatenados são válidos)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with gzip.open(path, "at", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerows(rows)


def load_last_status():
    p = C.STATE_DIR / "last_status.csv.gz"
    if not p.exists():
        return {}
    with gzip.open(p, "rt", encoding="utf-8", newline="") as f:
        return {r["point_id"]: r["status"] for r in csv.DictReader(f)}


def save_last_status(d):
    p = C.STATE_DIR / "last_status.csv.gz"
    with gzip.open(p, "wt", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["point_id", "status"])
        w.writerows(sorted(d.items()))


def record_sample(now, **kw):
    row = {"ts_utc": iso(now), "ok": 0, "http": "", "n_points": 0, "n_events": 0,
           "bytes": 0, "secs": "", "err": ""}
    row.update(kw)
    append_gz_csv(C.STATE_DIR / "samples" / f"{lisbon_date(now)}.csv.gz",
                  list(row.keys()), [list(row.values())])


# ---------------------------------------------------------------- estático
def refresh_static(now):
    meta_p = C.STATE_DIR / "static_meta.json"
    meta = load_json(meta_p, {})
    if meta.get("ts_utc"):
        age_h = (now - datetime.fromisoformat(meta["ts_utc"].replace("Z", "+00:00"))).total_seconds() / 3600
        if age_h < C.STATIC_REFRESH_H:
            return None
    try:
        raw, _ = fetch(C.INFRA_URL)
    except Exception as e:  # noqa: BLE001
        say(f"> ⚠️ Inventário estático não atualizado: {e}")
        return None
    rows, excerpt = parse_infra(io.BytesIO(raw))
    if not rows:
        say("> ⚠️ Inventário estático sem pontos lidos. Excerto do XML:")
        say("```xml\n" + (raw[:2500].decode("utf-8", "replace")) + "\n```")
        return None

    # Eventos de presença no inventário (entrada/saída de tomadas).
    old_p = C.STATE_DIR / "static_points.csv.gz"
    old_ids = set()
    if old_p.exists():
        with gzip.open(old_p, "rt", encoding="utf-8", newline="") as f:
            old_ids = {r["point_id"] for r in csv.DictReader(f)}
    new_ids = {r["point_id"] for r in rows}
    ev = [(iso(now), pid, 1) for pid in sorted(new_ids - old_ids)]
    ev += [(iso(now), pid, 0) for pid in sorted(old_ids - new_ids)]
    if ev:
        append_gz_csv(C.STATE_DIR / "static_events" / f"{lisbon_date(now)}.csv.gz",
                      ["ts_utc", "point_id", "present"], ev)

    with gzip.open(old_p, "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=INFRA_FIELDS)
        w.writeheader()
        w.writerows(rows)
    save_json(meta_p, {"ts_utc": iso(now), "n_points": len(rows),
                       "n_sites": len({r["site_id"] for r in rows})})
    return rows, excerpt


def static_diagnostics(rows):
    n = len(rows)
    pw = sorted(r["max_power_raw"] for r in rows if r["max_power_raw"] is not None)
    coords = sum(r["lat"] is not None and r["lon"] is not None for r in rows)
    say("\n### Diagnóstico do inventário")
    say(f"- Com coordenadas: {coords/n:.1%} · com potência: {len(pw)/n:.1%}")
    if pw:
        q = lambda f: pw[min(len(pw) - 1, int(f * len(pw)))]
        say(f"- Potência bruta — mín {pw[0]:g} · p25 {q(.25):g} · mediana {q(.5):g} · "
            f"p75 {q(.75):g} · máx {pw[-1]:g}")
    for field, label in (("connector_types", "Tipos de conector"),
                         ("charging_modes", "Modos de carregamento"),
                         ("hours_type", "Tipo de horário")):
        top = Counter(r[field] or "(vazio)" for r in rows).most_common(8)
        say(f"- {label}: " + " · ".join(f"`{md(k)}` {v}" for k, v in top))


def operators_report(rows):
    pts = Counter()
    sites = {}
    names = {}
    for r in rows:
        key = r["operator_id"] or "?"
        pts[key] += 1
        sites.setdefault(key, set()).add(r["site_id"])
        names[key] = r["operator_name"] or "?"
    total = sum(pts.values())
    out = C.STATE_DIR / "operators_ranking.csv"
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "operator_id", "operator_name", "locais", "pontos", "pct_pontos"])
        for i, (k, n) in enumerate(pts.most_common(), 1):
            w.writerow([i, k, names[k], len(sites[k]), n, round(100 * n / total, 2)])
    say("\n### Ranking de operadores (top 25, por nº de pontos)")
    say("| # | Operador | Código | Locais | Pontos | % |")
    say("|---|---|---|---|---|---|")
    for i, (k, n) in enumerate(pts.most_common(25), 1):
        say(f"| {i} | {md(names[k])} | {md(k)} | {len(sites[k])} | {n} | {100*n/total:.1f}% |")


# ---------------------------------------------------------------- principal
def main():
    t0 = time.time()
    now = datetime.now(timezone.utc)
    C.STATE_DIR.mkdir(parents=True, exist_ok=True)
    force = "--force" in sys.argv

    last = load_json(C.STATE_DIR / "last_run.json", {})
    if last.get("ts_utc") and not force:
        prev = datetime.fromisoformat(last["ts_utc"].replace("Z", "+00:00"))
        if (now - prev).total_seconds() < C.MIN_INTERVAL_S:
            print(f"Recolha anterior há {(now - prev).total_seconds():.0f}s — ignorada.")
            return 0
    save_json(C.STATE_DIR / "last_run.json", {"ts_utc": iso(now)})

    say(f"## Recolha {now.astimezone(C.TZ):%Y-%m-%d %H:%M} (Lisboa)")

    # Ficheiros de versões anteriores que já não são usados.
    for old in ("latest_status.xml.gz", "latest_infra.xml.gz"):
        (C.STATE_DIR / old).unlink(missing_ok=True)

    try:
        return process(now, t0)
    except Exception as e:  # noqa: BLE001 — erro inesperado: regista e falha o run
        record_sample(now, err=f"erro interno: {type(e).__name__}: {e}"[:300],
                      secs=round(time.time() - t0, 1))
        say(f"> ❌ Erro interno: `{type(e).__name__}: {e}`")
        say("```\n" + traceback.format_exc()[-3000:] + "\n```")
        raise


def process(now, t0):
    try:
        raw, http, rows, excerpt, retries = fetch_parse_status()
    except FeedError as e:
        record_sample(now, err=str(e)[:300], secs=round(time.time() - t0, 1))
        say(f"> ❌ Feed dinâmico indisponível ou inválido: {e}")
        return 0
    if retries:
        say(f"> ⚠️ Primeira resposta inválida; recuperado à tentativa {retries + 1}.")
    prev = load_last_status()
    cur = {}
    dups = {}
    for pid, st, _lu in rows:
        if pid in cur:
            dups.setdefault(pid, [cur[pid]]).append(st)
        cur[pid] = st
    dup = len(dups)

    if not cur or (prev and len(cur) < C.MIN_FEED_RATIO * sum(v != C.ABSENT for v in prev.values())):
        record_sample(now, http=http, bytes=len(raw), n_points=len(cur),
                      err="feed vazio ou parcial", secs=round(time.time() - t0, 1))
        say(f"> ❌ Feed vazio ou parcial ({len(cur)} pontos). Tratado como falha (sem dados).")
        if not cur:
            say("```xml\n" + raw[:2500].decode("utf-8", "replace") + "\n```")
        return 0

    events = [(iso(now), pid, st) for pid, st in cur.items() if prev.get(pid) != st]
    events += [(iso(now), pid, C.ABSENT) for pid, st in prev.items()
               if pid not in cur and st != C.ABSENT]
    new_last = {pid: C.ABSENT for pid in prev}
    new_last.update(cur)

    day = lisbon_date(now)
    if events:
        append_gz_csv(C.STATE_DIR / "events" / f"{day}.csv.gz",
                      ["ts_utc", "point_id", "status"], events)
    save_last_status(new_last)

    raw_day = C.STATE_DIR / "raw" / f"{day}.status.xml.gz"
    if day <= C.RAW_DAILY_UNTIL and not raw_day.exists():  # 1 cópia bruta por dia, para reprocessamento
        raw_day.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(raw_day, "wb") as f:
            f.write(raw)

    record_sample(now, ok=1, http=http, bytes=len(raw), n_points=len(cur),
                  n_events=len(events), secs=round(time.time() - t0, 1))

    say(f"- Pontos no feed: **{len(cur)}** · eventos (mudanças): **{len(events)}** · "
        f"duplicados: {dup} · {len(raw)/1e6:.1f} MB · {time.time()-t0:.1f}s")
    if dups:
        conflict = {k: v for k, v in dups.items() if len(set(v)) > 1}
        say(f"- IDs repetidos: {len(dups)} · com estados diferentes: {len(conflict)}")
        say("\n<details><summary>Amostra de IDs repetidos</summary>\n")
        for k, v in list(dups.items())[:15]:
            say(f"- `{k}`: {', '.join(v)}")
        say("</details>")
    say("\n### Estados no feed")
    say("| Estado | Pontos |")
    say("|---|---|")
    for st, n in Counter(cur.values()).most_common():
        say(f"| `{st or '(vazio)'}` | {n} |")

    static = refresh_static(now)
    if static:
        srows, sexcerpt = static
        sids = {r["point_id"] for r in srows}
        in_static = sum(pid in sids for pid in cur) / len(cur)
        in_dyn = sum(pid in cur for pid in sids) / len(sids)
        say(f"\n- Inventário: {len(srows)} pontos · dinâmico∩estático: "
            f"{in_static:.1%} dos pontos dinâmicos, {in_dyn:.1%} dos estáticos")
        static_diagnostics(srows)
        operators_report(srows)
        if not prev:  # primeira execução: mostrar estrutura para validação
            say("\n<details><summary>Excerto XML — estado</summary>\n\n```xml\n"
                f"{excerpt}\n```\n</details>")
            say("\n<details><summary>Excerto XML — inventário</summary>\n\n```xml\n"
                f"{sexcerpt}\n```\n</details>")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        write_summary()
