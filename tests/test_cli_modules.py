from pathlib import Path

from wikiroutes.cli_batch import parse_batch_file
from wikiroutes.cli_export import output_base_name


def test_output_base_name_removes_known_extension() -> None:
    assert output_base_name("voronezh.xlsx", "voronezh") == "voronezh"
    assert output_base_name("foo.kml", "voronezh") == "foo"


def test_output_base_name_defaults_to_city() -> None:
    assert output_base_name(None, "Voronezh") == "voronezh_routes"


def test_parse_batch_file_skips_comments_duplicates_and_batch(tmp_path: Path) -> None:
    path = tmp_path / "batch.txt"
    path.write_text(
        "# comment\n"
        "voronezh --dedup\n"
        "wikiroutes voronezh --dedup # duplicate\n"
        "voronezh --dedup\n"
        "voronezh --batch other.txt\n",
        encoding="utf-8",
    )

    commands = parse_batch_file(str(path))

    assert commands[0][1] == ["voronezh", "--dedup"]
    assert commands[1][1] == ["voronezh", "--dedup"]
    assert commands[2][1] == ["voronezh"]
