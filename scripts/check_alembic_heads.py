"""Fail when the Alembic revision graph does not have exactly one head."""

from alembic.config import Config
from alembic.script import ScriptDirectory


def main() -> None:
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()

    if len(heads) != 1:
        raise SystemExit(f"Expected exactly one Alembic head, but found {len(heads)}: {', '.join(heads) or '<none>'}")

    print(f"Alembic head: {heads[0]}")


if __name__ == "__main__":
    main()
