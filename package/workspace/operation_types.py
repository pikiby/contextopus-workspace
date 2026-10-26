"""Static operation bounds mirrored from Workspace's runtime guards.

Annotations publish the same limits to callers and reject invalid arguments
before dispatch. Runtime guards remain responsible for direct Python calls.
"""

from typing import Annotated, Literal

from pydantic import Field


Offset = Annotated[int, Field(ge=0)]
PageLimit = Annotated[int, Field(ge=1, le=100)]
EventLimit = Annotated[int, Field(ge=1, le=1000)]
SearchResultLimit = Annotated[int, Field(ge=1, le=1000)]
CandidateLimit = Annotated[int, Field(ge=1, le=100000)]
SearchOutput = Literal["materials", "fragments"]
ExcerptChars = Annotated[int, Field(ge=1, le=100000)]
BlobBytes = Annotated[int, Field(ge=0, le=16000000)]
ReservationTTL = Annotated[int, Field(ge=1, le=3600)]
ReservationReason = Annotated[str, Field(min_length=1, max_length=240)]
MaterialIDs = Annotated[list[str], Field(min_length=1, max_length=100)]
ContextNames = Annotated[list[str], Field(min_length=1, max_length=1000)]
EventID = Annotated[str, Field(min_length=1, max_length=1024)]
EventIDs = Annotated[list[EventID], Field(min_length=1, max_length=1000)]
LineageDepth = Annotated[int, Field(ge=0, le=20)]
ReferenceIndex = Annotated[int, Field(ge=-1)]
