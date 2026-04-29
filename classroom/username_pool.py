"""Generates unique, memorable, gender-neutral usernames for classroom sessions."""

import random

ADJECTIVES = [
    "Arctic", "Neon", "Thunder", "Crystal", "Cosmic", "Stellar", "Solar",
    "Lunar", "Ember", "Cobalt", "Crimson", "Jade", "Amber", "Onyx",
    "Indigo", "Teal", "Scarlet", "Silver", "Golden", "Iron", "Stone",
    "Blazing", "Frozen", "Storm", "Drift", "Spark", "Shadow", "Swift",
    "Bright", "Dark", "Wild", "Calm", "Sharp", "Quiet", "Brave",
    "Bold", "True", "Keen", "Vast", "Deep", "Tall", "Grand",
]

ANIMALS = [
    "Fox", "Tiger", "Wolf", "Eagle", "Bear", "Hawk", "Raven", "Lynx",
    "Falcon", "Puma", "Crane", "Orca", "Bison", "Moose", "Osprey",
    "Marten", "Heron", "Viper", "Gecko", "Condor", "Pelican", "Narwhal",
    "Walrus", "Marmot", "Kestrel", "Ibis", "Jackal", "Dingo", "Quokka",
    "Tapir", "Capybara", "Pangolin", "Axolotl", "Okapi", "Fossa",
]


def generate_username(exclude: set[str] | None = None) -> str:
    """Return a unique AdjAnimal username not in `exclude`."""
    exclude = exclude or set()
    attempts = 0
    while attempts < 500:
        name = random.choice(ADJECTIVES) + random.choice(ANIMALS)
        if name not in exclude:
            return name
        attempts += 1
    # Fallback: append a number
    return random.choice(ADJECTIVES) + random.choice(ANIMALS) + str(random.randint(2, 99))
