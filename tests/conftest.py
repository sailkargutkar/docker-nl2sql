"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from app.schema_dsl import Column, Relationship, Schema, Table


@pytest.fixture
def schema() -> Schema:
    return Schema(
        database="tmt",
        description="Test fixture schema",
        tables=[
            Table(
                name="Client",
                description="Business customer",
                columns=[
                    Column(name="id", type="uuid", pk=True),
                    Column(name="name", type="string"),
                    Column(name="email", type="string"),
                    Column(name="enabled", type="boolean"),
                    Column(name="createdAt", type="timestamptz"),
                    Column(name="OrganizationId", type="uuid", fk="Organization.id"),
                ],
            ),
            Table(
                name="Organization",
                columns=[
                    Column(name="id", type="uuid", pk=True),
                    Column(name="name", type="string"),
                ],
            ),
            Table(
                name="Employee",
                description="Registered employees",
                columns=[
                    Column(name="id", type="uuid", pk=True),
                    Column(name="name", type="string"),
                    Column(name="clientId", type="uuid", fk="Client.id"),
                    Column(name="salary", type="numeric"),
                    Column(name="active", type="boolean", synonyms=["enabled"]),
                    Column(name="hiredAt", type="date"),
                ],
            ),
            Table(
                name="User",
                description="Registered users",
                columns=[
                    Column(name="id", type="uuid", pk=True),
                    Column(name="name", type="string"),
                    Column(name="OrganizationId", type="uuid", fk="Organization.id"),
                ],
            ),
        ],
    )
