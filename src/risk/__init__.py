"""Risk system: risk assessment, engagement, history, value scales.

Public API (import from submodules):
    engagement: determine_engagement
    history: append_history_entry, pattern_signature, read_history
    loop: run_lightweight_risk_check, run_risk_loop
    package_builder: build_package_from_proposal, read_package, refresh_package
    serialization: load_risk_assessment
    types: PostureProfile, RiskMode, RiskPackage, RiskType, StepDecision
"""
