import json

from pipanel.core import config
from pipanel.core.config import Config, SiteOverride


def test_missing_file_gives_defaults(paths):
    cfg = config.load(paths.config_file)
    assert cfg.theme == "system" and cfg.tld == "test"


def test_roundtrip(paths):
    cfg = Config(theme="dark", parked=["/home/u/PHPProject"], linked={"app": "/x"})
    cfg.sites["blog"] = SiteOverride(php="8.2", https=True)
    config.save(cfg, paths.config_file)
    loaded = config.load(paths.config_file)
    assert loaded == cfg
    assert loaded.php_for("blog") == "8.2"
    assert loaded.php_for("other") == loaded.default_php


def test_corrupt_json_falls_back(paths):
    paths.config_file.write_text("{not json")
    assert config.load(paths.config_file) == Config()


def test_invalid_values_ignored(paths):
    paths.config_file.write_text(json.dumps({"theme": "neon", "parked": [1, "/ok"], "dns_port": "x"}))
    cfg = config.load(paths.config_file)
    assert cfg.theme == "system" and cfg.parked == ["/ok"] and cfg.dns_port == 5354


def test_save_is_atomic_no_tmp_left(paths):
    config.save(Config(), paths.config_file)
    assert not list(paths.config.glob("*.tmp"))


def test_db_engine_validated(paths):
    paths.config_file.write_text(json.dumps({"db_engine": "mariadb"}))
    assert config.load(paths.config_file).db_engine == "mariadb"
    paths.config_file.write_text(json.dumps({"db_engine": "oracle"}))
    assert config.load(paths.config_file).db_engine == "mysql"
