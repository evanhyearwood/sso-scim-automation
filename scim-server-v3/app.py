"""
SCIM 2.0 Service Provider — Flask Application
===============================================
A lightweight, config-driven SCIM server for Entra ID provisioning labs.
One codebase, multiple instances via config files.

Usage:
    python app.py                          # uses config.yaml in current dir
    python app.py --config configs/config-contoso.yaml

Developed for SCIP by Evan H. Yearwood
"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlencode, quote_plus
from xml.etree import ElementTree

import yaml
import requests
from flask import Flask, Response, g, jsonify, redirect, render_template, request, session, url_for

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config(path: str) -> dict:
    """Load and validate the YAML config file."""
    with open(path, "r") as f:
        cfg = yaml.safe_load(f)
    required = ["app_name", "port", "bearer_token", "database"]
    for key in required:
        if key not in cfg:
            raise SystemExit(f"[ERROR] Missing required config key: {key}")
    cfg.setdefault("groups_enabled", False)
    return cfg


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

DB_PATH: str = ""  # set at startup from config


def get_db() -> sqlite3.Connection:
    """Get a database connection for the current request."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def close_db(exc=None):
    """Close the database connection at the end of the request."""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Create tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id              TEXT PRIMARY KEY,
            external_id     TEXT,
            user_name       TEXT UNIQUE NOT NULL,
            given_name      TEXT DEFAULT '',
            family_name     TEXT DEFAULT '',
            display_name    TEXT DEFAULT '',
            email           TEXT DEFAULT '',
            title           TEXT DEFAULT '',
            department      TEXT DEFAULT '',
            active          INTEGER DEFAULT 1,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS groups (
            id              TEXT PRIMARY KEY,
            external_id     TEXT,
            display_name    TEXT NOT NULL,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS memberships (
            group_id        TEXT NOT NULL,
            user_id         TEXT NOT NULL,
            PRIMARY KEY (group_id, user_id),
            FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id)  REFERENCES users(id)  ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS activity_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT NOT NULL,
            method          TEXT NOT NULL,
            endpoint        TEXT NOT NULL,
            status_code     INTEGER NOT NULL,
            action          TEXT DEFAULT '',
            target          TEXT DEFAULT '',
            detail          TEXT DEFAULT ''
        );
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Auth Middleware
# ---------------------------------------------------------------------------

BEARER_TOKEN: str = ""  # set at startup from config


def require_bearer(f):
    """Decorator that validates the Authorization: Bearer header."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return scim_error(401, "Missing or malformed Authorization header")
        token = auth[7:]
        if token != BEARER_TOKEN:
            return scim_error(401, "Invalid bearer token")
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# SCIM Response Helpers
# ---------------------------------------------------------------------------

SCIM_CONTENT_TYPE = "application/scim+json"


def scim_response(data: dict, status: int = 200) -> Response:
    """Return a properly formatted SCIM JSON response."""
    resp = Response(
        json.dumps(data, indent=2),
        status=status,
        content_type=SCIM_CONTENT_TYPE,
    )
    return resp


def scim_error(status: int, detail: str) -> Response:
    """Return a SCIM error response."""
    body = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
        "status": str(status),
        "detail": detail,
    }
    return scim_response(body, status)


def now_iso() -> str:
    """Current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# SCIM Resource Serializers
# ---------------------------------------------------------------------------

def user_to_scim(row: sqlite3.Row) -> dict:
    """Convert a database user row to SCIM 2.0 JSON."""
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": row["id"],
        "externalId": row["external_id"] or "",
        "userName": row["user_name"],
        "name": {
            "givenName": row["given_name"],
            "familyName": row["family_name"],
        },
        "displayName": row["display_name"],
        "emails": [
            {"value": row["email"], "type": "work", "primary": True}
        ] if row["email"] else [],
        "title": row["title"],
        "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User": {
            "department": row["department"],
        },
        "active": bool(row["active"]),
        "meta": {
            "resourceType": "User",
            "created": row["created_at"],
            "lastModified": row["updated_at"],
            "location": f"{request.url_root}scim/v2/Users/{row['id']}",
        },
    }


def group_to_scim(row: sqlite3.Row, include_members: bool = True) -> dict:
    """Convert a database group row to SCIM 2.0 JSON."""
    result = {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        "id": row["id"],
        "externalId": row["external_id"] or "",
        "displayName": row["display_name"],
        "meta": {
            "resourceType": "Group",
            "created": row["created_at"],
            "lastModified": row["updated_at"],
            "location": f"{request.url_root}scim/v2/Groups/{row['id']}",
        },
    }
    if include_members:
        db = get_db()
        members = db.execute(
            "SELECT user_id FROM memberships WHERE group_id = ?", (row["id"],)
        ).fetchall()
        result["members"] = [
            {
                "value": m["user_id"],
                "$ref": f"{request.url_root}scim/v2/Users/{m['user_id']}",
                "type": "User",
            }
            for m in members
        ]
    return result


