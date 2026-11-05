"""AR5.6 — migrations « sûres pour une mise à jour progressive » (scripts/lint_migrations.py)."""

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lint_migrations.py"
spec = importlib.util.spec_from_file_location("lint_migrations", SCRIPT)
lm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lm)


def migration(tmp_path, body, waiver=""):
    p = tmp_path / "app" / "migrations" / "0002_x.py"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"{waiver}\nfrom django.db import migrations, models\n\nclass Migration(migrations.Migration):\n    operations = [\n{body}\n    ]\n"
    )
    return p


def rules(path):
    return sorted(r for r, _ in lm.check(path))


def test_the_repository_has_no_unreviewed_unsafe_migration():
    """Toute migration absente de la base gelée est contrôlée : en ajouter une dangereuse sans dérogation fait échouer la suite (donc la CI)."""
    assert lm.main([]) == 0


def test_the_baseline_covers_every_existing_migration_and_names_no_phantom():
    reviewed = set(json.loads(lm.BASELINE.read_text(encoding="utf-8"))["reviewed"])
    assert reviewed == {lm.key(p) for p in lm.migration_files()}


@pytest.mark.parametrize(
    "body, expected",
    [
        ("migrations.RemoveField(model_name='a', name='b')", ["M1"]),
        ("migrations.RenameField(model_name='a', old_name='b', new_name='c')", ["M1"]),
        ("migrations.DeleteModel(name='a')", ["M1"]),
        ("migrations.AddField(model_name='a', name='b', field=models.CharField(max_length=5))", ["M2"]),
        (
            "migrations.AddField(model_name='a', name='b', field=models.ForeignKey(to='x.y', on_delete=models.CASCADE))",
            ["M2"],
        ),
        ("migrations.AddField(model_name='a', name='b', field=models.CharField(max_length=5, default=''))", []),
        ("migrations.AddField(model_name='a', name='b', field=models.CharField(max_length=5, null=True))", []),
        ("migrations.AddField(model_name='a', name='b', field=models.ManyToManyField(to='x.y'))", []),
        ("migrations.AddIndex(model_name='a', index=models.Index(fields=['b'], name='i'))", ["M3"]),
        (
            "migrations.AddConstraint(model_name='a', constraint=models.UniqueConstraint(fields=['b'], name='u'))",
            ["M3"],
        ),
        ("migrations.RunSQL('ALTER TABLE a DROP COLUMN b')", ["M4"]),
        ("migrations.RunSQL('ALTER TABLE a RENAME TO c')", ["M4"]),
        ("migrations.RunSQL('CREATE INDEX CONCURRENTLY i ON a (b)')", []),
        ("migrations.AlterField(model_name='a', name='b', field=models.CharField(max_length=9))", []),
    ],
)
def test_each_rule_fires_on_what_breaks_the_previous_code(tmp_path, body, expected):
    assert rules(migration(tmp_path, body)) == expected


def test_an_index_on_a_table_created_by_the_same_migration_is_harmless(tmp_path):
    body = "migrations.CreateModel(name='Neuf', fields=[]),\n        migrations.AddIndex(model_name='neuf', index=models.Index(fields=['b'], name='i'))"
    assert rules(migration(tmp_path, body)) == []


def test_a_written_waiver_for_a_rule_removes_only_that_rule(tmp_path):
    body = "migrations.RemoveField(model_name='a', name='b'),\n        migrations.AddIndex(model_name='a', index=models.Index(fields=['b'], name='i'))"
    assert rules(migration(tmp_path, body)) == ["M1", "M3"]
    assert rules(migration(tmp_path, body, "# lint-migrations: allow M1 — retrait annoncé en 0.4")) == ["M3"]
    assert rules(migration(tmp_path, body, "# lint-migrations: allow M1, M3 — table vide")) == []


def test_a_new_unsafe_migration_makes_the_script_fail(tmp_path, monkeypatch, capsys):
    p = migration(tmp_path, "migrations.RemoveField(model_name='a', name='b')")
    monkeypatch.setattr(lm, "migration_files", lambda: [p])
    monkeypatch.setattr(lm, "baseline", lambda: set())
    assert lm.main([]) == 1 and "M1" in capsys.readouterr().out
    monkeypatch.setattr(lm, "baseline", lambda: {lm.key(p)})  # une fois examinée et gelée, elle n'est plus contrôlée
    assert lm.main([]) == 0
