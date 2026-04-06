# services/logic_service.py

from superpowers import analyze_portfolio as sp_analyze
from superpowers import suggest_rebalance as sp_rebalance

def analyze_portfolio(data):
    return sp_analyze(data)

def suggest_rebalance(data, budget=900):
    return sp_rebalance(data, budget)
