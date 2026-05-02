"""Domain-specific HTTP exceptions with structured JSON error bodies."""

from fastapi import HTTPException, status


class AgentNotFound(HTTPException):
    def __init__(self, agent_id: str) -> None:
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "AGENT_NOT_FOUND", "agent_id": agent_id},
        )


class AgentFSMConflict(HTTPException):
    """Raised when a lifecycle transition violates the FSM."""

    def __init__(self, agent_id: str, current: str, requested: str) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "FSM_CONFLICT",
                "agent_id": agent_id,
                "current_state": current,
                "requested_transition": requested,
            },
        )


class InsufficientFunds(HTTPException):
    def __init__(self, agent_id: str, available: float, requested: float) -> None:
        super().__init__(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "code": "INSUFFICIENT_FUNDS",
                "agent_id": agent_id,
                "available_usd": available,
                "requested_usd": requested,
            },
        )


class MetabolicRunaway(HTTPException):
    def __init__(self, agent_id: str, burn_rate: float) -> None:
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "METABOLIC_RUNAWAY",
                "agent_id": agent_id,
                "burn_rate_usd_per_hour": burn_rate,
            },
        )


class ContractEscrowLocked(HTTPException):
    def __init__(self, contract_id: str) -> None:
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "ESCROW_ALREADY_LOCKED", "contract_id": contract_id},
        )


class PermissionDenied(HTTPException):
    def __init__(self, detail: str = "Permission denied") -> None:
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "PERMISSION_DENIED", "message": detail},
        )


class RateLimitExceeded(HTTPException):
    def __init__(self, resource: str) -> None:
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "RATE_LIMIT_EXCEEDED", "resource": resource},
        )


class WebhookVerificationFailed(HTTPException):
    def __init__(self) -> None:
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "WEBHOOK_VERIFICATION_FAILED"},
        )
