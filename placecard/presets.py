"""SAMPLE guest lists for the demo presets (invented people, favourites drawn from the SAMPLE catalogue).

The favourites are generated deterministically from taste "archetypes" so the lists look like real
RSVP data: each guest has a primary and a secondary archetype, and 2-4 favourites picked from them.
Every favourite exists in sample_qloo.CATALOG, so the keyless demo resolves all of them (except the
deliberately unmatched ones in the "mystery" preset, which exist to show the agent asking a question).
"""
import random

POOLS = {
    "K": ["Parasite", "Decision to Leave", "Past Lives", "Train to Busan", "Squid Game", "Crash Landing on You", "BTS",
          "NewJeans", "Seoul Garden BBQ", "Jjim House", "Seoul"],
    "J": ["Spirited Away", "Your Name", "Perfect Days", "Hikaru Utada", "Joe Hisaishi", "Norwegian Wood",
          "Attack on Titan", "Menya Kumo Ramen", "Sakura Sushi Bar", "Kyoto"],
    "I": ["Khruangbin", "Tame Impala", "Arctic Monkeys", "Tom Misch", "Phoebe Bridgers", "Radiohead",
          "The Grand Budapest Hotel", "Moonrise Kingdom", "Lady Bird", "Everything Everywhere All at Once",
          "Vinyl & Matcha", "Kopitiam Records Bar"],
    "F": ["Chef's Table", "Street Food", "The Bear", "Jiro Dreams of Sushi", "Ratatouille", "Salt Fat Acid Heat",
          "Eat Pray Love", "Parts Unknown", "Nasi Lemak Corner", "Hawker Lane", "Trattoria Pagi", "Pastry Atelier",
          "Daun Kitchen"],
    "O": ["Free Solo", "The Secret Life of Walter Mitty", "Into the Wild", "Planet Earth", "Wild", "Born to Run",
          "Patagonia", "Nike", "Mount Kinabalu", "Langkawi", "Bali"],
    "N": ["Sapiens", "The Midnight Library", "Pride and Prejudice", "Dune", "1984", "Normal People", "Succession",
          "Oppenheimer", "Inception", "Interstellar", "Black Mirror", "Project Hail Mary"],
    "G": ["Taylor Swift", "Beyonce", "Dua Lipa", "Bruno Mars", "The Devil Wears Prada", "Crazy Rich Asians",
          "Mamma Mia!", "La La Land", "Barbie", "Emily in Paris", "Sunset Rooftop Bar"],
    "C": ["Norah Jones", "Frank Sinatra", "Nat King Cole", "Casablanca", "The Sound of Music", "Roman Holiday",
          "P. Ramlee", "Yuna", "Sudirman", "Paddington 2", "Downton Abbey"],
    "H": ["Ted Lasso", "The Office", "Friends", "Finding Nemo", "Coco", "Encanto", "Toy Story", "Brooklyn Nine-Nine",
          "Mrs. Doubtfire", "Coldplay", "Ed Sheeran", "Laut Seafood House"],
    "L": ["Bad Bunny", "Kendrick Lamar", "Frank Ocean", "SZA", "Daniel Caesar", "Spider-Man: Into the Spider-Verse",
          "Narcos", "Taco Libre"],
    "X": ["Knives Out", "Gone Girl", "Mindhunter", "Making a Murderer", "Serial", "Sherlock", "Se7en"],
    "R": ["Metallica", "Foo Fighters", "Linkin Park", "Mad Max: Fury Road", "John Wick", "Top Gun: Maverick",
          "Smoke & Embers"],
    "Y": ["The Legend of Zelda: Breath of the Wild", "Elden Ring", "Hades", "Stardew Valley", "Attack on Titan",
          "Ender's Game"],
    "B": ["The Social Network", "Silicon Valley", "Mr. Robot", "Lex Fridman Podcast", "Acquired", "Darknet Diaries",
          "Zero to One", "The Hard Thing About Hard Things", "The Pragmatic Programmer",
          "Designing Data-Intensive Applications", "Notion", "Apple", "Huberman Lab", "Atomic Habits", "Radiolab",
          "Thinking, Fast and Slow"],
    "M": ["Arijit Singh", "Ravi Shankar", "Dilwale Dulhania Le Jayenge", "Lagaan", "Banana Leaf Corner"],
}

