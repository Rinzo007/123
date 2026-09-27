from __future__ import annotations

import argparse


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="transit_planner")
    sub = parser.add_subparsers(dest="command")

    pack = sub.add_parser("pack", help="Собрать city pack из Overture")
    pack.add_argument("--city", required=True)
    pack.add_argument("--version", default="1")
    pack.add_argument("--output", required=True)
    pack.add_argument("--south", type=float)
    pack.add_argument("--west", type=float)
    pack.add_argument("--north", type=float)
    pack.add_argument("--east", type=float)
    pack.add_argument("--release", default=None)
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command != "pack":
        print("Transit Planner engine initialized.")
        return

    from .city_pack import build_and_write_overture_city_pack
    from .overture import OvertureSource

    bbox = None
    if None not in (args.south, args.west, args.north, args.east):
        bbox = (args.south, args.west, args.north, args.east)
    source = OvertureSource(
        release=args.release or OvertureSource().release,
    )
    manifest = build_and_write_overture_city_pack(
        args.output,
        city=args.city,
        version=args.version,
        source=source,
        bbox=bbox,
    )
    print(
        f"City pack готов: {manifest.city} {manifest.version}; "
        f"{len(manifest.files)} файлов; {manifest.total} байт"
    )


if __name__ == "__main__":
    main()
