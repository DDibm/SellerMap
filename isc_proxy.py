#!/usr/bin/env python3
"""
ISC EC lookup proxy — runs locally on port 3001.
The territory coverage page calls this to look up opportunities/accounts by ID.

Start with:  python3 isc_proxy.py
Stop with:   Ctrl+C
"""

import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import ibm_db

# ── connection ────────────────────────────────────────────────────────────────
# Reads DB2_CONN_STR from the environment (same variable used by the MCP server).
# Falls back to the hard-coded value so the proxy works when run standalone.
CONN_STR = os.environ.get(
    "DB2_CONN_STR",
    "DATABASE=BLUDB;"
    "HOSTNAME=cdc4b934-6d67-47f7-a12a-c97662b60515-ussouth.bt1ibm.db2.ibmappdomain.cloud;"
    "PORT=31165;"
    "PROTOCOL=TCPIP;"
    "SECURITY=SSL;"
    "UID=donovan_dutcher;"
    "PWD=yv2R_l.n.1k\\@8#;"
)
SCHEMA = "ISC"

def get_conn():
    return ibm_db.connect(CONN_STR, "", "")

def run_query(sql, params=()):
    conn = get_conn()
    stmt = ibm_db.prepare(conn, sql)
    if params:
        for i, p in enumerate(params, 1):
            ibm_db.bind_param(stmt, i, p)
    ibm_db.execute(stmt)
    cols = [ibm_db.field_name(stmt, i) for i in range(ibm_db.num_fields(stmt))]
    rows = []
    row = ibm_db.fetch_assoc(stmt)
    while row:
        rows.append({k.upper(): v for k, v in row.items()})
        row = ibm_db.fetch_assoc(stmt)
    ibm_db.close(conn)
    return rows

# ── lookup logic ──────────────────────────────────────────────────────────────
OPP_ID_RE  = re.compile(r'^006[A-Za-z0-9]{15}$')
ACCT_ID_RE = re.compile(r'^001[A-Za-z0-9]{15}$')

def search_accounts(name):
    """Search ACCOUNT_V by name (case-insensitive LIKE). Returns up to 20 matches."""
    term = name.strip()
    sql = f"""
        SELECT
          a.ID,
          a.NAME        AS ACCOUNT_NAME,
          a.BILLINGCITY,
          a.BILLINGSTATE,
          a.BILLINGSTATECODE,
          a.BILLINGCOUNTRYCODE,
          a.INDUSTRY,
          a.TYPE        AS ACCOUNT_TYPE
        FROM {SCHEMA}.ACCOUNT_V a
        WHERE UPPER(a.NAME) LIKE UPPER(?)
          AND a.ISDELETED = 0
        ORDER BY a.NAME
        FETCH FIRST 20 ROWS ONLY
    """
    rows = run_query(sql, (f'%{term}%',))
    return {
        "type": "name_search",
        "query": term,
        "matches": [
            {
                "id": r.get("ID"),
                "account_name": r.get("ACCOUNT_NAME"),
                "city": r.get("BILLINGCITY"),
                "state": r.get("BILLINGSTATE"),
                "state_code": r.get("BILLINGSTATECODE"),
                "country_code": r.get("BILLINGCOUNTRYCODE"),
                "industry": r.get("INDUSTRY"),
                "account_type": r.get("ACCOUNT_TYPE"),
            }
            for r in rows
        ]
    }

