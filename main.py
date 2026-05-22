"""
CarlinGomme Relay — Python + curl-cffi
Bypassa Cloudflare spoofando il TLS fingerprint di Chrome (senza browser).
Legge i cookies da Supabase, fa fetch diretti con TLS Chrome.
"""

import os, re, json, time, threading
from flask import Flask, request, jsonify
from curl_cffi import requests as cffi

app = Flask(__name__)

BASE        = "https://b2b.carlinigomme.com"
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

# Cache cookies in memoria (ricaricati ogni 30 min)
_cookie_cache: list = []
_cookie_ts: float   = 0

def carica_cookies() -> list:
    global _cookie_cache, _cookie_ts
    if _cookie_cache and (time.time() - _cookie_ts) < 1800:
        return _cookie_cache
    r = cffi.get(
        f"{SUPABASE_URL}/rest/v1/impostazioni?chiave=eq.carlin_session&select=valore",
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
        impersonate="chrome124",
        timeout=10,
    )
    rows = r.json()
    cookies = rows[0]["valore"]["cookies"] if rows else []
    _cookie_cache = cookies
    _cookie_ts = time.time()
    return cookies

def salva_cookies(cookies: list):
    global _cookie_cache, _cookie_ts
    _cookie_cache = cookies
    _cookie_ts = time.time()
    cffi.patch(
        f"{SUPABASE_URL}/rest/v1/impostazioni?chiave=eq.carlin_session",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        json={"valore": {"cookies": cookies, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")}},
        impersonate="chrome124",
        timeout=10,
    )

def cookie_dict(cookies: list) -> dict:
    return {c["name"]: c["value"] for c in cookies}

# ── Parser ────────────────────────────────────────────────────────────────────
def txt(html_cell: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html_cell)).strip()

def parse_prezzo(s: str) -> float:
    m = re.search(r"([\d]+[,\.]?\d{2})", s.replace("\xa0", ""))
    return float(m.group(1).replace(",", ".")) if m else 0.0

def parse_stagione(s: str) -> str | None:
    t = s.lower()
    if any(k in t for k in ["inv", "win", "blizzak", "snowprox", "wx"]):  return "invernale"
    if any(k in t for k in ["est", "sum", "primacy"]):                      return "estivo"
    if any(k in t for k in ["4s", "all season", "allseason", "4stagioni"]): return "4stagioni"
    return None

def parse_qty(s: str) -> int:
    return int(re.sub(r"[^\d]", "", s) or "0")

def formatta_misura(c: str) -> str:
    m = re.match(r"^(\d{3})(\d{2})(\d{2})$", c)
    return f"{m.group(1)}/{m.group(2)}R{m.group(3)}" if m else c

_SEASON_CLASS = {"e": "estivo", "i": "invernale", "4s": "4stagioni", "a": "4stagioni", "as": "4stagioni"}

def parse_html(html: str, misura_fmt: str, misura_compact: str) -> list:
    risultati = []
    for tr in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", html, re.IGNORECASE):
        if "€" not in tr:
            continue
        celle = [txt(td) for td in re.findall(r"<td[^>]*>([\s\S]*?)</td>", tr, re.IGNORECASE)]
        if len(celle) < 10:
            continue
        prezzo_str = celle[9] if len(celle) > 9 else ""
        if "€" not in prezzo_str:
            prezzo_str = next((c for c in celle if "€" in c), "")
        prezzo = parse_prezzo(prezzo_str)
        if prezzo <= 0:
            continue

        # Nome completo (senza misura)
        nome = celle[2].replace(misura_fmt, "").replace(misura_compact, "").strip() if len(celle) > 2 else ""

        # Marca: ultima parola se alfabetica, tutto maiuscolo, e non è una parola generica
        _NON_BRAND = {"season", "winter", "summer", "spring", "snow", "rain", "ice",
                      "plus", "sport", "pro", "max", "all", "extra", "size", "dot"}
        parole = nome.split()
        ultima = parole[-1] if parole else ""
        if (len(parole) >= 2 and ultima.isalpha() and ultima.isupper()
                and ultima.lower() not in _NON_BRAND and len(ultima) >= 3):
            marca   = ultima
            modello = " ".join(parole[:-1])
        else:
            marca   = ""
            modello = nome

        # Stagione: CSS class product-season-{e|i|4s} è più affidabile del testo
        sc_m = re.search(r'product-season-(\w+)', tr, re.I)
        if sc_m:
            stagione = _SEASON_CLASS.get(sc_m.group(1).lower()) or parse_stagione(nome)
        else:
            stagione = parse_stagione(nome)

        risultati.append({
            "marca":         marca,
            "modello":       modello[:120],
            "misura":        misura_fmt,
            "prezzo":        prezzo,
            "disponibilita": 1,          # available=true → tutti disponibili; qty non è nell'HTML
            "fornitore":     "CarlinGomme",
            "stagione":      stagione,
        })
    risultati.sort(key=lambda x: x["prezzo"])
    return risultati

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"ok": True, "engine": "curl-cffi", "uptime": int(time.monotonic())})

