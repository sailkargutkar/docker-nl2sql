"""
Seed training corpus for the intent classifier.

These examples are written to cover the *shape* of a question, not a specific
schema. The actual table/column resolution happens at inference time from the
schema DSL — so a seed like "how many X are there" generalizes to any table.

Feel free to extend. The fit pipeline also consumes successful production
queries from history.db, so this seed only has to bootstrap the cold start.
"""

from __future__ import annotations

# (question, intent_label)
SEED: list[tuple[str, str]] = [
    # count
    ("how many employees are there", "count"),
    ("count all clients", "count"),
    ("total number of tours", "count"),
    ("how many active users do we have", "count"),
    ("count of pending invoices", "count"),
    ("how many drivers signed up this month", "count"),
    ("number of bookings today", "count"),
    ("count vehicles in the fleet", "count"),
    ("how many organizations are registered", "count"),
    ("how many trips were completed", "count"),
    ("how many clients are enabled", "count"),
    ("count rows in the drivers table", "count"),
    ("total users in org swaraj", "count"),
    ("total employees belonging to client acme", "count"),
    ("total drivers for organization foo", "count"),
    ("total contact persons of client", "count"),
    ("total tours for client bar", "count"),

    # list / select
    ("show me all clients", "list"),
    ("list the employees", "list"),
    ("give me the names of active drivers", "list"),
    ("fetch all pending tours", "list"),
    ("display the invoices for march", "list"),
    ("show all vehicles", "list"),
    ("get all users who signed up yesterday", "list"),
    ("list organizations in mumbai", "list"),
    ("show the bookings from last week", "list"),
    ("employees with email set", "list"),
    ("all contact persons for client acme", "list"),
    ("drivers licensed to drive sedans", "list"),

    # sum
    ("total kilometers driven this month", "sum"),
    ("sum of invoice amounts", "sum"),
    ("total revenue from bookings", "sum"),
    ("how much did we bill client acme", "sum"),
    ("total hours worked by drivers", "sum"),
    ("aggregate distance covered", "sum"),

    # avg
    ("average trip duration", "avg"),
    ("mean invoice amount", "avg"),
    ("average number of bookings per day", "avg"),
    ("avg fare per trip", "avg"),
    ("average distance of a tour", "avg"),

    # min
    ("earliest booking date", "min"),
    ("lowest invoice amount", "min"),
    ("minimum trip duration", "min"),
    ("first tour created", "min"),
    ("smallest vehicle capacity", "min"),

    # max
    ("latest booking", "max"),
    ("highest invoice amount", "max"),
    ("maximum trip distance", "max"),
    ("last tour created", "max"),
    ("largest vehicle capacity", "max"),
    ("most recent login", "max"),

    # top
    ("top 5 clients by revenue", "top"),
    ("top 10 drivers by kilometers driven", "top"),
    ("first 3 bookings", "top"),
    ("top 20 most expensive invoices", "top"),
    ("top 5 organizations by employee count", "top"),

    # exists
    ("is there a client named acme", "exists"),
    ("does any employee have email admin at example", "exists"),
    ("are there any pending invoices", "exists"),
    ("do we have tours scheduled for tomorrow", "exists"),
    ("any drivers without a license", "exists"),
    ("are there any pending orders", "exists"),
    ("are there any active clients", "exists"),
    ("are there products without a category", "exists"),
    ("is there an organization called swaraj", "exists"),
    ("do we have any employees from mumbai", "exists"),
    ("any orders today", "exists"),
    ("any clients with overdue invoices", "exists"),
    ("does the database have a vehicles table", "exists"),
]
