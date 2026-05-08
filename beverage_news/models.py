from dataclasses import dataclass, field


@dataclass
class Source:
    name: str
    url: str
    rss: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    country: str = "Global"
    region: str = "Mundial"
    language: str = "en"
    trade: bool = False


@dataclass
class Company:
    name: str
    country: str
    segments: list[str]
    aliases: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    title: str
    url: str
    source: str = "Unknown"
    source_url: str = ""
    country: str = "Global"
    region: str = "Mundial"
    language: str = ""
    published: str = ""
    summary: str = ""
    discovery: str = ""
    trade_source: bool = False


@dataclass
class Article:
    title: str
    url: str
    source: str
    country: str
    region: str
    language: str
    published: str
    summary: str
    body: str
    companies: list[str]
    segments: list[str]
    keyword_categories: list[str]
    discovery: str
