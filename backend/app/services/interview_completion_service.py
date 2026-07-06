from __future__ import annotations


class InterviewCompletionService:
    """Legacy import shim.

    Interview completion is now handled by InterviewService against the
    user-scoped conversations/messages model.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def finish_session(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("InterviewCompletionService is deprecated; use InterviewService.finish_session")
