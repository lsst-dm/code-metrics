"""Command line interface for the code metrics tools."""

import click


@click.group()
@click.version_option()
def main() -> None:
    """Measure the size of a code base over time."""