@app.route("/reload-cookies", methods=["POST"])
def reload_cookies():
    global _cookie_cache, _cookie_ts
    _cookie_cache = []
    _cookie_ts = 0
    return jsonify({"ok": True})

@app.route("/debug")
def debug():
    q = re.sub(r"[^\d]", "", request.args.get("q", "2055516"))
    cookies = carica_cookies()
    url = f"{BASE}/search?q={q}&available=true"
    try:
        r = cffi.get(url, cookies=cookie_dict(cookies),
            headers={"Referer": f"{BASE}/home", "Accept": "text/html,*/*"},
            impersonate="chrome124", timeout=30, allow_redirects=False)
        body = r.text
        has_euro = "€" in body
        has_table = "<table" in body.lower()
        tr_with_euro = len([t for t in re.findall(r"<tr[^>]*>[\s\S]*?</tr>", body, re.I) if "€" in t])
        all_trs = re.findall(r"<tr[^>]*>[\s\S]*?</tr>", body, re.I)
        euro_trs = [t for t in all_trs if "€" in t]
        # Celle di testo delle prime 5 righe
        rows_cells = []
        for tr in euro_trs[:5]:
            tds = re.findall(r"<td[^>]*>([\s\S]*?)</td>", tr, re.I)
            rows_cells.append([txt(td) for td in tds])
        # HTML raw prima riga (troncato) per vedere tag nascosti
        raw_first = euro_trs[0][:3000] if euro_trs else ""
        return jsonify({
            "status": r.status_code,
            "has_table": has_table,
            "has_euro": has_euro,
            "tr_with_euro": tr_with_euro,
            "body_len": len(body),
            "cookies_loaded": len(cookies),
            "rows_cells": rows_cells,
            "raw_first_row": raw_first,
        })
    except Exception as e:
        return jsonify({"errore": str(e)}), 500

@app.route("/search")
def search():
    q = re.sub(r"[^\d]", "", request.args.get("q", ""))
    if len(q) < 6:
        return jsonify({"errore": "Misura non valida"}), 400

    cookies = carica_cookies()
    if not cookies:
        return jsonify({"ok": False, "errore": "Nessun cookie in Supabase"}), 503

    misura_fmt = formatta_misura(q)
    url = f"{BASE}/search?q={q}&available=true"

    try:
        r = cffi.get(
            url,
            cookies=cookie_dict(cookies),
            headers={
                "Referer": f"{BASE}/home",
                "Accept": "text/html,*/*",
                "Accept-Language": "it-IT,it;q=0.9",
            },
            impersonate="chrome124",
            timeout=30,
            allow_redirects=False,
        )
    except Exception as e:
        return jsonify({"ok": False, "errore": str(e), "risultati": []}), 500

    if r.status_code in (403, 503):
        return jsonify({"ok": False, "errore": f"CF_BLOCKED:{r.status_code}", "risultati": []}), 502

    loc = r.headers.get("location", "")
    if r.status_code >= 300 and "login" in loc:
        return jsonify({"ok": False, "errore": "Sessione scaduta", "risultati": []}), 503

    html = r.text
    if "just a moment" in html.lower():
        return jsonify({"ok": False, "errore": "CF_CHALLENGE", "risultati": []}), 502

    risultati = parse_html(html, misura_fmt, q)
    return jsonify({"ok": True, "risultati": risultati, "totale": len(risultati)})

# ── Keepalive thread ──────────────────────────────────────────────────────────
def keepalive_loop():
    time.sleep(60)  # attendi avvio completo
    while True:
        time.sleep(4 * 3600)
        try:
            cookies = carica_cookies()
            if not cookies:
                continue
            r = cffi.get(
                f"{BASE}/home",
                cookies=cookie_dict(cookies),
                impersonate="chrome124",
                timeout=15,
                allow_redirects=False,
            )
            if r.status_code < 400:
                # Aggiorna updated_at
                import copy
                salva_cookies(cookies)
                print(f"[relay-py] Keepalive OK {time.strftime('%H:%M:%S')}")
            else:
                print(f"[relay-py] Keepalive fallito: {r.status_code}")
        except Exception as e:
            print(f"[relay-py] Keepalive errore: {e}")

threading.Thread(target=keepalive_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3781))
    app.run(host="0.0.0.0", port=port)