# (name, group, primary archetype, secondary archetype)
WEDDING = [
    ("Grandma Rose", "Grandparents", "C", "H"), ("Grandpa Lee", "Grandparents", "C", "N"),
    ("Raymond Tan", "Tan family", "C", "F"), ("Pearl Tan", "Tan family", "C", "H"),
    ("Daniel Tan", "Tan family", "F", "B"), ("Mei Tan", "Tan family", "K", "G"),
    ("Zainab Karim", "Karim family", "C", "H"), ("Rahim Karim", "Karim family", "C", "F"),
    ("Aisyah Karim", "Karim family", "J", "I"), ("Hafiz Karim", "Karim family", "R", "Y"),
    ("Priya Nair", "Bride's friends", "I", "F"), ("Sofia Lim", "Bride's friends", "G", "K"),
    ("Jia Wen", "Bride's friends", "K", "J"), ("Nadia Aziz", "Bride's friends", "G", "L"),
    ("Chloe Wong", "Bride's friends", "I", "N"), ("Amira Yusof", "Bride's friends", "F", "O"),
    ("Lakshmi Pillai", "Bride's friends", "M", "G"),
    ("Ben Ong", "Groom's friends", "Y", "B"), ("Farid Hassan", "Groom's friends", "R", "L"),
    ("Kevin Lau", "Groom's friends", "B", "Y"), ("Wei Jie", "Groom's friends", "J", "Y"),
    ("Arjun Menon", "Groom's friends", "L", "I"), ("Jason Teoh", "Groom's friends", "O", "R"),
    ("Haikal Rosli", "Groom's friends", "O", "F"), ("Syafiq Daud", "Groom's friends", "R", "H"),
    ("Dr. Siva Raman", "Colleagues", "N", "X"), ("Grace Lee", "Colleagues", "N", "I"),
    ("Marcus Chin", "Colleagues", "X", "N"), ("Elena Gomez", "Colleagues", "L", "G"),
    ("Natalie Foo", "Colleagues", "X", "N"), ("Irfan Malik", "Colleagues", "B", "N"),
    ("Yuki Tanaka", "Colleagues", "J", "I"), ("Hannah Goh", "Colleagues", "H", "G"),
    ("Victor Lam", "Colleagues", "R", "X"), ("Anita Raj", "Colleagues", "M", "F"),
    ("Ivy Tan", "Cousins", "H", "G"), ("Zack Karim", "Cousins", "Y", "R"), ("Mira Karim", "Cousins", "G", "K"),
    ("Leon Tan", "Cousins", "O", "B"), ("Qian Hui", "Cousins", "I", "K"),
]

CONFERENCE = [
    ("Dr. Amara Okafor", "Speakers", "B", "N"), ("Liam Chen", "Speakers", "B", "I"),
    ("Sunita Rao", "Speakers", "B", "F"), ("Tomas Novak", "Speakers", "N", "B"),
    ("Hana Sato", "Speakers", "J", "B"), ("Marco Ricci", "Speakers", "F", "B"),
    ("Jordan Blake", "Sponsors", "B", "R"), ("Katrina Wolfe", "Sponsors", "G", "B"),
    ("Omar Haddad", "Sponsors", "B", "F"), ("Priscilla Ng", "Sponsors", "G", "O"),
    ("Stefan Berg", "Sponsors", "R", "O"), ("Aiko Mori", "Sponsors", "J", "N"),
    ("Noah Fischer", "Attendees", "Y", "B"), ("Leila Farah", "Attendees", "N", "I"),
    ("Ethan Park", "Attendees", "K", "B"), ("Zara Malik", "Attendees", "X", "N"),
    ("Diego Alvarez", "Attendees", "L", "B"), ("Fiona Walsh", "Attendees", "O", "N"),
    ("Ravi Kapoor", "Attendees", "M", "B"), ("Ming Zhao", "Attendees", "Y", "J"),
    ("Chloe Martin", "Attendees", "I", "F"), ("Ahmad Zulkifli", "Attendees", "F", "O"),
    ("Bella Santos", "Attendees", "X", "Y"), ("Sam Okoye", "Attendees", "R", "B"),
]

MYSTERY = [
    ("Aiman", "Friends", ["Spirited Away", "Khruangbin"]),
    ("Mei", "Friends", ["Parasite", "BTS"]),
    ("Priya", "Friends", ["The Grand Budapest Hotel", "Tame Impala"]),
    ("Jun", "Friends", ["Zxqv Obscure Band", "Nonexistent Diner"]),
    ("Sara", "Friends", ["Qwerty Unknown Film"]),
    ("Adam", "Friends", ["Spirited Away", "Joe Hisaishi"]),
]


