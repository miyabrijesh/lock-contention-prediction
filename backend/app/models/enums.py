from enum import Enum


class SeatState(str, Enum):
    AVAILABLE = "AVAILABLE"
    HELD = "HELD"
    BOOKED = "BOOKED"


class BookingStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


class AttemptOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    SEAT_UNAVAILABLE = "SEAT_UNAVAILABLE"
    LOCK_TIMEOUT = "LOCK_TIMEOUT"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ExperimentStatus(str, Enum):
    DRAFT = "DRAFT"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DemoScenarioName(str, Enum):
    BASELINE = "BASELINE"
    CONTENTION = "CONTENTION"


class DemoSessionStatus(str, Enum):
    WAITING_FOR_SELECTION = "WAITING_FOR_SELECTION"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"