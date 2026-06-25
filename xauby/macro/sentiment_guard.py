import os
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from xauby.utils.atomic_io import atomic_json_write

def fetch_json_url(url, headers=None, method="GET", payload=None):
    """Utility to fetch JSON from a URL using requests."""
    if not headers:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    else:
        if "User-Agent" not in headers:
            headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            
    try:
        if method == "POST":
            r = requests.post(url, headers=headers, json=payload, timeout=15)
        else:
            r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None

def fetch_xml_url(url):
    """Utility to fetch XML from a URL using requests."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            return r.content
        return None
    except Exception:
        return None

def get_dxy_score():
    """
    Fetches DXY (US Dollar Index) daily chart data from Yahoo Finance.
    Computes a score based on DXY direction:
    - If DXY is rising, score is negative (Strong Dollar is bearish for Gold).
    - If DXY is falling, score is positive (Weak Dollar is bullish for Gold).
    """
    # DX-Y.NYB is the ticker for DXY
    url = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?range=5d&interval=1d"
    data = fetch_json_url(url)
    if not data:
        return 0.0, 0.0 # Neutral if failed
    
    try:
        result = data["chart"]["result"][0]
        closes = result["indicators"]["quote"][0]["close"]
        # Filter out None values
        closes = [c for c in closes if c is not None]
        if len(closes) < 2:
            return 0.0, 0.0
        
        current_price = closes[-1]
        prev_price = closes[-2]
        
        # Calculate 5-day trend / slope
        # Simple linear slope representation
        diffs = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        avg_diff = sum(diffs) / len(diffs)
        
        # Normalize score between -1.0 and 1.0
        # If DXY changes by +0.5% daily, that's a strong trend.
        # Let's base it on percentage change.
        pct_change = ((current_price - closes[0]) / closes[0]) * 100.0
        
        # We invert the score because DXY strength is bad for Gold
        # Cap score between -1.0 and 1.0
        score = -max(-1.0, min(1.0, pct_change * 2.0)) # 0.5% DXY change fully caps score at -1.0 / 1.0
        
        return float(score), float(current_price)
    except Exception:
        return 0.0, 0.0

def get_fred_score(api_key):
    """
    Fetches Fed Funds Rate (FEDFUNDS) from FRED API.
    Computes a score based on interest rate level and change.
    Rising rates or high rates are generally bearish for non-yielding Gold.
    """
    if not api_key:
        return 0.0, 0.0
        
    url = f"https://api.stlouisfed.org/fred/series/observations?series_id=FEDFUNDS&api_key={api_key}&file_type=json&sort_order=desc&limit=3"
    data = fetch_json_url(url)
    if not data:
        return 0.0, 0.0
        
    try:
        obs = data.get("observations", [])
        if len(obs) < 2:
            return 0.0, 0.0
            
        current_rate = float(obs[0]["value"])
        prev_rate = float(obs[1]["value"])
        
        rate_change = current_rate - prev_rate
        
        # If rate is rising: negative score
        # If rate is falling: positive score
        # Base score on change and level:
        # High rates (e.g. > 5%) add some baseline bearish weight (-0.2)
        baseline = -0.1 if current_rate > 4.5 else 0.0
        
        if rate_change > 0:
            change_score = -0.3
        elif rate_change < 0:
            change_score = 0.3
        else:
            change_score = 0.0
            
        score = max(-1.0, min(1.0, baseline + change_score))
        return float(score), current_rate
    except Exception:
        return 0.0, 0.0

# ── AI Provider Registry ─────────────────────────────────────────────────────
# Each provider entry defines: base_url, default model, and api format.
# "openai" format = OpenAI-compatible chat/completions (used by most providers).
# "anthropic" format = Anthropic Messages API (used by Claude).
AI_PROVIDERS = {
    "minimax":  {"base_url": "https://api.minimax.io/anthropic/v1",                    "model": "MiniMax-M2.7",       "format": "anthropic"},
    "openai":   {"base_url": "https://api.openai.com/v1",                              "model": "gpt-4o-mini",        "format": "openai"},
    "gemini":   {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.0-flash",   "format": "openai"},
    "claude":   {"base_url": "https://api.anthropic.com/v1",                            "model": "claude-sonnet-4-20250514",     "format": "anthropic"},
    "deepseek": {"base_url": "https://api.deepseek.com",                                "model": "deepseek-chat",      "format": "openai"},
    "qwen":     {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",       "model": "qwen-plus",          "format": "openai"},
}

def _call_openai_compatible(base_url, model, api_key, prompt):
    """Call any OpenAI-compatible chat/completions endpoint."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an expert commodities analyst. Respond only with valid JSON."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.3,
        "stream": False
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    res = fetch_json_url(url, headers=headers, method="POST", payload=payload)
    if not res:
        return None
    try:
        return res["choices"][0]["message"]["content"].strip()
    except Exception:
        return None

