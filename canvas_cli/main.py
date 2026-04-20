"""Canvas CLI -- 8 task-oriented commands for AI agent workflows."""

import asyncio
import json
import sys

import click
from pathlib import Path

from .client import (
    CanvasError, AuthError, NetworkError,
    get, get_paginated, download_file,
    create_client, async_get, async_get_paginated,
)
from .config import API_BASE, require_base_url, save_config, show_config
from .resolve import resolve_course
from .output import success, error
from .submit import submit as _submit_command


# Commands that don't need a configured base_url (they manage config itself
# or provide read-only diagnostic info).
_CONFIG_EXEMPT_COMMANDS = {"config"}


class ErrorHandlingGroup(click.Group):
    """Click group that catches CanvasError and outputs proper error envelopes."""

    def invoke(self, ctx):
        # Fail fast if a network-touching command runs without base_url configured.
        # Peek at argv directly to stay compatible across Click 8/9.
        subcommand = next(
            (a for a in sys.argv[1:] if not a.startswith("-")), None
        )
        if subcommand and subcommand not in _CONFIG_EXEMPT_COMMANDS:
            try:
                require_base_url()
            except RuntimeError as e:
                error("ConfigError", str(e), exit_code=2)

        try:
            return super().invoke(ctx)
        except AuthError as e:
            error("AuthError", str(e), exit_code=3)
        except NetworkError as e:
            error("NetworkError", str(e), exit_code=4)
        except CanvasError as e:
            error("APIError", str(e), exit_code=1)
        except click.UsageError:
            # Let Click handle its own UsageError (and our GateError subclass):
            # it prints to stderr and exits 2. In JSON mode, emit an envelope.
            raise