# ---------------------------------------------------------------------------
# SCIM Filter Parser (handles eq and 'and' operators)
# ---------------------------------------------------------------------------

def parse_scim_filter(filter_str: str):
    """
    Parse a simple SCIM filter string.
    Supports: attribute eq "value" [and attribute eq "value"]
    Returns a list of (attribute, value) tuples.
    """
    conditions = []
    if not filter_str:
        return conditions
    parts = filter_str.split(" and ")
    for part in parts:
        part = part.strip()
        tokens = part.split(" ", 2)
        if len(tokens) == 3 and tokens[1].lower() == "eq":
            attr = tokens[0].strip()
            val = tokens[2].strip().strip('"')
            conditions.append((attr, val))
    return conditions


def apply_user_filter(conditions: list) -> str:
    """Convert parsed filter conditions to SQL WHERE clause for users."""
    mapping = {
        "userName": "user_name",
        "externalId": "external_id",
        "emails.value": "email",
        "displayName": "display_name",
        "name.givenName": "given_name",
        "name.familyName": "family_name",
    }
    clauses = []
    values = []
    for attr, val in conditions:
        col = mapping.get(attr)
        if col:
            clauses.append(f"{col} = ?")
            values.append(val)
    if clauses:
        return " AND ".join(clauses), values
    return "1=1", []


def apply_group_filter(conditions: list) -> str:
    """Convert parsed filter conditions to SQL WHERE clause for groups."""
    mapping = {
        "displayName": "display_name",
        "externalId": "external_id",
    }
    clauses = []
    values = []
    for attr, val in conditions:
        col = mapping.get(attr)
        if col:
            clauses.append(f"{col} = ?")
            values.append(val)
    if clauses:
        return " AND ".join(clauses), values
    return "1=1", []


# ---------------------------------------------------------------------------
# Flask App Factory
# ---------------------------------------------------------------------------

