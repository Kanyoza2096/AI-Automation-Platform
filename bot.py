"""
Kanyoza Systems Bot v9.0
Unified Messenger Bot + Code Guardian + Auto-Fix + Full REST API.
"""

import sys
import signal
import tempfile
import shutil
import logging
from pathlib import Path
from datetime import datetime, timezone

from flask import Flask, jsonify

from config.settings import settings
from config.logging_config import setup_logging

setup_logging(settings.log_level)
logger = logging.getLogger("kanyoza")

supabase_client = None

if settings.supabase_enabled:
    try:
        from supabase import create_client
        supabase_client = create_client(settings.supabase_url, settings.supabase_anon_key)
        logger.info("Supabase client initialized")
    except ImportError:
        logger.warning("supabase-py not installed.")
    except Exception as e:
        logger.error(f"Failed to initialize Supabase: {e}")

from core.memory import memory

if supabase_client:
    memory.supabase = supabase_client
    logger.info("Memory connected to Supabase")
else:
    logger.warning("Memory running in ephemeral mode")

app = Flask(__name__)

from routes.webhook import register_webhook_routes
from routes.api import register_api_routes, set_guardian_scheduler

register_webhook_routes(app)
register_api_routes(app)

from scheduler.post_scheduler import scheduler as post_scheduler

post_scheduler.supabase = supabase_client
post_scheduler.start()

guardian_scheduler = None

if settings.guardian_configured:
    try:
        from guardian.github_client import GitHubClient
        from guardian.scanner import CodeGuardianScanner
        from guardian.auto_fix import auto_fix
        from services.facebook import facebook as fb_service

        github = GitHubClient(settings.github_username, settings.github_token)
        scanner = CodeGuardianScanner()

        def run_guardian_scan():
            logger.info("🛡️  Code Guardian: Starting scan...")
            temp_dir = tempfile.mkdtemp(prefix="guardian_")
            try:
                repos = github.get_all_repos(exclude=settings.guardian_exclude_repos)
                if not repos:
                    return
                repo_paths = []
                for repo in repos:
                    try:
                        repo_paths.append({"name": repo["name"], "local_path": github.clone_or_pull(repo, temp_dir)})
                    except Exception as e:
                        logger.error(f"Failed to clone {repo['name']}: {e}")

                total_files = 0
                for rp in repo_paths:
                    all_files = list(Path(rp["local_path"]).rglob("*"))
                    exclude = {'venv', '.venv', 'env', '__pycache__', 'node_modules', '.git', 'dist', 'build'}
                    total_files += len([f for f in all_files if f.is_file() and not any(e in f.parts for e in exclude)])

                issues = scanner.scan_repos(repo_paths)
                report = scanner.build_report(issues, len(repos), total_files)

                if auto_fix.is_configured and report["total_issues"] > 0:
                    try:
                        for repo in repos:
                            repo_full = f"{settings.github_username}/{repo['name']}"
                            repo_issues = [i for i in report["issues"] if i.get("repo") == repo["name"]]
                            if repo_issues:
                                fix_results = auto_fix.auto_fix_all(repo_full, repo_issues)
                                if fix_results["fixed"] > 0:
                                    logger.info(f"🔧 Auto-fixed {fix_results['fixed']} issues in {repo['name']}")
                    except Exception as e:
                        logger.error(f"Auto-fix error: {e}")

                if settings.owner_psid:
                    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                    if report["total_issues"] == 0:
                        msg = f"🛡️ Code Guardian Report\n\n📅 {now}\n📂 {report['repos_scanned']} repos | {report['files_scanned']} files\n\n✅ ALL CLEAR"
                    else:
                        msg = f"🛡️ Code Guardian Report\n\n📅 {now}\n📂 {report['repos_scanned']} repos | {report['files_scanned']} files\n\n🔴 Critical: {report['critical']} 🟠 High: {report['high']} 🟡 Medium: {report['medium']}\n⚠️ {report['total_issues']} issues"
                    fb_service.send_message(settings.owner_psid, msg)

                logger.info(f"🛡️ Guardian complete: {report['total_issues']} issues")
            except Exception as e:
                logger.error(f"Guardian scan failed: {e}")
            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        from scheduler.scan_scheduler import ScanScheduler
        guardian_scheduler = ScanScheduler(run_guardian_scan, settings.guardian_scan_hours)
        guardian_scheduler.start()
        set_guardian_scheduler(guardian_scheduler)
        logger.info(f"🛡️ Code Guardian started (every {settings.guardian_scan_hours}h)")
    except ImportError as e:
        logger.warning(f"Guardian module not available: {e}")
else:
    logger.info("Code Guardian disabled")


def handle_shutdown(signum, frame):
    logger.info("Shutting down...")
    post_scheduler.stop(timeout=30)
    if guardian_scheduler:
        guardian_scheduler.stop()
    from services.facebook import facebook
    from services.gemini import gemini
    from services.news_scraper import news_scraper
    gemini.close()
    facebook.close()
    news_scraper.close()
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)


@app.route("/")
def home():
    return jsonify({"service": "Kanyoza Systems Bot", "version": "9.0", "status": "operational"})

@app.route("/health")
def health_check():
    s = post_scheduler.get_status()
    return jsonify({"status": "healthy", "version": "9.0", "scheduler": {"running": s["running"], "last_post": s["last_post"]}})


logger.info("=" * 50)
logger.info("KANYOZA SYSTEMS BOT v9.0")
logger.info(f"Gemini: {'✅' if settings.gemini_key else '❌'}")
logger.info(f"Facebook: {'✅' if settings.is_configured else '❌'}")
logger.info(f"Supabase: {'✅' if supabase_client else '❌'}")
logger.info("=" * 50)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=settings.port, threaded=True)  # nosec
