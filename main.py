"""
CarlinGomme Relay — Python + curl-cffi
Bypassa Cloudflare spoofando il TLS fingerprint di Chrome (senza browser).
Legge i cookies da Supabase, fa fetch diretti con TLS Chrome.
"""

import os, re, json, time, threading
import urllib.request, urllib.error
from flask import Flask, request, jsonify
from curl_cffi import requests as cffi

app = Flask(__name__)

BASE        = "https://b2b.carlinigomme.com"
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_KEY"].strip()

# ── Supabase via urllib standard (NO curl_cffi — non serve TLS fingerprinting) ──

def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }

def sb_get(path: str) -> list:
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    req = urllib.request.Request(url, headers=_sb_headers())
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read())

def sb_patch(path: str, data: dict):
    url = f"{SUPABASE_URL}/rest/v1/{path}"
    body = json.dumps(data).encode()
    headers = {**_sb_headers(), "Prefer": "return=minimal"}
    req = urllib.request.Request(url, data=body, headers=headers, method="PATCH")
    urllib.request.urlopen(req, timeout=12)

# Cache cookies in memoria (ricaricati ogni 30 min)
_cookie_cache: list = []
_cookie_ts: float   = 0

def carica_cookies() -> list:
    global _cookie_cache, _cookie_ts
    if _cookie_cache and (time.time() - _cookie_ts) < 1800:
        return _cookie_cache
    rows = sb_get("impostazioni?chiave=eq.carlin_session&select=valore")
    cookies = rows[0]["valore"]["cookies"] if rows else []
    _cookie_cache = cookies
    _cookie_ts = time.time()
    return cookies