def _favourites(preset, name, primary, secondary):
    rng = random.Random(f"{preset}:{name}")
    favs = rng.sample(POOLS[primary], 2)
    extra = rng.choice([p for p in POOLS[secondary] if p not in favs])
    favs.append(extra)
    if rng.random() < 0.35:
        more = [p for p in POOLS[primary] if p not in favs]
        favs.append(rng.choice(more))
    return favs


def _build(preset, roster):
    return [{"name": n, "group": g, "favorites": _favourites(preset, n, p, s)} for n, g, p, s in roster]


PRESETS = {
    "wedding": {
        "title": "Sample wedding (40 guests)",
        "table_size": 8,
        "guests": _build("wedding", WEDDING),
        "constraints": "Keep the Tan family together.\nAisyah Karim and Hafiz Karim apart (sibling rivalry).\n"
                       "Put Grandma Rose and Grandpa Lee at the head table.",
    },
    "conference": {
        "title": "Sample conference dinner (24 guests)",
        "table_size": 6,
        "guests": _build("conference", CONFERENCE),
        "constraints": "Put Dr. Amara Okafor at the head table.\nJordan Blake and Katrina Wolfe apart (competing sponsors).",
    },
    "mystery": {
        "title": "Mystery guests (6 guests, 3 unmatched)",
        "table_size": 3,
        "guests": [{"name": n, "group": g, "favorites": f} for n, g, f in MYSTERY],
        "constraints": "",
    },
}


# ------------------------------------------------------------------------------------------------------
# LIVE presets: shown instead of the SAMPLE ones when a Qloo key is set. Every favourite is a real title,
# artist, brand or place that Qloo's /search resolves (checked against the hackathon API, see TUNING.md),
# mixed across film, TV, music, books, podcasts, games, brands and places, with a strong Malaysian and wider
# Asian share. A trailing hint such as "(game)" or "(book)" tells the resolver which Qloo type to use where
# a bare name is ambiguous ("Hades" is also a band, "Dune" a film and a book).
LIVE_POOLS = {
    "K": ["Parasite", "Squid Game", "Crash Landing on You", "BTS", "BLACKPINK", "NewJeans", "Train to Busan",
          "Decision to Leave", "IU", "Oldboy", "Itaewon Class", "Seoul"],
    "J": ["Spirited Away", "Your Name", "My Neighbor Totoro", "Attack on Titan", "Joe Hisaishi", "Utada Hikaru",
          "Norwegian Wood", "Demon Slayer (tv show)", "Uniqlo", "Tokyo", "Kyoto (destination)"],
    "I": ["Khruangbin", "Tame Impala", "Arctic Monkeys", "Phoebe Bridgers", "Radiohead", "The Grand Budapest Hotel",
          "Moonrise Kingdom", "Lady Bird", "Everything Everywhere All at Once", "Fleabag", "Vampire Weekend"],
    "F": ["Chef's Table (tv show)", "Jiro Dreams of Sushi", "The Bear", "Ratatouille", "Anthony Bourdain: Parts Unknown",
          "Salt, Fat, Acid, Heat", "Jalan Alor Food Street", "Village Park Restaurant", "Din Tai Fung", "Nobu",
          "Street Food (tv show)"],
    "O": ["Free Solo", "Into the Wild", "Planet Earth", "The Secret Life of Walter Mitty", "Patagonia", "The North Face",
          "Mount Kinabalu", "Langkawi", "Born to Run", "Wild (book)", "Bali",
          "Our Planet"],
    "N": ["Sapiens", "The Midnight Library", "Pride and Prejudice", "Dune (book)", "1984", "Normal People",
          "Succession", "Oppenheimer", "Inception", "Interstellar", "Black Mirror", "Project Hail Mary"],
    "G": ["Taylor Swift", "Beyonce", "Dua Lipa", "Bruno Mars", "The Devil Wears Prada", "Crazy Rich Asians",
          "Mamma Mia!", "La La Land", "Barbie", "Emily in Paris"],
    "C": ["Norah Jones", "Frank Sinatra", "Nat King Cole", "Casablanca", "The Sound of Music", "Roman Holiday",
          "Downton Abbey", "Paddington 2", "Louis Armstrong"],
    "H": ["Ted Lasso", "The Office", "Friends", "Finding Nemo", "Coco", "Encanto", "Toy Story", "Brooklyn Nine-Nine",
          "Coldplay", "Ed Sheeran", "Mrs. Doubtfire", "Upin & Ipin"],
    "L": ["Bad Bunny", "Kendrick Lamar", "Frank Ocean", "SZA", "Daniel Caesar", "Spider-Man: Into the Spider-Verse",
          "Narcos", "Drake", "Tyler, the Creator"],
    "X": ["Knives Out", "Gone Girl (movie)", "Mindhunter", "Making a Murderer", "Serial", "Sherlock",
          "True Detective", "Zodiac"],
    "R": ["Metallica", "Foo Fighters", "Linkin Park", "Mad Max: Fury Road", "John Wick", "Top Gun: Maverick", "AC/DC",
          "Red Bull", "Fast & Furious"],
    "Y": ["The Legend of Zelda: Breath of the Wild", "Elden Ring", "Hades (game)", "Stardew Valley", "Minecraft (game)",
          "Hollow Knight", "Baldur's Gate 3", "Nintendo", "Mobile Legends (game)"],
    "B": ["The Social Network", "Silicon Valley", "Mr. Robot", "Lex Fridman Podcast", "Acquired", "Darknet Diaries",
          "Zero to One (book)", "The Hard Thing About Hard Things", "The Pragmatic Programmer", "Notion",
          "Apple Inc", "Huberman Lab", "Atomic Habits", "Radiolab", "Thinking, Fast and Slow", "Tesla"],
    "M": ["Siti Nurhaliza", "Yuna", "Ola Bola", "Munafik", "Mat Kilau", "Petronas Twin Towers", "Penang",
          "Boh Tea Plantation", "Petaling Street Market", "Batu Caves", "Namewee", "Zee Avi", "Sudirman", "Air Asia"],
    "D": ["Dilwale Dulhania Le Jayenge", "Lagaan (movie)", "3 Idiots", "Arijit Singh", "A. R. Rahman", "Ravi Shankar",
          "Sacred Games", "Kabhi Khushi Kabhie Gham", "Dangal", "Banana Leaf Apolo"],
    "Z": ["Jay Chou", "Kung Fu Hustle", "Crouching Tiger, Hidden Dragon", "In the Mood for Love", "Infernal Affairs",
          "Faye Wong", "JJ Lin", "Mayday (band)", "Ip Man", "The Untamed"],
    "S": ["The Raid (movie)", "Ong-Bak", "Bangkok", "Raisa", "Isyana Sarasvati", "Gundala", "The Protector (movie)",
          "Agnez Mo", "Hanoi", "Bali"],
}

