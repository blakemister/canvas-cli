"""Canvas submissions API calls for text, URL, and file submissions."""

from pathlib import Path

from .client import get, post_form, upload_file_to_url


def fetch_assignment(course_id: str, assignment_id: str) -> dict:
    """GET assignment with user's current submission state."""
    return get(
        f"/courses/{course_id}/assignments/{assignment_id}",
        [("include[]", "submission")],
    )


def submit_text(course_id: str, assignment_id: str, html_body: str) -> dict:
    """POST a text_entry submission. Returns the Canvas submission object."""
    return post_form(
        f"/courses/{course_id}/assignments/{assignment_id}/submissions",
        [
            ("submission[submission_type]", "online_text_entry"),
            ("submission[body]", html_body),
        ],
    )


def submit_url(course_id: str, assignment_id: str, url: str) -> dict:
    """POST a URL submission. Returns the Canvas submission object."""
    return post_form(
        f"/courses/{course_id}/assignments/{assignment_id}/submissions",
        [
            ("submission[submission_type]", "online_url"),
            ("submission[url]", url),
        ],
    )


def submit_file(course_id: str, assignment_id: str, file_path: Path) -> dict:
    """Three-step file submission flow.

    1. Init upload: POST submissions/self/files → get upload_url + params
    2. Upload: POST file to upload_url with signed params
    3. Submit: POST to submissions with file_ids[]=<id>

    Returns the final Canvas submission object. The file_id from step 2 is
    attached to the returned dict under the '_canvas_file_id' key for the
    receipt writer.
    """
    size = file_path.stat().st_size
    init_response = post_form(
        f"/courses/{course_id}/assignments/{assignment_id}/submissions/self/files",
        [
            ("name", file_path.name),
            ("size", str(size)),
        ],
    )

    upload_url = init_response["upload_url"]
    upload_params = init_response["upload_params"]
    file_param = init_response.get("file_param", "file")

    upload_result = upload_file_to_url(
        upload_url, upload_params, file_path, file_param=file_param,
    )
    file_id = upload_result.get("id")
    if not file_id:
        raise RuntimeError(
            f"Upload did not return a file id. Response: {upload_result}"
        )

    submission = post_form(
        f"/courses/{course_id}/assignments/{assignment_id}/submissions",
        [
            ("submission[submission_type]", "online_upload"),
            ("submission[file_ids][]", str(file_id)),
        ],
    )
    submission["_canvas_file_id"] = file_id
    return submission
