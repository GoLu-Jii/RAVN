import importlib.util
from pathlib import Path
import unittest

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


MIGRATION_PATH = (
    Path(__file__).parents[1]
    / "alembic"
    / "versions"
    / "d4b19f6e2a73_add_youtube_url_to_targets.py"
)
SPEC = importlib.util.spec_from_file_location("youtube_target_migration", MIGRATION_PATH)
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


class YoutubeTargetMigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade_add_and_remove_column(self):
        engine = sa.create_engine("sqlite:///:memory:")
        with engine.begin() as connection:
            connection.execute(sa.text("CREATE TABLE targets (id INTEGER PRIMARY KEY)"))
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                MIGRATION.upgrade()
                columns = {column["name"] for column in sa.inspect(connection).get_columns("targets")}
                self.assertIn("youtube_url", columns)

                MIGRATION.downgrade()
                columns = {column["name"] for column in sa.inspect(connection).get_columns("targets")}
                self.assertNotIn("youtube_url", columns)
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