def _call_anthropic(base_url, model, api_key, prompt):
    """Call Anthropic Messages API (Claude)."""
    base = base_url.rstrip('/')
    if "api.minimax.io/anthropic" in base and not base.endswith("/v1"):
        base += "/v1"
    url = f"{base}/messages"
    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01"
    }
    res = fetch_json_url(url, headers=headers, method="POST", payload=payload)
    if not res:
        return None
    try:
        for item in res.get("content", []):
            if item.get("type") == "text":
                return item.get("text", "").strip()
        return res["content"][0]["text"].strip()
    except Exception:
        return None

def get_news_sentiment_score(
    api_key,
    provider="minimax",
    model=None,
    base_url=None,
    rss_url=None,
    rss_backup_url=None,
    asset_keyword="Gold",
):
    """
    Fetches the latest news headlines from RSS feed and scores them
    using a configurable AI provider (OpenAI, Gemini, Claude, MiniMax, DeepSeek, Qwen).
    """
    if not api_key:
        return 0.0, "AI API key missing", []

    # Resolve provider config
    provider_lower = provider.lower()
    preset = AI_PROVIDERS.get(provider_lower, AI_PROVIDERS["openai"])
    effective_model = model or preset["model"]
    effective_url = base_url or preset["base_url"]
    api_format = preset["format"]

    # Fetch configured or default RSS feed
    feed_url = rss_url or "https://www.forexlive.com/feed/gold"
    xml_data = fetch_xml_url(feed_url)
    if not xml_data:
        # Try backup RSS
        feed_url = rss_backup_url or "https://www.cnbc.com/id/10000115/device/rss/rss.html"
        xml_data = fetch_xml_url(feed_url)
        
    if not xml_data:
        return 0.0, f"Failed to fetch RSS feeds from {feed_url}", []
        
    headlines = []
    try:
        root = ET.fromstring(xml_data)
        for item in root.findall(".//item")[:8]: # Grab top 8 headlines
            title = item.find("title")
            if title is not None and title.text:
                headlines.append(title.text.strip())
    except Exception as e:
        return 0.0, f"Failed to parse RSS XML: {e}", []
        
    if not headlines:
        return 0.0, "No headlines found", []
        
    prompt = (
        f"You are an expert commodities analyst. Analyze the following {asset_keyword} market headlines "
        f"and determine the short-term sentiment score for {asset_keyword}.\n"
        "Return ONLY a raw JSON object (no markdown backticks, no wrap, just the raw JSON) in this exact format:\n"
        '{"sentiment": "bullish"|"bearish"|"neutral", "score": float_between_-1.0_and_1.0, "reason": "extremely_brief_3_to_5_words_reason"}\n\n'
        "The 'reason' field MUST be extremely brief (3 to 5 words maximum, e.g., 'Safe-haven buying' or 'USD strength pressures').\n\n"
        "Headlines:\n" + "\n".join(f"- {h}" for h in headlines)
    )
    
    # Call the appropriate API format
    if api_format == "anthropic":
        text = _call_anthropic(effective_url, effective_model, api_key, prompt)
    else:
        text = _call_openai_compatible(effective_url, effective_model, api_key, prompt)

    if not text:
        return 0.0, f"{provider} API request failed", headlines
        
    try:
        # Remove reasoning model <think>...</think> blocks (common in DeepSeek and MiniMax)
        if "<think>" in text:
            if "</think>" in text:
                text = text.split("</think>", 1)[1].strip()
            else:
                idx = text.find("{")
                if idx != -1:
                    text = text[idx:].strip()

        # Clean markdown wraps if the model ignored instructions
        if "```" in text:
            if "```json" in text:
                text = text.split("```json", 1)[1]
            elif "```" in text:
                text = text.split("```", 1)[1]
            if "```" in text:
                text = text.split("```", 1)[0]
            text = text.strip()
            
        parsed = json.loads(text)
        score = float(parsed.get("score", 0.0))
        reason = parsed.get("reason", "Success")
        return score, reason, headlines
    except Exception as e:
        return 0.0, f"Failed to parse {provider} response: {e}. Raw: {str(text)[:150]}", headlines

