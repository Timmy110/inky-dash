import json

import pytest

from inkydash import jsonc
from inkydash.config import ConfigError, interpolate, json_schema, load_config, parse_config

from .conftest import ROOT, config_dict


def test_example_config_validates():
    config, _ = load_config(ROOT / "config.example.json")
    assert set(config.screens) == {"weather", "to-work", "photos", "status", "night"}


def test_published_schema_is_current():
    schema = json.loads((ROOT / "schema" / "config.schema.json").read_text(encoding="utf-8"))
    assert schema == json_schema(), "run `inky-dash validate` to regenerate schema/config.schema.json"


def errors_for(data) -> list[str]:
    with pytest.raises(ConfigError) as e:
        parse_config(data)
    return e.value.errors


@pytest.mark.parametrize("mutate,expected", [
    (lambda d: d["schedule"][0].update(screen="nope"), "unknown screen 'nope'"),
    (lambda d: d["schedule"][0]["when"].update(at="7:00"), "expected HH:MM"),
    (lambda d: d["schedule"][0]["when"].update(refresh="10 minutes"), "expected a duration"),
    (lambda d: d["schedule"][0].update(screens=["weather"]), "exactly one of 'screen' or 'screens'"),
    (lambda d: d["schedule"][2].update(screens=["weather", "night"]), "quiet screen can't be in a rotation"),
    (lambda d: d["schedule"][1].update(name="weather-daytime"), "duplicate rule name"),
    (lambda d: d.update(default_screen="missing"), "default_screen"),
    (lambda d: d["screens"]["weather"].update(location="mars"), "unknown location 'mars'"),
    (lambda d: d["screens"]["weather"].update(blocks={"tides": True}), "unknown blocks"),
    (lambda d: d["screens"]["night"].update(show="night"), "can't be another quiet screen"),
    (lambda d: d.update(off_days=["2026-13-01"]), "off_days"),
    (lambda d: d.update(timezone="Mars/Olympus"), "unknown time zone"),
    (lambda d: d["schedule"][1]["when"].update(skip_on=["holiday"], only_on=["holiday"]), "both skip_on and only_on"),
    (lambda d: d["schedule"][0]["when"].update(days=["mon"]), "only applies to every: week"),
    (lambda d: d.update(buttons={"map": {"weather": {"B": "next_image"}}}), "isn't available there"),
    (lambda d: d.update(buttons={"map": {"default": {"E": "refresh"}}}), "unknown keys"),
    (lambda d: d.update(buttons={"map": {"default": {"A": "goto:nowhere"}}}), "unknown screen"),
    (lambda d: d["providers"].update(routing={"car": ["waze"]}), "unknown provider"),
    (lambda d: d.update(colour="red"), "Extra inputs"),
])
def test_invalid_configs(mutate, expected):
    data = config_dict()
    mutate(data)
    errs = errors_for(data)
    assert any(expected in e for e in errs), errs


def test_error_paths_are_readable():
    data = config_dict()
    data["schedule"][2]["when"]["at"] = "25:00"
    errs = errors_for(data)
    assert any(e.startswith("schedule[2].when.at") for e in errs), errs


def test_button_overrides_by_type_and_screen_name():
    data = config_dict(buttons={"map": {"gallery": {"D": "hide_image"}, "photos": {"B": "goto:weather"}}})
    parse_config(data)


def test_env_interpolation():
    env = {"KEY": "abc"}
    assert interpolate({"a": "${KEY}", "b": "x-${KEY}-y", "c": "${MISSING}", "d": ["${KEY}"]}, env) == \
        {"a": "abc", "b": "x-abc-y", "c": None, "d": ["abc"]}


def test_missing_key_means_provider_skipped(monkeypatch):
    data = config_dict()
    data["providers"]["here"] = {"key": "${HERE_API_KEY}"}
    monkeypatch.delenv("HERE_API_KEY", raising=False)
    assert parse_config(data).providers.here.key is None


def test_jsonc_comments_and_trailing_commas():
    text = """{
      // a comment
      "a": "http://not-a-comment", /* block
      comment */ "b": [1, 2, 3,],
      "c": "quote \\" // still a string",
    }"""
    assert jsonc.loads(text) == {"a": "http://not-a-comment", "b": [1, 2, 3], "c": 'quote " // still a string'}


def test_load_dotenv_next_to_config(tmp_path, monkeypatch):
    monkeypatch.delenv("TOMTOM_API_KEY", raising=False)
    data = config_dict()
    data["providers"]["tomtom"] = {"key": "${TOMTOM_API_KEY}"}
    (tmp_path / "config.jsonc").write_text("// c\n" + json.dumps(data), encoding="utf-8")
    (tmp_path / ".env").write_text("# keys\nTOMTOM_API_KEY='from-dotenv'\n", encoding="utf-8")
    config, _ = load_config(tmp_path / "config.jsonc")
    assert config.providers.tomtom.key == "from-dotenv"


def test_navigation_from_alias_round_trips():
    config = parse_config(config_dict())
    nav = config.screens["to-work"]
    assert nav.from_ == "home"
    assert nav.model_dump(by_alias=True)["from"] == "home"


def test_relative_gallery_path_is_relative_to_config_file(tmp_path, monkeypatch):
    d = config_dict("photos")
    d["screens"]["more"] = {"type": "gallery", "sources": ["extra", {"kind": "folder", "path": "/abs/pics"}]}
    (tmp_path / "config.json").write_text(json.dumps(d), encoding="utf-8")
    monkeypatch.chdir(ROOT)  # the cwd must not matter
    config, _ = load_config(tmp_path / "config.json")
    base = tmp_path.resolve()
    assert config.screens["photos"].specs()[0].path == str(base / "photos")
    more = [s.path for s in config.screens["more"].specs()]
    assert more[0] == str(base / "extra")
    assert more[1].replace("\\", "/").endswith("/abs/pics")
