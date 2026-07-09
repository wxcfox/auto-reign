MANIFEST_PATH = "manifest.md"
RAW_SOURCE_DIR = "raw"
EXTRACTED_SOURCE_DIR = "extracted"

SOURCE_SIDE_CAR_DIRECTORIES = (RAW_SOURCE_DIR,)

WORKSPACE_DIRECTORIES = (
    RAW_SOURCE_DIR,
    EXTRACTED_SOURCE_DIR,
    "profile",
    "knowledge",
    "questions",
    "projects",
    "practice",
    "review",
    "state",
    "reports",
    ".revisions",
)

CANDIDATE_PROFILE_PATH = "profile/candidate.md"
TARGET_PROFILE_PATH = "profile/target.md"
MASTERY_PATH = "state/mastery.md"
REVIEW_STATUS_PATH = "review/status.md"
HIGH_FREQUENCY_PATH = "review/high-frequency.md"
