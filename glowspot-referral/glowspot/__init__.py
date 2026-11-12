"""Glowspot Cosmetics referral system - application factory."""
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

import click
from flask import Flask, abort, g, render_template, request, session
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash

from . import db as dbm
from . import services as svc


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    prod = os.environ.get("APP_ENV", "development") == "production"
    os.makedirs(app.instance_path, exist_ok=True)

    secret = os.environ.get("SECRET_KEY")
    if not secret:
        if prod:
            raise RuntimeError("SECRET_KEY environment variable must be set when APP_ENV=production.")
        keyfile = os.path.join(app.instance_path, "secret_key")
        if not os.path.exists(keyfile):
            with open(keyfile, "w") as fh:
                fh.write(secrets.token_hex(32))
            os.chmod(keyfile, 0o600)
        with open(keyfile) as fh:
            secret = fh.read().strip()

    app.config.update(
        SECRET_KEY=secret,
        DATABASE=os.environ.get("DATABASE_PATH", os.path.join(app.instance_path, "glowspot.db")),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=prod,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        MAX_CONTENT_LENGTH=1024 * 1024,
        PRODUCTION=prod,
        TEST_BANNER=os.environ.get("TEST_BANNER", ""),
    )
    if config:
        app.config.update(config)
    if os.environ.get("TRUST_PROXY") == "1":
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    app.teardown_appcontext(dbm.close_db)
    _bootstrap(app)

    # ------------------------------------------------------------ request hooks
    @app.before_request
    def load_user_and_check_csrf():
        if request.endpoint == "static":
            return
        g.user = None
        uid = session.get("uid")
        if uid:
            u = dbm.get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if u and u["active"]:
                g.user = u
            else:
                session.clear()
        if request.method == "POST":
            token, sent = session.get("csrf", ""), request.form.get("csrf_token", "")
            if not token or not hmac.compare_digest(token, sent):
                abort(400)

    @app.after_request
    def security_headers(resp):
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
        if app.config["PRODUCTION"]:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.endpoint != "static":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.context_processor
    def inject():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return {"csrf_token": session["csrf"], "METHODS": svc.METHODS, "banner": app.config["TEST_BANNER"],
                "today": svc.today_eat().isoformat()}

    # ------------------------------------------------------------ filters
    @app.template_filter("tzs")
    def tzs(n):
        return "-" if n is None else (f"-TZS {abs(int(n)):,}" if int(n) < 0 else f"TZS {int(n):,}")

    @app.template_filter("num")
    def num(n):
        return f"{int(n):,}"

    @app.template_filter("phone")
    def phone(p):
        return svc.format_phone(p)

    @app.template_filter("dt")
    def dt(s):
        if not s:
            return ""
        d = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone(svc.EAT)
        return d.strftime("%d %b %Y %H:%M")

    @app.template_filter("dmy")
    def dmy(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").strftime("%d %b %Y")
        except (TypeError, ValueError):
            return s or ""

    # ------------------------------------------------------------ errors
    def _err(code, title, msg):
        return render_template("error.html", code=code, title=title, msg=msg), code

    app.register_error_handler(400, lambda e: _err(400, "Please try again", "Your page expired or the request was not valid. Go back, refresh the page and try again."))
    app.register_error_handler(403, lambda e: _err(403, "Not allowed", "Your account does not have permission to open this page."))
    app.register_error_handler(404, lambda e: _err(404, "Page not found", "That page does not exist."))
    app.register_error_handler(413, lambda e: _err(413, "Too large", "That request was too large."))
    app.register_error_handler(500, lambda e: _err(500, "Something went wrong", "The problem has been logged. Please try again, or tell the manager."))

    from .views import bp
    app.register_blueprint(bp)
    _register_cli(app)
    return app


def _bootstrap(app):
    """Create the database, and optionally the first manager / demo data from environment variables."""
    conn = dbm.connect(app.config["DATABASE"])
    try:
        dbm.init_db(conn)
        if conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0:
            pw = os.environ.get("INITIAL_ADMIN_PASSWORD")
            if pw:
                username = os.environ.get("INITIAL_ADMIN_USERNAME", "admin")
                svc.validate_password(pw, username)
                conn.execute(
                    "INSERT INTO users(username,full_name,password_hash,role,active,must_change_password,created_at)"
                    " VALUES(?,?,?,?,1,1,?)",
                    (username, os.environ.get("INITIAL_ADMIN_NAME", "Shop Manager"), generate_password_hash(pw), "manager", svc.now_utc()))
                conn.commit()
        if os.environ.get("SEED_DEMO") == "1":
            from .demo import seed_demo
            seed_demo(conn)
    finally:
        conn.close()


def _register_cli(app):
    @app.cli.command("init-db")
    def init_db_cmd():
        """Create tables and default settings."""
        conn = dbm.connect(app.config["DATABASE"])
        dbm.init_db(conn)
        conn.close()
        click.echo("Database ready.")

    @app.cli.command("create-manager")
    @click.option("--username", prompt=True)
    @click.option("--name", prompt="Full name")
    @click.password_option()
    def create_manager(username, name, password):
        """Create a manager account (use this to set up the first administrator)."""
        conn = dbm.connect(app.config["DATABASE"])
        try:
            svc.validate_password(password, username)
            conn.execute(
                "INSERT INTO users(username,full_name,password_hash,role,active,must_change_password,created_at)"
                " VALUES(?,?,?,?,1,0,?)", (username, name, generate_password_hash(password), "manager", svc.now_utc()))
            conn.commit()
            click.echo("Manager created.")
        except Exception as e:  # noqa: BLE001 - show any problem to the operator
            raise click.ClickException(str(e))
        finally:
            conn.close()

    @app.cli.command("seed-demo")
    def seed_demo_cmd():
        """Add clearly-labelled synthetic demo data (for the temporary test site only)."""
        from .demo import seed_demo
        conn = dbm.connect(app.config["DATABASE"])
        click.echo(seed_demo(conn))
        conn.close()