def evaluate_sentiment_guard(config):
    """
    Main entry point. Evaluates the sentiment guard, blending DXY, FRED, and News scores.
    Handles caching to prevent redundant API queries.
    """
    from xauby.runtime.paths import ensure_runtime_dir, sentiment_guard_state_path
    cache_file = sentiment_guard_state_path()
    guard_cfg = config.get("macro_sentiment_guard", {})
    interval_sec = int(guard_cfg.get("check_interval_hours", 4)) * 3600

    # Ensure runtime root exists
    ensure_runtime_dir()
    
    # 1. Check Cache
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            
            # If the last check had an API error, bypass the cache to retry immediately
            last_reason = cached_data.get("news_reason", "")
            has_error = any(err in last_reason.lower() for err in ["fail", "error", "missing", "bad request", "unauthorized"])
            
            if not has_error:
                cached_ts_str = cached_data.get("timestamp", "")
                if cached_ts_str:
                    cached_ts = datetime.fromisoformat(cached_ts_str.replace("Z", "+00:00"))
                    now_utc = datetime.now(timezone.utc)
                    candle_hour = (now_utc.hour // 4) * 4
                    current_candle_start = now_utc.replace(
                        hour=candle_hour, minute=0, second=0, microsecond=0
                    )
                    if cached_ts >= current_candle_start:
                        return cached_data
            else:
                cached_ts_str = cached_data.get("timestamp", "")
                if cached_ts_str:
                    cached_ts = datetime.fromisoformat(cached_ts_str.replace("Z", "+00:00"))
                    if (datetime.now(timezone.utc) - cached_ts).total_seconds() < 900:
                        return cached_data
        except Exception:
            pass
            
    # 2. Fetch new values
    dxy_score, dxy_val = 0.0, 0.0
    fred_score, fred_val = 0.0, 0.0
    news_score = 0.0
    news_reason = "Disabled"
    news_headlines = []
    
    # DXY Trend
    if guard_cfg.get("use_dxy", True):
        dxy_score, dxy_val = get_dxy_score()
        
    # FRED interest rates (requires API Key from environment)
    fred_key = os.environ.get("FRED_API_KEY", "")
    if guard_cfg.get("use_fred", False) and fred_key:
        fred_score, fred_val = get_fred_score(fred_key)
        
    # AI News Sentiment (supports multiple providers: Gemini, OpenAI, Claude, MiniMax, DeepSeek, Qwen)
    provider = guard_cfg.get("news_provider", "minimax").strip().lower()
    model = guard_cfg.get("news_model", "").strip() or None
    base_url = guard_cfg.get("news_base_url", "").strip() or None
    
    env_keys = {
        "minimax": ["MINIMAX_API_KEY", "AI_API_KEY"],
        "openai": ["OPENAI_API_KEY", "AI_API_KEY"],
        "gemini": ["GEMINI_API_KEY", "AI_API_KEY"],
        "claude": ["CLAUDE_API_KEY", "ANTHROPIC_API_KEY", "AI_API_KEY"],
        "deepseek": ["DEEPSEEK_API_KEY", "AI_API_KEY"],
        "qwen": ["QWEN_API_KEY", "DASHSCOPE_API_KEY", "AI_API_KEY"],
    }
    
    api_key = ""
    candidate_keys = env_keys.get(provider, [f"{provider.upper()}_API_KEY", "AI_API_KEY"])
    for k in candidate_keys:
        val = os.environ.get(k, "")
        if val:
            api_key = val
            break

    if guard_cfg.get("use_news", False):
        if api_key:
            rss_url = guard_cfg.get("news_rss_url")
            rss_backup_url = guard_cfg.get("news_rss_backup_url")
            asset_keyword = guard_cfg.get("news_asset_keyword", "Gold")
            news_score, news_reason, news_headlines = get_news_sentiment_score(
                api_key,
                provider=provider,
                model=model,
                base_url=base_url,
                rss_url=rss_url,
                rss_backup_url=rss_backup_url,
                asset_keyword=asset_keyword
            )
        else:
            news_reason = f"Missing API Key for {provider.upper()}"
        
    # 3. Blend Scores
    # Weights if all are active
    weights = {
        "dxy": 0.4 if guard_cfg.get("use_dxy", True) else 0.0,
        "fred": 0.2 if (guard_cfg.get("use_fred", False) and fred_key) else 0.0,
        "news": 0.4 if (guard_cfg.get("use_news", False) and api_key) else 0.0
    }
    
    total_weight = sum(weights.values())
    if total_weight > 0:
        # Normalize weights
        norm_weights = {k: v / total_weight for k, v in weights.items()}
        blended_score = (
            (dxy_score * norm_weights["dxy"]) +
            (fred_score * norm_weights["fred"]) +
            (news_score * norm_weights["news"])
        )
    else:
        blended_score = 0.0
        
    # Ensure boundaries
    blended_score = max(-1.0, min(1.0, blended_score))
    
    # 4. Generate combined reason
    # Shorten news reason to prevent terminal layout breaking
    short_news = news_reason
    if short_news and len(short_news) > 25:
        short_news = short_news[:22] + "..."
        
    reasons = []
    if weights["dxy"] > 0:
        reasons.append("USD Strong" if dxy_score < 0 else "USD Weak")
    if weights["fred"] > 0:
        reasons.append(f"Rates {fred_val:.1f}%")
    if weights["news"] > 0:
        reasons.append(f"News: {short_news}")
        
    reason_str = ", ".join(reasons) if reasons else "No active indicators"
    
    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "score": round(blended_score, 3),
        "dxy_score": round(dxy_score, 3),
        "dxy_price": round(dxy_val, 2),
        "fred_score": round(fred_score, 3),
        "fred_rate": round(fred_val, 2),
        "news_score": round(news_score, 3),
        "news_reason": news_reason,
        "summary": reason_str,
        "headlines": news_headlines[:5]
    }
    
    # 5. Write Cache
    try:
        atomic_json_write(cache_file, output, indent=2)
    except Exception:
        pass
        
    return output

