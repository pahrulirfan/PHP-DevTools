import pytest

from pipanel.core.config import Config, SiteOverride
from pipanel.core.sites import (LARAVEL, PLAIN, PUBLIC, WORDPRESS, detect, relative_docroot, safe_subdir, scan,
                                  slugify)


def mk(root, rel, *files):
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    for f in files:
        (d / f).parent.mkdir(parents=True, exist_ok=True)
        (d / f).write_text("")
    return d


def test_detect_types(tmp_path):
    assert detect(mk(tmp_path, "l", "artisan", "public/index.php")) == (LARAVEL, tmp_path / "l/public")
    assert detect(mk(tmp_path, "w", "wp-config.php")) == (WORDPRESS, tmp_path / "w")
    assert detect(mk(tmp_path, "p", "public/index.php")) == (PUBLIC, tmp_path / "p/public")
    assert detect(mk(tmp_path, "x", "index.php")) == (PLAIN, tmp_path / "x")


def test_artisan_without_public_is_not_laravel(tmp_path):
    assert detect(mk(tmp_path, "a", "artisan"))[0] == PLAIN


def test_scan_parked(tmp_path):
    root = tmp_path / "PHPProject"
    mk(root, "Blog", "artisan", "public/index.php")
    mk(root, "My App_2", "index.php")
    mk(root, ".hidden")
    mk(root, "日本語")
    (root / "file.txt").write_text("")
    cfg = Config(parked=[str(root)], default_php="8.3")
    cfg.sites["blog"] = SiteOverride(php="8.2", https=True)
    sites = {s.name: s for s in scan(cfg)}
    assert set(sites) == {"blog", "my-app-2"}
    assert sites["blog"].php == "8.2" and sites["blog"].https and sites["blog"].kind == LARAVEL
    assert sites["my-app-2"].php == "8.3" and not sites["my-app-2"].https


def test_missing_parked_folder_ignored(tmp_path):
    assert scan(Config(parked=[str(tmp_path / "nope")])) == []


def test_link_overrides_parked(tmp_path):
    root = tmp_path / "P"
    mk(root, "app", "index.php")
    other = mk(tmp_path, "elsewhere", "index.php")
    s = scan(Config(parked=[str(root)], linked={"app": str(other)}))
    assert len(s) == 1 and s[0].path == other and s[0].linked


def test_invalid_link_name_skipped(tmp_path):
    d = mk(tmp_path, "d", "index.php")
    assert scan(Config(linked={"Bad Name": str(d)})) == []


def test_slugify():
    assert slugify("My App_2") == "my-app-2"
    assert slugify("日本語") is None


def test_custom_docroot_overrides_detection(tmp_path):
    root = tmp_path / "P"
    app = mk(root, "app", "index.php", "web/index.php")
    cfg = Config(parked=[str(root)])
    cfg.sites["app"] = SiteOverride(docroot="web")
    s = scan(cfg)[0]
    assert s.docroot == (app / "web").resolve() and s.custom_docroot
    assert relative_docroot(s) == "web"


@pytest.mark.parametrize("bad", ["../outside", "/etc", "missing", "web/../../x"])
def test_unsafe_or_missing_docroot_falls_back_to_detection(tmp_path, bad):
    root = tmp_path / "P"
    mk(root, "app", "index.php")
    (tmp_path / "outside").mkdir()
    cfg = Config(parked=[str(root)])
    cfg.sites["app"] = SiteOverride(docroot=bad)
    s = scan(cfg)[0]
    assert not s.custom_docroot and s.docroot == root / "app"


def test_safe_subdir_rejects_symlink_escape(tmp_path):
    base = mk(tmp_path, "proj", "index.php")
    (tmp_path / "secret").mkdir()
    (base / "link").symlink_to(tmp_path / "secret")
    assert safe_subdir(base, "link") is None
    assert safe_subdir(base, ".") == base.resolve()
