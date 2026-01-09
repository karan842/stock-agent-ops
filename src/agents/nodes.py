"""
Agent nodes for: 
- performance analysis
- market sentiment
- final Bloomberg-style report
"""
from datetime import datetime
import os
from dotenv import load_dotenv
from langchain_core.messages import SystemMessage, AIMessage
import re

load_dotenv()

from src.agents.tools import get_stock_predictions, get_stock_news, TOOLS_LIST
from logger.logger import get_logger

logger = get_logger()


# LLM setup (AWS Bedrock - Llama 3 70B)
try:
    from langchain_aws import ChatBedrock

    # Using OpenAI GPT-OSS 20B on Bedrock
    llm = ChatBedrock(
        model_id="openai.gpt-oss-20b-1:0",
        model_kwargs={"temperature": 0.3},
        region_name=os.getenv("AWS_REGION"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY")
    ).bind_tools(TOOLS_LIST)

except Exception as e:
    err_msg = str(e)
    print(f"⚠️ LLM Init Error: {err_msg}")
    class Mock:
        def invoke(self, *args, **kwargs):
            return AIMessage(content=f"Mock response: LLM unavailable. Error: {err_msg}")
    llm = Mock()


def clean_content(text: str) -> str:
    """Removes <reasoning>...</reasoning> tags from the output."""
    cleanup = re.sub(r'<reasoning>.*?</reasoning>', '', text, flags=re.DOTALL)
    return cleanup.strip()


# --------------------------------------------------------------------------
# PERFORMANCE ANALYST
# --------------------------------------------------------------------------
def performance_analyst_node(state: dict) -> dict:
    ticker = state["ticker"]
    predictions = state.get("predictions")
    logger.info(f"📈 [Agent: Performance] Analyzing trends for {ticker}")

    if predictions == "__MODEL_TRAINING__":
        logger.warning(f"⚠️ [Agent: Performance] Model for {ticker} is still training.")
        return {
            "messages": [AIMessage(content=f"Model for {ticker} is currently training.")],
            "predictions": predictions
        }
    
    prompt = f"""
    You are a Performance Analyst. Analyze the 7-day price forecast for {ticker}.
    DATA:
    {predictions}
    
    Give a concise 2-3 line summary of the projected trend (Bullish/Bearish/Side-ways) and the price range.
    """
    resp = llm.invoke([SystemMessage(content=prompt)])
    content = resp.content if hasattr(resp, "content") else str(resp)
    content = clean_content(content)
    if hasattr(resp, "content"):
        resp.content = content
    logger.info(f"DEBUG: Perf Output: {content[:100]}...")
    
    return {
        "messages": [resp],
        "predictions": predictions
    }


# --------------------------------------------------------------------------
# MARKET EXPERT
# --------------------------------------------------------------------------
def market_expert_node(state: dict) -> dict:
    ticker = state["ticker"]
    logger.info(f"🗞️ [Agent: Market Expert] Fetching and summarizing news for {ticker}")
    news = get_stock_news(ticker)

    prompt = f"""
You are a market strategist summarizing sentiment.
News:

{news}

Return a 3–5 line sentiment summary.
"""
    resp = llm.invoke([SystemMessage(content=prompt)])
    content = resp.content if hasattr(resp, "content") else str(resp)
    content = clean_content(content)
    if hasattr(resp, "content"):
        resp.content = content
    logger.info(f"DEBUG: News Output: {content[:100]}...")

    return {
        "messages": [resp],
        "news_sentiment": news
    }


# --------------------------------------------------------------------------
# REPORT GENERATOR
# --------------------------------------------------------------------------
def report_generator_node(state: dict) -> dict:
    ticker = state["ticker"]
    logger.info(f"📝 [Agent: Report Gen] Assembling Bloomberg-style report for {ticker}")
    predictions = state.get("predictions", "")
    news = state.get("news_sentiment", "")

    prompt = f"""
Write a clean Bloomberg-style markdown report.

PREDICTIONS:
{predictions}

NEWS:
{news}

End with: **Market Stance:** BULLISH/BEARISH/NEUTRAL | **Confidence:** High/Medium/Low
"""
    resp = llm.invoke([SystemMessage(content=prompt)])
    text = resp.content if hasattr(resp, "content") else str(resp)
    text = clean_content(text)
    if hasattr(resp, "content"):
        resp.content = text
    logger.info(f"DEBUG: Report Gen Output: {text[:100]}...")

    # Extract stance
    upper = text.upper()
    stance = (
        "BULLISH" if "BULLISH" in upper else
        "BEARISH" if "BEARISH" in upper else
        "NEUTRAL"
    )

    # Confidence
    confidence = (
        "High" if "HIGH" in upper else
        "Low" if "LOW" in upper else
        "Medium"
    )

    return {
        "messages": [resp],
        "final_report": text,
        "recommendation": stance,
        "confidence": confidence
    }


# --------------------------------------------------------------------------
# CRITIC NODE
# --------------------------------------------------------------------------
def critic_node(state: dict) -> dict:
    """
    Criticizes and refines the report. 
    It checks for consistency between predictions and recommendation.
    """
    ticker = state.get("ticker", "N/A")
    logger.info(f"⚖️ [Agent: Critic] Reviewing and refining report for {ticker}")
    current_report = state.get("final_report", "")
    predictions = state.get("predictions", "")
    
    prompt = f"""
    You are a Senior Editor. critique and refine this financial report.
    
    DATA:
    {predictions}
    
    DRAFT REPORT:
    {current_report}
    
    Your Job:
    1. Verify if the 'Market Stance' aligns with the data.
    2. Ensure the tone is professional (Bloomberg style).
    3. If everything is good, just output the Original Report.
    4. If there are issues, rewrite it to be better.
    
    Output ONLY the Final Report (whether original or improved).
    """
    
    resp = llm.invoke([SystemMessage(content=prompt)])
    final_text = resp.content if hasattr(resp, "content") else str(resp)
    final_text = clean_content(final_text)
    if hasattr(resp, "content"):
        resp.content = final_text
    logger.info(f"DEBUG: Critic Output: {final_text[:100]}...")

    # We treat the critic's output as the definitive 'final_report'
    return {
        "messages": [resp],
        "final_report": final_text
    }
