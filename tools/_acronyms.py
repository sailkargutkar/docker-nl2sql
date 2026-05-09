"""
Built-in synonym hints for common business / domain acronyms.

This is the deterministic, no-LLM equivalent of asking GPT "what does
GSTIN mean?". A small curated table covers the long tail of Indian
business, finance, government, and SaaS abbreviations the bulk of
real-world schemas use.

Adding new entries is one line — no API calls, no costs, no leaks.

Convention: keys are lowercased; values are lowercase synonym phrases
that a user might type instead of the exact column name.
"""

from __future__ import annotations


# ---------- Domain acronyms ----------

ACRONYM_SYNONYMS: dict[str, list[str]] = {
    # India tax / finance
    "gstin":  ["gst number", "gst id", "tax id", "tax registration", "vat"],
    "gst":    ["tax", "tax id", "vat"],
    "pan":    ["pan card", "pan number", "tax id", "permanent account number"],
    "tan":    ["tan number", "tax deduction account"],
    "pin":    ["postal code", "zip code", "pincode", "zip"],
    "tds":    ["tax deducted at source", "withholding tax"],
    "cin":    ["company id", "corporate id"],
    "udid":   ["disability id"],
    "aadhar": ["aadhaar", "uid", "unique id"],
    "aadhaar": ["aadhar", "uid", "unique id"],
    "ifsc":   ["bank code", "ifsc code"],
    "micr":   ["bank routing"],
    "upi":    ["upi id", "vpa", "virtual address"],

    # Generic identifiers / addressing
    "id":     ["identifier"],
    "uuid":   ["identifier", "uid"],
    "url":    ["link", "web address"],
    "uri":    ["link", "web address"],
    "ip":     ["ip address"],
    "dns":    ["domain"],
    "ssn":    ["social security number"],

    # Time / dates
    "ts":     ["timestamp", "time"],
    "dt":     ["date", "datetime"],
    "tz":     ["timezone"],
    "etl":    ["pipeline"],
    "eta":    ["arrival time", "estimated arrival"],

    # Money / commerce
    "amt":    ["amount"],
    "qty":    ["quantity", "count"],
    "sku":    ["product code", "item code", "stock keeping unit"],
    "ean":    ["barcode"],
    "po":     ["purchase order"],
    "inv":    ["invoice"],
    "ar":     ["accounts receivable", "receivable"],
    "ap":     ["accounts payable", "payable"],
    "cogs":   ["cost of goods"],
    "mrp":    ["max retail price", "list price"],
    "mom":    ["month over month"],
    "yoy":    ["year over year"],
    "ytd":    ["year to date"],

    # Telecom / contact
    "mob":    ["mobile", "phone", "phone number"],
    "tel":    ["telephone", "phone", "phone number"],
    "ext":    ["extension"],

    # Generic SaaS
    "kpi":    ["metric", "indicator"],
    "sla":    ["service level"],
    "crm":    ["customer relationship"],
    "erp":    ["enterprise resource planning"],
    "hr":     ["human resources", "people"],
    "qa":     ["quality assurance", "testing"],

    # Tour / fleet (tmt-relevant)
    "ets":    ["employee transport"],
    "tmt":    ["track my tour"],
    "dsn":    ["duty slip number"],
    "ds":     ["duty slip"],
    "rfid":   ["tag"],
    "obd":    ["onboard diagnostics"],
    "vin":    ["vehicle id"],
    "rc":     ["registration certificate"],

    # Booleans / status hints
    "fl":     ["flag"],
    "stat":   ["status"],
    "ind":    ["indicator", "flag"],
}


# ---------- Word-segment hints ----------

# Common camelCase / snake_case fragments that benefit from expansion.
# Different from ACRONYM_SYNONYMS in that these target *parts* of column
# names, not the whole name (e.g. paymentTerm → ["payment term", "billing term"]).
WORD_SYNONYMS: dict[str, list[str]] = {
    "no":         ["number"],
    "num":        ["number"],
    "name":       ["title", "label"],
    "phone":      ["mobile", "contact number", "telephone"],
    "mobile":     ["phone", "contact number", "cell"],
    "email":      ["email address", "mail"],
    "address":    ["location"],
    "street":     ["address line"],
    "city":       ["town"],
    "state":      ["region", "province"],
    "country":    ["nation"],
    "zip":        ["postal code", "pin"],
    "code":       ["identifier"],
    "type":       ["kind", "category"],
    "term":       ["duration", "period"],
    "payment":    ["billing", "pay"],
    "price":      ["cost", "amount"],
    "amount":     ["sum", "value", "total"],
    "total":      ["sum", "amount"],
    "discount":   ["reduction", "deduction"],
    "tax":        ["vat", "gst"],
    "license":    ["permit", "certification"],
    "expires":    ["expiry", "expiration", "valid till"],
    "expiry":     ["expires", "expiration"],
    "registered": ["enrolled", "signed up"],
    "active":     ["enabled", "live"],
    "enabled":    ["active", "on"],
    "disabled":   ["inactive", "off"],
    "verified":   ["confirmed", "validated"],
    "duration":   ["length", "period"],
    "distance":   ["kilometers", "km", "miles", "length"],
    "vehicle":    ["car", "transport"],
    "driver":     ["chauffeur"],
    "client":     ["customer", "company"],
    "guest":      ["passenger", "rider"],
    "tour":       ["trip", "journey"],
    "booking":    ["reservation"],
    "invoice":    ["bill"],
    "company":    ["organization", "firm", "business"],
    "organization": ["company", "firm", "business", "org"],
    "user":       ["account", "person"],
    "role":       ["permission", "access level"],
    "group":      ["team", "unit"],
    "department": ["dept", "division"],
    "category":   ["type", "kind", "group"],
    "subcategory": ["subtype", "subgroup"],
    "description": ["details", "summary"],
    "notes":      ["comments", "remarks"],
    "comments":   ["notes", "remarks"],
    "rating":     ["score", "stars"],
    "review":     ["feedback"],
    "tag":        ["label"],
    "icon":       ["image"],
    "pic":        ["picture", "image", "photo"],
    "photo":      ["picture", "image", "pic"],
    "url":        ["link"],
    "link":       ["url"],
}


def acronym_lookup(word: str) -> list[str]:
    """Return canonical synonyms for a known acronym, or []."""
    return ACRONYM_SYNONYMS.get(word.lower(), [])


def word_lookup(word: str) -> list[str]:
    """Return alternative phrasings for a single word, or []."""
    return WORD_SYNONYMS.get(word.lower(), [])
