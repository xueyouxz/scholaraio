"""CLI handler for `scholaraio serve`."""

from __future__ import annotations

import argparse

from scholaraio.core.config import Config


def cmd_serve(args: argparse.Namespace, cfg: Config) -> None:
    from scholaraio.serve import run_server

    run_server(host=args.host, port=args.port)
