"""
Guardian Auto-Fix Engine v1.0
- Automatically creates GitHub Pull Requests for fixable security issues
- Uses Gemini to generate the fix code
- Creates clean, descriptive PRs with before/after comparison
"""

import logging
import requests
import base64
from typing import Optional

from config.settings import settings

logger = logging.getLogger("kanyoza.guardian.auto_fix")


class AutoFixEngine:
    """Automatically fixes security issues and creates GitHub Pull Requests."""

    def __init__(self):
        self.github_token = getattr(settings, 'github_token', '')
        self.api_base = "https://api.github.com"

    @property
    def is_configured(self) -> bool:
        """Check if GitHub token is available."""
        return bool(self.github_token)

    def _headers(self) -> dict:
        """Standard GitHub API headers."""
        return {
            "Authorization": f"token {self.github_token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Kanyoza-Guardian",
        }

    # ------------------------------------------------------------------
    # DETECTION
    # ------------------------------------------------------------------

    def can_auto_fix(self, issue: dict) -> bool:
        """
        Check if an issue can be automatically fixed.
        Only certain issue types have reliable auto-fixes.
        """
        auto_fixable_patterns = [
            'Hardcoded API Key',
            'Hardcoded Password',
            'Hardcoded Token',
            'Hardcoded Secret Key',
            'Hardcoded Access Token',
            'Bare except clause',
            'Mutable default argument',
        ]

        message = issue.get('message', '')
        return any(pattern in message for pattern in auto_fixable_patterns)

    # ------------------------------------------------------------------
    # FIX GENERATION
    # ------------------------------------------------------------------

    def generate_fix(self, issue: dict, original_code: str) -> Optional[str]:
        """
        Use Gemini to generate a fix for the issue.
        Returns the fixed code or None if generation fails.
        """
        try:
            from services.gemini import gemini

            prompt = (
                "You are an expert security engineer. Fix this code issue.\n\n"
                f"ISSUE: {issue.get('message', '')}\n"
                f"FILE: {issue.get('file', '')}\n"
                f"LINE: {issue.get('line', '')}\n"
                f"RECOMMENDATION: {issue.get('recommendation', '')}\n\n"
                f"CURRENT CODE:\n```\n{original_code}\n```\n\n"
                "Return ONLY the complete fixed code. "
                "Do NOT include explanations, markdown, or code fences. "
                "Just the corrected code. Keep all other lines unchanged."
            )

            fixed_code = gemini._call_gemini(
                prompt=prompt,
                temperature=0.2,
                max_output_tokens=2000,
                top_p=0.9,
                timeout=30,
            )

            if fixed_code:
                fixed_code = fixed_code.strip()
                # Remove any code fences Gemini might add
                fixed_code = fixed_code.replace("```", "").strip()
                if len(fixed_code) > 10:
                    return fixed_code

            return None

        except Exception as e:
            logger.error(f"Failed to generate fix: {e}")
            return None

    # ------------------------------------------------------------------
    # LANGUAGE DETECTION
    # ------------------------------------------------------------------

    def _detect_language(self, file_path: str) -> str:
        """Detect programming language from file extension."""
        ext = file_path.split('.')[-1].lower() if '.' in file_path else ''

        language_map = {
            'py': 'python',
            'js': 'javascript',
            'ts': 'typescript',
            'jsx': 'jsx',
            'tsx': 'tsx',
            'json': 'json',
            'yaml': 'yaml',
            'yml': 'yaml',
            'sh': 'bash',
            'bash': 'bash',
            'sql': 'sql',
            'html': 'html',
            'css': 'css',
            'java': 'java',
            'go': 'go',
            'rs': 'rust',
            'rb': 'ruby',
            'php': 'php',
            'swift': 'swift',
            'kt': 'kotlin',
        }

        return language_map.get(ext, '')

    # ------------------------------------------------------------------
    # GITHUB PR CREATION
    # ------------------------------------------------------------------

    def create_fix_pr(
        self,
        repo_full_name: str,
        issue: dict,
        file_path: str,
        fixed_content: str
    ) -> Optional[str]:
        """
        Create a GitHub Pull Request with the fix.

        Steps:
        1. Get the default branch
        2. Create a new branch
        3. Commit the fix to the new branch
        4. Create a Pull Request

        Returns the PR URL or None if it fails.
        """
        if not self.is_configured:
            logger.warning("Auto-fix: GitHub token not configured")
            return None

        try:
            # Step 1: Get repository info
            repo_url = f"{self.api_base}/repos/{repo_full_name}"
            repo_response = requests.get(repo_url, headers=self._headers(), timeout=10)

            if repo_response.status_code != 200:
                logger.error(f"Failed to get repo: {repo_response.status_code}")
                return None

            repo_data = repo_response.json()
            default_branch = repo_data.get('default_branch', 'main')
            owner_login = repo_data.get('owner', {}).get('login', '')

            # Step 2: Get the current file to get its SHA
            file_url = f"{repo_url}/contents/{file_path}"
            file_response = requests.get(file_url, headers=self._headers(), timeout=10)

            if file_response.status_code != 200:
                logger.error(f"Failed to get file: {file_response.status_code}")
                return None

            file_data = file_response.json()
            file_sha = file_data.get('sha', '')

            # Step 3: Get the latest commit SHA on the default branch
            ref_url = f"{repo_url}/git/ref/heads/{default_branch}"
            ref_response = requests.get(ref_url, headers=self._headers(), timeout=10)

            if ref_response.status_code != 200:
                logger.error(f"Failed to get ref: {ref_response.status_code}")
                return None

            base_sha = ref_response.json().get('object', {}).get('sha', '')

            # Step 4: Create a new blob with the fixed content
            blob_url = f"{repo_url}/git/blobs"
            blob_response = requests.post(
                blob_url,
                headers=self._headers(),
                json={
                    "content": fixed_content,
                    "encoding": "utf-8",
                },
                timeout=10,
            )

            if blob_response.status_code != 201:
                logger.error(f"Failed to create blob: {blob_response.status_code}")
                return None

            new_blob_sha = blob_response.json().get('sha', '')

            # Step 5: Create a new tree
            tree_url = f"{repo_url}/git/trees"
            tree_response = requests.post(
                tree_url,
                headers=self._headers(),
                json={
                    "base_tree": base_sha,
                    "tree": [{
                        "path": file_path,
                        "mode": "100644",
                        "type": "blob",
                        "sha": new_blob_sha,
                    }],
                },
                timeout=10,
            )

            if tree_response.status_code != 201:
                logger.error(f"Failed to create tree: {tree_response.status_code}")
                return None

            new_tree_sha = tree_response.json().get('sha', '')

            # Step 6: Create a new commit
            commit_message = f"🔒 Security Fix: {issue.get('message', 'Auto-fix')[:70]}"
            commit_url = f"{repo_url}/git/commits"
            commit_response = requests.post(
                commit_url,
                headers=self._headers(),
                json={
                    "message": commit_message,
                    "tree": new_tree_sha,
                    "parents": [base_sha],
                },
                timeout=10,
            )

            if commit_response.status_code != 201:
                logger.error(f"Failed to create commit: {commit_response.status_code}")
                return None

            new_commit_sha = commit_response.json().get('sha', '')

            # Step 7: Create a new branch
            branch_name = f"guardian-fix-{file_path.replace('/', '-')[:40]}-line{issue.get('line', '')}"
            branch_url = f"{repo_url}/git/refs"

            branch_response = requests.post(
                branch_url,
                headers=self._headers(),
                json={
                    "ref": f"refs/heads/{branch_name}",
                    "sha": new_commit_sha,
                },
                timeout=10,
            )

            if branch_response.status_code not in [201, 422]:
                # 422 means branch already exists, try with random suffix
                import random
                branch_name = f"{branch_name}-{random.randint(100, 999)}"
                branch_response = requests.post(
                    branch_url,
                    headers=self._headers(),
                    json={
                        "ref": f"refs/heads/{branch_name}",
                        "sha": new_commit_sha,
                    },
                    timeout=10,
                )

            # Step 8: Create the Pull Request
            language = self._detect_language(file_path)
            pr_title = f"🛡️ Guardian Auto-Fix: {issue.get('message', 'Security Issue')[:80]}"
            pr_body = (
                f"## 🔒 Code Guardian Auto-Fix\n\n"
                f"**Issue:** {issue.get('message', '')}\n"
                f"**File:** `{file_path}` (line {issue.get('line', '')})\n"
                f"**Severity:** {issue.get('severity', 'unknown').upper()}\n"
                f"**Recommendation:** {issue.get('recommendation', '')}\n\n"
                f"---\n\n"
                f"### 📝 What Changed\n"
                f"This Pull Request was automatically generated by "
                f"[Kanyoza Code Guardian](https://kanyoza-systems-bot.onrender.com).\n\n"
                f"The fix replaces hardcoded credentials or fixes code anti-patterns "
                f"with secure alternatives.\n\n"
                f"### ✅ Checklist\n"
                f"- [ ] Review the changes\n"
                f"- [ ] Verify the fix doesn't break anything\n"
                f"- [ ] Merge when ready\n\n"
                f"---\n"
                f"*Generated by Kanyoza Systems Bot v7.0*"
            )

            pr_url = f"{repo_url}/pulls"
            pr_response = requests.post(
                pr_url,
                headers=self._headers(),
                json={
                    "title": pr_title,
                    "body": pr_body,
                    "head": branch_name,
                    "base": default_branch,
                },
                timeout=10,
            )

            if pr_response.status_code == 201:
                pr_html_url = pr_response.json().get('html_url', '')
                logger.info(f"✅ Auto-fix PR created: {pr_html_url}")
                return pr_html_url
            else:
                logger.error(f"PR creation failed: {pr_response.status_code} {pr_response.text[:300]}")
                return None

        except Exception as e:
            logger.error(f"Auto-fix PR creation error: {e}")
            return None

    # ------------------------------------------------------------------
    # BATCH AUTO-FIX
    # ------------------------------------------------------------------

    def auto_fix_all(self, repo_full_name: str, issues: list) -> dict:
        """
        Attempt to auto-fix all fixable issues in a scan.
        Returns summary of created PRs.
        """
        results = {
            'total': len(issues),
            'fixable': 0,
            'fixed': 0,
            'failed': 0,
            'pr_urls': [],
        }

        for issue in issues:
            if not self.can_auto_fix(issue):
                continue

            results['fixable'] += 1
            file_path = issue.get('file', '')
            snippet = issue.get('snippet', '')

            if not file_path or not snippet:
                results['failed'] += 1
                continue

            # Generate the fix
            fixed_code = self.generate_fix(issue, snippet)
            if not fixed_code:
                results['failed'] += 1
                continue

            # Create the PR
            pr_url = self.create_fix_pr(repo_full_name, issue, file_path, fixed_code)
            if pr_url:
                results['fixed'] += 1
                results['pr_urls'].append(pr_url)
            else:
                results['failed'] += 1

        logger.info(f"Auto-fix complete: {results}")
        return results


# Singleton
auto_fix = AutoFixEngine()
