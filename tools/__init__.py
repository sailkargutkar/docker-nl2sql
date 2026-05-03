"""
LLM-as-build-oracle tools.

Dev/CI-only scripts that use OpenAI to enrich the schema DSL, generate
training data, and label uncertain history rows. Production runtime is
LLM-free — these tools never run in the deployable Docker image.

See tools/README.md for usage and security guarantees.
"""
