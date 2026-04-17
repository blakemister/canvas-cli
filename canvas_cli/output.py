"""JSON envelope helpers following CLI-Anything patterns."""

import json
import sys

import click


def success(ctx, data, message=None):
    """Output success envelope to stdout. Only acts in --json mode."""
    if ctx.obj.get("json"):
        envelope = {"success": True, "data": data}
        if message:
            envelope["message"] = message
        click.echo(json.dumps(envelope, indent=2, default=str))


def error(error_type, message, exit_code=1):
    """Output error envelope and exit."""
    ctx = click.get_current_context(silent=True)
    if ctx and ctx.obj.get("json"):
        click.echo(json.dumps({
            "success": False,
            "error": error_type,
            "message": message,
        }, indent=2))
    else:
        click.echo(f"Error: {message}", err=True)
    sys.exit(exit_code)
