"""Return pose clauses, or None while a required verification is pending."""
from .extreme_filter import current_drc_factors


def filter_rules(mode,reconstruct,base,evidence):
    if mode==2 and not reconstruct:return ()
    if reconstruct and not (evidence and evidence.get('complete')):return None
    if mode==2:return evidence.get('connection_factors',())
    if not (base and base.get('checked')):return None
    rules=current_drc_factors(base,moving_only=reconstruct)
    if reconstruct:return (*rules,*evidence.get('red_factors',()),*evidence.get('connection_factors',()))
    return rules
