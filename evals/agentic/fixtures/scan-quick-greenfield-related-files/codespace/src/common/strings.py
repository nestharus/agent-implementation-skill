def normalize_slug(value: str) -> str:
    return value.strip().lower().replace(" ", "-")