def salva_cookies(cookies: list):
    global _cookie_cache, _cookie_ts
    _cookie_cache = cookies
    _cookie_ts = time.time()
    sb_patch(
        "impostazioni?chiave=eq.carlin_session",
        {"valore": {"cookies": cookies, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")}},
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
    if any(k in t for k in ["inv", "win", "blizzak", "snowprox", "wx"]):            return "invernale"
    if any(k in t for k in ["est", "sum", "primacy"]):                               return "estivo"
    if any(k in t for k in ["4s", "all season", "allseason", "allseason", "xseason",
                             "all-season", "4stagion"]):                             return "allseason"
    return None

def parse_qty(s: str) -> int:
    return int(re.sub(r"[^\d]", "", s) or "0")

def formatta_misura(c: str) -> str:
    m = re.match(r"^(\d{3})(\d{2})(\d{2})$", c)          # 2055516 → 205/55R16
    if m: return f"{m.group(1)}/{m.group(2)}R{m.group(3)}"
    m = re.match(r"^(\d{3})(\d{2})(\d{2})(\d)$", c)      # 38565225 → 385/65R22.5
    if m: return f"{m.group(1)}/{m.group(2)}R{m.group(3)}.{m.group(4)}"
    return c

_SEASON_CLASS = {"e": "estivo", "i": "invernale", "4s": "allseason", "a": "allseason", "as": "allseason"}

def parse_html(html: str, misura_fmt: str, misura_compact: str) -> list:
    # Struttura reale CarlinGomme B2B (19 colonne per riga prodotto):
    #   col 0-1: immagine/foto  col 2: nome  col 3: IC/CV
    #   col 9: listino uff.     col 10: Netto (prezzo d'acquisto)  col 12: PFU
    #   col 13: CT  col 14: NA 24h  col 15: AP 48h  col 16: AP2 48h  col 17: 3/4gg
    #
    # PROBLEMA: il <td> della colonna "Netto" (col 10) ha un attributo title="" con
    # HTML grezzo non escaped (contiene <tr><td>Prezzo Lordo... </td></tr>).
    # Questo spezza qualsiasi regex basata su </tr>.
    # SOLUZIONE: si ancora su data-id per segmentare il blocco per prodotto,
    # poi si usano <meta itemprop="price"> per i prezzi (immuni al title attr).

    # Trova posizione di ogni riga prodotto tramite data-id
    tr_positions = [(m.start(), m.group(1)) for m in re.finditer(
        r'<tr\b[^>]+data-id="(\d+)"', html, re.IGNORECASE
    )]
    if not tr_positions:
        return []

    deposit_labels = ["CT", "NA 24h", "AP 48h", "AP2 48h", "3/4 gg"]
    risultati = []

    for idx, (start_pos, _) in enumerate(tr_positions):
        end_pos = tr_positions[idx + 1][0] if idx + 1 < len(tr_positions) else len(html)
        block = html[start_pos:end_pos]

        # Prezzo: 1° meta = listino uff., 2° meta = Netto (acquisto), 3° = PFU
        metas = re.findall(r'<meta\s+itemprop="price"\s+content="([\d,\.]+)"', block, re.IGNORECASE)
        if len(metas) < 2:
            continue
        prezzo_netto = float(metas[1].replace(',', '.'))
        if prezzo_netto <= 0:
            continue

        # Prezzo IVA Inclusa dal tooltip (Netto + PFU + Logistica + IVA 22%)
        # HTML: <b>Prezzo IVA Incl.</b></td><td...><b>€ 39,32</b>
        iva_m = re.search(r"Prezzo IVA Incl\.</b></td>\s*<td[^>]*><b>(€\s*[\d,\.]+)", block, re.IGNORECASE)
        prezzo_ivato = parse_prezzo(iva_m.group(1)) if iva_m else 0.0

        # Prezzo Lordo (IVA escl.) dal tooltip — usato come prezzo_listino di riferimento
        # HTML: <td ...>Prezzo Lordo</td><td ...>€ 30,00</td>
        lordo_m = re.search(r"Prezzo Lordo</td>\s*<td[^>]*>(€\s*[\d,\.]+)", block, re.IGNORECASE)
        prezzo_lordo = parse_prezzo(lordo_m.group(1)) if lordo_m else 0.0

        # Stagione via CSS class
        sc_m = re.search(r'class="[^"]*product-season-(\w+)[^"]*"', block, re.IGNORECASE)

        # Nome: rimuovi title="..." (contengono HTML grezzo) poi prendi 3° TD
        block_clean = re.sub(r'\s+title="[^"]*"', '', block)
        tds = re.findall(r"<td[^>]*>([\s\S]*?)</td>", block_clean, re.IGNORECASE)
        nome = ""
        if len(tds) > 2:
            nome = txt(tds[2]).replace(misura_fmt, "").replace(misura_compact, "").strip().lstrip("*").strip()

        stagione = _SEASON_CLASS.get(sc_m.group(1).lower()) if sc_m else parse_stagione(nome)

        # Depositi: celle con class "text-right-inventory availability" → CT, NA, AP, AP2, 3/4gg
        # NB: quando qty >= 10/20 il sito può inserire un <span> o badge extra —
        #     catturiamo l'intero contenuto del TD e usiamo parse_qty per estrarre il numero.
        inv_raw = re.findall(
            r'<td\b[^>]*\btext-right-inventory\b[^>]*>([\s\S]*?)</td>',
            block, re.IGNORECASE
        )
        inv_vals = [str(parse_qty(v)) for v in inv_raw]
        depositi: dict = {}
        for j, lbl in enumerate(deposit_labels):
            if j < len(inv_vals):
                v = int(inv_vals[j])
                if v > 0:
                    depositi[lbl] = v

        # Disponibilità principale = CT (se > 0), altrimenti totale, altrimenti "Disponibile"
        ct_qty   = int(inv_vals[0]) if inv_vals else 0
        tot_inv  = sum(int(v) for v in inv_vals)
        disp     = ct_qty if ct_qty > 0 else (tot_inv if tot_inv > 0 else "Disponibile")

        # Prezzo esposto: IVA inclusa (il "prezzo finale" che vede l'operatore)
        # prezzo_listino = Prezzo Lordo IVA escl. (riferimento per confronto sconti)
        prezzo_esposto = prezzo_ivato if prezzo_ivato > 0 else prezzo_netto

        result: dict = {
            "marca":         "",
            "modello":       nome[:120],
            "misura":        misura_fmt,
            "prezzo":        prezzo_esposto,
            "disponibilita": disp,
            "fornitore":     "CarlinGomme",
            "stagione":      stagione,
        }
        if prezzo_lordo > 0:
            result["prezzo_listino"] = prezzo_lordo
        if depositi:
            result["depositi"] = depositi

        risultati.append(result)

    risultati.sort(key=lambda x: x["prezzo"])
    return risultati

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def root():
    return jsonify({"ok": True, "service": "carlinigomme-relay", "engine": "curl-cffi"})

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
        prod_trs = [t for t in euro_trs if len(re.findall(r"<td[^>]*>", t, re.I)) >= 10]

        # Brand logo: src o data-src con pattern "marche" o "brand" o nome marcaDa cell[0]
        brand_imgs = []
        for tr in prod_trs[:5]:
            imgs = re.findall(r'(?:data-src|src)="([^"]+)"', tr, re.I)
            brand_imgs.append(imgs[:8])

        # Celle complete di testo prime 3 righe prodotto
        rows_cells = []
        for tr in prod_trs[:3]:
            tds = re.findall(r"<td[^>]*>([\s\S]*?)</td>", tr, re.I)
            rows_cells.append([txt(td) for td in tds])

        # HTML raw prima riga prodotto completo (senza troncatura)
        raw_first = prod_trs[0] if prod_trs else ""

        # Cerca pattern depositi in tutte le righe
        deposito_sample = re.findall(r'class="[^"]*(?:deposit|stock|qty|magaz|ct\b|dispon)[^"]*"[^>]*>([^<]{0,30})', body, re.I)[:10]

        return jsonify({
            "status": r.status_code,
            "has_table": has_table,
            "has_euro": has_euro,
            "tr_with_euro": tr_with_euro,
            "prod_rows": len(prod_trs),
            "body_len": len(body),
            "cookies_loaded": len(cookies),
            "rows_cells": rows_cells,
            "brand_imgs_per_row": brand_imgs,
            "deposito_sample": deposito_sample,
            "raw_first_row": raw_first[:6000],
        })
    except Exception as e:
        return jsonify({"errore": str(e)}), 500

@app.route("/search")
def search():
    q = re.sub(r"[^\d]", "", request.args.get("q", ""))
    if len(q) < 6:
        return jsonify({"errore": "Misura non valida"}), 400

    try:
        cookies = carica_cookies()
    except Exception as e:
        return jsonify({"ok": False, "errore": f"Supabase error: {e}", "risultati": []}), 500

    if not cookies:
        return jsonify({"ok": False, "errore": "Nessun cookie in Supabase — rinnova la sessione", "risultati": []}), 503

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
