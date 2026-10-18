import json

import pytest

from pipanel.core.laravel import (BY_KEY, CATALOG, LaravelError, LaravelTools, declared_packages, is_installed,
                                  valid_package)
from pipanel.core.php import PHP
from pipanel.core.runner import Result


@pytest.fixture
def tools(paths, runner, tmp_path):
    php = PHP(paths, etc_php=tmp_path / "etc", sbin=tmp_path / "sbin", usr_bin=tmp_path / "bin")
    (paths.bin / "composer.phar").write_text("")
    return LaravelTools(paths, runner, php)


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "app"
    p.mkdir()
    (p / "artisan").write_text("")
    (p / "composer.json").write_text(json.dumps(
        {"require": {"php": "^8.2", "laravel/framework": "^11", "livewire/livewire": "^3"},
         "require-dev": {"laravel/pint": "^1", "pestphp/pest": "^3"}}))
    return p


def test_catalog_is_consistent():
    assert len({p.key for p in CATALOG}) == len(CATALOG)
    for p in CATALOG:
        assert p.packages and all(valid_package(n) for n in p.packages), p.key
        assert p.name and p.description


def test_installed_detection_reads_composer_json(tools, project):
    assert declared_packages(project) >= {"livewire/livewire", "laravel/pint"}
    assert tools.installed(project) == {"livewire", "pint"}   # pest needs BOTH of its packages
    assert is_installed(BY_KEY["livewire"], {"livewire/livewire"})
    assert not is_installed(BY_KEY["pest"], {"pestphp/pest"})


def test_missing_or_broken_composer_json(tools, tmp_path):
    assert declared_packages(tmp_path / "nope") == set()
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "composer.json").write_text("{not json")
    assert declared_packages(tmp_path / "bad") == set()


@pytest.mark.posix
def test_install_runs_composer_then_artisan_with_the_sites_php(tools, runner, project, paths):
    steps = tools.install(project, "8.2", BY_KEY["telescope"])
    assert [n for n, _ in steps] == ["composer require laravel/telescope", "php artisan telescope:install"]
    composer, artisan = runner.calls[-2], runner.calls[-1]
    assert composer[0].endswith("php8.2") and composer[1] == str(paths.bin / "composer.phar")
    assert composer[2:] == ["require", "laravel/telescope", "--no-interaction", "--no-progress"]
    assert artisan[1:] == ["artisan", "telescope:install", "--no-interaction"]
    assert runner.last_kw["cwd"] == str(project)
    assert runner.last_kw["env"]["PHP_INI_SCAN_DIR"].endswith("/php/8.2/conf.d")


def test_dev_and_multi_package_requires(tools, runner, project):
    tools.install(project, "8.3", BY_KEY["pest"])
    assert runner.calls[0][2:6] == ["require", "--dev", "pestphp/pest", "pestphp/pest-plugin-laravel"]
    assert runner.calls[1][1:3] == ["artisan", "pest:install"]


def test_post_commands_skipped_when_composer_fails(tools, runner, project):
    runner.on([str(tools.php.cli_bin("8.3"))], Result(2, "", "Your requirements could not be resolved"))
    steps = tools.install(project, "8.3", BY_KEY["telescope"])
    assert len(steps) == 1 and not steps[0][1].ok and len(runner.calls) == 1


def test_provider_publish_is_a_single_safe_argument(tools, runner, project):
    tools.install(project, "8.3", BY_KEY["permission"])
    assert runner.calls[-1][3] == "--provider=Spatie\\Permission\\PermissionServiceProvider"


@pytest.mark.parametrize("ok", ["spatie/laravel-ray", "vendor/pkg:^1.2", "a/b:~2.0|^3.0", "x/y:dev-main"])
def test_custom_package_accepted(ok):
    assert valid_package(ok)


@pytest.mark.parametrize("bad", ["", "nopackage", "Vendor/Pkg", "a/b;rm -rf /", "a/b c", "../x/y", "a/b:^1 && ls", "--dev/x", "a/b/c"])
def test_custom_package_rejected(tools, project, bad):
    assert not valid_package(bad)
    with pytest.raises(LaravelError, match="invalid package name"):
        tools.install_custom(project, "8.3", bad)


def test_custom_install_and_remove(tools, runner, project):
    tools.install_custom(project, "8.3", " spatie/laravel-ray:^1.37 ", dev=True)
    assert runner.calls[-1][2:5] == ["require", "--dev", "spatie/laravel-ray:^1.37"]
    tools.remove(project, "8.3", BY_KEY["livewire"])
    assert runner.calls[-1][2:4] == ["remove", "livewire/livewire"]


def test_requires_a_laravel_project_and_composer(tools, tmp_path, paths):
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(LaravelError, match="not a Laravel project"):
        tools.install(plain, "8.3", BY_KEY["pint"])
    (paths.bin / "composer.phar").unlink()
    app = tmp_path / "app2"
    app.mkdir(); (app / "artisan").write_text("")
    with pytest.raises(LaravelError, match="Composer is not installed"):
        tools.install(app, "8.3", BY_KEY["pint"])


def test_migrate_runs_artisan_with_the_sites_php(tools, runner, project):
    r = tools.migrate(project, "8.4")
    assert r.ok
    cmd = runner.calls[-1]
    assert cmd[0].endswith("php8.4") and cmd[1:] == ["artisan", "migrate", "--force", "--no-interaction"]
    assert runner.last_kw["cwd"] == str(project)
