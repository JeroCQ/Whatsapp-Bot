"""Health check endpoint for Dashboard backend dependencies."""

import logging
from typing import Annotated, Any

import requests
from fastapi import APIRouter, Depends, Header, HTTPException

from config import config
from dashboard_api import admin_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(admin_auth)])


def _check_env() -> dict[str, Any]:
    """Check that all critical environment variables are present and non-empty."""
    required_vars = {
        "DASHBOARD_API_KEY": config.DASHBOARD_API_KEY,
        "SUPABASE_URL": config.SUPABASE_URL,
        "SUPABASE_SERVICE_ROLE_KEY": config.SUPABASE_KEY,
        "GITHUB_TOKEN": config.GITHUB_TOKEN or None,
        "GITHUB_OWNER": config.GITHUB_OWNER,
        "GITHUB_REPO": config.GITHUB_REPO,
        "GITHUB_BRANCH": config.GITHUB_BRANCH,
        "GEMINI_API_KEY": config.GEMINI_API_KEY,
    }
    
    missing = [name for name, value in required_vars.items() if not value]
    
    return {
        "ok": len(missing) == 0,
        "missing": missing,
    }


def _check_supabase() -> dict[str, Any]:
    """Check Supabase Storage connectivity and permissions."""
    try:
        base_url = (config.SUPABASE_URL or "").rstrip("/")
        bucket = config.CATALOG_STORAGE_BUCKET or "catalogos"
        
        # Check if bucket exists by listing objects (minimal check)
        url = f"{base_url}/storage/v1/object/list/{bucket}"
        headers = {
            "Authorization": f"Bearer {config.SUPABASE_KEY}",
            "apikey": config.SUPABASE_KEY or "",
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 401:
            return {
                "ok": False,
                "error": "Authentication failed",
                "bucket": bucket,
            }
        
        if response.status_code == 404:
            return {
                "ok": False,
                "error": "Bucket not found",
                "bucket": bucket,
            }
        
        if response.status_code >= 400:
            return {
                "ok": False,
                "error": f"Storage error: HTTP {response.status_code}",
                "bucket": bucket,
            }
        
        # Test write permission with a small probe file
        test_key = ".health-check"
        test_url = f"{base_url}/storage/v1/object/{bucket}/{test_key}"
        test_headers = headers.copy()
        test_headers["Content-Type"] = "text/plain"
        
        try:
            # Try to upload a probe file
            test_response = requests.post(
                test_url,
                headers=test_headers,
                data=b"ok",
                timeout=10,
            )
            can_write = test_response.status_code < 400
            
            # Clean up probe file if upload succeeded
            if can_write:
                try:
                    requests.delete(test_url, headers=headers, timeout=10)
                except Exception as e:
                    logger.warning(f"Could not clean up health check probe: {e}")
        except Exception as e:
            logger.warning(f"Write permission test failed: {e}")
            can_write = False
        
        return {
            "ok": True,
            "bucket": bucket,
            "can_write": can_write,
        }
    
    except requests.Timeout:
        return {
            "ok": False,
            "error": "Connection timeout",
        }
    except requests.RequestException as e:
        return {
            "ok": False,
            "error": f"Request error: {type(e).__name__}",
        }
    except Exception as e:
        logger.error(f"Supabase health check error: {e}")
        return {
            "ok": False,
            "error": "Unknown error",
        }


def _check_github() -> dict[str, Any]:
    """Check GitHub repository and branch existence."""
    try:
        api_root = "https://api.github.com"
        repo_url = f"{api_root}/repos/{config.GITHUB_OWNER}/{config.GITHUB_REPO}"
        
        headers = {
            "Authorization": f"Bearer {config.GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        
        # Check if repo exists
        repo_response = requests.get(repo_url, headers=headers, timeout=10)
        
        if repo_response.status_code == 404:
            return {
                "ok": False,
                "error": "Repository not found",
                "repo": f"{config.GITHUB_OWNER}/{config.GITHUB_REPO}",
            }
        
        if repo_response.status_code == 401:
            return {
                "ok": False,
                "error": "GitHub authentication failed",
                "repo": f"{config.GITHUB_OWNER}/{config.GITHUB_REPO}",
            }
        
        if repo_response.status_code >= 400:
            return {
                "ok": False,
                "error": f"GitHub error: HTTP {repo_response.status_code}",
                "repo": f"{config.GITHUB_OWNER}/{config.GITHUB_REPO}",
            }
        
        # Check if branch exists
        branch_url = f"{repo_url}/branches/{config.GITHUB_BRANCH}"
        branch_response = requests.get(branch_url, headers=headers, timeout=10)
        
        branch_exists = branch_response.status_code == 200
        
        if not branch_exists and branch_response.status_code != 404:
            return {
                "ok": False,
                "error": f"Branch check failed: HTTP {branch_response.status_code}",
                "repo": f"{config.GITHUB_OWNER}/{config.GITHUB_REPO}",
                "branch": config.GITHUB_BRANCH,
            }
        
        # Check if SI file exists in the configured branch
        # Build path for current BUSINESS_ID
        si_path = f"src/clients/{config.BUSINESS_ID}/system_instruction.txt"
        file_url = f"{repo_url}/contents/{si_path}"
        
        file_response = requests.get(
            file_url,
            headers=headers,
            params={"ref": config.GITHUB_BRANCH},
            timeout=10,
        )
        
        si_exists = file_response.status_code == 200
        
        return {
            "ok": branch_exists,
            "repo": f"{config.GITHUB_OWNER}/{config.GITHUB_REPO}",
            "branch": config.GITHUB_BRANCH,
            "branch_exists": branch_exists,
            "si_path": si_path,
            "si_path_exists": si_exists,
        }
    
    except requests.Timeout:
        return {
            "ok": False,
            "error": "Connection timeout",
        }
    except requests.RequestException as e:
        return {
            "ok": False,
            "error": f"Request error: {type(e).__name__}",
        }
    except Exception as e:
        logger.error(f"GitHub health check error: {e}")
        return {
            "ok": False,
            "error": "Unknown error",
        }


def _check_gemini() -> dict[str, Any]:
    """Test Gemini API connectivity with a minimal generation call."""
    try:
        from google import genai
        from google.genai import errors, types
        
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        model_name = config.GEMINI_DASHBOARD_MODEL or "gemini-1.5-flash"
        
        # Minimal test: generate "ok"
        response = client.models.generate_content(
            model=model_name,
            contents="Reply with only: ok",
            config=types.GenerateContentConfig(max_output_tokens=10),
        )
        
        return {
            "ok": bool(response.text),
            "model": model_name,
            "response_length": len(response.text or ""),
        }
    
    except ImportError:
        return {
            "ok": False,
            "error": "Google GenAI library not installed",
        }
    except Exception as e:
        error_msg = str(e)
        # Sanitize error message to avoid exposing API key
        if "api_key" in error_msg.lower():
            error_msg = "Invalid or expired API key"
        
        logger.error(f"Gemini health check error: {e}")
        return {
            "ok": False,
            "error": error_msg[:100] if error_msg else "Unknown error",
            "model": config.GEMINI_DASHBOARD_MODEL or "gemini-1.5-flash",
        }


def _check_api_key(x_dashboard_api_key: str | None) -> dict[str, Any]:
    """Verify the X-Dashboard-API-Key header matches the configured key."""
    import hmac
    
    expected = config.DASHBOARD_API_KEY or ""
    provided = x_dashboard_api_key or ""
    
    matches = bool(expected) and bool(provided) and hmac.compare_digest(expected, provided)
    
    return {
        "ok": matches,
    }


@router.get("/dashboard-health")
async def dashboard_health(x_dashboard_api_key: str | None = Header(None)):
    """
    Health check endpoint for the Memo's dashboard backend.
    
    Verifies all critical dependencies:
    - Environment variables
    - Supabase Storage connectivity and permissions
    - GitHub repository and branch
    - Gemini API
    - Dashboard API key
    
    Requires: X-Dashboard-API-Key header
    
    Returns:
    {
        "ok": true/false,
        "checks": {
            "env": {...},
            "supabase": {...},
            "github": {...},
            "gemini": {...},
            "api_key": {...}
        }
    }
    """
    # Perform all checks
    env_check = _check_env()
    supabase_check = _check_supabase()
    github_check = _check_github()
    gemini_check = _check_gemini()
    api_key_check = _check_api_key(x_dashboard_api_key)
    
    # Determine overall status
    all_ok = all([
        env_check.get("ok", False),
        supabase_check.get("ok", False),
        github_check.get("ok", False),
        gemini_check.get("ok", False),
        api_key_check.get("ok", False),
    ])
    
    return {
        "ok": all_ok,
        "checks": {
            "env": env_check,
            "supabase": supabase_check,
            "github": github_check,
            "gemini": gemini_check,
            "api_key": api_key_check,
        }
    }