def lookup(raw_id):
    """
    Accepts an opportunity ID (starts with 006), account ID (starts with 001),
    or a plain-text company name to search.
    Returns a dict with account info + billing state.
    """
    rid = raw_id.strip()

    if OPP_ID_RE.match(rid):
        sql = f"""
            SELECT
              o.NAME        AS OPP_NAME,
              o.STAGENAME,
              o.AMOUNT,
              o.CLOSEDATE,
              o.MPP_NUMBER,
              o.OWNERID,
              a.NAME        AS ACCOUNT_NAME,
              a.BILLINGCITY,
              a.BILLINGSTATE,
              a.BILLINGSTATECODE,
              a.BILLINGCOUNTRYCODE,
              a.WEBSITE,
              a.INDUSTRY,
              u.FIRSTNAME    AS OWNER_FIRSTNAME,
              u.LASTNAME     AS OWNER_LASTNAME,
              u.EMAIL        AS OWNER_EMAIL,
              u.TITLE        AS OWNER_TITLE,
              u.DEPARTMENT   AS OWNER_DEPT
            FROM {SCHEMA}.OPPORTUNITIES_V o
            JOIN {SCHEMA}.ACCOUNT_V a ON o.ACCOUNTID = a.ID
            LEFT JOIN {SCHEMA}.USERS_V u ON o.OWNERID = u.ID
            WHERE o.ID = ?
              AND o.ISDELETED = 0
            FETCH FIRST 1 ROWS ONLY
        """
        rows = run_query(sql, (rid,))
        if not rows:
            return {"error": f"No opportunity found for ID {rid}"}
        r = rows[0]
        owner_name = " ".join(filter(None, [r.get("OWNER_FIRSTNAME"), r.get("OWNER_LASTNAME")])) or None
        return {
            "type": "opportunity",
            "opp_name": r.get("OPP_NAME"),
            "stage": r.get("STAGENAME"),
            "amount": str(r.get("AMOUNT") or ""),
            "close_date": str(r.get("CLOSEDATE") or ""),
            "mpp_number": r.get("MPP_NUMBER"),
            "account_name": r.get("ACCOUNT_NAME"),
            "city": r.get("BILLINGCITY"),
            "state": r.get("BILLINGSTATE"),
            "state_code": r.get("BILLINGSTATECODE"),
            "country_code": r.get("BILLINGCOUNTRYCODE"),
            "website": r.get("WEBSITE"),
            "industry": r.get("INDUSTRY"),
            "owner_id": r.get("OWNERID"),
            "owner_name": owner_name,
            "owner_email": r.get("OWNER_EMAIL"),
            "owner_title": r.get("OWNER_TITLE"),
            "owner_dept": r.get("OWNER_DEPT"),
        }

    elif ACCT_ID_RE.match(rid):
        sql = f"""
            SELECT
              a.NAME        AS ACCOUNT_NAME,
              a.BILLINGCITY,
              a.BILLINGSTATE,
              a.BILLINGSTATECODE,
              a.BILLINGCOUNTRYCODE,
              a.WEBSITE,
              a.INDUSTRY,
              a.TYPE        AS ACCOUNT_TYPE
            FROM {SCHEMA}.ACCOUNT_V a
            WHERE a.ID = ?
              AND a.ISDELETED = 0
            FETCH FIRST 1 ROWS ONLY
        """
        rows = run_query(sql, (rid,))
        if not rows:
            return {"error": f"No account found for ID {rid}"}
        r = rows[0]
        return {
            "type": "account",
            "account_name": r.get("ACCOUNT_NAME"),
            "city": r.get("BILLINGCITY"),
            "state": r.get("BILLINGSTATE"),
            "state_code": r.get("BILLINGSTATECODE"),
            "country_code": r.get("BILLINGCOUNTRYCODE"),
            "website": r.get("WEBSITE"),
            "industry": r.get("INDUSTRY"),
            "account_type": r.get("ACCOUNT_TYPE"),
        }

    else:
        # Treat as a company name search
        return search_accounts(rid)


# ── HTTP handler ──────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"[isc_proxy] {fmt % args}")

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/lookup":
            self._send_json({"error": "Not found"}, 404)
            return
        qs = parse_qs(parsed.query)
        ids = qs.get("id", [])
        if not ids:
            self._send_json({"error": "Missing ?id= parameter"}, 400)
            return
        try:
            result = lookup(ids[0])
            self._send_json(result)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)


# ── main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = 3001
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"ISC EC lookup proxy running at http://127.0.0.1:{port}/lookup?id=<OPP_OR_ACCT_ID>")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nProxy stopped.")