def create_app(config: dict) -> Flask:
    """Create and configure the Flask application."""

    global DB_PATH, BEARER_TOKEN
    DB_PATH = config["database"]
    BEARER_TOKEN = config["bearer_token"]

    app = Flask(__name__, template_folder="templates")
    app.config["APP_NAME"] = config["app_name"]
    app.config["GROUPS_ENABLED"] = config.get("groups_enabled", False)
    app.secret_key = config.get("flask_secret", secrets.token_hex(32))

    app.teardown_appcontext(close_db)
    init_db()

    # -------------------------------------------------------------------
    # Activity Logging
    # -------------------------------------------------------------------

    def log_activity(status_code, action="", target="", detail=""):
        """Record a SCIM operation in the activity log."""
        try:
            db = get_db()
            db.execute(
                """INSERT INTO activity_log
                   (timestamp, method, endpoint, status_code, action, target, detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    now_iso(),
                    request.method,
                    request.path,
                    status_code,
                    action,
                    target,
                    detail,
                ),
            )
            db.commit()
        except Exception:
            pass  # never let logging break a request

    @app.after_request
    def auto_log_scim(response):
        """Automatically log every SCIM endpoint request."""
        if request.path.startswith("/scim/v2") and request.path not in (
            "/scim/v2/ServiceProviderConfig",
            "/scim/v2/Schemas",
            "/scim/v2/ResourceTypes",
        ):
            action = ""
            target = ""
            detail = ""

            method = request.method
            path = request.path

            if "/Users" in path or "/Groups" in path:
                resource = "User" if "/Users" in path else "Group"

                if method == "POST":
                    action = f"Create {resource}"
                    try:
                        data = request.get_json(silent=True) or {}
                        target = data.get("userName", data.get("displayName", ""))
                    except Exception:
                        pass
                elif method == "PATCH":
                    action = f"Update {resource}"
                    try:
                        data = request.get_json(silent=True) or {}
                        ops = data.get("Operations", [])
                        parts = []
                        for op in ops:
                            p = op.get("path", "")
                            if p == "active" and op.get("value") in (False, "false", "False"):
                                action = f"Disable {resource}"
                            elif p == "active" and op.get("value") in (True, "true", "True"):
                                action = f"Enable {resource}"
                            elif p:
                                parts.append(p.split(":")[-1] if ":" in p else p)
                        if parts:
                            detail = f"Fields: {', '.join(parts)}"
                    except Exception:
                        pass
                    # extract target from URL
                    segments = path.rstrip("/").split("/")
                    if len(segments) > 3:
                        rid = segments[-1]
                        try:
                            db = get_db()
                            if resource == "User":
                                r = db.execute("SELECT user_name FROM users WHERE id = ?", (rid,)).fetchone()
                                target = r["user_name"] if r else rid[:12]
                            else:
                                r = db.execute("SELECT display_name FROM groups WHERE id = ?", (rid,)).fetchone()
                                target = r["display_name"] if r else rid[:12]
                        except Exception:
                            target = rid[:12]
                elif method == "DELETE":
                    action = f"Delete {resource}"
                    segments = path.rstrip("/").split("/")
                    target = segments[-1][:12] if len(segments) > 3 else ""
                elif method == "GET":
                    action = f"Query {resource}s"
                    f = request.args.get("filter", "")
                    if f:
                        detail = f"Filter: {f}"

            try:
                db = get_db()
                db.execute(
                    """INSERT INTO activity_log
                       (timestamp, method, endpoint, status_code, action, target, detail)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (now_iso(), method, path, response.status_code, action, target, detail),
                )
                db.commit()
            except Exception:
                pass

        return response

    # -------------------------------------------------------------------
    # Health / Root
    # -------------------------------------------------------------------

    @app.route("/")
    def index():
        return jsonify({
            "app": config["app_name"],
            "status": "running",
            "scim_endpoint": "/scim/v2",
            "dashboard": "/dashboard",
        })

    # -------------------------------------------------------------------
    # SCIM Service Provider Configuration (discovery)
    # -------------------------------------------------------------------

    @app.route("/scim/v2/ServiceProviderConfig")
    @require_bearer
    def service_provider_config():
        return scim_response({
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": True},
            "bulk": {"supported": False},
            "filter": {"supported": True, "maxResults": 100},
            "changePassword": {"supported": False},
            "sort": {"supported": False},
            "etag": {"supported": False},
            "authenticationSchemes": [
                {
                    "type": "oauthbearertoken",
                    "name": "OAuth Bearer Token",
                    "description": "Authentication via bearer token in Authorization header",
                }
            ],
        })

    @app.route("/scim/v2/Schemas")
    @require_bearer
    def schemas():
        return scim_response({
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": 0,
            "Resources": [],
        })

    @app.route("/scim/v2/ResourceTypes")
    @require_bearer
    def resource_types():
        types = [
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                "id": "User",
                "name": "User",
                "endpoint": "/scim/v2/Users",
                "schema": "urn:ietf:params:scim:schemas:core:2.0:User",
            }
        ]
        if config.get("groups_enabled"):
            types.append({
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                "id": "Group",
                "name": "Group",
                "endpoint": "/scim/v2/Groups",
                "schema": "urn:ietf:params:scim:schemas:core:2.0:Group",
            })
        return scim_response({
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": len(types),
            "Resources": types,
        })

    # -------------------------------------------------------------------
    # /Users endpoints
    # -------------------------------------------------------------------

    @app.route("/scim/v2/Users", methods=["GET"])
    @require_bearer
    def list_users():
        db = get_db()
        filter_str = request.args.get("filter", "")
        start = max(int(request.args.get("startIndex", 1)), 1)
        count = min(int(request.args.get("count", 100)), 100)

        conditions = parse_scim_filter(filter_str)
        where, values = apply_user_filter(conditions)

        total = db.execute(f"SELECT COUNT(*) FROM users WHERE {where}", values).fetchone()[0]
        rows = db.execute(
            f"SELECT * FROM users WHERE {where} ORDER BY created_at LIMIT ? OFFSET ?",
            values + [count, start - 1],
        ).fetchall()

        return scim_response({
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": total,
            "startIndex": start,
            "itemsPerPage": len(rows),
            "Resources": [user_to_scim(r) for r in rows],
        })

    @app.route("/scim/v2/Users/<user_id>", methods=["GET"])
    @require_bearer
    def get_user(user_id):
        db = get_db()
        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return scim_error(404, f"User {user_id} not found")
        return scim_response(user_to_scim(row))

    @app.route("/scim/v2/Users", methods=["POST"])
    @require_bearer
    def create_user():
        db = get_db()
        data = request.get_json()
        if not data:
            return scim_error(400, "Request body is required")

        user_name = data.get("userName", "")
        if not user_name:
            return scim_error(400, "userName is required")

        # Check for duplicate
        existing = db.execute(
            "SELECT id FROM users WHERE user_name = ?", (user_name,)
        ).fetchone()
        if existing:
            return scim_error(409, f"User with userName '{user_name}' already exists")

        name = data.get("name", {})
        emails = data.get("emails", [])
        email = emails[0]["value"] if emails else ""
        enterprise = data.get(
            "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User", {}
        )

        user_id = str(uuid.uuid4())
        ts = now_iso()

        db.execute(
            """INSERT INTO users
               (id, external_id, user_name, given_name, family_name,
                display_name, email, title, department, active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                data.get("externalId", ""),
                user_name,
                name.get("givenName", ""),
                name.get("familyName", ""),
                data.get("displayName", ""),
                email,
                data.get("title", ""),
                enterprise.get("department", ""),
                1 if data.get("active", True) else 0,
                ts,
                ts,
            ),
        )
        db.commit()

        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return scim_response(user_to_scim(row), 201)

    @app.route("/scim/v2/Users/<user_id>", methods=["PATCH"])
    @require_bearer
    def patch_user(user_id):
        db = get_db()
        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return scim_error(404, f"User {user_id} not found")

        data = request.get_json()
        if not data:
            return scim_error(400, "Request body is required")

        operations = data.get("Operations", [])
        for op in operations:
            op_type = op.get("op", "").lower()
            path = op.get("path", "")
            value = op.get("value")

            if op_type == "replace":
                _apply_user_replace(db, user_id, path, value)
            elif op_type == "add":
                _apply_user_replace(db, user_id, path, value)
            elif op_type == "remove":
                _apply_user_remove(db, user_id, path)

        db.execute("UPDATE users SET updated_at = ? WHERE id = ?", (now_iso(), user_id))
        db.commit()

        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return scim_response(user_to_scim(row))

    @app.route("/scim/v2/Users/<user_id>", methods=["PUT"])
    @require_bearer
    def put_user(user_id):
        db = get_db()
        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return scim_error(404, f"User {user_id} not found")

        data = request.get_json()
        if not data:
            return scim_error(400, "Request body is required")

        name = data.get("name", {})
        emails = data.get("emails", [])
        email = emails[0]["value"] if emails else ""
        enterprise = data.get(
            "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User", {}
        )
        ts = now_iso()

        db.execute(
            """UPDATE users SET
                external_id = ?, user_name = ?, given_name = ?, family_name = ?,
                display_name = ?, email = ?, title = ?, department = ?,
                active = ?, updated_at = ?
               WHERE id = ?""",
            (
                data.get("externalId", ""),
                data.get("userName", row["user_name"]),
                name.get("givenName", ""),
                name.get("familyName", ""),
                data.get("displayName", ""),
                email,
                data.get("title", ""),
                enterprise.get("department", ""),
                1 if data.get("active", True) else 0,
                ts,
                user_id,
            ),
        )
        db.commit()

        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return scim_response(user_to_scim(row))

    @app.route("/scim/v2/Users/<user_id>", methods=["DELETE"])
    @require_bearer
    def delete_user(user_id):
        db = get_db()
        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return scim_error(404, f"User {user_id} not found")
        db.execute("DELETE FROM memberships WHERE user_id = ?", (user_id,))
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        db.commit()
        return Response(status=204)

    # -------------------------------------------------------------------
    # PATCH helpers for Users
    # -------------------------------------------------------------------

    def _apply_user_replace(db, user_id, path, value):
        """Handle a SCIM PATCH replace or add operation on a user."""
        col_map = {
            "userName": "user_name",
            "name.givenName": "given_name",
            "name.familyName": "family_name",
            "displayName": "display_name",
            "title": "title",
            "active": "active",
            "externalId": "external_id",
            "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:department": "department",
        }

        if path and path in col_map:
            db_val = value
            if path == "active":
                db_val = 1 if value in (True, "True", "true") else 0
            db.execute(
                f"UPDATE users SET {col_map[path]} = ? WHERE id = ?",
                (db_val, user_id),
            )
        elif path and path.startswith("emails["):
            if isinstance(value, dict):
                db.execute(
                    "UPDATE users SET email = ? WHERE id = ?",
                    (value.get("value", ""), user_id),
                )
            elif isinstance(value, str):
                db.execute(
                    "UPDATE users SET email = ? WHERE id = ?", (value, user_id)
                )
        elif not path and isinstance(value, dict):
            # Entra sometimes sends replace with no path and a dict of attrs
            for k, v in value.items():
                if k in col_map:
                    db_val = v
                    if k == "active":
                        db_val = 1 if v in (True, "True", "true") else 0
                    db.execute(
                        f"UPDATE users SET {col_map[k]} = ? WHERE id = ?",
                        (db_val, user_id),
                    )
                elif k == "name" and isinstance(v, dict):
                    for nk, nv in v.items():
                        full = f"name.{nk}"
                        if full in col_map:
                            db.execute(
                                f"UPDATE users SET {col_map[full]} = ? WHERE id = ?",
                                (nv, user_id),
                            )
                elif k == "emails" and isinstance(v, list) and v:
                    db.execute(
                        "UPDATE users SET email = ? WHERE id = ?",
                        (v[0].get("value", ""), user_id),
                    )
                elif (
                    k == "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"
                    and isinstance(v, dict)
                ):
                    if "department" in v:
                        db.execute(
                            "UPDATE users SET department = ? WHERE id = ?",
                            (v["department"], user_id),
                        )

    def _apply_user_remove(db, user_id, path):
        """Handle a SCIM PATCH remove operation on a user."""
        col_map = {
            "title": "title",
            "displayName": "display_name",
            "externalId": "external_id",
            "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:department": "department",
        }
        if path in col_map:
            db.execute(
                f"UPDATE users SET {col_map[path]} = '' WHERE id = ?", (user_id,)
            )

    # -------------------------------------------------------------------
    # /Groups endpoints (only active if groups_enabled)
    # -------------------------------------------------------------------

    @app.route("/scim/v2/Groups", methods=["GET"])
    @require_bearer
    def list_groups():
        if not config.get("groups_enabled"):
            return scim_error(501, "Groups endpoint is not enabled for this application")
        db = get_db()
        filter_str = request.args.get("filter", "")
        start = max(int(request.args.get("startIndex", 1)), 1)
        count = min(int(request.args.get("count", 100)), 100)

        conditions = parse_scim_filter(filter_str)
        where, values = apply_group_filter(conditions)

        total = db.execute(f"SELECT COUNT(*) FROM groups WHERE {where}", values).fetchone()[0]
        rows = db.execute(
            f"SELECT * FROM groups WHERE {where} ORDER BY created_at LIMIT ? OFFSET ?",
            values + [count, start - 1],
        ).fetchall()

        return scim_response({
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
            "totalResults": total,
            "startIndex": start,
            "itemsPerPage": len(rows),
            "Resources": [group_to_scim(r) for r in rows],
        })

    @app.route("/scim/v2/Groups/<group_id>", methods=["GET"])
    @require_bearer
    def get_group(group_id):
        if not config.get("groups_enabled"):
            return scim_error(501, "Groups endpoint is not enabled for this application")
        db = get_db()
        row = db.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        if not row:
            return scim_error(404, f"Group {group_id} not found")
        return scim_response(group_to_scim(row))

    @app.route("/scim/v2/Groups", methods=["POST"])
    @require_bearer
    def create_group():
        if not config.get("groups_enabled"):
            return scim_error(501, "Groups endpoint is not enabled for this application")
        db = get_db()
        data = request.get_json()
        if not data:
            return scim_error(400, "Request body is required")

        display_name = data.get("displayName", "")
        if not display_name:
            return scim_error(400, "displayName is required")

        group_id = str(uuid.uuid4())
        ts = now_iso()

        db.execute(
            "INSERT INTO groups (id, external_id, display_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (group_id, data.get("externalId", ""), display_name, ts, ts),
        )

        # Handle initial members
        for member in data.get("members", []):
            member_id = member.get("value")
            if member_id:
                user_exists = db.execute(
                    "SELECT id FROM users WHERE id = ?", (member_id,)
                ).fetchone()
                if user_exists:
                    db.execute(
                        "INSERT OR IGNORE INTO memberships (group_id, user_id) VALUES (?, ?)",
                        (group_id, member_id),
                    )
        db.commit()

        row = db.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        return scim_response(group_to_scim(row), 201)

    @app.route("/scim/v2/Groups/<group_id>", methods=["PATCH"])
    @require_bearer
    def patch_group(group_id):
        if not config.get("groups_enabled"):
            return scim_error(501, "Groups endpoint is not enabled for this application")
        db = get_db()
        row = db.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        if not row:
            return scim_error(404, f"Group {group_id} not found")

        data = request.get_json()
        operations = data.get("Operations", [])

        for op in operations:
            op_type = op.get("op", "").lower()
            path = op.get("path", "")
            value = op.get("value")

            if op_type == "replace" and path == "displayName":
                db.execute(
                    "UPDATE groups SET display_name = ? WHERE id = ?",
                    (value, group_id),
                )
            elif op_type in ("add", "replace") and path == "members":
                members = value if isinstance(value, list) else [value]
                for m in members:
                    member_id = m.get("value") if isinstance(m, dict) else m
                    if member_id:
                        db.execute(
                            "INSERT OR IGNORE INTO memberships (group_id, user_id) VALUES (?, ?)",
                            (group_id, member_id),
                        )
            elif op_type == "remove" and path and "members[" in path:
                # path format: members[value eq "user-id"]
                member_id = path.split('"')[1] if '"' in path else ""
                if member_id:
                    db.execute(
                        "DELETE FROM memberships WHERE group_id = ? AND user_id = ?",
                        (group_id, member_id),
                    )

        db.execute(
            "UPDATE groups SET updated_at = ? WHERE id = ?", (now_iso(), group_id)
        )
        db.commit()

        row = db.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        return scim_response(group_to_scim(row))

    @app.route("/scim/v2/Groups/<group_id>", methods=["DELETE"])
    @require_bearer
    def delete_group(group_id):
        if not config.get("groups_enabled"):
            return scim_error(501, "Groups endpoint is not enabled for this application")
        db = get_db()
        row = db.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        if not row:
            return scim_error(404, f"Group {group_id} not found")
        db.execute("DELETE FROM memberships WHERE group_id = ?", (group_id,))
        db.execute("DELETE FROM groups WHERE id = ?", (group_id,))
        db.commit()
        return Response(status=204)

    # -------------------------------------------------------------------
    # Dashboard
    # -------------------------------------------------------------------

    @app.route("/dashboard")
    def dashboard():
        db = get_db()
        users = db.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
        groups = []
        memberships = {}
        if config.get("groups_enabled"):
            groups = db.execute(
                "SELECT * FROM groups ORDER BY created_at DESC"
            ).fetchall()
            for grp in groups:
                members = db.execute(
                    """SELECT u.user_name, u.display_name, u.active
                       FROM memberships m JOIN users u ON m.user_id = u.id
                       WHERE m.group_id = ?""",
                    (grp["id"],),
                ).fetchall()
                memberships[grp["id"]] = members
        activity = db.execute(
            "SELECT * FROM activity_log ORDER BY id DESC LIMIT 50"
        ).fetchall()
        return render_template(
            "dashboard.html",
            app_name=config["app_name"],
            users=users,
            groups=groups,
            memberships=memberships,
            groups_enabled=config.get("groups_enabled", False),
            activity=activity,
        )

    # -------------------------------------------------------------------
    # SSO Routes — SAML
    # -------------------------------------------------------------------

    if config.get("sso_protocol") == "saml":

        def _get_idp_sso_url():
            """Parse the IdP SSO URL from the Entra federation metadata XML."""
            md_path = config.get("saml_idp_metadata", "")
            if not os.path.exists(md_path):
                return None
            ns = {
                "md": "urn:oasis:names:tc:SAML:2.0:metadata",
                "ds": "http://www.w3.org/2000/09/xmldsig#",
            }
            tree = ElementTree.parse(md_path)
            root = tree.getroot()
            for sso in root.iter("{urn:oasis:names:tc:SAML:2.0:metadata}SingleSignOnService"):
                binding = sso.get("Binding", "")
                if "HTTP-Redirect" in binding:
                    return sso.get("Location")
            # fallback to POST binding
            for sso in root.iter("{urn:oasis:names:tc:SAML:2.0:metadata}SingleSignOnService"):
                return sso.get("Location")
            return None

        def _get_idp_cert_from_metadata():
            """Extract the IdP signing certificate from federation metadata."""
            md_path = config.get("saml_idp_metadata", "")
            if not os.path.exists(md_path):
                return None
            tree = ElementTree.parse(md_path)
            root = tree.getroot()
            for cert_elem in root.iter("{http://www.w3.org/2000/09/xmldsig#}X509Certificate"):
                if cert_elem.text:
                    return cert_elem.text.strip()
            return None

        @app.route("/saml/metadata")
        def saml_metadata():
            """Serve SP metadata XML for Entra to consume."""
            entity_id = config.get("saml_entity_id", "")
            acs_url = config.get("saml_acs_url", "")
            metadata_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
                     entityID="{entity_id}">
  <md:SPSSODescriptor AuthnRequestsSigned="false"
                      WantAssertionsSigned="false"
                      protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat>
    <md:NameIDFormat>urn:oasis:names:tc:SAML:2.0:nameid-format:persistent</md:NameIDFormat>
    <md:AssertionConsumerService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
                                Location="{acs_url}"
                                index="0"
                                isDefault="true"/>
  </md:SPSSODescriptor>
</md:EntityDescriptor>"""
            return Response(metadata_xml, content_type="application/xml")

        @app.route("/saml/login")
        def saml_login():
            """Redirect user to Entra IdP for SAML authentication."""
            idp_sso_url = _get_idp_sso_url()
            if not idp_sso_url:
                return render_template("sso_error.html",
                    app_name=config["app_name"],
                    error="IdP metadata not found",
                    detail=f"Download the Federation Metadata XML from Entra and save it to: {config.get('saml_idp_metadata', 'metadata/')}")

            # Build a minimal AuthnRequest
            request_id = f"_{''.join(secrets.token_hex(16))}"
            issue_instant = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            entity_id = config.get("saml_entity_id", "")
            acs_url = config.get("saml_acs_url", "")

            authn_request = f"""<samlp:AuthnRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
    xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
    ID="{request_id}"
    Version="2.0"
    IssueInstant="{issue_instant}"
    Destination="{idp_sso_url}"
    AssertionConsumerServiceURL="{acs_url}"
    ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST">
  <saml:Issuer>{entity_id}</saml:Issuer>
</samlp:AuthnRequest>"""

            import zlib
            deflated = zlib.compress(authn_request.encode("utf-8"))[2:-4]
            encoded = base64.b64encode(deflated).decode("utf-8")
            redirect_url = f"{idp_sso_url}?SAMLRequest={quote_plus(encoded)}"
            return redirect(redirect_url)

        @app.route("/saml/acs", methods=["POST"])
        def saml_acs():
            """Receive and parse the SAML assertion from Entra."""
            saml_response_b64 = request.form.get("SAMLResponse", "")
            if not saml_response_b64:
                return render_template("sso_error.html",
                    app_name=config["app_name"],
                    error="No SAMLResponse received",
                    detail="The IdP did not send a SAML response.")

            try:
                saml_xml = base64.b64decode(saml_response_b64)
                root = ElementTree.fromstring(saml_xml)

                ns = {
                    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
                    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
                }

                # Extract status
                status_elem = root.find(".//samlp:Status/samlp:StatusCode", ns)
                status = status_elem.get("Value", "Unknown") if status_elem is not None else "Unknown"

                # Extract NameID
                name_id_elem = root.find(".//saml:Assertion/saml:Subject/saml:NameID", ns)
                name_id = name_id_elem.text if name_id_elem is not None else "Not found"
                name_id_format = name_id_elem.get("Format", "Unknown") if name_id_elem is not None else "Unknown"

                # Extract attributes
                attributes = {}
                for attr_stmt in root.iter("{urn:oasis:names:tc:SAML:2.0:assertion}AttributeStatement"):
                    for attr in attr_stmt.iter("{urn:oasis:names:tc:SAML:2.0:assertion}Attribute"):
                        attr_name = attr.get("Name", "")
                        values = []
                        for val in attr.iter("{urn:oasis:names:tc:SAML:2.0:assertion}AttributeValue"):
                            if val.text:
                                values.append(val.text)
                        # Use friendly name if available
                        friendly = attr.get("FriendlyName", attr_name.split("/")[-1] if "/" in attr_name else attr_name)
                        attributes[friendly] = values[0] if len(values) == 1 else values

                # Extract issuer
                issuer_elem = root.find(".//saml:Assertion/saml:Issuer", ns)
                issuer = issuer_elem.text if issuer_elem is not None else "Unknown"

                # Extract conditions / audience
                audience_elem = root.find(".//saml:Assertion/saml:Conditions/saml:AudienceRestriction/saml:Audience", ns)
                audience = audience_elem.text if audience_elem is not None else "Not specified"

                # Log the SSO event
                try:
                    db = get_db()
                    db.execute(
                        """INSERT INTO activity_log
                           (timestamp, method, endpoint, status_code, action, target, detail)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (now_iso(), "POST", "/saml/acs", 200, "SAML SSO Login", name_id,
                         f"NameID format: {name_id_format.split(':')[-1]}"),
                    )
                    db.commit()
                except Exception:
                    pass

                return render_template("sso_profile.html",
                    app_name=config["app_name"],
                    protocol="SAML 2.0",
                    name_id=name_id,
                    name_id_format=name_id_format,
                    issuer=issuer,
                    audience=audience,
                    status=status,
                    attributes=attributes,
                    raw_xml=saml_xml.decode("utf-8", errors="replace"),
                )

            except Exception as e:
                return render_template("sso_error.html",
                    app_name=config["app_name"],
                    error="Failed to parse SAML response",
                    detail=str(e))

    # -------------------------------------------------------------------
    # SSO Routes — OIDC
    # -------------------------------------------------------------------

    if config.get("sso_protocol") == "oidc":

        @app.route("/auth/login")
        def oidc_login():
            """Redirect user to Entra for OIDC authentication."""
            client_id = config.get("oidc_client_id", "")
            tenant_id = config.get("oidc_tenant_id", "")
            redirect_uri = config.get("oidc_redirect_uri", "")

            if not client_id or client_id == "YOUR-APP-CLIENT-ID":
                return render_template("sso_error.html",
                    app_name=config["app_name"],
                    error="OIDC not configured",
                    detail="Update oidc_client_id and oidc_tenant_id in your config file.")

            # Generate PKCE challenge
            code_verifier = secrets.token_urlsafe(64)
            code_challenge = base64.urlsafe_b64encode(
                hashlib.sha256(code_verifier.encode()).digest()
            ).decode().rstrip("=")

            session["oidc_code_verifier"] = code_verifier
            session["oidc_state"] = secrets.token_urlsafe(32)

            authorize_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"
            params = {
                "client_id": client_id,
                "response_type": "code",
                "redirect_uri": redirect_uri,
                "scope": "openid profile email User.Read",
                "state": session["oidc_state"],
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "response_mode": "query",
            }
            return redirect(f"{authorize_url}?{urlencode(params)}")

        @app.route("/auth/callback")
        def oidc_callback():
            """Handle the OIDC authorization code callback."""
            error = request.args.get("error")
            if error:
                return render_template("sso_error.html",
                    app_name=config["app_name"],
                    error=f"OIDC Error: {error}",
                    detail=request.args.get("error_description", ""))

            code = request.args.get("code", "")
            state = request.args.get("state", "")

            if state != session.get("oidc_state"):
                return render_template("sso_error.html",
                    app_name=config["app_name"],
                    error="State mismatch",
                    detail="The state parameter does not match. Possible CSRF attack.")

            tenant_id = config.get("oidc_tenant_id", "")
            client_id = config.get("oidc_client_id", "")
            redirect_uri = config.get("oidc_redirect_uri", "")
            code_verifier = session.pop("oidc_code_verifier", "")

            # Exchange code for tokens
            token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
            token_data = {
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
                "scope": "openid profile email User.Read",
            }

            # Add client secret if configured (confidential client)
            client_secret = config.get("oidc_client_secret", "")
            if client_secret:
                token_data["client_secret"] = client_secret

            try:
                token_resp = requests.post(token_url, data=token_data, timeout=10)
                token_json = token_resp.json()

                if "error" in token_json:
                    return render_template("sso_error.html",
                        app_name=config["app_name"],
                        error=f"Token error: {token_json.get('error')}",
                        detail=token_json.get("error_description", ""))

                # Decode the ID token (without signature verification for lab purposes)
                id_token = token_json.get("id_token", "")
                access_token = token_json.get("access_token", "")

                # Decode JWT payload (base64)
                parts = id_token.split(".")
                if len(parts) >= 2:
                    payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
                    claims = json.loads(base64.urlsafe_b64decode(payload_b64))
                else:
                    claims = {}

                # Optionally call the /me endpoint for richer profile data
                user_profile = {}
                if access_token:
                    try:
                        me_resp = requests.get(
                            "https://graph.microsoft.com/v1.0/me",
                            headers={"Authorization": f"Bearer {access_token}"},
                            timeout=10,
                        )
                        if me_resp.status_code == 200:
                            user_profile = me_resp.json()
                    except Exception:
                        pass

                # Log the SSO event
                try:
                    db = get_db()
                    db.execute(
                        """INSERT INTO activity_log
                           (timestamp, method, endpoint, status_code, action, target, detail)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (now_iso(), "GET", "/auth/callback", 200, "OIDC SSO Login",
                         claims.get("preferred_username", claims.get("email", "unknown")),
                         f"Scopes: {token_json.get('scope', '')}"),
                    )
                    db.commit()
                except Exception:
                    pass

                return render_template("sso_profile.html",
                    app_name=config["app_name"],
                    protocol="OIDC / OAuth 2.0",
                    name_id=claims.get("preferred_username", claims.get("email", "Unknown")),
                    name_id_format="preferred_username",
                    issuer=claims.get("iss", "Unknown"),
                    audience=claims.get("aud", "Unknown"),
                    status="Success",
                    attributes={
                        "name": claims.get("name", ""),
                        "email": claims.get("email", claims.get("preferred_username", "")),
                        "oid": claims.get("oid", ""),
                        "tid": claims.get("tid", ""),
                        "ver": claims.get("ver", ""),
                        **{k: v for k, v in user_profile.items()
                           if k in ("displayName", "givenName", "surname", "jobTitle", "department", "mail")},
                    },
                    raw_xml=json.dumps(claims, indent=2),
                )

            except Exception as e:
                return render_template("sso_error.html",
                    app_name=config["app_name"],
                    error="Token exchange failed",
                    detail=str(e))

    # -------------------------------------------------------------------
    # SSO Landing Page
    # -------------------------------------------------------------------

    @app.route("/login")
    def login_page():
        """SSO login landing page."""
        protocol = config.get("sso_protocol", "none")
        if protocol == "saml":
            login_url = "/saml/login"
        elif protocol == "oidc":
            login_url = "/auth/login"
        else:
            login_url = None
        return render_template("login.html",
            app_name=config["app_name"],
            protocol=protocol.upper(),
            login_url=login_url)

    return app


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SCIM 2.0 Service Provider")
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to YAML config file (default: config.yaml)",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    app = create_app(config)

    protocol = config.get("sso_protocol", "none").upper()
    print(f"\n{'='*60}")
    print(f"  {config['app_name']} — SSO + SCIM Service Provider")
    print(f"  SCIM endpoint:  http://localhost:{config['port']}/scim/v2")
    print(f"  SSO login:      http://localhost:{config['port']}/login")
    print(f"  Dashboard:      http://localhost:{config['port']}/dashboard")
    print(f"  Database:       {config['database']}")
    print(f"  Groups:         {'Enabled' if config.get('groups_enabled') else 'Disabled'}")
    print(f"  SSO Protocol:   {protocol}")
    print(f"{'='*60}\n")

    app.run(host="0.0.0.0", port=config["port"], debug=True)