# (name, group, primary archetype, secondary archetype): the same fictional guests as the SAMPLE lists,
# with tastes remapped to the wider Malaysian / Asian mix.
LIVE_WEDDING = [
    ("Grandma Rose", "Grandparents", "C", "H"), ("Grandpa Lee", "Grandparents", "Z", "C"),
    ("Raymond Tan", "Tan family", "Z", "F"), ("Pearl Tan", "Tan family", "C", "M"),
    ("Daniel Tan", "Tan family", "F", "B"), ("Mei Tan", "Tan family", "K", "G"),
    ("Zainab Karim", "Karim family", "M", "H"), ("Rahim Karim", "Karim family", "M", "F"),
    ("Aisyah Karim", "Karim family", "J", "I"), ("Hafiz Karim", "Karim family", "R", "Y"),
    ("Priya Nair", "Bride's friends", "I", "F"), ("Sofia Lim", "Bride's friends", "G", "K"),
    ("Jia Wen", "Bride's friends", "K", "J"), ("Nadia Aziz", "Bride's friends", "G", "M"),
    ("Chloe Wong", "Bride's friends", "I", "N"), ("Amira Yusof", "Bride's friends", "F", "M"),
    ("Lakshmi Pillai", "Bride's friends", "D", "G"),
    ("Ben Ong", "Groom's friends", "Y", "B"), ("Farid Hassan", "Groom's friends", "R", "M"),
    ("Kevin Lau", "Groom's friends", "B", "Y"), ("Wei Jie", "Groom's friends", "J", "Y"),
    ("Arjun Menon", "Groom's friends", "L", "D"), ("Jason Teoh", "Groom's friends", "O", "R"),
    ("Haikal Rosli", "Groom's friends", "O", "M"), ("Syafiq Daud", "Groom's friends", "S", "M"),
    ("Dr. Siva Raman", "Colleagues", "N", "X"), ("Grace Lee", "Colleagues", "N", "I"),
    ("Marcus Chin", "Colleagues", "X", "N"), ("Elena Gomez", "Colleagues", "L", "G"),
    ("Natalie Foo", "Colleagues", "X", "N"), ("Irfan Malik", "Colleagues", "B", "N"),
    ("Yuki Tanaka", "Colleagues", "J", "I"), ("Hannah Goh", "Colleagues", "H", "M"),
    ("Victor Lam", "Colleagues", "R", "X"), ("Anita Raj", "Colleagues", "D", "F"),
    ("Ivy Tan", "Cousins", "M", "G"), ("Zack Karim", "Cousins", "Y", "M"), ("Mira Karim", "Cousins", "G", "M"),
    ("Leon Tan", "Cousins", "O", "B"), ("Qian Hui", "Cousins", "Z", "K"),
]

