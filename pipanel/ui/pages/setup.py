"""Set up a Laravel project that was just cloned: what is missing, and one click to fix it."""
from __future__ import annotations

from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QRadioButton,
                               QVBoxLayout)

from ...core import projects
from ...core.sites import Site
from ..widgets import hbox, label


def needs_text(needs: list[str]) -> str:
    return "; ".join(projects.NEEDS[n] for n in needs)


class SetupDialog(QDialog):
    def __init__(self, parent, ctl, site: Site):
        super().__init__(parent)
        self.setWindowTitle(f"Set up {site.name}")
        needs = projects.setup_needs(site.path)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        if needs:
            lay.addWidget(label("This project is not ready to run yet: " + needs_text(needs) + ".", wrap=True))
        steps = []
        if "vendor" in needs:
            steps.append(f"composer install (PHP {site.php})")
        if "env" in needs:
            steps.append(".env from .env.example, pointed at the PIPanel database")
        if needs and needs != ["vendor"]:
            steps.append("php artisan key:generate")
        steps += ["create the database from .env when it does not exist", "php artisan storage:link when needed"]
        lay.addWidget(label("PIPanel will run:\n  • " + "\n  • ".join(steps), "muted", wrap=True))

        lay.addWidget(label("Database content", name="cardTitle"))
        self.group = QButtonGroup(self)
        self.migrate = QRadioButton("Run the migrations")
        self.import_ = QRadioButton("Import a dump from the repository")
        self.none = QRadioButton("Leave it empty")
        self.dumps = QComboBox()
        self.dumps.setMinimumWidth(320)
        found = projects.sql_dumps(site.path)
        for f in found:
            self.dumps.addItem(str(f.relative_to(site.path)), str(f))
        for rb in (self.migrate, self.import_, self.none):
            self.group.addButton(rb)
            lay.addWidget(rb)
            if rb is self.import_:
                lay.addLayout(hbox(label("    "), self.dumps, None))
        self.import_.setEnabled(bool(found))
        self.dumps.setEnabled(bool(found))
        (self.import_ if found else self.migrate).setChecked(True)
        if not found:
            self.import_.setToolTip("No .sql or .sql.gz file was found in the project")

        self.npm = QCheckBox("npm install (front-end packages)")
        has_npm = (site.path / "package.json").is_file() and not (site.path / "node_modules").is_dir()
        self.npm.setVisible(has_npm)
        self.npm.setChecked(has_npm and ctl.node.available())
        lay.addWidget(self.npm)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Set up")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.setMinimumWidth(520)

    def choice(self) -> dict:
        database = "import" if self.import_.isChecked() else "none" if self.none.isChecked() else "migrate"
        return dict(database=database, dump=self.dumps.currentData() if database == "import" else None,
                    npm=self.npm.isVisible() and self.npm.isChecked())


def run_setup(page, site: Site, done=None) -> None:
    """Ask, then set the project up in the background and show every step."""
    dlg = SetupDialog(page, page.ctl, site)
    if dlg.exec() != QDialog.Accepted:
        return
    opts = dlg.choice()

    def finished(steps):
        page.win.show_steps(f"Set up {site.name}", steps)
        if done:
            done()
    page.run(lambda: page.ctl.setup_laravel(site.name, **opts), done=finished,
             label=f"Setting up {site.name}…", lock=f"composer:{site.path}")
