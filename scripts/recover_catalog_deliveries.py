#!/usr/bin/env python3
"""Review or enqueue catalog-delivery recovery from an operator terminal."""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def api_request(base_url: str, api_key: str, path: str, business_id: str, body: dict | None = None) -> dict:
    request = Request(
        f"{base_url.rstrip('/')}{path}?{urlencode({'client_name': business_id})}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Dashboard-API-Key": api_key, **({"Content-Type": "application/json"} if body else {})},
        method="POST" if body is not None else "GET",
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"Backend HTTP {exc.code}: {exc.read().decode(errors='replace')}") from exc
    except URLError as exc:
        raise RuntimeError(f"No fue posible contactar el backend: {exc.reason}") from exc


def recovery_payload(candidates: list[dict], phones: set[str], all_candidates: bool) -> list[dict]:
    selected = candidates if all_candidates else [item for item in candidates if item["phone_number"] in phones]
    return [{"phone_number": item["phone_number"], "catalog_id": item["catalog_id"]} for item in selected]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audita y recupera catálogos que no se entregaron")
    parser.add_argument("--backend-url", default=os.getenv("RECOVERY_BACKEND_URL"))
    parser.add_argument("--api-key", default=os.getenv("DASHBOARD_API_KEY"))
    parser.add_argument("--business-id", default=os.getenv("BUSINESS_ID"))
    parser.add_argument("--phone", action="append", default=[], help="Número a recuperar; se puede repetir")
    parser.add_argument("--all", action="store_true", help="Selecciona todos los resultados auditados")
    parser.add_argument("--execute", action="store_true", help="Encola los seleccionados; sin esto solo muestra")
    parser.add_argument("--confirm", help='Debe ser exactamente "REENVIAR" al usar --execute')
    args = parser.parse_args(argv)
    if not args.backend_url or not args.api_key or not args.business_id:
        parser.error("configura RECOVERY_BACKEND_URL, DASHBOARD_API_KEY y BUSINESS_ID")

    report = api_request(args.backend_url, args.api_key, "/api/catalog-delivery-recoveries", args.business_id)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not args.execute:
        return 0
    if args.confirm != "REENVIAR":
        parser.error('--execute requiere --confirm REENVIAR')
    selected = recovery_payload(report.get("recoveries", []), set(args.phone), args.all)
    if not selected:
        parser.error("no hay contactos seleccionados; usa --phone o --all")
    results = []
    for index in range(0, len(selected), 50):
        results.append(api_request(
            args.backend_url, args.api_key, "/api/catalog-delivery-recoveries/resend", args.business_id,
            {"recoveries": selected[index:index + 50], "confirmation": "REENVIAR"},
        ))
    print(json.dumps({"batches": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
