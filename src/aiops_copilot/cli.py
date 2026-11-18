"""Command line: serve, analyse a single alert, verify the audit log."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from .audit import AuditLog
from .config import load_settings
from .models import Alert


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aiops-copilot")
    p.add_argument("-c", "--config", help="path to config.yaml (or AIOPS_CONFIG)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve", help="run the Alertmanager webhook receiver")
    sv.add_argument("--host", default="0.0.0.0")
    sv.add_argument("--port", type=int, default=8080)
    an = sub.add_parser("analyze", help="analyse one alert JSON file (Alertmanager alert or payload)")
    an.add_argument("alert_file")
    sub.add_parser("audit-verify", help="check the audit log hash chain")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings(args.config)

    if args.cmd == "audit-verify":
        ok, msg = AuditLog(settings.audit_path).verify()
        print(msg)
        return 0 if ok else 1

    from .factory import build_pipeline

    pipeline = build_pipeline(settings)
    if args.cmd == "serve":
        import uvicorn

        from .server import create_app

        uvicorn.run(create_app(pipeline), host=args.host, port=args.port)
        return 0

    raw = json.load(open(args.alert_file))
    alert = Alert.model_validate(raw["alerts"][0] if "alerts" in raw else raw)
    incident = asyncio.run(pipeline.run(alert))
    json.dump(incident.model_dump(mode="json", exclude={"context": {"log_lines"}}), sys.stdout,
              indent=2, ensure_ascii=False)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
