"""TED's vocabularies, and how they map onto ours.

Every value here was read off live responses, not off documentation. The sampling
that produced them is written up in docs/TED_API_CONTRACT.md.

``form-type`` is the reliable axis for the stage of a procurement: prior
information and market consultation separate cleanly on it, which is what we
need. ``notice-type`` refines it. ``notice-subtype`` is an opaque code such as
"16", "E1" or "T01" and is stored as a passthrough string, never interpreted.
"""

from __future__ import annotations

from watchdog.core.enums import ContractNature, NoticeStage

# form-type -> the stage of the procurement. Observed values only.
FORM_TYPE_TO_STAGE: dict[str, NoticeStage] = {
    "planning": NoticeStage.PRIOR_INFORMATION,
    "consultation": NoticeStage.MARKET_CONSULTATION,
    "competition": NoticeStage.CONTRACT_NOTICE,
    "change": NoticeStage.MODIFICATION,
    "result": NoticeStage.AWARD,
    "dir-awa-pre": NoticeStage.AWARD,
}

# The reverse, for building a query. Only the stages we can ask TED for.
STAGE_TO_FORM_TYPE: dict[NoticeStage, str] = {
    NoticeStage.PRIOR_INFORMATION: "planning",
    NoticeStage.MARKET_CONSULTATION: "consultation",
    NoticeStage.CONTRACT_NOTICE: "competition",
    NoticeStage.MODIFICATION: "change",
    NoticeStage.AWARD: "result",
}

# notice-type, used only where it is more specific than form-type.
# "pmc" is a preliminary market consultation; the "pin-*" family is prior information.
NOTICE_TYPE_TO_STAGE: dict[str, NoticeStage] = {
    "pmc": NoticeStage.MARKET_CONSULTATION,
    "pin-only": NoticeStage.PRIOR_INFORMATION,
    "pin-buyer": NoticeStage.PRIOR_INFORMATION,
    "pin-rtl": NoticeStage.PRIOR_INFORMATION,
    "pin-tran": NoticeStage.PRIOR_INFORMATION,
    "pin-cfc-standard": NoticeStage.PRIOR_INFORMATION,
    "pin-cfc-social": NoticeStage.PRIOR_INFORMATION,
}

CONTRACT_NATURE_VALUES: dict[str, ContractNature] = {
    "services": ContractNature.SERVICES,
    "works": ContractNature.WORKS,
    "supplies": ContractNature.SUPPLIES,
}

# The classification scheme we understand. TED states it per notice, and a code
# from any other scheme is not a CPV code and is not stored as one.
CPV_SCHEME = "cpv"


def stage_for(form_type: str | None, notice_type: str | None) -> NoticeStage:
    """The stage of a notice: form-type first, notice-type as a second opinion."""
    if form_type:
        stage = FORM_TYPE_TO_STAGE.get(form_type.strip().lower())
        if stage is not None:
            return stage

    if notice_type:
        stage = NOTICE_TYPE_TO_STAGE.get(notice_type.strip().lower())
        if stage is not None:
            return stage

    return NoticeStage.OTHER


def contract_nature_for(value: str | None) -> ContractNature | None:
    """One TED nature value, or None if it is not one we know."""
    if not value:
        return None
    return CONTRACT_NATURE_VALUES.get(value.strip().lower())