@click.group(cls=ErrorHandlingGroup)
@click.option("--json", "output_json", is_flag=True, help="Output as JSON envelope")
@click.pass_context
def cli(ctx, output_json):
    """Canvas LMS CLI -- task-oriented commands for AI agent workflows."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = output_json


# --- Command 1: auth ---

@cli.command()
@click.pass_context
def auth(ctx):
    """Verify authentication, auto-refresh if needed."""
    get("/users/self/todo", [("per_page", "1")])
    if ctx.obj["json"]:
        success(ctx, {"authenticated": True})
    else:
        click.echo("Auth OK")


# --- Command 2: briefing ---

async def _briefing_async(course_filter: str | None, deep: bool = False) -> dict:
    """Fetch briefing data in two parallel phases.

    Phase 1: grades (which lists active courses), upcoming events, todo.
    Phase 2: per-course assignments + announcements, fanned out in parallel.

    With deep=True, also includes rubric data on all assignments.
    """
    assignment_includes = [("include[]", "submission")]
    if deep:
        assignment_includes.append(("include[]", "rubric"))

    async with create_client() as client:
        # Phase 1 — fetch global user data in parallel
        grades_raw, upcoming, todo = await asyncio.gather(
            async_get_paginated(client, "/courses", [
                ("enrollment_state", "active"),
                ("include[]", "total_scores"),
                ("include[]", "current_grading_period_scores"),
            ]),
            async_get(client, "/users/self/upcoming_events", [("per_page", 100)]),
            async_get_paginated(client, "/users/self/todo"),
        )

        active = [c for c in grades_raw if c.get("enrollments")]
        id_to_code = {str(c["id"]): c.get("course_code", str(c["id"])) for c in active}

        if course_filter:
            filter_cid = resolve_course(course_filter)
            target_ids = [filter_cid] if filter_cid in id_to_code else [filter_cid]
        else:
            target_ids = list(id_to_code.keys())

        # Phase 2 — per-course fan-out
        per_course_coros = []
        per_course_keys = []
        for cid in target_ids:
            per_course_keys.append((cid, "assignments"))
            per_course_coros.append(async_get_paginated(
                client, f"/courses/{cid}/assignments", assignment_includes,
            ))
            per_course_keys.append((cid, "announcements"))
            per_course_coros.append(async_get_paginated(
                client, f"/courses/{cid}/discussion_topics", {"only_announcements": "true"},
            ))

        per_course_results = await asyncio.gather(*per_course_coros) if per_course_coros else []

    submissions = {}
    announcements = {}
    for (cid, kind), data in zip(per_course_keys, per_course_results):
        code = id_to_code.get(cid, cid)
        if kind == "assignments":
            submissions[code] = data
        elif kind == "announcements":
            announcements[code] = data

    return {
        "grades": active,
        "upcoming": upcoming,
        "todo": todo,
        "submissions": submissions,
        "announcements": announcements,
    }


@cli.command()
@click.option("--course", help="Scope to a single course code")
@click.option("--deep", is_flag=True, help="Include rubric data on all assignments")
@click.pass_context
def briefing(ctx, course, deep):
    """All personal data: grades, upcoming, todo, submissions, announcements.

    One-call replacement for what /update-me, /status, /backlog, /feedback, /quiz each need.
    With --deep, also includes rubric data on all assignments.
    """
    data = asyncio.run(_briefing_async(course, deep=deep))

    if ctx.obj["json"]:
        success(ctx, data)
    else:
        click.echo(f"Grades:        {len(data['grades'])} courses")
        click.echo(f"Upcoming:      {len(data['upcoming'])} events")
        click.echo(f"Todo:          {len(data['todo'])} items")
        click.echo(f"Submissions:   {sum(len(v) for v in data['submissions'].values())} assignments across {len(data['submissions'])} courses")
        click.echo(f"Announcements: {sum(len(v) for v in data['announcements'].values())} across {len(data['announcements'])} courses")


# --- Command 3: sync ---

async def _sync_async(cid: str, deep: bool = False) -> dict:
    """Run all sync API calls in parallel for one course.

    With deep=True, also fetches all page content and discussion threads
    in a second parallel phase, injecting results into the base data.
    """
    async with create_client() as client:
        # Phase 1: core data (always includes rubric — it's free)
        details_task = async_get(client, f"/courses/{cid}", [("include[]", "syllabus_body")])
        modules_task = async_get_paginated(client, f"/courses/{cid}/modules", [("include[]", "items")])
        assignments_task = async_get_paginated(
            client,
            f"/courses/{cid}/assignments",
            [("include[]", "all_dates"), ("include[]", "submission"), ("include[]", "rubric")],
        )
        discussions_task = async_get_paginated(client, f"/courses/{cid}/discussion_topics")
        announcements_task = async_get_paginated(
            client,
            f"/courses/{cid}/discussion_topics",
            {"only_announcements": "true"},
        )

        details, modules, assignments, discussions, announcements_list = await asyncio.gather(
            details_task, modules_task, assignments_task, discussions_task, announcements_task,
        )

        # Phase 2: deep fetch — pages and discussion threads in parallel
        if deep:
            page_tasks = []
            page_indices = []  # (module_idx, item_idx)
            for mi, module in enumerate(modules):
                for ii, item in enumerate(module.get("items", [])):
                    if item.get("type") == "Page" and item.get("url"):
                        # item["url"] is the full API URL; strip API_BASE to get the endpoint
                        endpoint = item["url"].replace(API_BASE, "")
                        page_tasks.append(async_get(client, endpoint))
                        page_indices.append((mi, ii))

            thread_tasks = []
            thread_indices = []
            for di, disc in enumerate(discussions):
                if disc.get("assignment_id"):
                    thread_tasks.append(
                        async_get(client, f"/courses/{cid}/discussion_topics/{disc['id']}/view")
                    )
                    thread_indices.append(di)

            all_deep = await asyncio.gather(
                *(page_tasks + thread_tasks), return_exceptions=True,
            )

            # Inject page content into module items
            for idx, (mi, ii) in enumerate(page_indices):
                result = all_deep[idx]
                if not isinstance(result, Exception):
                    modules[mi]["items"][ii]["page_content"] = result.get("body", "")
                    modules[mi]["items"][ii]["page_title"] = result.get("title", "")

            # Inject thread views into discussion topics
            offset = len(page_tasks)
            for idx, di in enumerate(thread_indices):
                result = all_deep[offset + idx]
                if not isinstance(result, Exception):
                    discussions[di]["thread_view"] = result

    return {
        "course": details,
        "syllabus_body": details.get("syllabus_body"),
        "modules": modules,
        "assignments": assignments,
        "discussions": discussions,
        "announcements": announcements_list,
    }


@cli.command()
@click.argument("course")
@click.option("--deep", is_flag=True, help="Also fetch page content, rubrics, and discussion threads")
@click.pass_context
def sync(ctx, course, deep):
    """Full course snapshot: details, syllabus, modules, assignments, discussions, announcements.

    Returns everything needed to build the local file tree for one course.
    With --deep, also fetches all wiki page content and graded discussion threads.
    """
    cid = resolve_course(course)
    data = asyncio.run(_sync_async(cid, deep=deep))

    if ctx.obj["json"]:
        success(ctx, data)
    else:
        click.echo(f"Course:        {data['course'].get('name', '?')}")
        click.echo(f"Syllabus:      {'yes' if data['syllabus_body'] else 'no'}")
        click.echo(f"Modules:       {len(data['modules'])}")
        click.echo(f"Assignments:   {len(data['assignments'])}")
        click.echo(f"Discussions:   {len(data['discussions'])}")
        click.echo(f"Announcements: {len(data['announcements'])}")
        if deep:
            pages = sum(
                1 for m in data["modules"]
                for i in m.get("items", [])
                if "page_content" in i
            )
            rubrics = sum(1 for a in data["assignments"] if a.get("rubric"))
            threads = sum(1 for d in data["discussions"] if "thread_view" in d)
            click.echo(f"Pages:         {pages} (deep)")
            click.echo(f"Rubrics:       {rubrics} (deep)")
            click.echo(f"Threads:       {threads} (deep)")


# --- Command 3b: sync-all ---

@cli.command("sync-all")
@click.option("--deep", is_flag=True, help="Also fetch page content, rubrics, and discussion threads")
@click.pass_context
def sync_all(ctx, deep):
    """Sync ALL known courses in parallel. Returns a dict keyed by course code.

    Equivalent to running `canvas sync COURSE --deep` for every known course,
    but in a single process with shared connection pool.
    """
    data = asyncio.run(_sync_all_async(deep=deep))

    if ctx.obj["json"]:
        success(ctx, data)
    else:
        for code, course_data in data.items():
            name = course_data.get("course", {}).get("name", code)
            n_assign = len(course_data.get("assignments", []))
            n_mod = len(course_data.get("modules", []))
            click.echo(f"{code:8s} {name} -- {n_assign} assignments, {n_mod} modules")


async def _sync_all_async(deep: bool = False) -> dict:
    """Sync all user's active courses in parallel using a shared connection pool.

    Fetches the list of active courses from Canvas (same data as the
    grades endpoint), then fans out per-course syncs.
    """
    async with create_client() as client:
        # Phase 1: discover active courses
        grades_raw = await async_get_paginated(client, "/courses", [
            ("enrollment_state", "active"),
        ])
        active = [c for c in grades_raw if c.get("enrollments")]
        course_codes = [c.get("course_code") or str(c["id"]) for c in active]
        course_ids = [str(c["id"]) for c in active]

        # Phase 2: fan out syncs
        tasks = [_sync_one(client, cid, deep) for cid in course_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        data = {}
        for code, result in zip(course_codes, results):
            if isinstance(result, Exception):
                data[code] = {"error": str(result)}
            else:
                data[code] = result

    return data


async def _sync_one(client, cid: str, deep: bool) -> dict:
    """Sync a single course using a shared client (no context manager)."""
    details, modules, assignments, discussions, announcements_list = await asyncio.gather(
        async_get(client, f"/courses/{cid}", [("include[]", "syllabus_body")]),
        async_get_paginated(client, f"/courses/{cid}/modules", [("include[]", "items")]),
        async_get_paginated(
            client, f"/courses/{cid}/assignments",
            [("include[]", "all_dates"), ("include[]", "submission"), ("include[]", "rubric")],
        ),
        async_get_paginated(client, f"/courses/{cid}/discussion_topics"),
        async_get_paginated(
            client, f"/courses/{cid}/discussion_topics",
            {"only_announcements": "true"},
        ),
    )

    if deep:
        page_tasks = []
        page_indices = []
        for mi, module in enumerate(modules):
            for ii, item in enumerate(module.get("items", [])):
                if item.get("type") == "Page" and item.get("url"):
                    endpoint = item["url"].replace(API_BASE, "")
                    page_tasks.append(async_get(client, endpoint))
                    page_indices.append((mi, ii))

        thread_tasks = []
        thread_indices = []
        for di, disc in enumerate(discussions):
            if disc.get("assignment_id"):
                thread_tasks.append(
                    async_get(client, f"/courses/{cid}/discussion_topics/{disc['id']}/view")
                )
                thread_indices.append(di)

        if page_tasks or thread_tasks:
            all_deep = await asyncio.gather(
                *(page_tasks + thread_tasks), return_exceptions=True,
            )

            for idx, (mi, ii) in enumerate(page_indices):
                result = all_deep[idx]
                if not isinstance(result, Exception):
                    modules[mi]["items"][ii]["page_content"] = result.get("body", "")
                    modules[mi]["items"][ii]["page_title"] = result.get("title", "")

            offset = len(page_tasks)
            for idx, di in enumerate(thread_indices):
                result = all_deep[offset + idx]
                if not isinstance(result, Exception):
                    discussions[di]["thread_view"] = result

    return {
        "course": details,
        "syllabus_body": details.get("syllabus_body"),
        "modules": modules,
        "assignments": assignments,
        "discussions": discussions,
        "announcements": announcements_list,
    }


# --- Command 4: page ---

@cli.command()
@click.argument("course")
@click.argument("page_url")
@click.pass_context
def page(ctx, course, page_url):
    """Get wiki page content by URL slug (e.g. 'week-1-overview')."""
    cid = resolve_course(course)
    data = get(f"/courses/{cid}/pages/{page_url}")

    if ctx.obj["json"]:
        success(ctx, data)
    else:
        click.echo(f"Title: {data.get('title', '?')}")
        body = data.get("body", "")
        if body:
            click.echo(body)


# --- Command 5: download ---

@cli.command()
@click.argument("course")
@click.argument("file_id")
@click.option("--dir", "save_dir", default=".", help="Directory to save file")
@click.pass_context
def download(ctx, course, file_id, save_dir):
    """Download a course file by ID."""
    cid = resolve_course(course)
    meta = get(f"/courses/{cid}/files/{file_id}")

    download_url = meta["url"]
    filename = meta["filename"]
    dest = Path(save_dir) / filename

    download_file(download_url, dest)

    result = {"path": str(dest), "filename": filename, "size": meta.get("size")}
    if ctx.obj["json"]:
        success(ctx, result)
    else:
        click.echo(f"Downloaded: {dest}")


# --- Command 6: thread ---

@cli.command()
@click.argument("course")
@click.argument("topic_id")
@click.pass_context
def thread(ctx, course, topic_id):
    """Get full discussion thread with all replies."""
    cid = resolve_course(course)
    data = get(f"/courses/{cid}/discussion_topics/{topic_id}/view")

    if ctx.obj["json"]:
        success(ctx, data)
    else:
        participants = {
            p["id"]: p.get("display_name", "?")
            for p in data.get("participants", [])
        }
        for entry in data.get("view", []):
            _print_entry(entry, participants, 0)


def _print_entry(entry, participants, indent):
    prefix = "  " * indent
    name = participants.get(entry.get("user_id"), "?")
    msg = entry.get("message", "")
    click.echo(f"{prefix}{name}: {msg}")
    for reply in entry.get("replies", []):
        _print_entry(reply, participants, indent + 1)


# --- Command 7: rubric ---

@cli.command()
@click.argument("course")
@click.argument("assignment_id")
@click.pass_context
def rubric(ctx, course, assignment_id):
    """Get rubric details for an assignment."""
    cid = resolve_course(course)
    data = get(
        f"/courses/{cid}/assignments/{assignment_id}",
        [("include[]", "rubric"), ("include[]", "rubric_settings")],
    )

    result = {
        "rubric": data.get("rubric", []),
        "rubric_settings": data.get("rubric_settings", {}),
    }

    if ctx.obj["json"]:
        success(ctx, result)
    else:
        rubric_data = result["rubric"]
        if not rubric_data:
            click.echo("No rubric for this assignment.")
            return
        for criterion in rubric_data:
            click.echo(f"  {criterion.get('description', '?')} [{criterion.get('points', '?')} pts]")
            for rating in criterion.get("ratings", []):
                click.echo(f"    - {rating.get('description', '?')}: {rating.get('points', '?')} pts")


# --- Command 8: submit ---

cli.add_command(_submit_command)


# --- Command 9: config ---

@cli.group()
def config():
    """Manage persistent CLI config at ~/.canvas-cli/config.json."""
    pass


@config.command("set")
@click.argument("key")
@click.argument("value")
@click.pass_context
def config_set(ctx, key, value):
    """Persist a config value. Keys: base_url, cookie_file, token_refresh_script, upload_size_limit_mb."""
    try:
        save_config(key, value)
    except ValueError as e:
        error("ConfigError", str(e), exit_code=2)
    if ctx.obj["json"]:
        success(ctx, {"key": key, "value": value})
    else:
        click.echo(f"Saved: {key} = {value}")


@config.command("show")
@click.pass_context
def config_show(ctx):
    """Show effective config (env + file merged)."""
    cfg = show_config()
    if ctx.obj["json"]:
        success(ctx, cfg)
    else:
        for k, v in cfg.items():
            click.echo(f"{k}: {v or '(unset)'}")