LIVE_CONFERENCE = [
    ("Dr. Amara Okafor", "Speakers", "B", "N"), ("Liam Chen", "Speakers", "B", "I"),
    ("Sunita Rao", "Speakers", "B", "D"), ("Tomas Novak", "Speakers", "N", "B"),
    ("Hana Sato", "Speakers", "J", "B"), ("Marco Ricci", "Speakers", "F", "B"),
    ("Jordan Blake", "Sponsors", "B", "R"), ("Katrina Wolfe", "Sponsors", "G", "B"),
    ("Omar Haddad", "Sponsors", "B", "F"), ("Priscilla Ng", "Sponsors", "Z", "O"),
    ("Stefan Berg", "Sponsors", "R", "O"), ("Aiko Mori", "Sponsors", "J", "N"),
    ("Noah Fischer", "Attendees", "Y", "B"), ("Leila Farah", "Attendees", "N", "I"),
    ("Ethan Park", "Attendees", "K", "B"), ("Zara Malik", "Attendees", "X", "N"),
    ("Diego Alvarez", "Attendees", "L", "B"), ("Fiona Walsh", "Attendees", "O", "N"),
    ("Ravi Kapoor", "Attendees", "D", "B"), ("Ming Zhao", "Attendees", "Z", "Y"),
    ("Chloe Martin", "Attendees", "I", "F"), ("Ahmad Zulkifli", "Attendees", "M", "O"),
    ("Bella Santos", "Attendees", "S", "Y"), ("Sam Okoye", "Attendees", "R", "B"),
]

# Three of these resolve, three do not: the agent should ask rather than guess.
LIVE_MYSTERY = [
    ("Aiman", "Friends", ["Spirited Away", "Khruangbin"]),
    ("Mei", "Friends", ["Parasite", "BTS"]),
    ("Priya", "Friends", ["The Grand Budapest Hotel", "Tame Impala"]),
    ("Jun", "Friends", ["Zxqv Obscure Band", "Nonexistent Diner Qwx"]),
    ("Sara", "Friends", ["Qwerty Unknown Film Xyzzy"]),
    ("Adam", "Friends", ["Spirited Away", "Joe Hisaishi"]),
]


def _live_favourites(preset, name, primary, secondary):
    rng = random.Random(f"live:{preset}:{name}")
    favs = rng.sample(LIVE_POOLS[primary], 2)
    favs.append(rng.choice([p for p in LIVE_POOLS[secondary] if p not in favs]))
    if rng.random() < 0.35:
        favs.append(rng.choice([p for p in LIVE_POOLS[primary] if p not in favs]))
    return favs


def _live_build(preset, roster):
    return [{"name": n, "group": g, "favorites": _live_favourites(preset, n, p, s)} for n, g, p, s in roster]


LIVE_PRESETS = {
    "wedding": {
        "title": "Wedding (40 guests)",
        "table_size": 8,
        "guests": _live_build("wedding", LIVE_WEDDING),
        "constraints": PRESETS["wedding"]["constraints"],
    },
    "conference": {
        "title": "Conference dinner (24 guests)",
        "table_size": 6,
        "guests": _live_build("conference", LIVE_CONFERENCE),
        "constraints": PRESETS["conference"]["constraints"],
    },
    "mystery": {
        "title": "Mystery guests (6 guests, 3 unmatched)",
        "table_size": 3,
        "guests": [{"name": n, "group": g, "favorites": f} for n, g, f in LIVE_MYSTERY],
        "constraints": "",
    },
}


def for_mode(live):
    """The guest lists to offer: real-title lists for live Qloo, the SAMPLE-catalogue lists otherwise."""
    return LIVE_PRESETS if live else PRESETS
