from dataclasses import dataclass, field


@dataclass
class Job:
    source: str
    title: str
    company: str
    url: str
    location: str = ""
    remote: bool = False
    description: str = ""
    salary_text: str = ""
    salary_min: float | None = None   # LPA for INR, annual for other currencies
    salary_max: float | None = None
    salary_currency: str = ""         # "INR" | "USD" | "EUR" | ""
    posted_at: str = ""               # ISO timestamp, best effort
    skills: list[str] = field(default_factory=list)
    contact_name: str = ""
    contact_url: str = ""
    extra: dict = field(default_factory=dict)
