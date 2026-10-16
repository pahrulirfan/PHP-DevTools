from pipanel.core import ports
from pipanel.core.runner import Result

SS = ('LISTEN 0 4096 127.0.0.1:80 0.0.0.0:*\n'
      'LISTEN 0 511 0.0.0.0:8080 0.0.0.0:* users:(("nginx",pid=7,fd=5))\n'
      'LISTEN 0 128 [::]:3306 [::]:*\n')
DOCKER = ("ddev-router\t127.0.0.1:80->80/tcp, 127.0.0.1:443->443/tcp, 127.0.0.1:8025-8026->8025-8026/tcp\n"
          "web\t0.0.0.0:9000->9000/tcp, [::]:9000->9000/tcp\nddev-ssh-agent\t\n")


def test_listeners_parse_ss(runner):
    runner.on(["ss"], Result(0, SS))
    assert ports.listeners(runner) == {80: "", 8080: "nginx", 3306: ""}


def test_docker_publishers(runner):
    runner.on(["docker"], Result(0, DOCKER))
    assert ports.docker_publishers(runner) == {80: "ddev-router", 443: "ddev-router", 8025: "ddev-router",
                                               8026: "ddev-router", 9000: "web"}


def test_docker_missing_or_not_allowed_is_ignored(runner):
    runner.on(["docker"], Result(127, "", "not found"))
    assert ports.docker_publishers(runner) == {}


def test_owner_names_ddev_and_others(runner):
    runner.on(["ss"], Result(0, SS))
    runner.on(["docker"], Result(0, DOCKER))
    assert ports.owner(runner, 80) == "the Docker container ddev-router (DDEV)"
    assert ports.owner(runner, 8080) == "nginx"          # visible process name wins when it is not a container
    assert ports.owner(runner, 3306) == "another program"
    assert ports.owner(runner, 9999) == ""               # free


def test_advice_matches_the_owner():
    assert "ddev poweroff" in ports.advice("the Docker container ddev-router (DDEV)")
    assert "Stop that container" in ports.advice("the Docker container web")
    assert "Settings" in ports.advice("apache2")
