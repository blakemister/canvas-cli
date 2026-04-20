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
    # Detect --json from ctx.obj if the group callback has run; otherwise
    # fall back to argv so we emit a proper envelope during early-failure paths.
    json_mode = bool(ctx and ctx.obj and ctx.obj.get("json")) or ("--json" in sys.argv)
    if json_mode:
        click.echo(json.dumps({
            "success": False,
            "error": error_type,
            "message": message,
        }, indent=2))
    else:
        click.echo(f"Error: {message}", err=True)
    sys.exit(exit_code)
